"""Application startup, shutdown, and background loops."""

import asyncio
import contextlib
import json
import logging
import os
import time
from contextlib import asynccontextmanager

import httpx

from spira_home.clock import VirtualClock
from spira_home.connectors.mqtt import MQTTConnector
from spira_home.models import Task
from spira_home.runtime import Runtime


log = logging.getLogger('spira.home')


async def consume(runtime, connector):
    """Feed independent connector events back into Home after reconnects."""
    while True:
        try:
            async for event in connector.events():
                runtime.observe(event)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception('connector subscription disconnected')
            await asyncio.sleep(1)


async def worker(runtime):
    """Schedule local work and reconcile controls that arrived during dispatch."""
    last_compaction=0
    while True:
        try:
            await runtime.tick()
            if time.monotonic()-last_compaction>=60:
                runtime.compact()
                last_compaction=time.monotonic()
            # STOP after dispatch cannot erase its actual outcome; restore on the next pass.
            for row in runtime.store.rows('SELECT id,body FROM tasks WHERE restore_status IS NULL'):
                t = Task.model_validate_json(row['body'])
                if runtime.store.one('SELECT * FROM controls WHERE id IN (?,?)', ('stop:'+t.plan_id, 'disable:'+t.key)):
                    await runtime.restore(row['id'])
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception('execution loop failed; no unresolved dispatch is retried')
        await asyncio.sleep(.2)


async def publish(runtime):
    """Drain the legacy result webhook when configured; C-1–C-4 belong to ServiceBridge."""
    url = os.getenv('SERVICE_RESULTS_URL')
    if not url:
        return
    async with httpx.AsyncClient(timeout=5) as client:
        while True:
            for row in runtime.store.rows('SELECT * FROM outbox WHERE delivered=0 ORDER BY seq LIMIT 20'):
                try:
                    result = await client.post(url, json=dict(result_id=row['seq'], result=json.loads(row['body'])))
                    result.raise_for_status()
                    runtime.store.write('UPDATE outbox SET delivered=1 WHERE seq=?', (row['seq'],))
                except httpx.HTTPError:
                    break
            await asyncio.sleep(1)


@asynccontextmanager
async def lifespan(app):
    """Own configured connectors, the recovered runtime, and background loops."""
    connectors = {}
    if os.getenv('MQTT_ENABLED', '1') == '1':
        c = MQTTConnector(os.getenv('MQTT_HOST', '127.0.0.1'), int(os.getenv('MQTT_PORT', '18883')), os.getenv('MQTT_PREFIX', 'spira/testbed'))
        await c.start()
        connectors['mqtt'] = c
    if os.getenv('HA_URL'):
        from spira_home.connectors.ha import HAConnector
        connectors['ha'] = HAConnector(os.environ['HA_URL'], os.environ['HA_TOKEN'])
    if os.getenv('MATTER_URL'):
        from spira_home.connectors.matter import MatterConnector
        connectors['matter'] = MatterConnector(os.environ['MATTER_URL'], json.loads(os.getenv('MATTER_EVENT_ROLES', '{}')))
    clock = None
    if os.getenv('HOME_TEST_MODE') == '1':
        if set(connectors) != {'mqtt'}:
            raise RuntimeError('virtual clock mode is isolated to the MQTT testbed')
        clock_data = await connectors['mqtt'].request('clock')
        if not clock_data['virtual']:
            raise RuntimeError('Home virtual mode requires an independent virtual world clock')
        clock = VirtualClock(clock_data['timestamp'])
    runtime = Runtime(os.getenv('HOME_DB', 'state/home.db'), connectors, clock)
    service = None
    if os.getenv('SERVICE_URL'):
        from spira_home.service import ServiceBridge
        service = ServiceBridge(runtime, os.environ['SERVICE_URL'], os.environ['HOME_HOUSEHOLD_ID'], os.getenv('HUB_KEY',''))
    app.state.runtime = runtime
    jobs = [asyncio.create_task(worker(runtime)), asyncio.create_task(publish(runtime))]
    jobs += [asyncio.create_task(consume(runtime, c)) for c in connectors.values()]
    if service:
        jobs.append(asyncio.create_task(service.run()))
    yield
    for job in jobs:
        job.cancel()
    for job in jobs:
        with contextlib.suppress(asyncio.CancelledError):
            await job
    for c in connectors.values():
        await c.close()
    if service:
        await service.close()
    runtime.store.close()

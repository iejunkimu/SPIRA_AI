"""Durable Home-side adapter for the actual Settle 3.2 hub REST boundaries."""
import asyncio
import json
import logging
import time
import uuid
import httpx
from spira_home.bridge import Plan, translate, execution_wire, event_wire, iso
from spira_home.models import Event, Task

log=logging.getLogger('spira.service')


class ServiceBridge:
    def __init__(self,runtime,url,household,key=''):
        self.runtime,self.household=runtime,household
        self.client=httpx.AsyncClient(base_url=url.rstrip('/'),headers={'X-Hub-Key':key} if key else {},timeout=5)
        record=runtime.store.one("SELECT value FROM bridge_state WHERE id='hub_id'")
        if not record:
            runtime.store.write('INSERT INTO bridge_state VALUES(?,?)',('hub_id',uuid.uuid4().hex))
            record=runtime.store.one("SELECT value FROM bridge_state WHERE id='hub_id'")
        self.hub_id=record['value']
        self.last_inventory=0
        self.last_pull=0

    async def ingest_plan(self,body):
        plan=Plan.model_validate(body)
        if plan.household_id!=self.household:
            raise ValueError('plan household does not match this node')
        tasks,cancelled=translate(plan)
        return self.runtime.admit_plan(plan.plan_id,plan.plan_version,plan.model_dump(),tasks,cancelled)

    async def register(self):
        devices=[]
        for c in self.runtime.connectors.values():
            for cap in await c.discover():
                devices.append(dict(id=cap.target.partition(':')[2],target=cap.target,type=cap.device_type,room=cap.room,
                                    connect=cap.substrate,commands=cap.commands,writable=cap.writable,
                                    events=cap.events,available=cap.available,families=cap.families,
                                    conditional_write=cap.conditional_write,assumption=cap.assumption))
        response=await self.client.put('/v1/hub/households/'+self.household+'/devices',json=dict(household_id=self.household,updated_at=iso(self.runtime.now()),devices=devices))
        response.raise_for_status()

    async def pull(self):
        # Per-plan versions are not a global monotonic cursor. Pull current snapshots
        # and rely on durable per-plan identity/floors until Service defines a global cursor.
        response=await self.client.get('/v1/hub/plans',params={'household':self.household,'since_version':0})
        response.raise_for_status()
        body=response.json()
        plans=body.get('plans',[]) if isinstance(body,dict) else body
        for plan in plans[:200]:
            try:
                await self.ingest_plan(plan)
            except ValueError as e:
                self.error('PLAN_REJECTED',type(e).__name__)

    def error(self,kind,detail):
        self.runtime.store.write('INSERT OR REPLACE INTO bridge_state VALUES(?,?)',('last_error',json.dumps(dict(kind=kind,detail=detail,at=time.time()))))

    async def flush(self):
        for row in self.runtime.store.rows('SELECT * FROM event_outbox WHERE delivered=0 ORDER BY seq LIMIT 30'):
            event=Event.model_validate_json(row['body'])
            response=await self.client.post('/v1/hub/events',json=[event_wire(event,self.household)])
            if response.status_code==422:
                self.error('EVENT_SCHEMA_REJECTED',event.event_id)
                self.runtime.store.write('UPDATE event_outbox SET delivered=-1 WHERE seq=?',(row['seq'],))
            else:
                response.raise_for_status()
                self.runtime.store.write('UPDATE event_outbox SET delivered=1 WHERE seq=?',(row['seq'],))
        for row in self.runtime.store.rows('SELECT * FROM outbox WHERE delivered=0 ORDER BY seq LIMIT 30'):
            result=json.loads(row['body'])
            key=json.dumps([result['plan_id'],result['plan_version'],result['task_id']],separators=(',',':'))
            task=self.runtime.task(key)
            task['result']=result
            wire=execution_wire(task)
            wire.update(household_id=self.household,result_id=self.hub_id+':'+str(row['seq']))
            response=await self.client.post('/v1/hub/executions',json=wire)
            if response.status_code==422:
                self.error('EXECUTION_SCHEMA_REJECTED',wire['result_id'])
                self.runtime.store.write('UPDATE outbox SET delivered=-1 WHERE seq=?',(row['seq'],))
            else:
                response.raise_for_status()
                self.runtime.store.write('UPDATE outbox SET delivered=1 WHERE seq=?',(row['seq'],))

    async def step(self):
        current=time.monotonic()
        if current-self.last_inventory>=15:
            await self.register()
            self.last_inventory=current
        if current-self.last_pull>=2:
            await self.pull()
            self.last_pull=current
        await self.flush()

    async def run(self):
        backoff=.5
        while True:
            try:
                await self.step()
                backoff=.5
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.error('SERVICE_UNAVAILABLE',type(e).__name__)
                backoff=min(10,backoff*2)
            await asyncio.sleep(backoff)

    async def close(self):
        await self.client.aclose()

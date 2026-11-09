"""Opt-in independent world fault controls. Never imported by the Home runtime."""
import copy
import json
import threading
import time
import uuid

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal


class Fault(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str
    target: str = 'mqtt:light'
    kind: Literal['read', 'command'] = 'read'
    skip: int = Field(default=0, ge=0)
    stage: Literal['before', 'after'] = 'after'
    hold: bool = False
    drop: bool = False
    delay: float = Field(default=0, ge=0, le=10)
    stale: bool = False
    omit: list[str] = Field(default_factory=list)
    partial: list[str] | None = None


class Pressure:
    def __init__(self, world):
        self.w = world
        self.faults = []
        self.pending = {}
        world.db.execute('CREATE TABLE IF NOT EXISTS pressure_journal(seq INTEGER PRIMARY KEY AUTOINCREMENT,body TEXT)')
        world.db.commit()

    def record(self, kind, **data):
        with self.w.lock:
            row = dict(kind=kind, occurrence_time=self.w.now(), observation_time=time.time(),
                       processing_time=time.monotonic(), **data)
            self.w.db.execute('INSERT INTO pressure_journal(body) VALUES(?)', (json.dumps(row),))
            self.w.db.commit()

    def arm(self, fault):
        with self.w.lock:
            if any(f['id'] == fault.id for f in self.faults) or fault.id in self.pending:
                raise HTTPException(409, 'fault identity is already armed')
            data = fault.model_dump()
            data['snapshot'] = self.w.state(fault.target)
            self.faults.append(data)
            self.record('fault.armed', fault=fault.model_dump())
        return {'armed': fault.id}

    def select(self, kind, body):
        with self.w.lock:
            for fault in self.faults:
                if fault['kind'] == kind and fault['target'] == body.get('target'):
                    if fault['skip']:
                        fault['skip'] -= 1
                        return None
                    self.faults.remove(fault)
                    return fault
        return None

    def message(self, client, body, kind):
        fault = self.select(kind, body)
        # A held/delayed response must not block MQTT's network loop or another
        # actor's HTTP access to the independent device state.
        if fault:
            thread = threading.Thread(target=self.respond, args=(client, body, kind, fault), daemon=True)
            thread.start()
        else:
            self.respond(client, body, kind, None)

    def respond(self, client, body, kind, fault):
        release = threading.Event()
        if fault and fault['hold']:
            with self.w.lock:
                self.pending[fault['id']] = release
        def boundary(stage):
            if fault and fault['stage'] == stage:
                self.record('fault.reached', fault_id=fault['id'], stage=stage,
                            target=body.get('target'), operation_id=body.get('operation_id'))
                if fault['hold'] and not release.wait(15):
                    self.record('fault.expired', fault_id=fault['id'])
                if fault['delay']:
                    time.sleep(fault['delay'])
        try:
            boundary('before')
            with self.w.lock:
                request = copy.deepcopy(body)
                if kind == 'command':
                    self.record('command.received', target=body['target'], operation_id=body['operation_id'],
                                desired=body['desired'], expected=body.get('expected'))
                    if fault and fault['partial'] is not None:
                        request['desired'] = {k:v for k,v in body['desired'].items() if k in fault['partial']}
                data = self.w.handle(kind, request)
                if kind == 'read':
                    if fault and fault['stale']:
                        data = copy.deepcopy(fault['snapshot'])
                    if fault:
                        for prop in fault['omit']:
                            data['values'].pop(prop, None)
                            data['revisions'].pop(prop, None)
                    self.record('read.sampled', target=body['target'], observation=data)
                elif kind == 'command':
                    self.record('command.receipt', target=body['target'], receipt=data)
                reply = dict(request_id=body['request_id'], data=data)
            boundary('after')
        except Exception as error:
            reply = dict(request_id=body.get('request_id'), error=type(error).__name__+': '+str(error))
        finally:
            if fault:
                with self.w.lock:
                    self.pending.pop(fault['id'], None)
        if fault and fault['drop']:
            self.record('reply.lost', kind_lost=kind, target=body.get('target'))
            return
        if body.get('reply_to', '').startswith(self.w.prefix+'/reply/'):
            client.publish(body['reply_to'], json.dumps(reply), qos=1, retain=False)
            if kind in ('read', 'command'):
                self.record('reply.sent', reply_kind=kind, target=body['target'], data=reply.get('data'))


def install(app):
    """Routes exist only in explicitly enabled independent world processes."""
    def pressure():
        value = app.state.world.pressure
        if value is None:
            raise HTTPException(404, 'WORLD_PRESSURE is disabled')
        return value

    @app.post('/pressure/fault')
    def arm(body: Fault):
        return pressure().arm(body)

    @app.post('/pressure/release/{identifier}')
    def release(identifier: str):
        p = pressure()
        with p.w.lock:
            if identifier not in p.pending:
                raise HTTPException(409, 'fault has not reached a hold boundary')
            p.pending[identifier].set()
        return {'released': identifier}

    @app.get('/pressure/journal')
    def journal(after: int = 0):
        p = pressure()
        with p.w.lock:
            return [dict(sequence=r[0], **json.loads(r[1])) for r in p.w.db.execute(
                'SELECT seq,body FROM pressure_journal WHERE seq>? ORDER BY seq LIMIT 2000', (after,))]

    @app.post('/pressure/clear')
    def clear():
        p = pressure()
        with p.w.lock:
            p.faults.clear()
            for event in p.pending.values():
                event.set()
        return {'cleared': True}

    @app.post('/pressure/event')
    def event(body: dict):
        p = pressure()
        with p.w.lock:
            target = body.get('target')
            data = dict(event_id=body.get('event_id', uuid.uuid4().hex), type=body['type'],
                        source=body.get('source', target or 'world'), at=body.get('at', p.w.now()),
                        provenance='UNKNOWN', substrate='VIRTUAL_MQTT', payload=body.get('payload', {}))
            if target:
                data['observation'] = p.w.state(target)
            p.record('event.delivered', event=data)
            p.w.client.publish(p.w.prefix+'/events', json.dumps(data), qos=1, retain=False)
        return data

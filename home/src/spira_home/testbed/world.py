"""Small adversary actor using a real MQTT broker; owns state outside Home."""
import json
import os
import sqlite3
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
import paho.mqtt.client as mqtt
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict
from typing import Any, Literal
from spira_home.testbed.profiles import profiles, validate


class Mutation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    target: str = 'mqtt:light'
    values: dict[str, Any] | None = None
    mode: Literal['normal', 'reject', 'unchanged', 'delayed', 'readback_unavailable', 'blind_after_command', 'offline'] | None = None
    delay: float = 0.4
    event_type: str | None = None
    source: str = 'mqtt:contact'
    payload: dict[str, Any] = {}
    provenance: Literal['SIMULATOR','USER','UNKNOWN'] = 'SIMULATOR'


class World:
    def __init__(self, db, host, port, prefix):
        Path(db).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(db, check_same_thread=False)
        self.db.execute('CREATE TABLE IF NOT EXISTS state(id TEXT PRIMARY KEY,body TEXT)')
        self.db.execute('CREATE TABLE IF NOT EXISTS ops(id TEXT PRIMARY KEY,body TEXT)')
        self.db.execute('CREATE TABLE IF NOT EXISTS control(id TEXT PRIMARY KEY,value TEXT)')
        self.db.execute('CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,target TEXT,due REAL)')
        self.virtual = os.getenv('WORLD_CLOCK', '0') == '1'
        selected=self.db.execute("SELECT value FROM control WHERE id='profile'").fetchone()
        if not selected:
            self.db.execute("INSERT INTO control VALUES('profile',?)",(os.getenv('WORLD_PROFILE','lg_assumed'),))
            self.db.commit()
            selected=self.db.execute("SELECT value FROM control WHERE id='profile'").fetchone()
        self.profiles = profiles(selected[0])
        if self.virtual and not self.db.execute("SELECT value FROM control WHERE id='clock'").fetchone():
            self.db.execute("INSERT INTO control VALUES('clock',?)", (os.getenv('WORLD_START',str(time.time())),))
            self.db.commit()
        self.lock = threading.RLock()
        from spira_home.testbed.pressure import Pressure
        self.pressure = Pressure(self) if os.getenv('WORLD_PRESSURE') == '1' else None
        self.mode, self.delay = {}, {}
        self.count = self.db.execute('SELECT count(*) FROM ops').fetchone()[0]
        self.prefix = prefix
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id='world-'+uuid.uuid4().hex)
        self.client.on_connect = self.connect
        self.client.on_message = self.message
        for target, profile in self.profiles.items():
            if not self.db.execute('SELECT * FROM state WHERE id=?', (target,)).fetchone():
                self.save(target, profile['values'].copy(), {k: uuid.uuid4().hex for k in profile['values']}, 'SIMULATOR')
        self.client.will_set(prefix+'/availability', 'offline', retain=True)
        self.client.connect(host, port, 20)
        self.client.loop_start()

    def connect(self, client, userdata, flags, reason, props):
        for topic in ['discover', 'read', 'command', 'clock']:
            client.subscribe(self.prefix+'/'+topic, qos=1)
        client.publish(self.prefix+'/availability', 'online', retain=True)

    def state(self, target):
        row = self.db.execute('SELECT body FROM state WHERE id=?', (target,)).fetchone()
        if not row:
            raise KeyError(target)
        return json.loads(row[0])

    def save(self, target, values, revisions, provenance):
        data = dict(target=target, values=values, revisions=revisions, provenance=provenance,
                    substrate='VIRTUAL_MQTT', available=True, observed_at=self.now())
        self.db.execute('INSERT OR REPLACE INTO state VALUES(?,?)', (target, json.dumps(data)))
        self.db.commit()
        return data

    def now(self):
        if self.virtual:
            return float(self.db.execute("SELECT value FROM control WHERE id='clock'").fetchone()[0])
        return time.time()

    def progress(self):
        with self.lock:
            for identifier, target, due in list(self.db.execute('SELECT * FROM jobs WHERE due<=?', (self.now(),))):
                state = self.state(target)
                if state['values'].get('job_id')==identifier and state['values'].get('run_state')=='running':
                    self.mutate(target, {'run_state':'completed'},provenance='DEVICE')
                    self.event('job.completed',target,payload={'job_id':identifier})
                self.db.execute('DELETE FROM jobs WHERE id=?',(identifier,))
                self.db.commit()

    def advance(self, timestamp):
        with self.lock:
            if not self.virtual:
                raise ValueError('virtual clock is not enabled')
            if timestamp < self.now():
                raise ValueError('clock cannot move backwards')
            self.db.execute("UPDATE control SET value=? WHERE id='clock'", (str(timestamp),))
            self.db.commit()
            self.event('clock.advanced','world',payload={'timestamp':timestamp})
            self.progress()
            return {'timestamp':self.now(),'next_due':self.next_due()}

    def next_due(self):
        value=self.db.execute('SELECT min(due) FROM jobs WHERE due>?',(self.now(),)).fetchone()[0]
        return value

    def event(self, type, source, observation=None, payload=None, provenance='SIMULATOR'):
        data = dict(event_id=uuid.uuid4().hex, type=type, source=source, at=self.now(),
                    provenance=provenance, substrate='VIRTUAL_MQTT', observation=observation, payload=payload or {})
        self.client.publish(self.prefix+'/events', json.dumps(data), qos=1, retain=False)

    def mutate(self, target, values, provenance='SIMULATOR'):
        with self.lock:
            state = self.state(target)
            for k, v in values.items():
                if k not in state['values']:
                    raise ValueError('unknown property')
                if state['values'][k] != v or provenance != 'SPIRA':
                    state['revisions'][k] = uuid.uuid4().hex
                state['values'][k] = v
            state = self.save(target, state['values'], state['revisions'], provenance)
            if self.pressure:
                self.pressure.record('effect.applied' if provenance == 'SPIRA' else 'external.mutation',
                                     target=target, values=values, world_state=state,
                                     ground_truth_actor='controller' if provenance == 'SPIRA' else 'independent_actor')
            self.event('state.changed', target, state, provenance=provenance)
            return state

    def message(self, client, userdata, msg):
        if msg.retain:
            return
        if self.pressure:
            try:
                self.pressure.message(client, json.loads(msg.payload), msg.topic.rsplit('/', 1)[-1])
            except (ValueError, KeyError):
                pass
            return
        try:
            body = json.loads(msg.payload)
            with self.lock:
                data = self.handle(msg.topic.rsplit('/', 1)[-1], body)
            reply = dict(request_id=body['request_id'], data=data)
        except Exception as e:
            if 'body' not in locals():
                return
            reply = dict(request_id=body.get('request_id'), error=type(e).__name__+': '+str(e))
        if body.get('reply_to', '').startswith(self.prefix+'/reply/'):
            client.publish(body['reply_to'], json.dumps(reply), qos=1, retain=False)

    def handle(self, kind, body):
        self.progress()
        if kind == 'clock':
            return {'timestamp':self.now(),'virtual':self.virtual,'next_due':self.next_due()}
        if kind == 'discover':
            return [dict(target=target, writable=p['writable'], families=p['families'],
                         available=self.mode.get(target) != 'offline', conditional_write=True,
                         substrate='VIRTUAL_MQTT', device_type=p['type'], room=p.get('room'),
                         events=p.get('events',[]), assumption=p.get('assumption'), noisy=p.get('noisy',False),
                         commands=p.get('commands',[])) for target,p in self.profiles.items()]
        target = body['target']
        mode = self.mode.get(target, 'normal')
        if kind == 'read':
            if mode in ('offline', 'readback_unavailable'):
                raise RuntimeError(mode)
            state = self.state(target)
            state['observed_at'] = self.now()
            return state
        operation = body['operation_id']
        old = self.db.execute('SELECT body FROM ops WHERE id=?', (operation,)).fetchone()
        if old:
            return json.loads(old[0])
        current = self.state(target)
        desired = body['desired']
        status = 'ACCEPTED'
        invalid = validate(self.profiles[target],desired,body.get('expected'))
        if invalid:
            status = invalid
        elif mode == 'offline':
            status = 'UNAVAILABLE'
        elif mode == 'reject':
            status = 'REJECTED'
        elif body.get('expected') and any(current['revisions'].get(k) != v for k,v in body['expected'].items()):
            status = 'CONFLICT'
        if target=='mqtt:robot_01' and desired.get('run_state')=='running' and current['values'].get('run_state')=='running':
            status='REJECTED'
        result = dict(status=status, operation_id=operation)
        if status=='ACCEPTED' and desired.get('run_state')=='running':
            result['job_id']=operation
        # Durable receipt dedup: unresolved delayed operations are not silently replayed.
        self.db.execute('INSERT INTO ops VALUES(?,?)', (operation, json.dumps(result)))
        self.db.commit()
        self.count += 1
        if status == 'ACCEPTED' and mode != 'unchanged':
            if desired.get('run_state')=='running':
                desired={**desired,'job_id':operation}
                self.db.execute('INSERT INTO jobs VALUES(?,?,?)',(operation,target,self.now()+self.profiles[target].get('job_seconds',90)))
                self.db.commit()
            if mode == 'delayed':
                timer = threading.Timer(self.delay.get(target, .4), self.mutate, args=(target, desired, 'SPIRA'))
                timer.daemon = True
                timer.start()
            else:
                self.mutate(target, desired,provenance='SPIRA')
            if mode == 'blind_after_command':
                self.mode[target] = 'readback_unavailable'
        return result


@asynccontextmanager
async def lifespan(app):
    app.state.world = World(os.getenv('WORLD_DB', 'state/world.db'), os.getenv('MQTT_HOST', '127.0.0.1'),
                            int(os.getenv('MQTT_PORT', '18883')), os.getenv('MQTT_PREFIX', 'spira/testbed'))
    yield
    app.state.world.client.disconnect()
    app.state.world.client.loop_stop()
    app.state.world.db.close()


app = FastAPI(title='Independent household adversary', lifespan=lifespan)
from spira_home.testbed.pressure import install
install(app)


@app.get('/world')
def inspect():
    w = app.state.world
    with w.lock:
        w.progress()
        return dict(devices=[w.state(r[0]) for r in w.db.execute('SELECT id FROM state')], commands=w.count, modes=w.mode, timestamp=w.now(), virtual=w.virtual, next_due=w.next_due())


@app.post('/mutate')
def mutate(body: Mutation):
    w = app.state.world
    try:
        with w.lock:
            if body.mode:
                w.mode[body.target], w.delay[body.target] = body.mode, body.delay
                w.event('availability.changed', body.target)
            if body.values is not None:
                w.mutate(body.target, body.values,body.provenance)
            if body.event_type:
                w.event(body.event_type, body.source,payload=body.payload,provenance=body.provenance)
            return w.state(body.target)
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))


class Advance(BaseModel):
    timestamp: float


@app.post('/sim/clock')
def advance_clock(body: Advance):
    try:
        return app.state.world.advance(body.timestamp)
    except ValueError as e:
        raise HTTPException(409,str(e))


@app.get('/devices')
def devices():
    return inspect()


class ProfileMode(BaseModel):
    profile: Literal['lg_assumed','public_api']


@app.post('/sim/profile')
def select_profile(body: ProfileMode):
    if not app.state.world.virtual:
        raise HTTPException(404,'profile switching is only available in the isolated virtual testbed')
    with app.state.world.lock:
        app.state.world.profiles=profiles(body.profile)
        app.state.world.db.execute("UPDATE control SET value=? WHERE id='profile'",(body.profile,))
        app.state.world.db.commit()
        app.state.world.event('capability.changed','world',payload={'profile':body.profile})
    return {'profile':body.profile}

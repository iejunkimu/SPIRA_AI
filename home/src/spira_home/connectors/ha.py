"""HA supplies I/O; Home retains eligibility, verification and ownership."""
import json
from datetime import datetime
import httpx
import websockets
from spira_home.models import Capability, Observation, Receipt, Event
from .base import Unavailable


class HAConnector:
    def __init__(self, url, token):
        self.url, self.token = url.rstrip('/'), token
        self.client = httpx.AsyncClient(base_url=self.url, headers={'Authorization': 'Bearer '+token} if token else {}, timeout=5)
        self.subscribed = False

    async def request(self, method, path, **kwargs):
        try:
            r = await self.client.request(method, path, **kwargs)
            r.raise_for_status()
            return r.json()
        except httpx.HTTPError as e:
            raise Unavailable('connector HTTP request failed: '+type(e).__name__) from e

    async def discover(self):
        states = await self.request('GET', '/api/states')
        return [Capability(target='ha:'+s['entity_id'], writable=['power'],
                           available=s['state'] not in ('unavailable', 'unknown'), substrate='HA')
                for s in states if s['entity_id'].split('.')[0] in ('switch', 'light', 'input_boolean')]

    def observation(self, state):
        return Observation(target='ha:'+state['entity_id'], values={'power': state['state'] == 'on'} if state['state'] in ('on', 'off') else {},
                           revisions={}, available=state['state'] not in ('unknown', 'unavailable'),
                           substrate='HA', provenance='UNKNOWN')

    async def read(self, target):
        state = await self.request('GET', '/api/states/'+target.removeprefix('ha:'))
        return self.observation(state)

    async def execute(self, target, desired, operation_id, expected=None):
        if expected:
            return Receipt(status='UNSUPPORTED', operation_id=operation_id, detail='HA has no atomic property CAS')
        if set(desired) != {'power'} or not isinstance(desired['power'], bool):
            return Receipt(status='UNSUPPORTED', operation_id=operation_id)
        entity = target.removeprefix('ha:')
        domain = entity.split('.')[0]
        if domain not in ('light', 'switch', 'input_boolean'):
            return Receipt(status='UNSUPPORTED', operation_id=operation_id)
        await self.request('POST', '/api/services/'+domain+('/turn_on' if desired['power'] else '/turn_off'), json={'entity_id': entity})
        return Receipt(status='ACCEPTED', operation_id=operation_id)

    async def events(self):
        if self.subscribed:
            import uuid
            yield Event(event_id='ha-gap:'+uuid.uuid4().hex, type='observation.gap',
                        source='ha', substrate='HA', payload={'reason':'HA_WEBSOCKET_RECONNECT_WITHOUT_REPLAY'})
        url = self.url.replace('http://', 'ws://').replace('https://', 'wss://')+'/api/websocket'
        async with websockets.connect(url) as ws:
            await ws.recv()
            await ws.send(json.dumps({'type': 'auth', 'access_token': self.token}))
            if json.loads(await ws.recv())['type'] != 'auth_ok':
                raise Unavailable('HA authentication failed')
            await ws.send(json.dumps({'id': 1, 'type': 'subscribe_events', 'event_type': 'state_changed'}))
            self.subscribed = True
            async for message in ws:
                data = json.loads(message)
                if data.get('type') != 'event':
                    continue
                event = data['event']
                state = event['data'].get('new_state')
                if state:
                    yield Event(event_id='ha:'+event['context']['id']+':'+state['entity_id']+':'+event['time_fired'], type='state.changed',
                                source='ha:'+state['entity_id'], at=datetime.fromisoformat(event['time_fired']).timestamp(),
                                substrate='HA', provenance='UNKNOWN', observation=self.observation(state))

    async def close(self):
        await self.client.aclose()

"""Thin adapter to the upstream Matter controller helper, never a state echo."""
import asyncio
from urllib.parse import urlencode
from spira_home.models import Capability, Observation, Receipt, Event
from .ha import HAConnector
from .base import Unavailable


class MatterConnector(HAConnector):
    def __init__(self, url, event_roles=None):
        super().__init__(url, '')
        self.cursor = 0
        self.epoch = ''
        self.event_roles = event_roles or {}

    async def discover(self):
        return [Capability.model_validate(x) for x in await self.request('GET','/capabilities')]

    async def read(self, target):
        observation = Observation.model_validate(await self.request('GET', '/read?' + urlencode({'target': target})))
        if observation.target != target:
            raise Unavailable('Matter read target mismatch')
        return observation

    async def execute(self, target, desired, operation_id, expected=None):
        receipt = Receipt.model_validate(await self.request('POST','/command',json=dict(target=target,desired=desired,operation_id=operation_id,expected=expected)))
        if receipt.operation_id != operation_id:
            raise Unavailable('Matter command receipt identity mismatch')
        return receipt

    async def events(self):
        while True:
            body = await self.request('GET', '/events?' + urlencode({'after': self.cursor, 'epoch': self.epoch}))
            if body.get('gap'):
                yield Event(event_id='matter-gap:'+body['epoch']+':'+str(body['cursor']),
                            type='observation.gap', source='matter:controller', substrate='VIRTUAL_MATTER',
                            payload={'reason': 'controller restart or bounded event buffer overrun'})
            for e in body['events']:
                event = Event.model_validate(e)
                # Normalize atomic protocol facts here. Home's local rules retain
                # responsibility for correlation and household meaning.
                p = event.payload
                # An event received late must retain its occurrence time. A
                # device-relative timestamp cannot authorize a wall-clock rule.
                timestamp = p.get('timestamp')
                timed = p.get('timestamp_kind') == 'epoch' and isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool)
                if event.type == 'matter.event' and timed:
                    event.at = timestamp / 1000
                role = self.event_roles.get(event.source)
                if event.type == 'matter.event' and timed and role == 'doorbell' and p.get('cluster') == 'switch' and p.get('event') == 'initialPress':
                    event.type = 'doorbell.pressed'
                elif event.type == 'matter.event' and timed and role == 'contact' and p.get('cluster') == 'booleanState' and p.get('event') == 'stateChange':
                    value = p.get('data', {}).get('stateValue')
                    if isinstance(value, bool):
                        event.type = 'contact.closed' if value else 'contact.opened'
                yield event
            self.cursor, self.epoch = body['cursor'], body['epoch']
            await asyncio.sleep(.1)

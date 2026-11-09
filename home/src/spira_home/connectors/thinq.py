"""Official ThinQ SDK boundary. No invented LG display/lock properties.

Mappings are admitted from actual device profile objects. This adapter does not
assume a model-independent preparation/start/lock capability.
"""
import asyncio
from spira_home.models import Capability, Observation, Receipt, Event
from .base import Unavailable


class ThinQConnector:
    def __init__(self, api, devices):
        # target -> {device_id, profile: actual SDK profile, read: SDK status parser}
        self.api, self.devices = api, devices

    async def discover(self):
        return [Capability(target=target, writable=list(device['profile'].writable_properties),
                           families=device.get('families', ['SET_STATE']), substrate='THINQ')
                for target, device in self.devices.items()]

    async def read(self, target):
        d = self.devices.get(target)
        if not d:
            raise Unavailable('ThinQ target not admitted')
        from aiohttp import ClientError
        from thinqconnect.thinq_api import ThinQAPIException
        try:
            status = await self.api.async_get_device_status(d['device_id'])
        except (ThinQAPIException, ClientError, TimeoutError) as e:
            raise Unavailable('ThinQ status unavailable: ' + type(e).__name__) from e
        if status is None:
            raise Unavailable('ThinQ state missing')
        return Observation(target=target, values=d['read'](status), substrate='THINQ', provenance='UNKNOWN')

    async def execute(self, target, desired, operation_id, expected=None):
        d = self.devices.get(target)
        if not d or expected or len(desired) != 1:
            return Receipt(status='UNSUPPORTED', operation_id=operation_id)
        profile = d['profile']
        from aiohttp import ClientError
        from thinqconnect.thinq_api import ThinQAPIException
        attribute, value = next(iter(desired.items()))
        if attribute not in profile.writable_properties:
            return Receipt(status='UNSUPPORTED', operation_id=operation_id)
        try:
            prop = profile.get_property(attribute)
            kind = prop['type']
            if kind == 'enum':
                payload = profile.get_enum_attribute_payload(attribute, value)
            elif kind == 'range':
                payload = profile.get_range_attribute_payload(attribute, value)
            else:
                payload = profile.get_attribute_payload(attribute, value)
            if not payload:
                return Receipt(status='UNSUPPORTED', operation_id=operation_id)
        except (ValueError, KeyError):
            return Receipt(status='REJECTED', operation_id=operation_id)
        try:
            await self.api.async_post_device_control(d['device_id'], payload)
            return Receipt(status='ACCEPTED', operation_id=operation_id)
        except (ThinQAPIException, ClientError, TimeoutError, ValueError, KeyError) as e:
            # An API/transport exception after dispatch does not prove no effect.
            # Runtime records INDETERMINATE and must not blindly retry.
            raise Unavailable('ThinQ control uncertain: ' + type(e).__name__) from e

    async def events(self):
        # Honest fallback until authenticated SDK MQTT callback is wired.
        # Polling observes independent vendor status, never a command echo.
        last = {}
        while True:
            for target in self.devices:
                try:
                    observation = await self.read(target)
                    if last.get(target) != observation.values:
                        last[target] = observation.values
                        yield Event(event_id='thinq:'+target+':'+str(observation.observed_at), type='state.changed',
                                    source=target, substrate='THINQ', observation=observation)
                except Exception:
                    yield Event(event_id='thinq:unavailable:'+target+':'+str(__import__('time').time()),
                                type='availability.changed', source=target, substrate='THINQ')
            await asyncio.sleep(30)

    async def close(self):
        pass

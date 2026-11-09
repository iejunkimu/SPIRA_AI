"""MQTT is transport only. Read replies must be generated from device-owned state."""
import asyncio
import json
import uuid
import paho.mqtt.client as mqtt
from spira_home.models import Capability, Observation, Receipt, Event
from .base import Unavailable


class MQTTConnector:
    def __init__(self, host='127.0.0.1', port=18883, prefix='spira/testbed'):
        self.host, self.port, self.prefix = host, port, prefix
        self.pending = {}
        self.queue = asyncio.Queue(maxsize=4096)
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id='home-'+uuid.uuid4().hex)
        self.client.on_connect = self._connect
        self.client.on_message = self._message
        self.client.on_disconnect = self._disconnect
        self.ready = asyncio.Event()
        self.connected_once = False

    async def start(self):
        self.loop = asyncio.get_running_loop()
        await asyncio.to_thread(self.client.connect, self.host, self.port, 20)
        self.client.loop_start()
        try:
            await asyncio.wait_for(self.ready.wait(), 5)
        except TimeoutError:
            raise Unavailable('MQTT connection timeout')

    def _connect(self, client, userdata, flags, reason, properties):
        if reason == 0:
            client.subscribe(self.prefix+'/reply/'+client._client_id.decode()+'/#', qos=1)
            client.subscribe(self.prefix+'/events', qos=1)
            self.loop.call_soon_threadsafe(self.ready.set)
            if self.connected_once:
                self.loop.call_soon_threadsafe(self._gap, 'MQTT_RECONNECTED_WITHOUT_REPLAY')
            self.connected_once = True

    def _disconnect(self, client, userdata, flags, reason, properties):
        self.loop.call_soon_threadsafe(self.ready.clear)
        self.loop.call_soon_threadsafe(self._gap, 'MQTT_SUBSCRIPTION_DISCONNECTED')

    def _gap(self, reason):
        if self.queue.full():
            self.queue.get_nowait()
        self.queue.put_nowait(Event(event_id='mqtt-gap:'+uuid.uuid4().hex,
            type='observation.gap', source='mqtt', substrate='VIRTUAL_MQTT', payload={'reason':reason}))

    def _message(self, client, userdata, msg):
        try:
            body = json.loads(msg.payload)
        except (ValueError, UnicodeDecodeError):
            return
        self.loop.call_soon_threadsafe(self._deliver, msg.topic, body, msg.retain)

    def _deliver(self, topic, body, retained):
        if topic.endswith('/events'):
            if retained:
                return
            try:
                event = Event.model_validate(body)
                if not self.queue.full():
                    self.queue.put_nowait(event)
                else:
                    self._gap('MQTT_EVENT_BUFFER_OVERFLOW')
            except ValueError:
                return
        else:
            future = self.pending.get(body.get('request_id'))
            if future and not future.done() and not retained:
                future.set_result(body)

    async def request(self, kind, **body):
        request_id = uuid.uuid4().hex
        future = self.loop.create_future()
        self.pending[request_id] = future
        body.update(request_id=request_id, reply_to=self.prefix+'/reply/'+self.client._client_id.decode()+'/'+request_id)
        try:
            self.client.publish(self.prefix+'/'+kind, json.dumps(body), qos=1, retain=False)
            response = await asyncio.wait_for(future, 2)
            if response.get('error'):
                raise Unavailable(response['error'])
            return response['data']
        except TimeoutError as e:
            raise Unavailable('MQTT '+kind+' reply unavailable') from e
        finally:
            self.pending.pop(request_id, None)

    async def discover(self):
        return [Capability.model_validate(c) for c in await self.request('discover')]

    async def read(self, target):
        return Observation.model_validate(await self.request('read', target=target))

    async def execute(self, target, desired, operation_id, expected=None):
        return Receipt.model_validate(await self.request('command', target=target, desired=desired,
                                                       operation_id=operation_id, expected=expected))

    async def events(self):
        while True:
            yield await self.queue.get()

    async def close(self):
        self.client.disconnect()
        await asyncio.to_thread(self.client.loop_stop)

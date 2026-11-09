import json
import httpx
import pytest
from spira_home.connectors.matter import MatterConnector
from spira_home.connectors.base import Unavailable


def client(handler, roles=None):
    c = MatterConnector('http://protocol', roles)
    c.client = httpx.AsyncClient(base_url=c.url, transport=httpx.MockTransport(handler))
    return c


async def test_multiple_targets_are_passed_without_plug_assumptions():
    calls = []
    def handler(req):
        calls.append(req)
        if req.url.path == '/read':
            return httpx.Response(200, json={'target':req.url.params['target'], 'values':{'humidity':52}, 'substrate':'VIRTUAL_MATTER'})
        return httpx.Response(200, json={'status':'ACCEPTED', 'operation_id':'o'})
    c = client(handler)
    target = 'matter:node-b/17'
    assert (await c.read(target)).target == target
    assert (await c.execute(target, {'mode':1}, 'o')).status == 'ACCEPTED'
    assert json.loads(calls[1].content)['target'] == target
    await c.close()


async def test_missing_target_is_unavailable_and_no_state_is_invented():
    c = client(lambda req:httpx.Response(404, json={'error':'not commissioned'}))
    with pytest.raises(Unavailable):
        await c.read('matter:absent/9')
    await c.close()


def raw_event(kind='initialPress', cluster='switch', timestamp_kind='epoch'):
    return dict(event_id='native:1', type='matter.event', source='matter:n/3', at=9000,
                substrate='VIRTUAL_MATTER', payload=dict(cluster=cluster,event=kind,number='1',
                timestamp=1000000,timestamp_kind=timestamp_kind,data={'stateValue':False}))


@pytest.mark.parametrize('role,time_kind,expected', [('doorbell','epoch','doorbell.pressed'),
                         (None,'epoch','matter.event'),('doorbell','system','matter.event')])
async def test_protocol_event_roles_and_occurrence_time(role,time_kind,expected):
    event = raw_event(timestamp_kind=time_kind)
    c = client(lambda req:httpx.Response(200,json={'epoch':'a','cursor':1,'events':[event]}), {'matter:n/3':role})
    stream=c.events(); observed=await anext(stream)
    assert observed.type==expected and observed.provenance=='UNKNOWN'
    assert observed.at==(1000 if time_kind=='epoch' else 9000)
    await stream.aclose(); await c.close()


async def test_controller_restart_resets_cursor_and_reports_evidence_gap():
    requests=[]
    def handler(req):
        requests.append(req.url)
        return httpx.Response(200,json={'epoch':'new','cursor':1,'gap':True,'events':[raw_event()]})
    c=client(handler,{'matter:n/3':'doorbell'});c.cursor=250;c.epoch='old'
    stream=c.events()
    assert (await anext(stream)).type=='observation.gap'
    assert (await anext(stream)).type=='doorbell.pressed'
    assert requests[0].params['epoch']=='old' and requests[0].params['after']=='250'
    await stream.aclose();await c.close()


async def test_cross_target_read_and_wrong_receipt_cannot_verify_another_device():
    c=client(lambda req:httpx.Response(200,json={'target':'matter:other/1','values':{'power':True},'substrate':'VIRTUAL_MATTER'}))
    with pytest.raises(Unavailable,match='target mismatch'):await c.read('matter:n/2')
    await c.close()
    c=client(lambda req:httpx.Response(200,json={'status':'ACCEPTED','operation_id':'another-attempt'}))
    with pytest.raises(Unavailable,match='identity mismatch'):await c.execute('matter:n/2',{'power':True},'ours')
    await c.close()

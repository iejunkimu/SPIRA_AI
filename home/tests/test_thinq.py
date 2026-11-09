from unittest.mock import AsyncMock
import pytest
pytest.importorskip('thinqconnect')
from thinqconnect.devices.air_purifier import AirPurifierProfile
from thinqconnect.devices.const import Property
from thinqconnect.thinq_api import ThinQAPIException
from spira_home.connectors.thinq import ThinQConnector
from spira_home.connectors.base import Unavailable
from spira_home.models import Task
from spira_home.runtime import Runtime


def connector():
    # A validator fixture, explicitly not an official appliance/example profile.
    profile=AirPurifierProfile({'property':{'operation':{'airPurifierOperationMode':{
        'type':'enum','mode':['r','w'],'value':{'r':['POWER_ON','POWER_OFF'],'w':['POWER_ON','POWER_OFF']}}}}})
    api=AsyncMock();api.async_get_device_status.return_value={str(Property.AIR_PURIFIER_OPERATION_MODE):'POWER_OFF'}
    return ThinQConnector(api,{'thinq:validator':dict(device_id='validator',profile=profile,read=lambda x:x)})


async def test_real_sdk_validator_refuses_unsupported_values_and_cas():
    c=connector();prop=str(Property.AIR_PURIFIER_OPERATION_MODE)
    assert (await c.execute('thinq:validator',{prop:'invented'},'a')).status=='REJECTED'
    assert (await c.execute('thinq:validator',{'show_notice':'x'},'b')).status=='UNSUPPORTED'
    assert (await c.execute('thinq:validator',{prop:'POWER_ON'},'c',{'x':'revision'})).status=='UNSUPPORTED'
    c.api.async_post_device_control.assert_not_called()
    result=await c.execute('thinq:validator',{prop:'POWER_ON'},'d')
    assert result.status=='ACCEPTED'
    c.api.async_post_device_control.assert_awaited_once_with('validator',{'operation':{'airPurifierOperationMode':'POWER_ON'}})


async def test_official_api_errors_do_not_leak_or_become_safe_retries(tmp_path):
    c=connector();prop=str(Property.AIR_PURIFIER_OPERATION_MODE)
    error=ThinQAPIException('test','sensitive vendor detail',{'Authorization':'secret'})
    c.api.async_get_device_status.side_effect=error
    with pytest.raises(Unavailable,match='status unavailable: ThinQAPIException') as raised:
        await c.read('thinq:validator')
    assert 'sensitive' not in str(raised.value) and 'secret' not in str(raised.value)
    c.api.async_get_device_status.side_effect=None
    c.api.async_post_device_control.side_effect=error
    r=Runtime(str(tmp_path/'thinq.db'),{'thinq':c})
    import time
    t=Task(episode_id='sdk',plan_id='sdk',plan_version=1,task_id='uncertain',target='thinq:validator',desired={prop:'POWER_ON'},valid_until=time.time()+60,retry={'max_attempts':3})
    r.submit(t);await r.tick();await r.tick()
    assert r.task(t.key)['status']=='INDETERMINATE'
    assert r.task(t.key)['attempts']==1
    r.store.close()


async def test_malformed_sdk_response_after_dispatch_is_uncertain():
    c=connector();prop=str(Property.AIR_PURIFIER_OPERATION_MODE)
    c.api.async_post_device_control.side_effect=ValueError('Malformed JSON response after request')
    with pytest.raises(Unavailable,match='control uncertain: ValueError'):
        await c.execute('thinq:validator',{prop:'POWER_ON'},'malformed-response')
    c.api.async_post_device_control.assert_awaited_once()

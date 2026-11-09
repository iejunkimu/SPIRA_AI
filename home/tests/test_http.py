import httpx
import pytest
from spira_home.app import app
from spira_home.models import now
from spira_home.runtime import Runtime


async def test_http_identity_contract_and_unknown_ingress(tmp_path):
    app.state.runtime=Runtime(str(tmp_path/'http.db'),{})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://home') as c:
        body=dict(episode_id='e',plan_id='p',plan_version=1,task_id='t',target='missing:device',desired={'power':True},valid_until=now()+60)
        first=await c.post('/v1/tasks',json=body)
        repeat=await c.post('/v1/tasks',json=body)
        assert first.status_code==repeat.status_code==200
        assert first.json()['id']==repeat.json()['id']
        assert (await c.post('/v1/tasks',json={**body,'desired':{'power':False}})).status_code==409
        assert (await c.post('/v1/tasks',json={**body,'order':'not Home input'})).status_code==422
        assert (await c.post('/v1/control',json={'action':'stop_all'})).status_code==200
        await app.state.runtime.tick()
        result=(await c.get('/v1/results')).json()
        assert result[0]['result']['effect_status']=='CANCELLED'
    app.state.runtime.store.close()


async def test_http_auth_is_enforced_on_control_and_results(tmp_path,monkeypatch):
    monkeypatch.setenv('HOME_TOKEN','test-only-secret')
    app.state.runtime=Runtime(str(tmp_path/'auth.db'),{})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://home') as c:
        assert (await c.get('/health')).status_code==200
        assert (await c.get('/v1/results')).status_code==401
        assert (await c.get('/v1/results',headers={'Authorization':'Bearer test-only-secret'})).status_code==200
    app.state.runtime.store.close()


async def test_plan_translation_failure_is_atomic_and_household_bound(tmp_path,monkeypatch):
    app.state.runtime=Runtime(str(tmp_path/'wire.db'),{})
    monkeypatch.setenv('HOME_HOUSEHOLD_ID','h')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://home') as c:
        plan=dict(plan_id='p',household_id='wrong',plan_version=1,sent_at=now(),valid_until=now()+60,tasks=[])
        assert (await c.post('/plans',json=plan)).status_code==403
        plan['household_id']='h';plan['tasks']=[{'task_id':'line','actions':[{'device':'light','cmd':'on'},{'device':'oven','cmd':'start'}]}]
        assert (await c.post('/plans',json=plan)).status_code==422
        assert app.state.runtime.store.rows('SELECT * FROM plans')==[]
        assert (await c.get('/console')).status_code==200
        assert (await c.post('/v1/test/world',json={'values':{'power':True}})).status_code==404
        assert (await c.post('/v1/test/clock',json={'timestamp':now()+60})).status_code==404
    app.state.runtime.store.close()

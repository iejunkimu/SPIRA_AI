import json
from copy import deepcopy
import pytest
from spira_home.bridge import Plan,translate
from spira_home.clock import VirtualClock
from spira_home.models import Task,Trigger,Event,Receipt,Capability,Observation
from spira_home.rules import evaluate
from spira_home.runtime import Runtime,Conflict
from spira_home.connectors.base import Unavailable
from test_runtime import Device


def plan(**kwargs):
    p=dict(plan_id='wire',household_id='h',plan_version=1,sent_at=1000,valid_until=10000,
           tasks=[dict(task_id='line',actions=[dict(device='light',cmd='on'),dict(device='plug',cmd='off')])])
    p.update(kwargs)
    return Plan.model_validate(p)


def task(**kwargs):
    p=dict(episode_id='e',plan_id='p',plan_version=1,task_id='t',target='test:light',desired={'power':True},valid_until=10000,verify_seconds=0)
    p.update(kwargs)
    return Task(**p)


@pytest.mark.parametrize('delta,accepted',[(-61,False),(-59,True),(0,True),(59,True),(61,False)])
def test_package_correlates_both_orders_without_inventing_arrival(delta,accepted):
    r=Trigger(type='package_brought_in.v1',camera_source='camera',contact_source='door')
    events=[Event(event_id='camera',type='camera.package_removed',source='camera',at=1000,payload={'removed_count':1},substrate='TEST'),Event(event_id='door',type='doorlock.opened',source='door',at=1000+delta,substrate='TEST')]
    assert evaluate(r,events,1100,900)[0] is accepted
    events[0].payload={'removed_count':0}
    assert not evaluate(r,events,1100,900)[0]


def test_event_end_quiet_is_reset_and_fallback_is_bounded():
    r=Trigger(type='event_end.v1',contact_source='door',at=1000,quiet_seconds=30,fallback_at=1200)
    def ev(identifier,kind,t):return Event(event_id=identifier,type=kind,source='door',at=t,substrate='TEST')
    events=[ev('close','doorlock.locked',1010)]
    assert not evaluate(r,events,1039,900)[0]
    assert evaluate(r,events,1040,900)[0]
    events.append(ev('open','doorlock.opened',1030))
    assert not evaluate(r,events,1100,900)[0]
    assert evaluate(r,events,1200,900)[0]


def test_plan_group_translation_and_missing_authority_are_explicit():
    tasks,cancelled=translate(plan())
    assert len(tasks)==2 and tasks[0].line_id==tasks[1].line_id=='line'
    assert tasks[0].key!=tasks[1].key
    body=plan().model_dump();body['tasks'][0]['trigger']={'event':'guest_arrived'}
    with pytest.raises(ValueError,match='binding'):translate(Plan.model_validate(body))
    body=plan().model_dump();body['valid_until']=None
    with pytest.raises(ValueError,match='deadline'):translate(Plan.model_validate(body))


async def test_plan_admission_atomicity_duplicate_conflict_and_line_cancel(tmp_path):
    r=Runtime(str(tmp_path/'plan.db'),{},VirtualClock(1000))
    p=plan();tasks,cancelled=translate(p)
    result=r.admit_plan(p.plan_id,p.plan_version,p.model_dump(),tasks,cancelled)
    assert len(result['task_keys'])==2
    assert r.admit_plan(p.plan_id,p.plan_version,p.model_dump(),tasks,cancelled)['duplicate']
    with pytest.raises(Conflict):r.admit_plan(p.plan_id,p.plan_version,{**p.model_dump(),'sent_at':1001},tasks)
    assert len(r.store.rows('SELECT * FROM tasks'))==2
    r.disable_line('wire','line');await r.tick()
    assert all(x['status']=='CANCELLED' for x in r.store.rows('SELECT status FROM tasks'))
    r.store.close()


async def test_retry_only_known_no_effect_failure_and_stop_floor(tmp_path):
    d=Device();clock=VirtualClock(1000);r=Runtime(str(tmp_path/'retry.db'),{'test':d},clock)
    original=d.discover
    async def offline():
        caps=await original();caps[0].available=False;return caps
    d.discover=offline
    t=task(retry={'max_attempts':3,'backoff_seconds':2})
    r.submit(t);await r.tick()
    assert r.task(t.key)['status']=='RETRY_WAIT' and d.commands==0
    clock.advance(1002);d.discover=original;await r.tick()
    assert r.task(t.key)['status']=='VERIFIED' and d.commands==1
    r.store.close()


async def test_accepted_mismatch_is_not_retried_by_retry_policy(tmp_path):
    d=Device();d.mode='unchanged';clock=VirtualClock(1000);r=Runtime(str(tmp_path/'mismatch.db'),{'test':d},clock)
    t=task(retry={'max_attempts':10})
    r.submit(t);await r.tick();clock.advance(1050);await r.tick()
    assert d.commands==1 and r.task(t.key)['status']=='MISMATCH'
    r.store.close()


async def test_maintenance_stability_resets_then_restores(tmp_path):
    d=Device();d.values['humidity']=70;d.rev['humidity']='a'
    c=VirtualClock(1000);r=Runtime(str(tmp_path/'maintenance.db'),{'test':d},c)
    t=task(temporary=True,restore=True,end={'after_seconds':100,'completion':{'target':'test:light','property':'humidity','minimum':45,'maximum':55,'stable_seconds':10}})
    r.submit(t);await r.tick()
    d.mutate(humidity=50);await r.tick();c.advance(1005);d.mutate(humidity=70);await r.tick()
    c.advance(1006);d.mutate(humidity=50);await r.tick();c.advance(1015);await r.tick()
    assert d.values['power'] is True
    c.advance(1016);await r.tick()
    assert d.values['power'] is False and r.task(t.key)['restore_status']=='RESTORED'
    r.store.close()


async def test_not_in_use_unknown_and_done_do_not_dispatch(tmp_path):
    for name,values,kind,expected in [('busy',{'in_use':True},'not_in_use','IN_USE_BY_PERSON'),('unknown',{},'not_in_use','PRECONDITION_UNKNOWN'),('done',{'done_today':True},'not_already_done','ALREADY_DONE_BY_PERSON')]:
        d=Device();d.values.update(values);r=Runtime(str(tmp_path/(name+'.db')),{'test':d},VirtualClock(1000))
        t=task(prechecks=[{'kind':kind}]);r.submit(t);await r.tick()
        assert d.commands==0 and expected in r.task(t.key)['result']['reason']
        r.store.close()


async def test_restore_obligation_survives_unavailable_end(tmp_path):
    d=Device();c=VirtualClock(1000);r=Runtime(str(tmp_path/'restore.db'),{'test':d},c)
    t=task(temporary=True,restore=True,end={'after_seconds':10});r.submit(t);await r.tick()
    d.mode='blind';c.advance(1010);await r.tick()
    assert r.task(t.key)['ended_at'] is None
    d.mode='normal';c.advance(1015);await r.tick()
    assert r.task(t.key)['restore_status']=='RESTORED' and d.values['power'] is False
    r.store.close()


def test_plan_default_numbers_survive_service_serialization(tmp_path):
    r=Runtime(str(tmp_path/'canonical.db'),{},VirtualClock(1000))
    p=plan();tasks,c=translate(p)
    r.admit_plan(p.plan_id,1,p.model_dump(),tasks,c)
    reparsed=Plan.model_validate_json(p.model_dump_json())
    tasks,c=translate(reparsed)
    assert r.admit_plan(p.plan_id,1,reparsed.model_dump(),tasks,c)['duplicate']
    r.store.close()


async def test_owned_restore_observation_is_not_manual_override(tmp_path):
    d=Device();r=Runtime(str(tmp_path/'own-restore.db'),{'test':d},VirtualClock(1000))
    t=task(temporary=True,restore=True);r.submit(t);await r.tick()
    execute=d.execute
    async def observed_execute(target,desired,operation_id,expected=None):
        receipt=await execute(target,desired,operation_id,expected)
        if expected:r.reconcile(await d.read(target))
        return receipt
    d.execute=observed_execute
    await r.restore(t.key)
    assert r.task(t.key)['restore_status']=='RESTORED'
    assert r.task(t.key)['status']=='VERIFIED'
    r.store.close()


async def test_two_runtime_readers_cannot_dispatch_same_reserved_task(tmp_path):
    import asyncio
    d=Device();path=str(tmp_path/'shared.db');c=VirtualClock(1000)
    first=Runtime(path,{'test':d},c);second=Runtime(path,{'test':d},c)
    t=task();first.submit(t)
    read=d.read
    async def yielding_read(target):
        await asyncio.sleep(.01)
        return await read(target)
    d.read=yielding_read
    await asyncio.gather(first.tick(),second.tick())
    assert d.commands==1
    first.store.close();second.store.close()


def test_retention_preserves_active_rule_history_and_unpublished_truth(tmp_path):
    clock=VirtualClock(1000);r=Runtime(str(tmp_path/'retention.db'),{},clock)
    t=task(trigger={'type':'raw_event.v1','event_type':'button.pressed','source':'button'},valid_until=1000+30*86400)
    r.submit(t)
    r.observe(Event(event_id='raw',type='button.pressed',source='button',at=1000,substrate='TEST'))
    clock.advance(1000+15*86400)
    assert r.compact()['events_removed']==0
    r.control('stop',plan_id=t.plan_id)
    r.store.write("UPDATE tasks SET status='CANCELLED' WHERE id=?",(t.key,))
    assert r.compact()['events_removed']==1
    assert r.store.one('SELECT * FROM event_outbox WHERE event_id=?',('raw',))
    assert r.task(t.key)['body']['task_id']=='t'
    r.store.write('UPDATE event_outbox SET delivered=1')
    assert r.compact()['published_events_removed']==1
    r.store.close()

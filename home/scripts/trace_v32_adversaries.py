"""Software-only pressure on the deployed v3.2 Home boundary; independent HTTP peers."""
import argparse
import json
import time
import uuid
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from run_v32 import Runner
from reality_adversary.process import s10_process


def run(home,world,service,restart_home=False):
    r=Runner(home,world,service);checks=[]
    assert r.get(r.h,'/health')['virtual_time']
    r.post(r.w,'/sim/profile',{'profile':'lg_assumed'})
    for p in {x['body']['plan_id'] for x in r.get(r.h,'/v1/tasks')}:
        r.post(r.h,'/v1/control',{'action':'end','plan_id':p})
    base=r.get(r.w,'/world')['timestamp'];prefix='adversary-'+uuid.uuid4().hex[:8]
    def row(key):return next(x for x in r.get(r.h,'/v1/tasks') if x['id']==key)
    def submit(name,**kw):
        body=dict(episode_id=prefix,plan_id=prefix+'-'+name,plan_version=1,task_id=name,target='mqtt:light',desired={'power':True},valid_until=r.get(r.w,'/world')['timestamp']+300,verify_seconds=0)
        body.update(kw);key=r.post(r.h,'/v1/tasks',body)['id'];r.settle();return body,key
    def record(name,body):
        checks.append(dict(name=name,status='PASS',evidence=body));print(name+' PASS',flush=True)
    def end(body):r.post(r.h,'/v1/control',{'action':'end','plan_id':body['plan_id']})

    # Known no-effect unavailability is retryable; accepted mismatch is not.
    r.post(r.w,'/mutate',{'target':'mqtt:light','mode':'offline','values':{'power':False}})
    t,k=submit('offline-retry',retry={'max_attempts':3,'backoff_seconds':2})
    assert row(k)['status']=='RETRY_WAIT'
    r.post(r.w,'/mutate',{'target':'mqtt:light','mode':'normal'})
    r.advance(base+2);assert row(k)['status']=='VERIFIED';record('bounded-offline-retry',row(k));end(t)
    base=r.get(r.w,'/world')['timestamp']
    r.post(r.w,'/mutate',{'target':'mqtt:light','mode':'offline'})
    t,k=submit('deadline',valid_until=base+3,retry={'max_attempts':10,'backoff_seconds':2})
    r.advance(base+3);assert row(k)['status']=='EXPIRED';record('deadline-terminates-retry',row(k));end(t)
    r.post(r.w,'/mutate',{'target':'mqtt:light','mode':'normal','values':{'power':False}})

    # Group partial outcome is explicit, never promoted to whole-line success.
    base=r.get(r.w,'/world')['timestamp']
    plan=dict(plan_id=prefix+'-partial',household_id='h_v32',plan_version=1,sent_at=base,valid_until=base+120,
              tasks=[dict(task_id='group',actions=[dict(device='light',cmd='on'),dict(device='absent',cmd='on')])])
    r.post(r.h,'/plans',plan);r.settle()
    summary=r.get(r.h,'/v1/plans/'+plan['plan_id']+'/summary')
    statuses={x['status'] for x in summary['lines']['group']}
    assert statuses=={'VERIFIED','UNSUPPORTED'},summary;record('compose-partial-failure',summary);end(plan)

    # One line disabled, its sibling still reacts to the same raw source.
    r.post(r.w,'/mutate',{'target':'mqtt:light','values':{'power':False}})
    r.post(r.w,'/mutate',{'target':'mqtt:plug','values':{'power':False}})
    plan=dict(plan_id=prefix+'-line-off',household_id='h_v32',plan_version=1,sent_at=base,valid_until=base+120,
              tasks=[dict(task_id=name,actions=[dict(device=device,cmd='on')],trigger={'event':'button.pressed','source':'button'}) for name,device in [('off-line','light'),('keep-line','plug')]])
    r.post(r.h,'/plans',plan)
    r.post(r.h,'/v1/plans/'+plan['plan_id']+'/lines/off-line/disable',{})
    r.post(r.w,'/mutate',{'event_type':'button.pressed','source':'button'});r.settle()
    summary=r.get(r.h,'/v1/plans/'+plan['plan_id']+'/summary')
    assert summary['lines']['off-line'][0]['status']=='CANCELLED' and summary['lines']['keep-line'][0]['status']=='VERIFIED';record('per-line-disable',summary);end(plan)

    # Current state beats Service assumptions: busy appliance and protected load.
    r.post(r.w,'/mutate',{'target':'mqtt:oven_01','values':{'in_use':True}})
    t,k=submit('busy-oven',target='mqtt:oven_01',family='CONFIGURE_FOR_NEXT_USE',desired={'preset':{'temp_c':170,'minutes':20}},prechecks=[{'kind':'not_in_use'}])
    assert row(k)['status']=='BLOCKED' and row(k)['result']['reason']=='IN_USE_BY_PERSON';record('in-use-precheck',row(k));end(t)
    t,k=submit('protected-router',target='mqtt:plug_router',desired={'power':False})
    assert row(k)['status']=='UNSUPPORTED';record('protected-load-not-advertised',row(k));end(t)
    t,k=submit('oven-start-forbidden',target='mqtt:oven_01',desired={'start':True})
    assert row(k)['status']=='UNSUPPORTED';record('cooking-start-not-advertised',row(k));end(t)

    # Sensor trigger stability is evaluated against independently owned state.
    r.post(r.w,'/mutate',{'target':'mqtt:humidity','values':{'humidity':70}})
    r.post(r.w,'/mutate',{'target':'mqtt:light','values':{'power':False}})
    t,k=submit('sensor-trigger',trigger={'type':'state.v1','condition':{'target':'mqtt:humidity','property':'humidity','maximum':55,'stable_seconds':5}})
    r.post(r.w,'/mutate',{'target':'mqtt:humidity','values':{'humidity':50}});r.settle()
    due=r.get(r.h,'/v1/timeline')['next_due'];current=r.get(r.w,'/world')['timestamp']
    assert due==current+5,(due,current)
    r.advance(due);assert row(k)['status']=='VERIFIED';record('stable-sensor-trigger',row(k));end(t)

    # An independently replaced job cannot be stopped by the old task.
    r.post(r.w,'/mutate',{'target':'mqtt:robot_01','values':{'run_state':'idle','job_id':None,'in_use':False}})
    t,k=submit('job',target='mqtt:robot_01',family='RUN_JOB',desired={'run_state':'running','room':'living'},end={'after_seconds':120})
    assert row(k)['status']=='RUNNING'
    r.post(r.w,'/mutate',{'target':'mqtt:robot_01','values':{'job_id':'external-job','run_state':'running'},'provenance':'USER'});r.settle()
    assert row(k)['status']=='OVERRIDDEN';end(t)
    robot=next(x for x in r.get(r.w,'/world')['devices'] if x['target']=='mqtt:robot_01')
    assert robot['values']['run_state']=='running' and robot['values']['job_id']=='external-job';record('external-job-not-stopped',row(k))
    r.post(r.w,'/mutate',{'target':'mqtt:robot_01','values':{'run_state':'idle','job_id':None}})

    # Noisy jobs at night and already completed human work are not dispatched.
    current=r.get(r.w,'/world')['timestamp'];night=datetime.fromtimestamp(current,ZoneInfo('Asia/Seoul')).replace(hour=23,minute=0,second=0,microsecond=0)
    if night.timestamp()<=current:night+=timedelta(days=1)
    r.advance(night.timestamp())
    t,k=submit('quiet-hours',target='mqtt:robot_01',family='RUN_JOB',desired={'run_state':'running','room':'living'},prechecks=[{'kind':'quiet_hours'}])
    assert row(k)['status']=='BLOCKED' and row(k)['result']['reason']=='QUIET_HOURS';record('quiet-hours-block',row(k));end(t)
    r.post(r.w,'/mutate',{'target':'mqtt:robot_01','values':{'done_today':True}})
    t,k=submit('already-cleaned',target='mqtt:robot_01',family='RUN_JOB',desired={'run_state':'running','room':'living'},prechecks=[{'kind':'not_already_done'}])
    assert row(k)['status']=='BLOCKED' and row(k)['result']['reason']=='ALREADY_DONE_BY_PERSON';record('already-done-block',row(k));end(t)
    r.post(r.w,'/mutate',{'target':'mqtt:robot_01','values':{'done_today':False}})

    r.post(r.w,'/mutate',{'target':'mqtt:conditioner','values':{'mode':'off','in_use':False}})
    t,k=submit('maintenance-limit',target='mqtt:conditioner',family='MAINTAIN_CONDITION',desired={'mode':'deodorize'},temporary=True,restore=True,end={'after_seconds':5})
    assert row(k)['status']=='VERIFIED'
    r.advance(r.get(r.w,'/world')['timestamp']+5)
    assert row(k)['restore_status']=='RESTORED' and row(k)['result']['end_reason']=='MAX_DURATION';record('maintenance-time-limit',row(k));end(t)

    # Offline Service does not erase local execution truth, events, or publication obligations.
    r.post(r.s,'/fixture/availability',{'offline':True})
    try:
        r.post(r.w,'/mutate',{'target':'mqtt:light','values':{'power':False}})
        t,k=submit('service-outage',temporary=True,restore=True)
        assert row(k)['status']=='VERIFIED'
        r.post(r.w,'/mutate',{'target':'mqtt:light','values':{'power':False},'provenance':'USER'});r.settle()
        assert row(k)['status']=='OVERRIDDEN'
        if restart_home:
            s10_process('restart', profile='v32')
            for _ in range(80):
                try:
                    if row(k)['status']=='OVERRIDDEN':break
                except Exception:pass
                time.sleep(.1)
            else:raise AssertionError('Home did not recover its durable override during Service outage')
    finally:
        r.post(r.s,'/fixture/availability',{'offline':False})
    for _ in range(180):
        records=r.get(r.s,'/fixture/records')
        matches=[x for x in records['executions'] if x['plan_id']==t['plan_id']]
        if {x['result']['effect_status'] for x in matches}>={'VERIFIED','OVERRIDDEN'}:break
        time.sleep(.1)
    else:raise AssertionError('durable Service outbox did not catch up')
    record('service-outage-replay-with-home-restart' if restart_home else 'service-outage-replay',matches);end(t)
    return dict(status='PASS',scope='v3.2 runtime/Service/independent MQTT software adversaries',checks=checks)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--home',default='http://127.0.0.1:8730');p.add_argument('--world',default='http://127.0.0.1:8731');p.add_argument('--service',default='http://127.0.0.1:8732');p.add_argument('--output',default='docs/evidence/v32-adversaries.json');p.add_argument('--restart-home',action='store_true',help='Restart only the local S10 team v32 Home');a=p.parse_args()
    result=run(a.home,a.world,a.service,a.restart_home);Path(a.output).write_text(json.dumps(result,indent=2)+'\n')

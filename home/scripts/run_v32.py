"""Replay Service-authored plans against independent processes using one virtual clock.

Only household effects and raw facts are fixtures. No order/intent/App/KB pipeline
is implemented here. Jumping visits every Home/world timer boundary in order.
"""
import argparse
import copy
import json
import time
import uuid
from datetime import datetime,timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import httpx


class Runner:
    def __init__(self,home,world,service):
        self.h=httpx.Client(base_url=home,timeout=20)
        self.w=httpx.Client(base_url=world,timeout=10)
        self.s=httpx.Client(base_url=service,timeout=10)
        self.checks=[]
        self.visited=[]

    def get(self,c,path):
        for attempt in range(30):
            try:
                r=c.get(path);r.raise_for_status();return r.json()
            except (httpx.TransportError,httpx.HTTPStatusError):
                if attempt==29:raise
                time.sleep(.1)

    def post(self,c,path,body):
        r=c.post(path,json=body);r.raise_for_status();return r.json()

    def settle(self):
        # Ensure broker-delivered facts have been consumed before evaluating timers.
        time.sleep(.08)
        self.post(self.h,'/v1/test/tick',{})
        time.sleep(.08)
        self.post(self.h,'/v1/test/tick',{})

    def advance(self,target):
        current=self.get(self.w,'/world')['timestamp']
        while current<target:
            timeline=self.get(self.h,'/v1/timeline')
            world=self.get(self.w,'/world')
            bounds=[x for x in [timeline.get('next_due'),world.get('next_due'),target] if x is not None and x>current]
            step=min(bounds)
            self.post(self.w,'/sim/clock',{'timestamp':step})
            for _ in range(50):
                if self.get(self.h,'/health')['timestamp']>=step:break
                time.sleep(.03)
            else:raise AssertionError('Home did not observe independent world clock')
            self.settle()
            current=step
            self.visited.append(step)

    def run(self,path):
        spec=json.loads(Path(path).read_text())
        current=self.get(self.w,'/world')['timestamp']
        self.post(self.w,'/sim/profile',{'profile':spec.get('profile','lg_assumed')})
        d=datetime.fromtimestamp(current,ZoneInfo('Asia/Seoul'))
        base=(d+timedelta(days=(3-d.weekday())%7)).replace(hour=21,minute=0,second=0,microsecond=0)
        if base.timestamp()<=current+60:base+=timedelta(days=7)
        assert self.get(self.h,'/health')['virtual_time'], 'scenario runner requires isolated virtual Home'
        for old_plan in {r['body']['plan_id'] for r in self.get(self.h,'/v1/tasks')}:
            self.post(self.h,'/v1/control',{'action':'end','plan_id':old_plan})
        self.advance(base.timestamp())
        identifier=spec['name']+'-'+uuid.uuid4().hex[:8]
        def resolve(value):
            if isinstance(value,str):
                if value=='$PLAN':return identifier
                if value.startswith('@+'):
                    return base.timestamp()+float(value[2:])
            if isinstance(value,list):return [resolve(x) for x in value]
            if isinstance(value,dict):return {k:resolve(v) for k,v in value.items()}
            return value
        for seed in spec.get('seed',[]):
            self.post(self.w,'/mutate',{**seed,'mode':'normal'})
        self.settle()
        plan=resolve(spec['plan'])
        self.post(self.s,'/fixture/plans',plan)
        for _ in range(100):
            rows=[r for r in self.get(self.h,'/v1/tasks') if r['body']['plan_id']==identifier]
            if rows:break
            time.sleep(.05)
        else:raise AssertionError('Service pull did not reach Home')
        duplicate=self.post(self.h,'/plans',plan)
        assert duplicate['duplicate'],duplicate
        self.settle()
        for number,step in enumerate(spec['timeline']):
            self.advance(base.timestamp()+step['at'])
            if 'event' in step:
                self.post(self.w,'/mutate',step['event'])
            if 'mutate' in step:
                self.post(self.w,'/mutate',step['mutate'])
            if 'control' in step:
                self.post(self.h,'/v1/control',resolve(step['control']))
            self.settle()
            if 'expect' in step:
                e=step['expect']
                state=next(d for d in self.get(self.w,'/world')['devices'] if d['target']==e['device'])
                assert all(state['values'].get(k)==v for k,v in e['values'].items()),(number,e,state)
                self.checks.append(dict(step=number,at=base.timestamp()+step['at'],expect=e,observed=state))
            if 'expect_line' in step:
                e=step['expect_line']
                rows=[r for r in self.get(self.h,'/v1/tasks') if r['body']['plan_id']==identifier and r['body']['line_id']==e['line']]
                assert rows and all(r['status']==e['status'] for r in rows),(number,e,rows)
                self.checks.append(dict(step=number,expect_line=e,observed=[r['status'] for r in rows]))
        self.post(self.h,'/v1/control',{'action':'end','plan_id':identifier})
        for _ in range(100):
            records=self.get(self.s,'/fixture/records')
            executions=[x for x in records['executions'] if x.get('plan_id')==identifier]
            if len(executions)>=len(rows):break
            time.sleep(.05)
        events=[x for x in records['events'] if x['payload'].get('plan_id')==identifier]
        assert executions,'no C-4 execution publication'
        assert records['devices'],'no C-2 registration'
        result=dict(status='PASS',scenario=spec['name'],standing=spec['standing'],plan_id=identifier,
                    source=str(path),base=base.isoformat(),checks=self.checks,visited_clock_boundaries=self.visited,
                    service_plan_pull='PASS',duplicate_push='PASS',device_registration='PASS',
                    execution_publications=executions,derived_events=events,
                    tasks=[r for r in self.get(self.h,'/v1/tasks') if r['body']['plan_id']==identifier])
        print(spec['name']+': PASS ('+str(len(self.checks))+' state/lifecycle assertions)',flush=True)
        return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('scenario');p.add_argument('--home',default='http://127.0.0.1:8730');p.add_argument('--world',default='http://127.0.0.1:8731');p.add_argument('--service',default='http://127.0.0.1:8732');p.add_argument('--output',default='docs/evidence/v32-birthday.json');a=p.parse_args()
    result=Runner(a.home,a.world,a.service).run(a.scenario)
    Path(a.output).write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')

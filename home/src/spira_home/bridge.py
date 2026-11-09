"""Settle 3.2 C-1/C-2/C-3/C-4 wire compatibility; no Service intent logic."""
from datetime import datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo
from pydantic import Field, model_validator
from spira_home.models import Strict, Task, Trigger, End, Precheck, Retry


def timestamp(value, timezone='Asia/Seoul'):
    if isinstance(value,(int,float)):
        return float(value)
    parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
    if parsed.tzinfo is None:
        parsed=parsed.replace(tzinfo=ZoneInfo(timezone))
    return parsed.timestamp()


def iso(value):
    return datetime.fromtimestamp(value,ZoneInfo('Asia/Seoul')).isoformat()


class Action(Strict):
    device: str
    cmd: str
    value: Any = None
    room: str | None = None

    @model_validator(mode='after')
    def value_required(self):
        if self.cmd in ('color','brightness','gallery_scene','play_music_app','set_cook_standby','set_course','show_notice','maintain') and self.value is None:
            raise ValueError('command requires an explicit value')
        return self


class PlanTask(Strict):
    task_id: str
    status: Literal['confirmed','cancelled','disabled'] = 'confirmed'
    way: str | None = None
    need: str | None = None
    actions: list[Action] = Field(default_factory=list)
    trigger: dict = Field(default_factory=lambda:{'immediate':True})
    end: dict | None = None
    precheck: list[str] = Field(default_factory=list)
    deadline: str | float | None = None
    retry: Retry = Field(default_factory=Retry)
    policy: Literal['ALLOW','PROHIBITED'] = 'ALLOW'


class Plan(Strict):
    plan_id: str
    household_id: str
    plan_version: int = Field(ge=1)
    sent_at: str | float
    episode_id: str | None = None
    tasks: list[PlanTask] = Field(default_factory=list,max_length=200)
    cancelled: list[str] = Field(default_factory=list)
    valid_until: str | float | None = None
    timezone: str = 'Asia/Seoul'
    schedule: dict = Field(default_factory=dict)
    bindings: dict[str,str] = Field(default_factory=dict)

    @model_validator(mode='after')
    def unique(self):
        if len({t.task_id for t in self.tasks})!=len(self.tasks):
            raise ValueError('duplicate semantic task IDs')
        return self


COMMANDS={
    'on':('SET_STATE',lambda v:{'power':True}),
    'off':('SET_STATE',lambda v:{'power':False}),
    'color':('SET_STATE',lambda v:{'color':v}),
    'brightness':('SET_STATE',lambda v:{'brightness':v}),
    'gallery_scene':('SET_STATE',lambda v:{'power':True,'scene':v}),
    'play_music_app':('SET_STATE',lambda v:{'power':True,'music':v}),
    'set_cook_standby':('CONFIGURE_FOR_NEXT_USE',lambda v:{'preset':v}),
    'set_course':('CONFIGURE_FOR_NEXT_USE',lambda v:{'preset':v}),
    'show_notice':('PRESENT_INFORMATION',lambda v:{'notices':v if isinstance(v,list) else [v]}),
    'clean':('RUN_JOB',lambda v:{'run_state':'running','room':v.get('room','living') if isinstance(v,dict) else 'living'}),
    'stop':('STOP',lambda v:{'run_state':'stopped'}),
    'child_lock':('SET_STATE',lambda v:{'child_lock':True if v is None else v}),
    'hot_water_lock':('SET_STATE',lambda v:{'hot_water_lock':True if v is None else v}),
    'maintain':('MAINTAIN_CONDITION',lambda v:{'mode':v}),
}


def target(device):
    return device if ':' in device else 'mqtt:'+device


def rule_from_wire(data, plan, device=None, ending=False):
    if not data or data.get('immediate'):
        return Trigger()
    if 'type' in data:
        return Trigger.model_validate(data)
    if 'time' in data:
        return Trigger(type='at',at=timestamp(data['time'],plan.timezone))
    if 'condition' in data:
        return Trigger(type='state.v1',condition=data['condition'])
    event=data.get('event')
    bindings=plan.bindings
    def binding(name):
        if name not in bindings:
            raise ValueError('delegated '+str(event)+' requires '+name+' source binding')
        return bindings[name]
    if event=='guest_arrived':
        return Trigger(type='first_guest_arrival.v1',bell_source=binding('doorbell'),contact_source=binding('door'),window_seconds=data.get('window_seconds',60))
    if event=='package.brought_in':
        return Trigger(type='package_brought_in.v1',camera_source=binding('camera'),contact_source=binding('door'),window_seconds=data.get('window_seconds',60))
    if event=='event_end':
        if 'end' not in plan.schedule:
            raise ValueError('event_end requires Service schedule.end')
        end=timestamp(plan.schedule['end'],plan.timezone)
        return Trigger(type='event_end.v1',at=end,contact_source=binding('door'),quiet_seconds=data.get('quiet_seconds',1800),fallback_at=end+data.get('fallback_seconds',10800))
    if event in ('departed','returned'):
        if 'start' not in plan.schedule:
            raise ValueError('departure/return requires Service schedule.start')
        return Trigger(type=event+'.v1',at=timestamp(plan.schedule['start'],plan.timezone),contact_source=binding('door'))
    if event=='fridge.door_closed' and ending:
        return Trigger(type='door_cycle.v1',contact_source=data.get('source') or device)
    if event=='oven.started_by_user':
        return Trigger(type='raw_event.v1',event_type='oven.started',source=device,payload_equals={'by':'user'})
    if not event:
        raise ValueError('unrecognized trigger shape')
    return Trigger(type='raw_event.v1',event_type=event,source=data.get('source') or device)


def translate(plan: Plan):
    tasks=[]
    cancelled=set(plan.cancelled)
    for line in plan.tasks:
        if line.status!='confirmed':
            cancelled.add(line.task_id)
            continue
        deadline=line.deadline or plan.valid_until
        if deadline is None:
            raise ValueError('bounded deadline required on task or plan: '+line.task_id)
        for index,action in enumerate(line.actions):
            if action.cmd not in COMMANDS:
                raise ValueError('unknown logical command: '+action.cmd)
            family,convert=COMMANDS[action.cmd]
            desired=convert(action.value)
            device=target(action.device)
            trigger=rule_from_wire(line.trigger,plan,action.device)
            if trigger.type=='first_guest_arrival.v1':
                if 'start' not in plan.schedule:
                    raise ValueError('guest arrival requires Service schedule.start validity')
                valid_from=max(timestamp(plan.sent_at,plan.timezone),timestamp(plan.schedule['start'],plan.timezone)-1800)
            else:
                valid_from=timestamp(plan.sent_at,plan.timezone)
            end=None
            if line.end:
                end_data=line.end
                end_trigger=rule_from_wire(end_data,plan,action.device,True) if 'event' in end_data or 'time' in end_data or 'type' in end_data else None
                alternatives=[rule_from_wire({'event':end_data['or']},plan,action.device,True)] if end_data.get('or') else []
                end=End(trigger=end_trigger,alternatives=alternatives,after_seconds=end_data.get('after_seconds'),completion=end_data.get('condition'))
            checks=[]
            for check in line.precheck:
                if check=='still_confirmed':
                    continue
                kind={'in_use_by_person':'not_in_use','already_done_by_person':'not_already_done','quiet_hours':'quiet_hours'}.get(check)
                if not kind:
                    raise ValueError('unsupported precheck: '+check)
                checks.append(Precheck(kind=kind))
            if action.room:
                checks.append(Precheck(kind='room',value=action.room))
            restore=bool(line.end and line.end.get('restore'))
            tasks.append(Task(episode_id=plan.episode_id or plan.plan_id,plan_id=plan.plan_id,plan_version=plan.plan_version,
                              task_id=line.task_id+'@'+str(index),line_id=line.task_id,logical_command=action.cmd,target=device,family=family,desired=desired,
                              trigger=trigger,valid_from=valid_from,valid_until=timestamp(deadline,plan.timezone),
                              end=end,temporary=restore,restore=restore,prechecks=checks,retry=line.retry,policy=line.policy))
    return tasks,sorted(cancelled)


def execution_wire(row):
    t=Task.model_validate(row['body'])
    result=row['result'] or {}
    receipt=result.get('command_status') or row.get('command_receipt') or {}
    observation=result.get('observation')
    return dict(task_id=t.line_id or t.task_id,action_id=t.task_id,episode_id=t.episode_id,plan_id=t.plan_id,
                plan_version=t.plan_version,device_id=t.target.partition(':')[2],family=t.family,
                attempted=result.get('attempted',False),command=t.logical_command or t.family,
                precheck=row.get('gate_evidence'),desired_effect=t.desired,
                saved_state=(row.get('before_state') or {}).get('values'),
                result=dict(ok=receipt.get('status')=='ACCEPTED',verified=result.get('effect_status') in ('VERIFIED','RUNNING','COMPLETED'),
                            effect_status=result.get('effect_status'),at=iso(result.get('reported_at',row['received'])),
                            observed_state=(observation or {}).get('values'),provenance=(observation or {}).get('provenance','UNKNOWN'),
                            substrate=(observation or {}).get('substrate'),reason=result.get('reason')),
                restore=dict(status=result.get('restore_status'),end_reason=result.get('end_reason'),at=iso(result.get('reported_at',row['received']))) if result.get('restore_status') else None)


def event_wire(event,household):
    payload={**event.payload,'provenance':event.provenance,'substrate':event.substrate}
    payload['by']={'USER':'user','SPIRA':'settle'}.get(event.provenance,'unknown')
    if event.observation:
        payload.update(state=event.observation.values,property_revisions=event.observation.revisions)
    return dict(event_id=event.event_id,household_id=household,type=event.type,source=event.source,at=iso(event.at),payload=payload)

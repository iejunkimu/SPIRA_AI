"""Named traces and seeded, dependency-preserving occurrence sequence search."""
import copy
import random


def mutate(**values):
    return {'op':'mutate', 'body':{'target':'mqtt:light', 'mode':'normal', 'values':values}}


def submit(**body):
    return {'op':'submit', 'body':body}


def wait(status, label='a'):
    return {'op':'wait', 'path':['tasks',label,'status'], 'equals':status}


def check(invariant, path, value):
    return {'op':'assert', 'invariant':invariant, 'path':path, 'equals':value}


def taskcheck(invariant, field, value, label='a'):
    return check(invariant, ['tasks',label,field], value)


def fault(identifier, **body):
    return {'op':'fault', 'body':{'id':identifier, **body}}


def boundary(identifier):
    return {'op':'boundary', 'id':identifier}


def release(identifier):
    return {'op':'release', 'id':identifier}


def advance(seconds):
    return {'op':'advance', 'seconds':seconds}


def named():
    temporary = dict(temporary=True, restore=True)
    base = [mutate(power=False, brightness=10)]
    cases = {}
    cases['R1_accepted_then_network_loss'] = base+[
        fault('ack', kind='command', hold=True, drop=True), submit(retry={'max_attempts':3}), boundary('ack'),
        check('I1',['world','mqtt:light','values','power'],True),
        {'op':'process','action':'kill'}, {'op':'process','action':'start'}, release('ack'),
        wait('INDETERMINATE'), advance(3), check('I3',['commands'],1), check('I10',['commands'],1)]
    cases['R2_manual_change_then_end'] = base+[submit(**temporary), wait('VERIFIED'),
        mutate(power=False), wait('OVERRIDDEN'), {'op':'end'},
        check('I5',['world','mqtt:light','values','power'],False), check('I12',['commands'],1)]
    cases['R3_manual_change_during_restore'] = base+[submit(desired={'brightness':80}, **temporary), wait('VERIFIED'),
        fault('restore', kind='command', stage='before', hold=True), {'op':'end','async':True}, boundary('restore'),
        mutate(brightness=25), release('restore'), {'op':'join'},
        taskcheck('I6','restore_status','RESTORE_SKIPPED_OVERRIDE'), check('I12',['world','mqtt:light','values','brightness'],25)]
    cases['R4_stale_plan_during_delayed_read'] = base+[
        fault('pre-read', hold=True, stage='before'), submit(), boundary('pre-read'),
        {'op':'supersede'}, release('pre-read'), wait('SUPERSEDED'), check('I4',['commands'],0)]
    condition = {'target':'mqtt:humidity','property':'humidity','maximum':55,'stable_seconds':5}
    sensor = lambda value:{'op':'mutate','body':{'target':'mqtt:humidity','values':{'humidity':value}}}
    cases['R5_sensor_noise_around_threshold'] = base+[sensor(70),
        submit(trigger={'type':'state.v1','condition':condition}), {'op':'tick'}]
    for value in [54,56,54,56,54]:
        cases['R5_sensor_noise_around_threshold'] += [sensor(value), advance(2), taskcheck('I11','status','WAITING')]
    cases['R5_sensor_noise_around_threshold'] += [advance(5), wait('VERIFIED'), check('I1',['commands'],1)]
    cases['R6_maintenance_environment_disturbance'] = [sensor(70),
        {'op':'mutate','body':{'target':'mqtt:conditioner','mode':'normal','values':{'mode':'off','in_use':False}}},
        submit(target='mqtt:conditioner',family='MAINTAIN_CONDITION',desired={'mode':'auto_humidity'},
               end={'after_seconds':60,'completion':condition},**temporary), wait('VERIFIED'),
        {'op':'dynamics','sensor':'mqtt:humidity','property':'humidity','actuator':'mqtt:conditioner',
         'actuator_property':'mode','active_value':'auto_humidity',
         'model':{'value':57,'actuator_effect':-3,'drift':.1,'noise':.1,'inertia':.3,'seed':6},
         'disturbances':[0,0,15,0,0]}, taskcheck('I11','restore_status',None),
        sensor(50), {'op':'tick'}, advance(5), taskcheck('I6','restore_status','RESTORED'),
        check('I12',['world','mqtt:conditioner','values','mode'],'off')]
    # R7 is executed by the native Matter adapter, not imitated in MQTT.
    cases['R8_partial_composite_with_restore'] = base+[
        {'op':'mutate','body':{'target':'mqtt:plug','mode':'offline','values':{'power':False}}},
        submit(line_id='group',**temporary), {'op':'submit','label':'b','body':{'target':'mqtt:plug','line_id':'group'}},
        {'op':'submit','label':'c','body':{'target':'mqtt:light_living','desired':{'power':True},'line_id':'group',**temporary}},
        wait('VERIFIED'), wait('UNAVAILABLE','b'), wait('VERIFIED','c'), mutate(power=False), wait('OVERRIDDEN'),
        taskcheck('I9','status','UNAVAILABLE','b'), taskcheck('I9','status','VERIFIED','c'),
        {'op':'summary','line':'group','equals':'PARTIAL'},
        {'op':'end'}, check('I12',['world','mqtt:light','values','power'],False),
        {'op':'mutate','body':{'target':'mqtt:plug','mode':'normal'}}]
    cases['R9_home_crash_during_dispatch'] = base+[
        fault('dispatch',kind='command',stage='before',hold=True), submit(), boundary('dispatch'),
        {'op':'process','action':'kill'}, release('dispatch'), {'op':'process','action':'start'},
        wait('INDETERMINATE'), taskcheck('I10','status','INDETERMINATE'), check('I3',['commands'],1)]
    cases['R10_away_and_back'] = base+[submit(**temporary),wait('VERIFIED'),mutate(power=False),mutate(power=True),
        wait('OVERRIDDEN'), {'op':'end'}, check('I5',['world','mqtt:light','values','power'],True),
        taskcheck('I6','restore_status','RESTORE_SKIPPED_OVERRIDE')]
    cases['stale_successful_readback'] = [mutate(power=True),fault('stale',skip=1,stale=True),
        advance(1),mutate(power=False), {'op':'mutate','body':{'target':'mqtt:light','mode':'unchanged'}},
        submit(), {'op':'settle'}, taskcheck('I1','status','UNVERIFIABLE')]
    cases['partial_readback_null'] = base+[fault('omit',skip=1,omit=['power']),
        submit(desired={'power':True}), wait('MISMATCH'), taskcheck('I1','status','MISMATCH')]
    cases['partial_application'] = base+[fault('partial',kind='command',partial=['power']),
        submit(desired={'power':True,'brightness':80}), wait('MISMATCH'), taskcheck('I9','status','MISMATCH')]
    cases['unknown_precheck'] = base+[submit(preconditions={'door_closed':True}),wait('BLOCKED'),check('I2',['commands'],0)]
    cases['partial_sibling_precheck'] = base+[
        {'op':'mutate','body':{'target':'mqtt:washer','mode':'normal','values':{'in_use':False}}},
        fault('missing-in-use',target='mqtt:washer',omit=['in_use']),
        submit(prechecks=[{'kind':'not_in_use','target':'mqtt:washer'}]), wait('BLOCKED'),check('I2',['commands'],0)]
    cases['stop_during_delayed_dispatch_read'] = base+[fault('stop-read',stage='before',hold=True),submit(),boundary('stop-read'),
        {'op':'stop','async':True},release('stop-read'),{'op':'join'},wait('CANCELLED'),check('I4',['commands'],0)]
    cases['deadline_during_delayed_dispatch_read'] = base+[fault('deadline-read',stage='before',hold=True),
        submit(deadline_seconds=1),boundary('deadline-read'),{'op':'clock_jump','seconds':2},release('deadline-read'),
        wait('EXPIRED'),check('I4',['commands'],0)]
    cases['authority_during_verification'] = base+[
        fault('verify',skip=1,hold=True),submit(**temporary),boundary('verify'),
        mutate(power=False),mutate(power=True),release('verify'),{'op':'sleep','seconds':.3},
        {'op':'end'},check('I6',['world','mqtt:light','values','power'],True),
        taskcheck('I5','restore_status','RESTORE_SKIPPED_OVERRIDE')]
    cases['external_before_fresh_verification'] = base+[
        fault('fresh-verify',skip=1,stage='before',hold=True),submit(**temporary),boundary('fresh-verify'),
        mutate(power=False),mutate(power=True),release('fresh-verify'),{'op':'settle'},
        {'op':'end'},check('I6',['world','mqtt:light','values','power'],True),
        taskcheck('I5','restore_status','RESTORE_SKIPPED_OVERRIDE')]
    cases['null_desired_omitted_from_read'] = [
        {'op':'mutate','body':{'target':'mqtt:washer','mode':'normal','values':{'preset':{'course':'normal','dose':25}}}},
        fault('null',target='mqtt:washer',skip=1,omit=['preset']),
        submit(target='mqtt:washer',family='CONFIGURE_FOR_NEXT_USE',desired={'preset':None}),
        {'op':'settle'},taskcheck('I1','status','MISMATCH')]
    cases['sensor_gap_resets_stability'] = base+[sensor(50),
        submit(trigger={'type':'state.v1','condition':condition}), {'op':'tick'}, advance(2),
        {'op':'mutate','body':{'target':'mqtt:humidity','mode':'readback_unavailable'}},
        advance(4),taskcheck('I11','status','WAITING'),
        {'op':'mutate','body':{'target':'mqtt:humidity','mode':'normal'}}, {'op':'tick'},
        advance(4),taskcheck('I11','status','WAITING'),advance(1),wait('VERIFIED')]
    cases['explicit_gap_resets_stability'] = base+[sensor(50),
        submit(trigger={'type':'state.v1','condition':condition}), {'op':'tick'},advance(3),
        {'op':'event','body':{'type':'observation.gap','source':'mqtt','payload':{'reason':'bounded-history-loss'}}},
        {'op':'sleep','seconds':.1},{'op':'tick'},advance(2),taskcheck('I11','status','WAITING'),advance(3),wait('VERIFIED')]
    cases['clock_discontinuity_stale_sample'] = base+[sensor(50),
        submit(trigger={'type':'state.v1','condition':condition}), {'op':'tick'},
        fault('old-sensor',target='mqtt:humidity',stale=True,stage='before',hold=True),boundary('old-sensor'),
        {'op':'clock_jump','seconds':20},release('old-sensor'),
        {'op':'tick'},taskcheck('I11','status','WAITING')]
    cases['world_restart_manual_while_home_offline'] = base+[submit(**temporary),wait('VERIFIED'),
        {'op':'process','action':'kill'},mutate(power=False),mutate(power=True),
        {'op':'process','action':'restart','target':'world'}, {'op':'process','action':'start'},
        wait('OVERRIDDEN'),{'op':'end'},check('I10',['commands'],1),
        check('I5',['world','mqtt:light','values','power'],True)]
    cases['restart_cannot_bridge_sensor_interval'] = base+[sensor(50),
        submit(trigger={'type':'state.v1','condition':condition}),{'op':'tick'},advance(2),
        {'op':'process','action':'kill'},sensor(70),{'op':'clock_jump','seconds':10},sensor(50),
        {'op':'process','action':'start'},{'op':'tick'},taskcheck('I11','status','WAITING'),
        advance(5),wait('VERIFIED')]
    cases['preadmission_same_timestamp_fact'] = base+[
        {'op':'event','body':{'type':'contact.opened','source':'old-door','event_id':'before-admission'}},
        {'op':'sleep','seconds':.1},
        submit(trigger={'type':'raw_event.v1','event_type':'contact.opened','source':'old-door'}),
        {'op':'tick'},taskcheck('I8','status','WAITING'),check('I4',['commands'],0),
        {'op':'event','body':{'type':'contact.opened','source':'old-door','event_id':'after-admission'}},
        wait('VERIFIED'),check('I8',['commands'],1)]
    cases['late_duplicate_out_of_order_events'] = base+[
        submit(trigger={'type':'raw_event.v1','source':'door','event_type':'contact.opened'}),
        {'op':'event','body':{'type':'contact.opened','source':'door','offset':-1,'event_id':'old'}},
        {'op':'tick'},check('I8',['commands'],0),
        {'op':'event','body':{'type':'contact.opened','source':'door','event_id':'current'}},wait('VERIFIED'),
        {'op':'event','body':{'type':'contact.opened','source':'door','event_id':'current'}},
        {'op':'event','body':{'type':'contact.opened','source':'door','offset':-2,'event_id':'older'}},
        {'op':'tick'},check('I8',['commands'],1),check('I7',['tasks','a','result','observation','provenance'],'SPIRA')]
    cases['job_replacement_and_old_completion'] = [
        {'op':'mutate','body':{'target':'mqtt:robot_01','mode':'normal','values':{'run_state':'idle','job_id':None,'done_today':False}}},
        submit(target='mqtt:robot_01',family='RUN_JOB',desired={'run_state':'running','room':'living'},**temporary),wait('RUNNING'),
        {'op':'mutate','body':{'target':'mqtt:robot_01','values':{'run_state':'running','job_id':'external-job'}}},wait('OVERRIDDEN'),
        advance(95),{'op':'end'},check('I5',['world','mqtt:robot_01','values','job_id'],'external-job'),
        check('I12',['world','mqtt:robot_01','values','run_state'],'running')]
    cases['effect_after_verification_timeout'] = base+[
        {'op':'mutate','body':{'target':'mqtt:light','mode':'delayed','delay':.25}},
        submit(verify_seconds=0,retry={'max_attempts':4}),wait('MISMATCH'),
        {'op':'sleep','seconds':.3},advance(3),taskcheck('I1','status','MISMATCH'),
        check('I3',['commands'],1),check('I1',['world','mqtt:light','values','power'],True)]
    cases['lost_ack_without_restart'] = base+[fault('lost-ack',kind='command',drop=True),
        submit(retry={'max_attempts':3}),wait('INDETERMINATE'),advance(3),check('I3',['commands'],1)]
    cases['missing_readback_after_effect'] = base+[
        {'op':'mutate','body':{'target':'mqtt:light','mode':'blind_after_command'}},
        submit(retry={'max_attempts':3}),wait('UNVERIFIABLE'),advance(3),
        check('I1',['world','mqtt:light','values','power'],True),check('I3',['commands'],1)]
    cases['supersession_after_irreversible_dispatch'] = base+[
        fault('already-sent',kind='command',stage='before',hold=True),submit(),boundary('already-sent'),
        {'op':'supersede'},release('already-sent'),{'op':'settle'},
        check('I4',['commands'],1),{'op':'tick'},check('I4',['commands'],1),
        check('I1',['world','mqtt:light','values','power'],True)]
    cases['end_while_verification_waits'] = base+[
        fault('end-verify',skip=1,stage='before',hold=True),submit(**temporary),boundary('end-verify'),
        {'op':'end','async':True},mutate(power=False),mutate(power=True),release('end-verify'),{'op':'join'},
        check('I12',['world','mqtt:light','values','power'],True),check('I5',['commands'],1)]
    cases['job_completes_while_home_offline'] = [
        {'op':'mutate','body':{'target':'mqtt:robot_01','mode':'normal','values':{'run_state':'idle','job_id':None,'done_today':False}}},
        submit(target='mqtt:robot_01',family='RUN_JOB',desired={'run_state':'running','room':'living'},**temporary),wait('RUNNING'),
        {'op':'process','action':'kill'},{'op':'clock_jump','seconds':95},{'op':'process','action':'start'},
        wait('COMPLETED'),check('I10',['commands'],1),taskcheck('I1','status','COMPLETED')]
    cases['gap_during_pending_condition_read'] = base+[sensor(50),
        submit(trigger={'type':'state.v1','condition':condition}),{'op':'tick'},advance(3),
        fault('condition',target='mqtt:humidity',stage='before',hold=True),boundary('condition'),
        {'op':'event','body':{'type':'observation.gap','source':'mqtt'}},{'op':'clock_jump','seconds':2},
        release('condition'),{'op':'tick'},taskcheck('I11','status','WAITING')]
    cases['job_replacement_during_stop'] = [
        {'op':'mutate','body':{'target':'mqtt:robot_01','mode':'normal','values':{'run_state':'idle','job_id':None,'done_today':False}}},
        submit(target='mqtt:robot_01',family='RUN_JOB',desired={'run_state':'running','room':'living'}),wait('RUNNING'),
        fault('stop-job',target='mqtt:robot_01',kind='command',stage='before',hold=True),{'op':'end','async':True},boundary('stop-job'),
        {'op':'mutate','body':{'target':'mqtt:robot_01','values':{'job_id':'external-job'}}},release('stop-job'),{'op':'join'},
        check('I12',['world','mqtt:robot_01','values','run_state'],'running'),check('I5',['world','mqtt:robot_01','values','job_id'],'external-job')]
    cases['departure_return_trigger_context'] = base+[
        submit(temporary=True,restore=True,trigger={'type':'raw_event.v1','source':'entry','event_type':'doorlock.locked'},
               end={'trigger':{'type':'returned.v1','contact_source':'entry','at':0}}),
        {'op':'event','body':{'type':'doorlock.locked','source':'entry'}},wait('VERIFIED'),advance(1),
        {'op':'event','body':{'type':'doorlock.opened','source':'entry'}},{'op':'tick'},taskcheck('I12','restore_status','RESTORED')]
    return {name:{'name':name,'steps':copy.deepcopy(steps)} for name,steps in cases.items()}


def generated(seed):
    """Seeded grammar varies ordering, effects, loss, authority and deadlines."""
    rng = random.Random(seed)
    library = named()
    choices = [key for key in library if key not in ('R1_accepted_then_network_loss','R9_home_crash_during_dispatch')]
    name = rng.choice(choices)
    case = copy.deepcopy(library[name])
    case.update(name='generated-'+str(seed)+'-'+name, seed=seed, family=name)
    # Add world-only interleavings at dependency-safe points. Never perturb Home
    # state or erase the assertions that make the sequence falsifiable.
    steps = case['steps']
    for _ in range(rng.randint(1,5)):
        index = rng.randrange(1,len(steps))
        step = rng.choice([
            {'op':'event','body':{'type':'unrelated.fact','source':'noise','offset':-rng.randint(0,30)}},
            {'op':'mutate','body':{'target':'mqtt:contact','values':{'open':rng.choice([True,False])}}},
            {'op':'sleep','seconds':rng.choice([0,.005,.02,.06])},
        ])
        steps.insert(index,step)
    # Values and verification delay vary without destroying scenario meaning.
    for step in steps:
        if step['op'] == 'submit':
            step.setdefault('body', {}).setdefault('verify_seconds',rng.choice([0,.01,.04]))
        if step['op'] == 'fault' and step['body'].get('hold'):
            step['body']['delay'] = rng.choice([0,.005,.015,.05])
    return case

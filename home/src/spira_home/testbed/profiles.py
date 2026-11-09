"""Declared virtual capability fixtures. Vendor assumptions are labelled, never live claims."""

def profiles(mode='lg_assumed'):
    lg = mode == 'lg_assumed'
    def p(type, values, writable, families=('SET_STATE',), room='living', **kwargs):
        return dict(type=type, values=values, writable=writable, families=list(families), room=room, **kwargs)
    data = {
        'mqtt:light':p('light', {'power':False,'brightness':10}, ['power','brightness']),
        'mqtt:plug':p('smart_plug', {'power':False}, ['power']),
        'mqtt:contact':p('contact_sensor', {'open':False}, [], events=['contact.opened','contact.closed']),
        'mqtt:light_living':p('light', {'power':False,'brightness':40,'color':'warm','in_use':False}, ['power','brightness','color']),
        'mqtt:tv_01':p('tv', {'power':False,'scene':None,'music':None,'in_use':False}, ['power','scene','music'] if lg else ['power'], assumption='LG_MEDIA_ASSUMPTION' if lg else None),
        'mqtt:fridge_01':p('refrigerator', {'notices':[],'door_open':False}, ['notices'] if lg else [], ['PRESENT_INFORMATION'], 'kitchen', assumption='LG_DISPLAY_ASSUMPTION' if lg else None, events=['fridge.door_opened','fridge.door_closed']),
        'mqtt:oven_01':p('oven', {'preset':None,'in_use':False,'consumed':False}, ['preset'] if lg else [], ['CONFIGURE_FOR_NEXT_USE'], 'kitchen', assumption='LG_PRESET_ASSUMPTION' if lg else None, never=['start']),
        'mqtt:robot_01':p('robot_vacuum', {'run_state':'idle','job_id':None,'room':'living','in_use':False,'done_today':False}, ['run_state','room'], ['RUN_JOB','STOP'], noisy=True, job_seconds=90),
        'mqtt:induction_01':p('cooktop', {'child_lock':False,'in_use':False}, ['child_lock'] if lg else [], room='kitchen', assumption='UNVERIFIED_REMOTE_LOCK_ASSUMPTION' if lg else None, never=['on','unlock']),
        'mqtt:purifier_water_01':p('water_purifier', {'hot_water_lock':False,'in_use':False}, ['hot_water_lock'] if lg else [], room='kitchen', assumption='UNVERIFIED_REMOTE_LOCK_ASSUMPTION' if lg else None, never=['unlock']),
        'mqtt:plug_kettle':p('smart_plug', {'power':True,'in_use':False}, ['power'], room='kitchen'),
        'mqtt:plug_iron':p('smart_plug', {'power':True,'in_use':False}, ['power'], room='bedroom'),
        'mqtt:plug_router':p('smart_plug', {'power':True}, [], never=['off']),
        'mqtt:doorbell_01':p('doorbell', {}, [], events=['doorbell.pressed']),
        'mqtt:doorlock_01':p('door_contact', {'open':False,'locked':True}, [], events=['doorlock.opened','doorlock.locked']),
        'mqtt:sim_camera':p('camera_count_fact', {'package_count':0}, [], events=['camera.package_removed','camera.package_placed']),
        'mqtt:phone':p('information_sink', {'notices':[]}, ['notices'], ['PRESENT_INFORMATION'], assumption='VIRTUAL_PHONE_NOT_PUSH_NOTIFICATION'),
        'mqtt:humidity':p('humidity_sensor', {'humidity':70.0}, [], room='bedroom'),
        'mqtt:odor':p('odor_sensor', {'odor':80.0}, [], room='bedroom'),
        'mqtt:conditioner':p('condition_controller', {'mode':'off','in_use':False}, ['mode'], ['MAINTAIN_CONDITION','SET_STATE'], 'bedroom'),
        'mqtt:washer':p('washer', {'preset':None,'in_use':False,'consumed':False}, ['preset'], ['CONFIGURE_FOR_NEXT_USE'], 'laundry', never=['start']),
        'mqtt:dryer':p('dryer', {'door_open':False,'in_use':False}, [], room='laundry', events=['dryer.door_opened','dryer.door_closed']),
    }
    for target,profile in data.items():
        commands=[]
        writable=profile['writable']
        if 'power' in writable:commands.extend([{'cmd':'on'},{'cmd':'off'}])
        for prop,command in [('color','color'),('brightness','brightness'),('notices','show_notice'),('scene','gallery_scene'),('music','play_music_app'),('child_lock','child_lock'),('hot_water_lock','hot_water_lock'),('mode','maintain')]:
            if prop in writable:commands.append({'cmd':command})
        if 'preset' in writable:
            commands.append({'cmd':'set_cook_standby' if profile['type']=='oven' else 'set_course','value_schema':{'temp_c':[40,250],'minutes':[1,180]} if profile['type']=='oven' else {'course':['delicate','normal'],'dose':[0,100]}})
        if 'run_state' in writable:commands.extend([{'cmd':'clean'},{'cmd':'stop'}])
        profile['commands']=commands
    return data


def validate(profile, desired, expected=None):
    if not set(desired) <= set(profile['writable']):
        return 'UNSUPPORTED'
    for key,value in desired.items():
        if key in ('child_lock','hot_water_lock') and value is False and not expected:
            return 'PROHIBITED'  # only still-owned CAS restoration may undo our virtual lock
        if key in ('power','child_lock','hot_water_lock') and not isinstance(value,bool):
            return 'REJECTED'
        if key == 'brightness' and (isinstance(value,bool) or not isinstance(value,(int,float)) or not 0 <= value <= 100):
            return 'REJECTED'
        if key == 'mode' and value not in ('off','deodorize','auto_humidity'):
            return 'REJECTED'
        if key == 'notices' and (not isinstance(value,list) or len(value)>3 or any(not isinstance(x,str) or len(x)>500 for x in value)):
            return 'REJECTED'
        if key == 'preset' and value is not None:
            if not isinstance(value,dict):
                return 'REJECTED'
            if profile['type']=='oven' and (set(value)-{'mode','temp_c','minutes'} or not 40<=value.get('temp_c',0)<=250 or not 1<=value.get('minutes',0)<=180):
                return 'REJECTED'
            if profile['type']=='washer' and (set(value)-{'course','dose'} or value.get('course') not in ('delicate','normal') or not 0<=value.get('dose',0)<=100):
                return 'REJECTED'
        if key == 'run_state' and value not in ('running','stopped'):
            return 'REJECTED'
    return None

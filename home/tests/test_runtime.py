import asyncio
import uuid
import pytest
from spira_home.models import Task, Observation, Capability, Receipt, Event, now
from spira_home.runtime import Runtime, Conflict
from spira_home.connectors.base import Unavailable


class Device:
    def __init__(self):
        self.values = {'power': False, 'brightness': 10}
        self.rev = {k: uuid.uuid4().hex for k in self.values}
        self.commands = 0
        self.mode = 'normal'
        self.read_hook = None
        self.cas_hook = None

    def mutate(self, **values):
        self.values.update(values)
        self.rev.update({k: uuid.uuid4().hex for k in values})

    async def discover(self):
        return [Capability(target='test:light', writable=list(self.values), conditional_write=True, substrate='TEST')]

    async def read(self, target):
        if self.read_hook:
            self.read_hook()
            self.read_hook = None
        if self.mode == 'blind' and self.commands:
            raise Unavailable('readback lost')
        return Observation(target=target, values=self.values.copy(), revisions=self.rev.copy(), substrate='TEST')

    async def execute(self, target, desired, operation_id, expected=None):
        self.commands += 1
        if expected:
            if self.cas_hook:
                self.cas_hook()
            if any(self.rev.get(k) != v for k,v in expected.items()):
                return Receipt(status='CONFLICT', operation_id=operation_id)
        if self.mode != 'unchanged':
            self.mutate(**desired)
        return Receipt(status='ACCEPTED', operation_id=operation_id)


@pytest.fixture
def setup(tmp_path):
    d = Device()
    r = Runtime(str(tmp_path/'home.db'), {'test': d})
    yield r, d
    r.store.close()


def task(**kw):
    body = dict(episode_id='ep', plan_id='plan', plan_version=1, task_id='task', target='test:light',
                desired={'power': True}, valid_until=now()+60, verify_seconds=0)
    body.update(kw)
    return Task(**body)


async def test_acceptance_is_not_effect_truth(setup):
    r,d = setup
    d.mode = 'unchanged'
    t = task()
    r.submit(t)
    await r.tick()
    assert r.task(t.key)['result']['effect_status'] == 'MISMATCH'
    assert r.task(t.key)['result']['command_status']['status'] == 'ACCEPTED'


async def test_blind_readback_is_not_verified(setup):
    r,d = setup
    d.mode = 'blind'
    t = task()
    r.submit(t)
    await r.tick()
    assert r.task(t.key)['status'] == 'UNVERIFIABLE'


async def test_partial_restore_preserves_manual_property(setup):
    r,d = setup
    t = task(desired={'power': True, 'brightness': 80}, temporary=True, restore=True)
    r.submit(t)
    await r.tick()
    d.mutate(brightness=30)
    await r.restore(t.key)
    assert d.values == {'power': False, 'brightness': 30}
    assert r.task(t.key)['restore_status'] == 'RESTORED'


async def test_change_away_and_back_loses_ownership(setup):
    r,d = setup
    t = task(temporary=True, restore=True)
    r.submit(t)
    await r.tick()
    d.mutate(power=False)
    d.mutate(power=True)
    await r.restore(t.key)
    assert d.values['power'] is True
    assert r.task(t.key)['restore_status'] == 'RESTORE_SKIPPED_OVERRIDE'
    assert r.task(t.key)['result']['command_status']['status']=='ACCEPTED'


async def test_restore_race_uses_native_cas(setup):
    r,d = setup
    t = task(temporary=True, restore=True)
    r.submit(t)
    await r.tick()
    d.cas_hook = lambda: d.mutate(power=False)
    await r.restore(t.key)
    assert r.task(t.key)['restore_status'] == 'RESTORE_SKIPPED_OVERRIDE'


@pytest.mark.parametrize('control,expected', [('stop','CANCELLED'), ('version','SUPERSEDED')])
async def test_control_arriving_during_read_blocks_dispatch(setup, control, expected):
    r,d = setup
    t = task()
    r.submit(t)
    d.read_hook = lambda: r.control('stop', plan_id=t.plan_id) if control == 'stop' else r.advance(t.plan_id, 2)
    await r.tick()
    assert d.commands == 0
    assert r.task(t.key)['status'] == expected


async def test_unknown_precondition_and_policy(setup):
    r,d = setup
    t = task(preconditions={'door_closed': True})
    r.submit(t)
    await r.tick()
    assert r.task(t.key)['status'] == 'BLOCKED'
    prohibited = task(task_id='prohibited', policy='PROHIBITED')
    r.submit(prohibited)
    await r.tick()
    assert r.task(prohibited.key)['status'] == 'PROHIBITED'
    assert d.commands == 0


async def test_durable_version_dedup_and_recovery(tmp_path):
    path = str(tmp_path/'home.db')
    d = Device()
    t = task(temporary=True, restore=True)
    r = Runtime(path, {'test': d})
    r.submit(t)
    await r.tick()
    r.advance('plan', 2)
    r.store.close()
    r = Runtime(path, {'test': d})
    r.submit(t)
    await r.tick()
    assert d.commands == 2  # supersession restores owned temporary effect, without redispatch
    assert d.values['power'] is False
    with pytest.raises(Conflict):
        r.submit(task(task_id='late'))
    await r.restore(t.key)
    assert d.values['power'] is False
    assert r.store.rows('SELECT * FROM outbox')
    r.store.close()


async def test_uncertain_dispatch_never_replays(tmp_path):
    path = str(tmp_path/'home.db')
    d = Device()
    t = task()
    r = Runtime(path, {'test': d})
    r.submit(t)
    r.store.write("UPDATE tasks SET status='EXECUTING' WHERE id=?", (t.key,))
    r.store.close()
    r = Runtime(path, {'test': d})
    await r.tick()
    assert r.task(t.key)['status'] == 'INDETERMINATE'
    assert d.commands == 0
    r.store.close()


async def test_trigger_sources_order_window_and_event_dedup(setup):
    r,d = setup
    t = task(trigger={'type':'first_guest_arrival.v1','bell_source':'bell','contact_source':'contact','window_seconds':1})
    r.submit(t)
    e = Event(event_id='e',type='doorbell.pressed',source='wrong',substrate='TEST')
    assert r.observe(e)
    assert not r.observe(e)
    r.observe(Event(event_id='c',type='contact.opened',source='contact',substrate='TEST'))
    await r.tick()
    assert d.commands == 0
    r.observe(Event(event_id='b',type='doorbell.pressed',source='bell',substrate='TEST'))
    r.observe(Event(event_id='c2',type='contact.opened',source='contact',substrate='TEST'))
    await r.tick()
    assert d.commands == 1


async def test_restore_read_failure_is_published(setup):
    r,d = setup
    t = task(temporary=True, restore=True)
    r.submit(t)
    await r.tick()
    d.mode='blind'
    await r.restore(t.key)
    latest=r.store.rows('SELECT body FROM outbox ORDER BY seq DESC LIMIT 1')[0]
    import json
    assert json.loads(latest['body'])['restore_status']=='RESTORE_FAILED'


async def test_stop_all_pause_blocks_new_plans_and_restores_owned_effects(setup):
    r,d = setup
    t=task(temporary=True,restore=True)
    r.submit(t)
    await r.tick()
    r.control('pause')
    await r.end(t.plan_id)
    assert d.values['power'] is False
    later=task(plan_id='new-plan')
    r.submit(later)
    await r.tick()
    assert r.task(later.key)['status']=='WAITING'
    assert d.commands==2


async def test_delayed_intermediate_snapshot_does_not_revoke_later_read_witness(setup):
    r,d=setup
    t=task(desired={'power':True,'brightness':80},temporary=True,restore=True)
    r.submit(t);await r.tick()
    witness=r.task(t.key)['after_state']
    stale=Observation(target=t.target,values={'power':True,'brightness':10},
        observed_at=witness['observed_at']-.1,substrate='TEST')
    r.observe(Event(event_id='delayed-intermediate',type='state.changed',source=t.target,
        substrate='TEST',observation=stale))
    assert r.task(t.key)['status']=='VERIFIED'
    assert r.store.one('SELECT id FROM events WHERE id=?',('delayed-intermediate',))
    # At the witness time a revision change must still revoke authority, even
    # with the same value. This preserves virtual-clock away-and-back protection.
    current=Observation(target=t.target,values={'brightness':80},revisions={'brightness':'outside'},
        observed_at=witness['observed_at'],substrate='TEST')
    r.reconcile(current)
    assert r.task(t.key)['status']=='OVERRIDDEN'
    assert r.store.one("SELECT owned FROM ownership WHERE property='power'")['owned']==1

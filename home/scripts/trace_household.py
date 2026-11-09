"""Run against real Home + independent MQTT world processes; no in-process world mocks."""
import argparse
import json
import time
import uuid
from pathlib import Path
import httpx


def run(home, world, token=''):
    client = httpx.Client(base_url=home, headers={'Authorization': 'Bearer '+token} if token else {}, timeout=40)
    actor = httpx.Client(base_url=world, timeout=5)
    prefix = 'trace-'+uuid.uuid4().hex[:10]
    receipts = []

    def mutate(**data):
        r = actor.post('/mutate', json=data)
        r.raise_for_status()
        return r.json()

    def submit(name, **overrides):
        t = dict(episode_id=prefix, plan_id=prefix+'-'+name, plan_version=1, task_id=name,
                 target='mqtt:light', desired={'power': True}, valid_from=0, valid_until=time.time()+120)
        t.update(overrides)
        r = client.post('/v1/tasks', json=t)
        r.raise_for_status()
        return t, r.json()['id']

    def wait(key, status=None, limit=8):
        end = time.time()+limit
        while time.time() < end:
            row = next(r for r in client.get('/v1/tasks').json() if r['id'] == key)
            if row['status'] not in ('WAITING', 'EXECUTING') and (status is None or row['status'] == status):
                return row
            time.sleep(.1)
        raise AssertionError(f'task did not reach {status}: {row}')

    def end(t):
        r = client.post('/v1/control', json={'action': 'end', 'plan_id': t['plan_id']})
        r.raise_for_status()

    def record(trace, row, **extra):
        receipts.append(dict(trace=trace, status='PASS', task=row, **extra))
        print(trace+' PASS', flush=True)

    mutate(mode='normal', values={'power': False, 'brightness': 10})
    t, key = submit('T0', temporary=True, restore=True)
    row = wait(key)
    assert row['status'] == 'VERIFIED', row
    assert row['result']['command_status']['status'] == 'ACCEPTED'
    assert row['result']['observation']['values']['power'] is True
    record('T0', row)
    end(t)

    mutate(values={'power': False})
    t, key = submit('T1', trigger={'type': 'first_guest_arrival.v1', 'bell_source': 'mqtt:bell', 'contact_source': 'mqtt:contact'})
    time.sleep(.2)  # observations follow node-local task admission
    mutate(event_type='doorbell.pressed', source='mqtt:bell')
    time.sleep(.1)
    mutate(event_type='contact.opened', source='mqtt:contact')
    row = wait(key)
    assert row['status'] == 'VERIFIED', row
    record('T1', row)

    mutate(values={'power': False}, mode='unchanged')
    _, key = submit('T2', verify_seconds=.2)
    row = wait(key)
    assert row['status'] == 'MISMATCH', row
    record('T2', row)

    mutate(mode='normal', values={'power': False, 'brightness': 10})
    t, key = submit('T3', desired={'power': True, 'brightness': 80}, temporary=True, restore=True)
    assert wait(key)['status'] == 'VERIFIED'
    mutate(values={'brightness': 30})
    row = wait(key, 'OVERRIDDEN')
    record('T3', row)
    end(t)
    state = actor.get('/world').json()
    light = next(d for d in state['devices'] if d['target'] == 'mqtt:light')
    assert light['values'] == {'power': False, 'brightness': 30}, light
    record('T4', wait(key), world_after_restore=light)

    mutate(values={'power': False})
    t, key = submit('T5', trigger={'type': 'raw_event.v1', 'event_type': 'button.pressed', 'source': 'mqtt:button'})
    r = client.post('/v1/plans/current', json={'plan_id': t['plan_id'], 'version': 2})
    r.raise_for_status()
    mutate(event_type='button.pressed', source='mqtt:button')
    row = wait(key)
    assert row['status'] == 'SUPERSEDED'
    r = client.post('/v1/tasks', json={**t, 'task_id': 'late'})
    assert r.status_code == 409
    record('T5', row)

    # T6: external runner restarts Home between prepare and verify; this script also checks durable dedup.
    mutate(values={'power': False})
    t, key = submit('T6', temporary=True, restore=True)
    row = wait(key)
    assert row['status'] == 'VERIFIED'
    count = actor.get('/world').json()['commands']
    r = client.post('/v1/tasks', json=t)
    r.raise_for_status()
    time.sleep(.3)
    assert actor.get('/world').json()['commands'] == count
    record('T6-dedup', row, restart_required=True)
    end(t)

    for mode, expected in [('reject', 'REJECTED'), ('offline', 'UNAVAILABLE'), ('delayed', 'VERIFIED'), ('blind_after_command', 'UNVERIFIABLE')]:
        mutate(mode='normal', values={'power': False})
        mutate(mode=mode)
        _, key = submit('adversary-'+mode, verify_seconds=1.2)
        row = wait(key)
        assert row['status'] == expected, row
        record('adversary-'+mode, row)
    mutate(mode='normal')
    return dict(run=prefix, substrate='independent MQTT actor', receipts=receipts)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--home', default='http://127.0.0.1:8710')
    p.add_argument('--world', default='http://127.0.0.1:8711')
    p.add_argument('--output', default='docs/evidence/household-traces.json')
    p.add_argument('--token', default='')
    args = p.parse_args()
    result = run(args.home, args.world, args.token)
    Path(args.output).write_text(json.dumps(result, indent=2)+'\n')

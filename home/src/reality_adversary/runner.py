"""Scenario interpreter. All Home access is through its public HTTP contract."""
import copy
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx

from reality_adversary.checker import evaluate


class Runner:
    def __init__(self, home, world, process=None):
        self.h = httpx.Client(base_url=home, timeout=20)
        self.w = httpx.Client(base_url=world, timeout=20)
        self.process = process
        self.pool = ThreadPoolExecutor(max_workers=2)
        self.home_offline = False
        self.world_offline = False

    def close(self):
        self.pool.shutdown(wait=True)
        self.h.close()
        self.w.close()

    def get(self, client, path, **params):
        r = client.get(path, params=params)
        r.raise_for_status()
        return r.json()

    def post(self, client, path, body=None):
        r = client.post(path, json={} if body is None else body)
        r.raise_for_status()
        return r.json()

    def poll(self, predicate, limit=8):
        end = time.monotonic()+limit
        last = None
        while time.monotonic() < end:
            try:
                last = predicate()
                if last:
                    return last
            except httpx.TransportError:
                pass
            time.sleep(.025)
        raise AssertionError('Occurrence boundary not reached: '+str(last))

    def snapshot(self):
        if self.home_offline or self.world_offline:
            return dict(tasks={}, world={}, journal=[], events=[], results=[], commands=None,
                        occurrence_time=None, observation_time=time.time(), processing_time=time.monotonic(),
                        home_offline=self.home_offline, world_offline=self.world_offline)
        world = self.get(self.w, '/world')
        tasks = {label:row for row in self.get(self.h, '/v1/tasks') for label,key in self.keys.items() if row['id'] == key}
        journal = self.get(self.w, '/pressure/journal', after=self.journal_cursor)
        if journal:
            self.journal_cursor = journal[-1]['sequence']
        events = self.get(self.h, '/v1/events', after=self.event_cursor)
        self.observed_events.extend(events)
        if events:
            self.event_cursor = events[-1]['sequence']
        results = self.get(self.h, '/v1/results', after=self.result_cursor)
        if results:
            self.result_cursor = results[-1]['result_id']
        results = [x for x in results if x['result']['plan_id'] == self.prefix]
        state = dict(tasks=tasks, world={x['target']:x for x in world['devices']},
                     commands=world['commands']-self.baseline, journal=journal, events=events, results=results,
                     observed_events=self.observed_events.copy(),
                     occurrence_time=world['timestamp'], observation_time=time.time(), processing_time=time.monotonic())
        return state

    def value(self, path):
        state = self.snapshot()
        self.trace['timeline'].append(state)
        value = state
        for part in path:
            value = value.get(part) if isinstance(value, dict) else value[part]
        return value, state

    def advance(self, seconds):
        target = self.get(self.w, '/world')['timestamp']+seconds
        while True:
            current = self.get(self.w, '/world')['timestamp']
            if current >= target:
                return
            timeline = self.get(self.h, '/v1/timeline')
            world = self.get(self.w, '/world')
            due = [x for x in (target, timeline['next_due'], world['next_due']) if x is not None and x > current]
            step = min(due)
            self.post(self.w, '/sim/clock', {'timestamp':step})
            self.poll(lambda:self.get(self.h, '/health')['timestamp'] >= step)
            self.post(self.h, '/v1/test/tick')

    def execute_step(self, step):
        op = step['op']
        if op == 'mutate':
            state = self.post(self.w, '/mutate', {'provenance':'UNKNOWN', **step['body']})
            if step['body'].get('values') is not None and not self.home_offline and step.get('observed',True):
                self.poll(lambda:any((x['event'].get('observation') or {}).get('revisions') == state['revisions'] and
                    x['event']['source'] == state['target'] for x in self.get(self.h, '/v1/events', tail=True)))
            return state
        if op == 'fault':
            return self.post(self.w, '/pressure/fault', step['body'])
        if op == 'release':
            return self.post(self.w, '/pressure/release/'+step['id'])
        if op == 'boundary':
            return self.poll(lambda:next((x for x in self.get(self.w, '/pressure/journal', after=self.start_cursor)
                                          if x['kind'] == step.get('kind', 'fault.reached') and
                                          (not step.get('id') or x.get('fault_id') == step['id'])), None))
        if op == 'submit':
            label = step.get('label', 'a')
            now = self.get(self.w, '/world')['timestamp']
            body = dict(episode_id=self.prefix, plan_id=self.prefix, plan_version=1, task_id=label,
                        target='mqtt:light', desired={'power':True}, valid_until=now+300, verify_seconds=0)
            body.update(copy.deepcopy(step.get('body', {})))
            if 'deadline_seconds' in body:
                body['valid_until'] = now+body.pop('deadline_seconds')
            if body.get('trigger', {}).get('at') == '$OLD':
                body['trigger']['at'] = now-60
            row = self.post(self.h, '/v1/tasks', body)
            self.keys[label] = row['id']
            self.bodies[label] = body
            return row
        if op == 'supersede':
            return self.post(self.h, '/v1/plans/current', {'plan_id':self.prefix, 'version':step.get('version', 2)})
        if op in ('end', 'stop'):
            body = {'action':op, 'plan_id':self.prefix}
            if step.get('async'):
                self.futures.append(self.pool.submit(self.post, self.h, '/v1/control', body))
                return {'pending':op}
            return self.post(self.h, '/v1/control', body)
        if op == 'join':
            return [f.result(timeout=20) for f in self.futures]
        if op == 'advance':
            return self.advance(step['seconds'])
        if op == 'clock_jump':
            now = self.get(self.w, '/world')['timestamp']
            self.post(self.w, '/sim/clock', {'timestamp':now+step['seconds']})
            if self.home_offline:
                return {'world_timestamp':now+step['seconds'], 'home_observation':'OFFLINE'}
            return self.poll(lambda:self.get(self.h, '/health')['timestamp'] >= now+step['seconds'])
        if op == 'event':
            body = copy.deepcopy(step['body'])
            now = self.get(self.w, '/world')['timestamp']
            body['at'] = now+body.pop('offset', 0)
            if 'event_id' in body:
                body['event_id'] = self.prefix+'-'+body['event_id']
            emitted = self.post(self.w, '/pressure/event', body)
            if not self.home_offline and step.get('observed', True):
                self.poll(lambda:any(x['event']['event_id'] == emitted['event_id']
                    for x in self.get(self.h, '/v1/events', tail=True)))
            return emitted
        if op == 'tick':
            return self.post(self.h, '/v1/test/tick')
        if op == 'dynamics':
            from reality_adversary.dynamics import ScalarDynamics
            model = ScalarDynamics(**step['model'])
            values = []
            for disturbance in step['disturbances']:
                world = self.get(self.w, '/world')
                actuator = next(d for d in world['devices'] if d['target'] == step['actuator'])
                active = actuator['values'].get(step['actuator_property']) == step['active_value']
                value = model.step(active, disturbance)
                self.post(self.w, '/mutate', {'target':step['sensor'], 'values':{step['property']:value}, 'provenance':'UNKNOWN'})
                self.advance(model.interval)
                values.append(value)
            return {'synthetic_parameters':True, 'samples':values}
        if op == 'wait':
            return self.poll(lambda:self.value(step['path'])[0] == step['equals'], step.get('limit', 8))
        if op == 'settle':
            return self.poll(lambda:self.value(['tasks',step.get('label','a'),'status'])[0] not in ('WAITING','EXECUTING'), step.get('limit',8))
        if op == 'assert':
            value, state = self.value(step['path'])
            self.trace['timeline'].append(state)
            assertion = dict(invariant=step['invariant'], path=step['path'], expected=step['equals'], actual=value,
                             occurrence_time=state['occurrence_time'], processing_time=time.monotonic())
            self.trace['assertions'].append(assertion)
            return assertion
        if op == 'summary':
            summary = self.get(self.h, '/v1/plans/'+self.prefix+'/summary')
            self.trace['assertions'].append(dict(invariant='I9', actual=summary['line_status'][step['line']],
                                                  expected=step['equals'], source='public plan summary'))
            return summary
        if op == 'process':
            if self.process is None:
                raise ValueError('Scenario requires an explicitly selected process adapter')
            result = self.process(step['action'], step.get('target', 'home'))
            if step.get('target', 'home') == 'home':
                self.home_offline = step['action'] in ('kill', 'stop')
            elif step['target'] == 'world':
                self.world_offline = step['action'] in ('kill', 'stop')
            if step['action'] in ('start', 'restart'):
                if step.get('target', 'home') == 'home':
                    self.poll(lambda:self.get(self.h, '/health'), limit=30)
                else:
                    self.poll(lambda:self.get(self.w, '/world'), limit=30)
            return result
        if op == 'sleep':
            time.sleep(step['seconds'])
            return None
        raise ValueError('Unknown occurrence: '+op)

    def run(self, scenario):
        self.prefix = 'reality-'+uuid.uuid4().hex[:12]
        self.keys, self.bodies, self.futures = {}, {}, []
        self.observed_events = []
        self.post(self.w, '/pressure/clear')
        self.baseline = self.get(self.w, '/world')['commands']
        journal = self.get(self.w, '/pressure/journal')
        # Drain bounded pages without assuming the first page is the tail.
        self.journal_cursor = 0
        while journal:
            self.journal_cursor = journal[-1]['sequence']
            journal = self.get(self.w, '/pressure/journal', after=self.journal_cursor)
        self.start_cursor = self.journal_cursor
        events = self.get(self.h, '/v1/events', tail=True)
        self.event_cursor = events[-1]['sequence'] if events else 0
        results = self.get(self.h, '/v1/results')
        self.result_cursor = 0
        while results:
            self.result_cursor = results[-1]['result_id']
            results = self.get(self.h, '/v1/results', after=self.result_cursor)
        self.trace = dict(name=scenario['name'], seed=scenario.get('seed'), scenario=scenario,
                          substrate='VIRTUAL_MQTT', episode=self.prefix, timeline=[], assertions=[])
        started = time.monotonic()
        try:
            for number, step in enumerate(scenario['steps']):
                output = self.execute_step(step)
                self.trace['timeline'].append(dict(sequence=number, actor='adversary', action=step,
                                                   output=output, **self.snapshot()))
            self.trace['evaluation'] = evaluate(self.trace)
        except Exception as error:
            self.trace['error'] = dict(type=type(error).__name__, message=str(error), step=number)
            self.trace['evaluation'] = evaluate(self.trace)
            self.trace['evaluation']['status'] = 'INCONCLUSIVE' if self.trace['evaluation']['status'] != 'VIOLATION' else 'VIOLATION'
        finally:
            self.post(self.w, '/pressure/clear')
            if self.home_offline and self.process:
                self.process('start', 'home')
                self.home_offline = False
                self.poll(lambda:self.get(self.h, '/health'), limit=30)
            for future in self.futures:
                future.result(timeout=20)
            self.post(self.h, '/v1/control', {'action':'end', 'plan_id':self.prefix})
        self.trace['runtime_seconds'] = round(time.monotonic()-started, 4)
        return self.trace

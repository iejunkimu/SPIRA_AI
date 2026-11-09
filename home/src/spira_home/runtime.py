"""Home authority, local scheduling, execution, and reconciliation."""

import asyncio
import json
import time
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo
from spira_home.models import Task, Trigger, Event, Observation
from spira_home.clock import Clock
from spira_home.connectors.base import Unavailable
from spira_home.store import Store
from spira_home.rules import evaluate, next_rule_time


def canonical(value):
    def normalize(item):
        if isinstance(item,float) and item.is_integer():
            return int(item)
        if isinstance(item,list):
            return [normalize(x) for x in item]
        if isinstance(item,dict):
            return {k:normalize(v) for k,v in item.items()}
        return item
    return json.dumps(normalize(value),sort_keys=True,separators=(',',':'))


class Conflict(ValueError):
    pass


class Runtime:
    """Own Home execution truth; connectors own observations of the world."""
    def __init__(self, db: str, connectors: dict, clock=None):
        """Recover durable work before any new tick or connector event can run."""
        self.store = Store(db)
        self.connectors = connectors
        self.clock = clock or Clock()
        self.lock = asyncio.Lock()
        # Process downtime has no continuous sensor witness, even when a
        # persisted condition timer happened to have been active before death.
        self.store.write('UPDATE tasks SET trigger_since=NULL,stable_since=NULL')
        for row in self.store.rows("SELECT * FROM tasks WHERE status='EXECUTING'"):
            t = Task.model_validate_json(row['body'])
            self.result(t, 'INDETERMINATE', attempted=True, reason='RESTART_DURING_DISPATCH')
        self.store.write("UPDATE tasks SET restore_status='INDETERMINATE' WHERE restore_status='RESTORING'")

    def now(self):
        return self.clock.now()

    def usable(self, observation, not_before=None):
        # Read observations carry source time, not the time an old reply finally
        # arrives. Virtual time has no transport-clock skew; wall-clock peers
        # allow a bounded five-second skew for prechecks/condition sampling.
        floor = not_before if not_before is not None else self.now()-(0 if self.clock.virtual else 5)
        return observation.available and observation.observed_at >= floor

    def connector(self, target):
        prefix = target.partition(':')[0]
        if prefix not in self.connectors:
            raise Unavailable('connector not configured: '+prefix)
        return self.connectors[prefix]

    # Plan and task admission

    def submit(self, task: Task):
        """Admit one normalized task; a repeated identity must have the same body."""
        old = self.store.one('SELECT * FROM tasks WHERE id=?', (task.key,))
        if old:
            if canonical(Task.model_validate_json(old['body']).model_dump()) != canonical(task.model_dump()):
                raise Conflict('same task identity has different content')
            return self.task(task.key)
        plan = self.store.one('SELECT * FROM plans WHERE id=?', (task.plan_id,))
        if plan and task.plan_version < plan['version']:
            raise Conflict('stale plan version')
        with self.store.db:
            self.store.db.execute('INSERT INTO plans VALUES(?,?) ON CONFLICT(id) DO UPDATE SET version=max(version,excluded.version)', (task.plan_id, task.plan_version))
            self.store.db.execute('INSERT INTO tasks(id,body,status,received,event_floor) VALUES(?,?,?,?,?)', (task.key, task.model_dump_json(), 'WAITING', self.now(), self.event_cursor()))
        return self.task(task.key)

    def admit_plan(self, plan_id, version, wire, tasks, cancelled=()):
        """Commit the version fence and all translated actions together."""
        encoded = canonical(wire)
        old = self.store.one('SELECT body FROM plan_batches WHERE id=? AND version=?', (plan_id, version))
        if old:
            if canonical(json.loads(old['body'])) != encoded:
                raise Conflict('same plan version has different content')
            return {'accepted': True, 'duplicate': True, 'task_keys': [t.key for t in tasks]}
        floor = self.store.one('SELECT version FROM plans WHERE id=?', (plan_id,))
        if floor and version < floor['version']:
            raise Conflict('stale plan version')
        for t in tasks:
            old_task = self.store.one('SELECT body FROM tasks WHERE id=?', (t.key,))
            if old_task and Task.model_validate_json(old_task['body']) != t:
                raise Conflict('task identity conflict in plan')
        if len({t.key for t in tasks}) != len(tasks):
            raise Conflict('duplicate action identities in plan')
        with self.store.db:
            self.store.db.execute('INSERT INTO plans VALUES(?,?) ON CONFLICT(id) DO UPDATE SET version=max(version,excluded.version)', (plan_id, version))
            self.store.db.execute('INSERT INTO plan_batches VALUES(?,?,?,?)', (plan_id, version, encoded, self.now()))
            for t in tasks:
                self.store.db.execute('INSERT OR IGNORE INTO tasks(id,body,status,received,event_floor) VALUES(?,?,?,?,?)', (t.key, t.model_dump_json(), 'WAITING', self.now(), self.event_cursor()))
            for row in self.store.rows('SELECT id,body FROM tasks'):
                t = Task.model_validate_json(row['body'])
                if t.plan_id == plan_id and (t.line_id or t.task_id) in cancelled:
                    self.store.db.execute('INSERT OR REPLACE INTO controls VALUES(?,?)', ('disable:'+t.key, 'true'))
        return {'accepted': True, 'duplicate': False, 'task_keys': [t.key for t in tasks]}

    def advance(self, plan_id, version):
        self.store.write('INSERT INTO plans VALUES(?,?) ON CONFLICT(id) DO UPDATE SET version=max(version,excluded.version)', (plan_id, version))

    # Durable results and execution authority

    def task(self, key):
        row = self.store.one('SELECT * FROM tasks WHERE id=?', (key,))
        if not row:
            raise KeyError(key)
        fields = ('body', 'result', 'before_state', 'after_state', 'command_receipt', 'gate_evidence')
        return {k: json.loads(v) if k in fields and v else v for k, v in row.items()}

    def result(self, t, effect, attempted=False, command=None, observation=None, reason=None, restore=None, phase=None):
        row = self.task(t.key)
        r = dict(schema_version=1, episode_id=t.episode_id, plan_id=t.plan_id,
                 plan_version=t.plan_version, task_id=t.task_id, line_id=t.line_id or t.task_id,
                 target=t.target, family=t.family, attempted=attempted, command_status=command,
                 effect_status=effect, observation=observation.model_dump() if observation else None,
                 reason=reason, restore_status=restore, reported_at=self.now(),
                 attempts=row['attempts'], virtual_time=self.clock.virtual)
        self.store.publish(t.key, r, phase or effect)
        return r

    def gate(self, t):
        """Fence stale, stopped, expired, prohibited, or paused work before dispatch."""
        floor = self.store.one('SELECT version FROM plans WHERE id=?', (t.plan_id,))
        if floor['version'] != t.plan_version:
            return 'SUPERSEDED'
        if self.store.one('SELECT * FROM controls WHERE id IN (?,?)', ('stop:'+t.plan_id, 'disable:'+t.key)):
            return 'CANCELLED'
        if self.now() >= t.valid_until:
            return 'EXPIRED'
        if t.policy == 'PROHIBITED':
            return 'PROHIBITED'
        if self.store.one("SELECT * FROM controls WHERE id='pause' AND value='true'"):
            return 'PAUSED'
        return None

    def control(self, action, plan_id=None, task_key=None):
        key = {'pause': 'pause', 'resume': 'pause', 'stop': 'stop:'+str(plan_id), 'disable': 'disable:'+str(task_key)}[action]
        self.store.write('INSERT OR REPLACE INTO controls VALUES(?,?)', (key, 'false' if action == 'resume' else 'true'))

    def disable_line(self, plan_id, line_id):
        for row in self.store.rows('SELECT id,body FROM tasks'):
            t = Task.model_validate_json(row['body'])
            if t.plan_id == plan_id and (t.line_id or t.task_id) == line_id:
                self.control('disable', task_key=t.key)

    # Observation ingest and ownership reconciliation

    def observe(self, event: Event):
        """Persist deduplicated independent facts for triggers and the Service outbox."""
        if event.type == 'clock.advanced' and self.clock.virtual:
            self.clock.advance(event.payload['timestamp'])
            return True
        if event.type == 'observation.gap':
            # Transport loss is known at processing time, not at a device's
            # occurrence clock (which can be isolated virtual time).
            event = event.model_copy(update={'at':self.now(), 'payload':{
                **event.payload, 'source_reported_at':event.at}})
        if event.at > self.now()+5 or event.at < self.now()-300:
            return False
        if self.store.one('SELECT id FROM events WHERE id=?', (event.event_id,)):
            return False
        with self.store.db:
            self.store.db.execute('INSERT INTO events VALUES(?,?,?)', (event.event_id, event.model_dump_json(), self.now()))
            self.store.db.execute('INSERT OR IGNORE INTO event_outbox(event_id,body) VALUES(?,?)', (event.event_id, event.model_dump_json()))
        if event.type == 'observation.gap':
            # A sustained interval cannot bridge an explicitly lost observation
            # stream. New independent samples must establish stability again.
            self.store.write('UPDATE tasks SET trigger_since=NULL,stable_since=NULL')
        if event.observation:
            self.reconcile(event.observation)
        return True

    def reconcile(self, observation):
        """Relinquish property authority on independent change so the outside actor wins."""
        for row in self.store.rows('SELECT * FROM ownership WHERE target=? AND owned=1', (observation.target,)):
            t = Task.model_validate(self.task(row['task'])['body'])
            # Subscription delivery can lag a later independent verification
            # read (e.g. intermediate state of two sequential Matter commands).
            # Retain the raw event, but do not replace a newer state witness
            # with an older snapshot. Equal-time revision changes still count.
            witness = self.task(t.key)['after_state']
            if witness and observation.substrate == witness['substrate'] and observation.observed_at < witness['observed_at']:
                continue
            prop = row['property']
            if prop not in observation.values:
                continue
            # A CAS restoration is an owned in-flight attempt, not an external override.
            if self.task(t.key)['restore_status']=='RESTORING' and observation.values[prop]==json.loads(row['before_value']):
                continue
            # Job progress is device-owned; its command identity is checked at completion/stop.
            if t.family == 'RUN_JOB' and prop in ('run_state', 'job_id') and observation.values.get('job_id') == self.task(t.key)['operation_id']:
                continue
            changed = observation.values[prop] != json.loads(row['desired'])
            if row['revision'] and prop in observation.revisions:
                changed |= row['revision'] != observation.revisions[prop]
            if changed:
                self.store.write('UPDATE ownership SET owned=0 WHERE target=? AND property=?', (row['target'], prop))
                prior = self.task(row['task'])['result'] or {}
                self.result(t, 'OVERRIDDEN', attempted=True, command=prior.get('command_status'), observation=observation, reason='EXTERNAL_CHANGE_OBSERVED')

    # Local facts, triggers, and scheduling

    def event_cursor(self):
        """Separate later facts from pre-admission facts even at the same timestamp."""
        row = self.store.one("SELECT seq FROM sqlite_sequence WHERE name='event_outbox'")
        return row['seq'] if row else 0

    def facts(self, received, event_floor=0):
        return [Event.model_validate_json(r['body']) for r in self.store.rows(
            'SELECT e.body FROM events e JOIN event_outbox o ON o.event_id=e.id WHERE e.received>=? AND o.seq>? ORDER BY e.received,o.seq',
            (received, event_floor))]

    def triggered(self, t, received):
        match, _ = evaluate(t.trigger, self.facts(received, self.task(t.key)['event_floor']), min(self.now(), t.valid_until), max(received, t.valid_from))
        return match

    async def condition(self, condition, t, column):
        try:
            state = await self.connector(condition.target).read(condition.target)
            matches = self.usable(state) and condition.matches(state.values)
        except (Unavailable, TimeoutError):
            matches = False
        # An observation gap can reset the durable timer while read I/O waits.
        # Do not resurrect the pre-await snapshot after that reset.
        row = self.task(t.key)
        if not matches:
            if row[column] is not None:
                self.store.write('UPDATE tasks SET '+column+'=NULL WHERE id=?', (t.key,))
            return False
        since = row[column]
        if since is None:
            since = self.now()
            self.store.write('UPDATE tasks SET '+column+'=? WHERE id=?', (since, t.key))
        return self.now()-since >= condition.stable_seconds

    async def local_trigger(self, t, row):
        """Evaluate the admitted trigger against local time and independent facts."""
        if t.trigger.type == 'state.v1':
            return await self.condition(t.trigger.condition, t, 'trigger_since')
        match, evidence = evaluate(t.trigger, self.facts(row['received'], row['event_floor']), min(self.now(), t.valid_until), max(row['received'], t.valid_from))
        if match and t.trigger.type not in ('immediate', 'at'):
            kind = {'first_guest_arrival.v1':'guest_arrived', 'package_brought_in.v1':'package.brought_in',
                    'event_end.v1':'event_end', 'departed.v1':'departed', 'returned.v1':'returned'}.get(t.trigger.type)
            if kind:
                self.derived(t, kind, evidence)
        return match

    def derived(self, t, kind, evidence):
        identifier = 'derived:'+json.dumps([t.plan_id,t.plan_version,kind],separators=(',',':'))
        if not self.store.one('SELECT id FROM events WHERE id=?', (identifier,)):
            self.observe(Event(event_id=identifier, type=kind, source='home', at=self.now(),
                               substrate='HOME_RULE', payload={'rule':kind+'.v1','evidence_ids':evidence,'plan_id':t.plan_id,'plan_version':t.plan_version}))

    async def tick(self):
        """Serialize lifecycle, due work, and ownership checks within Home."""
        async with self.lock:
            await self.lifecycle()
            for row in self.store.rows("SELECT * FROM tasks WHERE status IN ('WAITING','RETRY_WAIT')"):
                t = Task.model_validate_json(row['body'])
                reason = self.gate(t)
                if reason and reason != 'PAUSED':
                    self.result(t, reason, reason=reason)
                elif not reason and self.now() >= t.valid_from and (row['next_attempt'] is None or self.now() >= row['next_attempt']) and await self.local_trigger(t, row):
                    await self.execute(t)
            for target in {r['target'] for r in self.store.rows('SELECT target FROM ownership WHERE owned=1')}:
                try:
                    self.reconcile(await self.connector(target).read(target))
                except (Unavailable, TimeoutError):
                    pass

    # Current eligibility, dispatch, and verification

    async def precheck(self, t, before, cap):
        """Check current eligibility; a plan cannot attest to present household state."""
        for check in t.prechecks:
            if check.kind == 'quiet_hours':
                hour = datetime.fromtimestamp(self.now(), ZoneInfo(check.timezone)).hour
                quiet = (hour >= check.quiet_start or hour < check.quiet_end) if check.quiet_start > check.quiet_end else check.quiet_start <= hour < check.quiet_end
                if cap.noisy and quiet:
                    return 'QUIET_HOURS'
                continue
            if check.kind == 'room':
                if cap.room is None or cap.room != check.value:
                    return 'ROOM_MISMATCH_OR_UNKNOWN'
                continue
            state = before if not check.target or check.target == t.target else await self.connector(check.target).read(check.target)
            if not self.usable(state):
                return 'PRECONDITION_STATE_UNAVAILABLE_OR_STALE'
            prop = check.property or ('in_use' if check.kind == 'not_in_use' else 'done_today')
            if prop not in state.values:
                return 'PRECONDITION_UNKNOWN:'+prop
            if check.kind == 'not_in_use' and state.values[prop] is not False:
                return 'IN_USE_BY_PERSON'
            if check.kind == 'not_already_done' and state.values[prop] is not False:
                return 'ALREADY_DONE_BY_PERSON'
        return None

    def failure(self, t, status, reason=None, attempted=False, receipt=None, safe_retry=False):
        row = self.task(t.key)
        retry = safe_retry and row['attempts'] < t.retry.max_attempts and self.now() < t.valid_until
        if retry:
            delay = min(60, t.retry.backoff_seconds * 2**max(0, row['attempts']-1))
            self.store.write('UPDATE tasks SET next_attempt=? WHERE id=?', (min(t.valid_until, self.now()+delay), t.key))
        return self.result(t, status, attempted=attempted, command=receipt, reason=reason,
                           phase='RETRY_WAIT' if retry else status)

    async def execute(self, t):
        """Reserve one operation, dispatch it, then verify with fresh independent readback."""
        self.store.write('UPDATE tasks SET attempts=attempts+1,next_attempt=NULL WHERE id=?', (t.key,))
        if t.family == 'ABSTAIN':
            return self.result(t, 'ABSTAINED')
        try:
            c = self.connector(t.target)
            caps = {cap.target: cap for cap in await c.discover()}
            cap = caps.get(t.target)
            if not cap or not set(t.desired) <= set(cap.writable) or t.family not in cap.families:
                return self.result(t, 'UNSUPPORTED', reason='CAPABILITY_NOT_ADVERTISED')
            if not cap.available:
                return self.failure(t, 'UNAVAILABLE', safe_retry=True)
            before = await c.read(t.target)
            if not self.usable(before):
                return self.failure(t, 'UNAVAILABLE', safe_retry=True)
            if any(k not in before.values or before.values[k] != v for k, v in t.preconditions.items()):
                return self.failure(t, 'BLOCKED', reason='PRECONDITION_FALSE_OR_UNKNOWN', safe_retry=True)
            reason = await self.precheck(t, before, cap)
            if reason:
                return self.failure(t, 'BLOCKED', reason=reason, safe_retry=reason != 'ALREADY_DONE_BY_PERSON')
            if any(k not in before.values for k in t.desired):
                return self.result(t, 'BLOCKED', reason='BEFORE_STATE_UNKNOWN')
            for r in self.store.rows('SELECT * FROM ownership WHERE target=? AND owned=1', (t.target,)):
                if r['property'] in t.desired:
                    owner=Task.model_validate(self.task(r['task'])['body'])
                    sibling=(owner.plan_id,owner.plan_version,owner.line_id)==(t.plan_id,t.plan_version,t.line_id) and t.line_id is not None
                    if sibling and json.loads(r['desired'])==t.desired[r['property']]:
                        continue
                    return self.failure(t, 'BLOCKED', reason='PROPERTY_ALREADY_OWNED', safe_retry=True)
            reason = self.gate(t)
            if reason:
                return self.result(t, reason)
            gate_evidence={'still_confirmed':True,'online':bool(cap.available and before.available),'in_use_by_person':before.values.get('in_use'),'already_done_by_person':before.values.get('done_today'),'room':cap.room,'checked_at':self.now()}
            self.store.write('UPDATE tasks SET gate_evidence=? WHERE id=?',(json.dumps(gate_evidence),t.key))
            op = uuid.uuid4().hex
            with self.store.db:
                reservation=self.store.db.execute("UPDATE tasks SET status='EXECUTING',before_state=?,operation_id=?,started_at=?,started_event_floor=? WHERE id=? AND status IN ('WAITING','RETRY_WAIT')", (before.model_dump_json(), op, self.now(), self.event_cursor(), t.key))
            if reservation.rowcount!=1:
                return self.task(t.key)['result']
            receipt = await c.execute(t.target, t.desired, op)
            self.store.write('UPDATE tasks SET command_receipt=? WHERE id=?', (receipt.model_dump_json(), t.key))
            if receipt.status != 'ACCEPTED':
                return self.failure(t, receipt.status, attempted=True, receipt=receipt.model_dump(), safe_retry=receipt.status in ('REJECTED','UNAVAILABLE'))
            end = time.monotonic()+t.verify_seconds
            after = None
            while True:
                try:
                    after = await c.read(t.target)
                    if not self.usable(after, max(self.task(t.key)['started_at'], before.observed_at)):
                        after = None
                    if after and all(k in after.values and after.values[k] == v for k, v in t.desired.items()):
                        break
                except (Unavailable, TimeoutError):
                    after = None
                if time.monotonic() >= end:
                    break
                await asyncio.sleep(.1)
            effect = 'UNVERIFIABLE' if after is None or not after.available else 'VERIFIED' if all(k in after.values and after.values[k] == v for k, v in t.desired.items()) else 'MISMATCH'
            lost = set()
            if after and effect == 'VERIFIED':
                row = self.task(t.key)
                reached, revisions = set(), {}
                # Authority can be lost before the verification read creates
                # ownership rows. Retain that observation history across the
                # dispatch/verification boundary, including away-and-back.
                for event in self.facts(row['started_at'], row['started_event_floor']):
                    state = event.observation
                    if not state or state.target != t.target or state.observed_at < before.observed_at:
                        continue
                    for prop, value in t.desired.items():
                        if prop not in state.values:
                            continue
                        if state.values[prop] == value:
                            if prop in reached and prop in revisions and prop in state.revisions and revisions[prop] != state.revisions[prop]:
                                lost.add(prop)
                            reached.add(prop)
                            if prop in state.revisions:
                                revisions[prop] = state.revisions[prop]
                        elif prop in reached:
                            lost.add(prop)
                for prop, revision in revisions.items():
                    if prop in after.revisions and revision != after.revisions[prop]:
                        lost.add(prop)
            with self.store.db:
                self.store.db.execute('UPDATE tasks SET after_state=? WHERE id=?', (after.model_dump_json() if after else None, t.key))
                if t.temporary and after and effect == 'VERIFIED':
                    for k, v in t.desired.items():
                        if before.values[k] != v:
                            self.store.db.execute('INSERT OR REPLACE INTO ownership VALUES(?,?,?,?,?,?,?)', (t.target, k, t.key, json.dumps(before.values[k]), json.dumps(v), after.revisions.get(k), int(k not in lost)))
            if lost and effect == 'VERIFIED':
                return self.result(t, 'OVERRIDDEN', attempted=True, command=receipt.model_dump(), observation=after,
                                   reason='AUTHORITY_CHANGED_DURING_VERIFICATION')
            if t.family == 'RUN_JOB' and effect == 'VERIFIED':
                effect = 'RUNNING' if receipt.job_id == op else 'UNVERIFIABLE'
            return self.result(t, effect, attempted=True, command=receipt.model_dump(), observation=after)
        except (Unavailable, TimeoutError) as e:
            row = self.task(t.key)
            attempted = row['status'] == 'EXECUTING'
            return self.failure(t, 'INDETERMINATE' if attempted else 'UNAVAILABLE', attempted=attempted, reason=str(e), safe_retry=not attempted)
        except Exception as e:
            row = self.task(t.key)
            return self.result(t, 'INDETERMINATE' if row['status']=='EXECUTING' else 'FAILED', attempted=row['status']=='EXECUTING', reason=type(e).__name__)

    # Lifecycle, job stop, and guarded restoration

    async def lifecycle(self):
        """Apply end rules and reconcile only the effects or jobs this task still owns."""
        for row in self.store.rows('SELECT * FROM tasks WHERE started_at IS NOT NULL AND ended_at IS NULL'):
            t = Task.model_validate_json(row['body'])
            retired = self.gate(t) in ('CANCELLED','SUPERSEDED')
            finish, evidence, reason = False, [], None
            if t.end:
                if t.end.after_seconds and self.now() >= row['started_at']+t.end.after_seconds:
                    finish, reason = True, 'MAX_DURATION'
                for end_rule in ([t.end.trigger] if t.end.trigger else [])+t.end.alternatives:
                    # Return/door-cycle predicates need the opening/locking
                    # context that triggered this action at the same instant.
                    # Admission still excludes older facts; the dispatch cursor
                    # must not erase the delegated predicate's prerequisite.
                    floor = row['event_floor'] if end_rule.type in ('returned.v1','door_cycle.v1') else row['started_event_floor']
                    match, evidence = evaluate(end_rule, self.facts(row['started_at'], floor), self.now(), row['started_at'])
                    if match:
                        finish, reason = True, 'END_TRIGGER'
                        if end_rule.type == 'event_end.v1':
                            self.derived(t, 'event_end', evidence)
                if t.end.completion and await self.condition(t.end.completion, t, 'stable_since'):
                    finish, reason = True, 'CONDITION_SATISFIED'
            if t.family == 'RUN_JOB' and row['status'] == 'RUNNING':
                try:
                    state = await self.connector(t.target).read(t.target)
                    if state.values.get('job_id') != row['operation_id'] or state.values.get('run_state') in ('stopped','idle'):
                        self.result(t,'OVERRIDDEN',attempted=True,command=row['command_receipt'] and json.loads(row['command_receipt']),observation=state,reason='JOB_REPLACED_OR_EXTERNALLY_STOPPED')
                        self.store.write('UPDATE tasks SET ended_at=? WHERE id=?',(self.now(),t.key))
                        continue
                    if state.values.get('job_id') == row['operation_id'] and state.values.get('run_state') == 'completed':
                        self.result(t, 'COMPLETED', attempted=True, command=row['command_receipt'] and json.loads(row['command_receipt']), observation=state)
                        self.store.write('UPDATE ownership SET owned=0 WHERE task=?', (t.key,))
                        finish, reason = True, 'JOB_COMPLETED'
                except (Unavailable, TimeoutError):
                    pass
            if retired or finish:
                if row['restore_retry_at'] and self.now()<row['restore_retry_at']:
                    continue
                if t.restore and reason != 'JOB_COMPLETED':
                    await self._restore(t.key)
                    updated=self.task(t.key)
                    if updated['restore_status'] is None and updated['result'] and updated['result'].get('restore_status')=='RESTORE_FAILED':
                        continue
                if t.family == 'RUN_JOB' and row['status'] == 'RUNNING' and reason != 'JOB_COMPLETED':
                    try:
                        await self.stop_job(t)
                    except (Unavailable,TimeoutError):
                        self.store.write('UPDATE tasks SET restore_retry_at=? WHERE id=?',(self.now()+5,t.key))
                        continue
                self.store.write('UPDATE tasks SET ended_at=? WHERE id=?', (self.now(), t.key))
                outcome=self.task(t.key)['result'] or {}
                outcome.update(end_reason=reason or 'CANCELLED_OR_SUPERSEDED',ended_at=self.now(),reported_at=self.now())
                self.store.publish(t.key,outcome)
                if row['status'] in ('VERIFIED','RUNNING') and reason != 'JOB_COMPLETED' and not t.restore:
                    self.result(t, 'ENDED', attempted=True, command=self.task(t.key)['command_receipt'], reason=reason or 'CANCELLED')

    async def stop_job(self, t):
        c = self.connector(t.target)
        state = await c.read(t.target)
        row = self.task(t.key)
        if state.values.get('job_id') != row['operation_id']:
            return self.result(t, 'OVERRIDDEN', attempted=True, observation=state, reason='JOB_OWNERSHIP_LOST')
        # STOP only the job this attempt created, using a fresh native conditional write.
        if any(prop not in state.revisions for prop in ('run_state','job_id')):
            return self.result(t, 'UNVERIFIABLE', attempted=True, reason='JOB_STOP_HAS_NO_CAS')
        receipt = await c.execute(t.target, {'run_state':'stopped'}, uuid.uuid4().hex,
                                  {prop:state.revisions[prop] for prop in ('run_state','job_id')})
        after = await c.read(t.target)
        return self.result(t, 'STOPPED' if receipt.status=='ACCEPTED' and after.values.get('run_state')=='stopped' else 'UNVERIFIABLE', attempted=True, command=receipt.model_dump(), observation=after)

    async def restore(self, key):
        async with self.lock:
            return await self._restore(key)

    async def _restore(self, key):
        """Restore the prior value only with fresh ownership and a native revision guard."""
        row = self.task(key)
        t = Task.model_validate(row['body'])
        if row['restore_status'] or not t.restore:
            return self.task(key)
        owners = self.store.rows('SELECT * FROM ownership WHERE task=?', (key,))
        if not owners:
            return self.task(key)
        outcome = None
        try:
            c = self.connector(t.target)
            cap = next((x for x in await c.discover() if x.target == t.target), None)
            if not cap or not cap.available:
                raise Unavailable('restore capability unavailable')
            current = await c.read(t.target)
            if not self.usable(current) or any(r['property'] not in current.values for r in owners if r['owned']):
                raise Unavailable('restore state is incomplete or stale')
            self.reconcile(current)
            owned = self.store.rows('SELECT * FROM ownership WHERE task=? AND owned=1', (key,))
            if not owned:
                outcome = 'RESTORE_SKIPPED_OVERRIDE'
            elif not cap.conditional_write or any(not r['revision'] for r in owned):
                outcome = 'RESTORE_SKIPPED_UNSAFE_NO_CAS'
            else:
                desired = {r['property']: json.loads(r['before_value']) for r in owned}
                expected = {r['property']: r['revision'] for r in owned}
                self.store.write("UPDATE tasks SET restore_status='RESTORING' WHERE id=?", (key,))
                receipt = await c.execute(t.target, desired, uuid.uuid4().hex, expected)
                if receipt.status == 'CONFLICT':
                    outcome = 'RESTORE_SKIPPED_OVERRIDE'
                elif receipt.status != 'ACCEPTED':
                    outcome = 'RESTORE_FAILED'
                else:
                    state = await c.read(t.target)
                    outcome = 'RESTORED' if self.usable(state, current.observed_at) and all(k in state.values and state.values[k] == v for k, v in desired.items()) else 'RESTORE_FAILED'
            self.store.write('UPDATE tasks SET restore_status=? WHERE id=?', (outcome, key))
            if outcome in ('RESTORED','RESTORE_SKIPPED_OVERRIDE'):
                self.store.write('UPDATE ownership SET owned=0 WHERE task=?', (key,))
        except (Unavailable, TimeoutError):
            # Before dispatch failure retains a retryable restore obligation.
            if self.task(key)['restore_status'] == 'RESTORING':
                outcome = 'INDETERMINATE'
                self.store.write('UPDATE tasks SET restore_status=? WHERE id=?', (outcome, key))
            else:
                outcome = 'RESTORE_FAILED'
                self.store.write('UPDATE tasks SET restore_retry_at=? WHERE id=?',(self.now()+5,key))
        result = self.task(key)['result'] or {}
        result.update(restore_status=outcome, reported_at=self.now())
        self.store.publish(key, result)
        return self.task(key)

    async def end(self, plan_id):
        self.control('stop', plan_id=plan_id)
        for row in self.store.rows('SELECT id,body FROM tasks'):
            if Task.model_validate_json(row['body']).plan_id == plan_id:
                await self.restore(row['id'])
        await self.tick()

    # Timer frontier and retention

    def next_due(self):
        candidates = []
        for row in self.store.rows('SELECT * FROM tasks WHERE ended_at IS NULL'):
            t = Task.model_validate_json(row['body'])
            if row['status'] in ('WAITING','RETRY_WAIT'):
                if t.trigger.condition and row['trigger_since'] is not None and row['trigger_since']+t.trigger.condition.stable_seconds>self.now():
                    candidates.append(row['trigger_since']+t.trigger.condition.stable_seconds)
                candidates.extend(x for x in [t.valid_from, t.valid_until, row['next_attempt'], next_rule_time(t.trigger, self.facts(row['received'], row['event_floor']), self.now(), row['received'])] if x is not None and x > self.now())
            if row['restore_retry_at'] and row['restore_retry_at']>self.now():
                candidates.append(row['restore_retry_at'])
            if row['started_at'] and t.end:
                candidates.extend(x for x in [next_rule_time(rule,self.facts(row['started_at'],row['started_event_floor']),self.now(),row['started_at']) for rule in t.end.alternatives] if x is not None and x>self.now())
                candidates.extend(x for x in [row['started_at']+t.end.after_seconds if t.end.after_seconds else None,
                    next_rule_time(t.end.trigger, self.facts(row['started_at'],row['started_event_floor']), self.now(), row['started_at']) if t.end.trigger else None,
                    row['stable_since']+t.end.completion.stable_seconds if row['stable_since'] is not None and t.end.completion else None] if x is not None and x > self.now())
        return min(candidates) if candidates else None


    def compact(self, retention_seconds=14*86400):
        cutoff=self.now()-retention_seconds
        protected=[]
        for row in self.store.rows('SELECT * FROM tasks WHERE ended_at IS NULL'):
            t=Task.model_validate_json(row['body'])
            if row['status'] in ('WAITING','RETRY_WAIT') and t.trigger.type not in ('immediate','at','state.v1'):
                protected.append(row['received'])
            if row['started_at'] is not None and t.end and (t.end.trigger or t.end.alternatives):
                protected.append(row['started_at'])
        if protected:cutoff=min(cutoff,min(protected))
        with self.store.db:
            events=self.store.db.execute('DELETE FROM events WHERE received<?',(cutoff,)).rowcount
            published_events=self.store.db.execute("DELETE FROM event_outbox WHERE delivered=1 AND json_extract(body,'$.at')<?",(cutoff,)).rowcount
            published_results=self.store.db.execute("DELETE FROM outbox WHERE delivered=1 AND json_extract(body,'$.reported_at')<?",(cutoff,)).rowcount
        return {'event_cutoff':cutoff,'events_removed':events,'published_events_removed':published_events,'published_results_removed':published_results,'unacknowledged_preserved':True,'task_identity_preserved':True}

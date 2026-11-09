"""Evidence-scoped three-valued oracle shared by every substrate adapter."""
from collections import Counter

INVARIANTS = {
    'I1': 'No false physical success', 'I2': 'Unknown is not safe',
    'I3': 'No blind retry after uncertain dispatch', 'I4': 'No new stale work',
    'I5': 'External authority wins', 'I6': 'Restore only still-owned properties',
    'I7': 'Unknown actor stays unknown', 'I8': 'No invented event meaning',
    'I9': 'Partial effects remain partial', 'I10': 'Restart cannot duplicate uncertain work',
    'I11': 'Observation gaps weaken claims', 'I12': 'Lifecycle end cannot overwrite external authority',
}


def evaluate(trace):
    evidence = {key: [] for key in INVARIANTS}
    tasks = {}
    evidence_gaps = []
    for sample in trace.get('timeline', []):
        for task in sample.get('tasks', {}).values():
            body = task.get('body', {})
            tasks[(body.get('plan_id'),body.get('plan_version'),body.get('task_id'))] = task
    def add(key, passed, detail):
        evidence[key].append(dict(status='PASS' if passed else 'VIOLATION', detail=detail))
    for assertion in trace.get('assertions', []):
        add(assertion['invariant'], assertion['actual'] == assertion['expected'], assertion)
    for sample in trace.get('timeline', []):
        for result in sample.get('results', []):
            r = result['result']
            if r.get('effect_status') != 'VERIFIED':
                continue
            task = sample.get('tasks', {}).get(r['task_id'])
            if task and task.get('started_at') is None:
                task = tasks.get((r.get('plan_id'),r.get('plan_version'),r['task_id']), task)
            if not task:
                continue
            observation = r.get('observation')
            desired = task['body']['desired']
            supported = bool(observation and observation.get('available') and
                             observation['target'] == task['body']['target'] and
                             all(k in observation['values'] and observation['values'][k] == v for k,v in desired.items()))
            if task.get('started_at') is None:
                evidence_gaps.append(dict(invariant='I1',result_id=result['result_id'],reason='Non-atomic HTTP snapshot lacks dispatch timestamp'))
                continue
            fresh = bool(observation and observation['observed_at'] >= task['started_at'])
            add('I1', supported and fresh, dict(task=task['id'], result_id=result['result_id'],
                                              independent_observation=supported, fresh=fresh))
        for item in sample.get('events', []):
            event = item['event']
            if (event.get('observation') or {}).get('provenance') == 'UNKNOWN':
                add('I7', event['provenance'] != 'USER', dict(event_id=event['event_id']))
    verdicts = {}
    for key, name in INVARIANTS.items():
        items = evidence[key]
        status = 'VIOLATION' if any(x['status'] == 'VIOLATION' for x in items) else 'PASS' if items else 'INCONCLUSIVE'
        verdicts[key] = dict(name=name, status=status, checks=len(items),
                             violations=[x for x in items if x['status'] == 'VIOLATION'])
    counts = Counter(x['status'] for x in verdicts.values())
    return dict(status='VIOLATION' if counts['VIOLATION'] else 'PASS' if counts['PASS'] else 'INCONCLUSIVE',
                invariants=verdicts, counts=dict(counts), evidence_gaps=evidence_gaps,
                scope='Only evidenced assertions pass; unexercised invariants remain INCONCLUSIVE')


def minimize(steps, reproduces, budget=80):
    """Bounded ddmin; invalid/unreproduced candidates must return False."""
    current = list(steps)
    granularity, attempts = 2, 0
    while len(current) >= 2 and attempts < budget:
        width = max(1, (len(current)+granularity-1)//granularity)
        reduced = False
        for start in range(0, len(current), width):
            candidate = current[:start]+current[start+width:]
            attempts += 1
            if reproduces(candidate):
                current, granularity, reduced = candidate, max(2, granularity-1), True
                break
            if attempts >= budget:
                break
        if not reduced:
            if granularity >= len(current):
                break
            granularity = min(len(current), granularity*2)
    return dict(steps=current, attempts=attempts, algorithm='bounded ddmin', one_minimal=attempts < budget)

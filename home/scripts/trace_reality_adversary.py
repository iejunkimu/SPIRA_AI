"""Run/replay/search external household occurrence sequences against real Home."""
import argparse
import json
import hashlib
import time
from pathlib import Path

from reality_adversary.checker import INVARIANTS, minimize
from reality_adversary.process import LocalStack, s10_process
from reality_adversary.runner import Runner
from reality_adversary.scenarios import generated, named


def write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False)+'\n')


def run(args):
    root = Path(args.output)
    root.mkdir(parents=True, exist_ok=True)
    stack = LocalStack(args.mqtt_port) if args.local else None
    runner = Runner(stack.home if stack else args.home, stack.world if stack else args.world,
                    process=stack if stack else s10_process if args.s10 else None)
    started = time.monotonic()
    source_at_start = {str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(Path('src').rglob('*.py'))}
    records, coverage, combinations = [], {key:0 for key in INVARIANTS}, set()
    try:
        if stack:
            stack('start', 'world')
            runner.poll(lambda:runner.get(runner.w, '/world'))
            stack('start')
        runner.poll(lambda:runner.get(runner.h, '/health'))
        replay = json.loads(Path(args.replay).read_text()) if args.replay else None
        scenarios = [replay.get('scenario', replay)] if replay else (
            [value for key,value in named().items() if not args.only or key in args.only]+[
                generated(seed) for seed in range(args.seed,args.seed+args.runs)])
        for scenario in scenarios:
            trace = runner.run(scenario)
            verdict = trace['evaluation']['status']
            record = dict(name=scenario['name'],seed=scenario.get('seed'),status=verdict,
                          scenario_sha256=hashlib.sha256(json.dumps(scenario,sort_keys=True).encode()).hexdigest(),
                          runtime_seconds=trace['runtime_seconds'],invariants={k:v['status'] for k,v in trace['evaluation']['invariants'].items()})
            if trace.get('error'):
                record['error'] = trace['error']
            records.append(record)
            for key,value in trace['evaluation']['invariants'].items():
                if value['status'] == 'PASS':
                    coverage[key] += value['checks']
            for row in trace['timeline']:
                for task in row.get('tasks', {}).values():
                    combinations.add((scenario.get('family', scenario['name']), row.get('action',{}).get('op'),
                                      task['status'], task['restore_status'], task['attempts']))
            if args.keep_all or scenario.get('seed') is None or verdict != 'PASS':
                write(root/'traces'/(scenario['name']+'.json'), trace)
            print(scenario['name']+': '+verdict, flush=True)
            if verdict == 'VIOLATION':
                original = root/'counterexamples'/(scenario['name']+'-original.json')
                write(original, trace)
                violated = {k for k,v in trace['evaluation']['invariants'].items() if v['status']=='VIOLATION'}
                if args.shrink:
                    automatic = {k for k in violated if any('independent_observation' in x['detail'] for x in trace['evaluation']['invariants'][k]['violations'])}
                    def reproduces(steps):
                        # Shrinking a known observation defect must retain its
                        # causal oracle failure, not just break an expectation
                        # by removing the scenario's prerequisite occurrences.
                        if not stack:
                            return False
                        clean = LocalStack(args.mqtt_port)
                        probe = Runner(clean.home,clean.world,clean)
                        try:
                            clean('start','world');probe.poll(lambda:probe.get(probe.w,'/world'))
                            clean('start');probe.poll(lambda:probe.get(probe.h,'/health'))
                            candidate = probe.run({**scenario,'steps':steps})
                            if candidate.get('error'):
                                return False
                            if automatic:
                                return any(any('independent_observation' in x['detail'] for x in candidate['evaluation']['invariants'][k]['violations']) for k in automatic)
                            # Keep assertions plus their dependency boundaries
                            # for explicit semantic failures; use the named
                            # deterministic trace if no safe shrink oracle exists.
                            return False
                        except (KeyError, ValueError, AssertionError):
                            return False
                        finally:
                            probe.close();clean.close()
                    minimal = minimize(scenario['steps'], reproduces, args.shrink_budget)
                    write(root/'counterexamples'/(scenario['name']+'-minimal.json'), {**scenario,'steps':minimal.pop('steps'),'minimization':minimal})
                if args.fail_fast:
                    break
    finally:
        runner.close()
        if stack:
            stack.close()
    summary = dict(scope='Real Home HTTP + independent MQTT world/broker processes',
                   execution_node='S10 Docker' if args.s10 else 'development host',
                   seeds_executed=[r['seed'] for r in records if r['seed'] is not None],
                   runs=len(records),violations=sum(r['status']=='VIOLATION' for r in records),
                   inconclusive=sum(r['status']=='INCONCLUSIVE' for r in records),
                   invariant_evidence_checks=coverage, unique_transition_state_combinations=len(combinations),
                   transition_state_combinations=sorted(combinations, key=str),
                   runtime_seconds=round(time.monotonic()-started,3), records=records)
    summary['source_sha256'] = source_at_start
    summary['source_changed_during_run'] = any(hashlib.sha256(Path(path).read_bytes()).hexdigest()!=digest for path,digest in source_at_start.items())
    write(root/'run-summary.json',summary)
    return 1 if summary['violations'] or summary['inconclusive'] else 0


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--local',action='store_true',help='Own disposable Home/world subprocesses; existing MQTT broker required')
    p.add_argument('--s10',action='store_true',help='Run on S10 with bounded process control of the team reality profile')
    p.add_argument('--home',default='http://127.0.0.1:8755')
    p.add_argument('--world',default='http://127.0.0.1:8756')
    p.add_argument('--mqtt-port',type=int,default=18883)
    p.add_argument('--runs',type=int,default=0)
    p.add_argument('--seed',type=int,default=0)
    p.add_argument('--only',nargs='+')
    p.add_argument('--replay')
    p.add_argument('--output',default='docs/evidence/reality-adversary/mqtt-mac')
    p.add_argument('--keep-all',action='store_true')
    p.add_argument('--shrink',action='store_true')
    p.add_argument('--shrink-budget',type=int,default=40)
    p.add_argument('--fail-fast',action='store_true')
    raise SystemExit(run(p.parse_args()))

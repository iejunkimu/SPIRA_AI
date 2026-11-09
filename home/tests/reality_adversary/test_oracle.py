import copy

import pytest

from reality_adversary.checker import INVARIANTS, evaluate, minimize
from reality_adversary.dynamics import ScalarDynamics
from reality_adversary.scenarios import generated


def test_absence_of_evidence_is_inconclusive():
    verdict = evaluate({})
    assert verdict['status'] == 'INCONCLUSIVE'
    assert all(x['status'] == 'INCONCLUSIVE' for x in verdict['invariants'].values())


@pytest.mark.parametrize('invariant', INVARIANTS)
def test_oracle_detects_each_explicit_violation(invariant):
    trace = {'assertions':[dict(invariant=invariant, expected=0, actual=1)]}
    assert evaluate(trace)['invariants'][invariant]['status'] == 'VIOLATION'


@pytest.mark.parametrize('observation', [None, {'target':'x','values':{},'available':True,'observed_at':10},
    {'target':'x','values':{'value':None},'available':True,'observed_at':9},
    {'target':'other','values':{'value':None},'available':True,'observed_at':10}])
def test_oracle_rejects_missing_stale_and_wrong_target_verification(observation):
    task = {'id':'a','started_at':10,'body':{'target':'x','desired':{'value':None}}}
    trace = {'timeline':[{'tasks':{'a':task},'results':[{'result_id':1,'result':{
        'task_id':'a','effect_status':'VERIFIED','observation':observation}}]}]}
    assert evaluate(trace)['invariants']['I1']['status'] == 'VIOLATION'


def test_delta_debugging_retains_small_reproducible_cause():
    steps = ['noise','command','noise','disconnect','noise','restart','noise']
    cause = {'command','disconnect','restart'}
    result = minimize(steps, lambda candidate:cause <= set(candidate))
    assert result['steps'] == ['command','disconnect','restart']
    assert result['one_minimal']


def test_seed_and_synthetic_dynamics_reproduce():
    assert generated(42) == generated(42)
    assert generated(42) != generated(43)
    a = ScalarDynamics(seed=4,latency=2)
    b = copy.deepcopy(a)
    assert [a.step(True,x) for x in [0,0,20,0]] == [b.step(True,x) for x in [0,0,20,0]]


def test_non_atomic_http_snapshot_is_inconclusive_until_a_dispatch_witness_exists():
    task={'id':'a','started_at':None,'body':{'target':'x','desired':{'value':True},'task_id':'a','plan_id':'p','plan_version':1}}
    result={'result_id':1,'result':{'task_id':'a','plan_id':'p','plan_version':1,'effect_status':'VERIFIED',
        'observation':{'target':'x','values':{'value':True},'available':True,'observed_at':10}}}
    trace={'timeline':[{'tasks':{'a':task},'results':[result]}]}
    assert evaluate(trace)['invariants']['I1']['status']=='INCONCLUSIVE'
    trace['timeline'].append({'tasks':{'a':{**task,'started_at':9}},'results':[]})
    assert evaluate(trace)['invariants']['I1']['status']=='PASS'

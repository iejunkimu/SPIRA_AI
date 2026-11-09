"""Real subprocess/MQTT regressions. No Home internals or process-state writes."""
import socket

import pytest

from reality_adversary.process import LocalStack
from reality_adversary.runner import Runner
from reality_adversary.scenarios import named


@pytest.fixture(scope='module')
def runner():
    try:
        with socket.create_connection(('127.0.0.1',18883),timeout=.5):
            pass
    except OSError:
        pytest.skip('Real MQTT broker required at 127.0.0.1:18883; start base Compose')
    stack = LocalStack()
    r = Runner(stack.home,stack.world,stack)
    try:
        stack('start','world')
        r.poll(lambda:r.get(r.w,'/world'))
        stack('start')
        r.poll(lambda:r.get(r.h,'/health'))
        yield r
    finally:
        r.close()
        stack.close()


@pytest.mark.parametrize('name',list(named()))
def test_named_occurrences_against_real_runtime(runner,name):
    trace = runner.run(named()[name])
    assert 'error' not in trace, trace.get('error')
    assert trace['evaluation']['status'] == 'PASS', trace['evaluation']

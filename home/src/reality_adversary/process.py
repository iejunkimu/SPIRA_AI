"""Explicit process owners; no shell expressions accepted from scenarios."""
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path


class LocalStack:
    def __init__(self, mqtt_port=18883):
        self.temp = tempfile.TemporaryDirectory(prefix='spira-reality-')
        self.root = Path(self.temp.name)
        self.children, self.logs = {}, []
        self.prefix = self.root.name
        self.ports = {}
        for name in ('home', 'world'):
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                self.ports[name] = sock.getsockname()[1]
        self.env = dict(os.environ, MQTT_HOST='127.0.0.1', MQTT_PORT=str(mqtt_port), MQTT_PREFIX=self.prefix,
                        HOME_DB=str(self.root/'home.db'), WORLD_DB=str(self.root/'world.db'),
                        WORLD_CLOCK='1', WORLD_START='1793880000', WORLD_PRESSURE='1', HOME_TEST_MODE='1')
        for key in ('SERVICE_URL', 'HA_URL', 'MATTER_URL', 'SERVICE_RESULTS_URL', 'HOME_TOKEN'):
            self.env.pop(key, None)

    @property
    def home(self):
        return 'http://127.0.0.1:'+str(self.ports['home'])

    @property
    def world(self):
        return 'http://127.0.0.1:'+str(self.ports['world'])

    def __call__(self, action, target='home'):
        if target not in ('home', 'world'):
            raise ValueError('Local adapter owns only its disposable Home/world processes')
        if action in ('kill', 'restart', 'stop') and target in self.children:
            child = self.children[target]
            if action in ('kill', 'restart'):
                child.kill()
            else:
                child.terminate()
            child.wait(timeout=10)
        if action in ('start', 'restart'):
            if target in self.children and self.children[target].poll() is None:
                raise ValueError('Process already running')
            module = 'spira_home.app' if target == 'home' else 'spira_home.testbed.world'
            log = (self.root/(target+'.log')).open('a')
            self.logs.append(log)
            self.children[target] = subprocess.Popen(
                [sys.executable, '-m', 'uvicorn', module+':app', '--host', '127.0.0.1', '--port', str(self.ports[target])],
                env=self.env, stdout=log, stderr=log)
        return dict(action=action, target=target, pid=self.children[target].pid)

    def close(self):
        for child in self.children.values():
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
        for log in self.logs:
            log.close()
        self.temp.cleanup()


def s10_process(action, target='home'):
    services = {'home':'reality-home', 'world':'reality-world', 'broker':'mqtt'}
    actions = {'kill':['stop', '-t', '0'], 'stop':['stop'], 'start':['start'], 'restart':['restart', '-t', '0']}
    if action not in actions or target not in services:
        raise ValueError('Unsupported bounded S10 process occurrence')
    command = ['docker', '-H', 'unix:///opt/spira-home/docker.sock', 'compose', '-f', '/opt/spira-home/compose.yaml',
               '--profile', 'reality', *actions[action], services[target]]
    result = subprocess.run(['ssh', 's10plus', *command], capture_output=True, text=True, timeout=55)
    result.check_returncode()
    return dict(action=action, target=target, exit_code=result.returncode, scope='S10 project containers; preserved SQLite')

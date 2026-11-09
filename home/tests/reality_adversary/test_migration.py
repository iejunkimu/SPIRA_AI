import sqlite3
import subprocess
import sys

from spira_home.clock import VirtualClock
from spira_home.models import Event, Task
from spira_home.runtime import Runtime


async def test_legacy_task_does_not_reuse_preadmission_fact_at_same_time(tmp_path):
    path=str(tmp_path/'legacy.db')
    r=Runtime(path,{},VirtualClock(1000))
    r.observe(Event(event_id='old',type='door',source='door',substrate='TEST',at=1000))
    t=Task(episode_id='ep',plan_id='p',plan_version=1,task_id='a',target='test:light',desired={'power':True},
           trigger={'type':'raw_event.v1','source':'door','event_type':'door'},valid_until=2000)
    r.submit(t);r.store.close()
    # Reconstruct a pre-cursor schema without altering any identity or event.
    with sqlite3.connect(path) as db:
        db.execute('ALTER TABLE tasks DROP COLUMN event_floor')
        db.execute('ALTER TABLE tasks DROP COLUMN started_event_floor')
    r=Runtime(path,{},VirtualClock(1000))
    assert not r.triggered(t,1000)
    r.observe(Event(event_id='new',type='door',source='door',substrate='TEST',at=1000))
    assert r.triggered(t,1000)
    r.store.close()


def test_process_death_cannot_commit_a_cursor_column_without_its_backfill(tmp_path):
    path=str(tmp_path/'migration-crash.db')
    r=Runtime(path,{},VirtualClock(1000))
    r.observe(Event(event_id='old',type='door',source='door',substrate='TEST',at=1000))
    t=Task(episode_id='ep',plan_id='p',plan_version=1,task_id='a',target='test:light',desired={'power':True},
           trigger={'type':'raw_event.v1','source':'door','event_type':'door'},valid_until=2000)
    r.submit(t);r.store.close()
    with sqlite3.connect(path) as db:
        db.execute('ALTER TABLE tasks DROP COLUMN event_floor')
        db.execute('ALTER TABLE tasks DROP COLUMN started_event_floor')
    # Native SQLite authorizer fires while compiling the backfill, after ALTER
    # executed. Kill the actual child here; no Home result/state is fabricated.
    program='''
import os,sqlite3,sys
from spira_home.store import Store
connect=sqlite3.connect
def selected(*args,**kwargs):
    db=connect(*args,**kwargs)
    def authorizer(action,table,column,*rest):
        if action==sqlite3.SQLITE_UPDATE and table=='tasks' and column=='event_floor':
            os._exit(73)
        return sqlite3.SQLITE_OK
    db.set_authorizer(authorizer)
    return db
sqlite3.connect=selected
Store(sys.argv[1])
'''
    child=subprocess.run([sys.executable,'-c',program,path],capture_output=True,text=True,timeout=10)
    assert child.returncode==73,child.stderr
    r=Runtime(path,{},VirtualClock(1000))
    assert not r.triggered(t,1000)
    r.store.close()

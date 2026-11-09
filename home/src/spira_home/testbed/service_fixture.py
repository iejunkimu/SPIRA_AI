"""Executable peer for the real S7 hub API. No intent, KB, App or memory implementation."""
import json
import os
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI,Request,HTTPException
from pydantic import BaseModel,ConfigDict
from typing import Any
from spira_home.bridge import Plan


@asynccontextmanager
async def lifespan(app):
    path=os.getenv('SERVICE_FIXTURE_DB','state/service-fixture.db')
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    app.state.db=sqlite3.connect(path)
    app.state.db.executescript('''
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS plans(id TEXT PRIMARY KEY,version INTEGER,body TEXT);
    CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,body TEXT);
    CREATE TABLE IF NOT EXISTS executions(id TEXT PRIMARY KEY,body TEXT);
    CREATE TABLE IF NOT EXISTS devices(household TEXT PRIMARY KEY,body TEXT);
    CREATE TABLE IF NOT EXISTS controls(id TEXT PRIMARY KEY,value TEXT);
    ''')
    yield
    app.state.db.close()


app=FastAPI(title='Settle 3.2 Service contract fixture (no inference)',lifespan=lifespan)


@app.middleware('http')
async def admit(request: Request, call_next):
    if request.url.path.startswith('/v1/hub'):
        key=os.getenv('HUB_KEY','')
        if key and request.headers.get('X-Hub-Key')!=key:
            from starlette.responses import JSONResponse
            return JSONResponse({'detail':'Unauthorized'},status_code=401)
        offline=app.state.db.execute("SELECT value FROM controls WHERE id='offline'").fetchone()
        if offline and offline[0]=='true':
            from starlette.responses import JSONResponse
            return JSONResponse({'detail':'injected Service outage'},status_code=503)
    return await call_next(request)


@app.post('/fixture/plans')
async def queue(plan: Plan):
    old=app.state.db.execute('SELECT version,body FROM plans WHERE id=?',(plan.plan_id,)).fetchone()
    body=plan.model_dump_json()
    if old and (plan.plan_version<old[0] or (plan.plan_version==old[0] and old[1]!=body)):
        raise HTTPException(409,'version/content conflict')
    app.state.db.execute('INSERT OR REPLACE INTO plans VALUES(?,?,?)',(plan.plan_id,plan.plan_version,body))
    app.state.db.commit()
    return {'queued':True}


@app.get('/v1/hub/plans')
async def pull(household: str,since_version: int=0):
    return {'plans':[json.loads(r[0]) for r in app.state.db.execute('SELECT body FROM plans') if json.loads(r[0])['household_id']==household]}


@app.put('/v1/hub/households/{household}/devices')
async def devices(household: str,body: dict):
    if body.get('household_id')!=household or not isinstance(body.get('devices'),list):
        raise HTTPException(422,'invalid C-2 device inventory')
    app.state.db.execute('INSERT OR REPLACE INTO devices VALUES(?,?)',(household,json.dumps(body)))
    app.state.db.commit()
    return {'accepted':True}


@app.post('/v1/hub/events')
async def events(body: list[dict]):
    accepted=[]
    for event in body:
        if not all(k in event for k in ['event_id','household_id','type','source','at','payload']):
            raise HTTPException(422,'invalid C-3 event')
        if event['payload'].get('by') not in ('user','settle','unknown'):
            raise HTTPException(422,'actor provenance is required (unknown extension supported)')
        cursor=app.state.db.execute('INSERT OR IGNORE INTO events VALUES(?,?)',(event['event_id'],json.dumps(event)))
        if cursor.rowcount:accepted.append(event['event_id'])
    app.state.db.commit()
    return {'accepted':accepted}


@app.post('/v1/hub/executions')
async def executions(body: dict):
    if not all(k in body for k in ['result_id','task_id','plan_version','device_id','result','attempted']):
        raise HTTPException(422,'invalid C-4 execution')
    if not isinstance(body['result'].get('verified'),bool) or not isinstance(body['result'].get('ok'),bool):
        raise HTTPException(422,'separate command/effect truth required')
    app.state.db.execute('INSERT OR IGNORE INTO executions VALUES(?,?)',(body['result_id'],json.dumps(body)))
    app.state.db.commit()
    return {'accepted':True}


@app.post('/fixture/availability')
async def availability(body: dict):
    app.state.db.execute('INSERT OR REPLACE INTO controls VALUES(?,?)',('offline','true' if body.get('offline') else 'false'))
    app.state.db.commit()
    return {'accepted':True}


@app.get('/fixture/records')
async def records():
    return {name:[json.loads(r[0]) for r in app.state.db.execute('SELECT body FROM '+name)] for name in ['plans','events','executions','devices']}

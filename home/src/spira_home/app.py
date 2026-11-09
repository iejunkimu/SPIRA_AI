"""Home HTTP surface: integration, operator diagnostics, and test controls."""

import asyncio
import json
import os
import secrets
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from spira_home.bootstrap import consume as consume, lifespan as lifespan, publish as publish, worker as worker
from spira_home.bridge import Plan, translate
from spira_home.models import Task
from spira_home.runtime import Conflict


app = FastAPI(title='SPIRA Home Runtime', version='0.1.0', lifespan=lifespan)


@app.middleware('http')
async def auth(request: Request, call_next):
    token = os.getenv('HOME_TOKEN')
    if token and request.url.path != '/health' and not secrets.compare_digest(request.headers.get('Authorization', ''), 'Bearer '+token):
        from starlette.responses import JSONResponse
        return JSONResponse(status_code=401, content={'detail': 'Unauthorized'})
    return await call_next(request)


# Home integration API: health, inventory, plan/task admission, controls and results.


@app.get('/health')
async def health():
    return {'status': 'running', 'role': 'household-execution-boundary', 'timestamp':app.state.runtime.now(), 'virtual_time':app.state.runtime.clock.virtual}


@app.get('/capabilities')
async def capabilities():
    result = []
    for c in app.state.runtime.connectors.values():
        try:
            result.extend([x.model_dump() for x in await c.discover()])
        except Exception as e:
            result.append({'status': 'UNAVAILABLE', 'reason': type(e).__name__})
    return result


@app.post('/v1/tasks')
async def submit(task: Task):
    try:
        return app.state.runtime.submit(task)
    except Conflict as e:
        raise HTTPException(409, str(e))


@app.get('/v1/tasks')
async def tasks():
    r = app.state.runtime
    return [r.task(row['id']) for row in r.store.rows('SELECT id FROM tasks ORDER BY received,rowid')]


class PlanVersion(BaseModel):
    model_config = ConfigDict(extra='forbid')
    plan_id: str
    version: int = Field(ge=1)


@app.post('/v1/plans/current')
async def advance(body: PlanVersion):
    app.state.runtime.advance(body.plan_id, body.version)
    return {'accepted': True}


class Control(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Literal['stop', 'disable', 'pause', 'resume', 'end', 'stop_all']
    plan_id: str | None = None
    task_key: str | None = None


@app.post('/v1/control')
async def control(body: Control):
    r = app.state.runtime
    if body.action == 'stop_all':
        r.control('pause')
        for p in r.store.rows('SELECT id FROM plans'):
            await r.end(p['id'])
    elif body.action in ('stop', 'end'):
        if not body.plan_id:
            raise HTTPException(422, 'plan_id required')
        await r.end(body.plan_id)
    elif body.action == 'disable':
        if not body.task_key:
            raise HTTPException(422, 'task_key required')
        r.control('disable', task_key=body.task_key)
        await r.restore(body.task_key)
    else:
        r.control(body.action)
    return {'accepted': True}


@app.get('/v1/results')
async def results(after: int = 0):
    return [dict(result_id=r['seq'], result=json.loads(r['body'])) for r in app.state.runtime.store.rows('SELECT * FROM outbox WHERE seq>? ORDER BY seq LIMIT 100', (after,))]


@app.post('/plans')
async def plans(plan: Plan):
    household=os.getenv('HOME_HOUSEHOLD_ID')
    if household and plan.household_id!=household:
        raise HTTPException(403,'household mismatch')
    try:
        tasks,cancelled=translate(plan)
        return app.state.runtime.admit_plan(plan.plan_id,plan.plan_version,plan.model_dump(),tasks,cancelled)
    except Conflict as e:
        raise HTTPException(409,str(e))
    except ValueError as e:
        raise HTTPException(422,str(e))


@app.post('/v1/plans/{plan_id}/lines/{line_id}/disable')
async def disable_line(plan_id: str,line_id: str):
    r=app.state.runtime
    r.disable_line(plan_id,line_id)
    await r.tick()
    return {'accepted':True}


@app.get('/v1/events')
async def events(after: int=0,tail: bool=False):
    rows=app.state.runtime.store.rows('SELECT * FROM event_outbox WHERE seq>? ORDER BY seq '+('DESC' if tail else 'ASC')+' LIMIT 200',(after,))
    if tail:rows.reverse()
    return [dict(sequence=row['seq'],event=json.loads(row['body'])) for row in rows]


@app.get('/v1/plans/{plan_id}/summary')
async def plan_summary(plan_id: str):
    r=app.state.runtime
    rows=[r.task(row['id']) for row in r.store.rows('SELECT id,body FROM tasks') if Task.model_validate_json(row['body']).plan_id==plan_id]
    if not rows:
        raise HTTPException(404,'plan not found')
    lines={}
    for row in rows:
        line=row['body']['line_id'] or row['body']['task_id']
        lines.setdefault(line,[]).append(dict(task_id=row['body']['task_id'],target=row['body']['target'],status=row['status'],restore=row['restore_status']))
    aggregate={}
    for line,items in lines.items():
        states={x['status'] for x in items}
        if states & {'WAITING','RETRY_WAIT','EXECUTING'}:aggregate[line]='PENDING'
        elif len(states)==1:aggregate[line]=next(iter(states))
        elif states <= {'VERIFIED','COMPLETED'}:aggregate[line]='VERIFIED'
        else:aggregate[line]='PARTIAL'
    return {'plan_id':plan_id,'lines':lines,'line_status':aggregate}


# Operator diagnostics: inspect the clock and connector observations.


@app.get('/v1/timeline')
async def timeline():
    r=app.state.runtime
    return {'timestamp':r.now(),'virtual':r.clock.virtual,'next_due':r.next_due()}


@app.get('/v1/states')
async def states():
    observations=[];unavailable=[]
    r=app.state.runtime
    for c in r.connectors.values():
        try:
            for cap in await c.discover():
                try:
                    observations.append({**(await c.read(cap.target)).model_dump(),'assumption':cap.assumption})
                except Exception as e:
                    unavailable.append({'target':cap.target,'reason':type(e).__name__})
        except Exception as e:
            unavailable.append({'target':'connector','reason':type(e).__name__})
    return {'observations':observations,'unavailable':unavailable}


@app.get('/console',include_in_schema=False)
async def console():
    from fastapi.responses import HTMLResponse
    from importlib.resources import files
    return HTMLResponse(files('spira_home').joinpath('console.html').read_text())


# Test-only operations: runtime checks enforce an isolated virtual clock.


@app.post('/v1/test/tick')
async def test_tick():
    if not app.state.runtime.clock.virtual:
        raise HTTPException(404,'not a virtual testbed')
    await app.state.runtime.tick()
    return await timeline()


async def virtual_world(path,body):
    url=os.getenv('WORLD_URL')
    if not app.state.runtime.clock.virtual or not url:
        raise HTTPException(404,'isolated virtual world is not configured')
    async with httpx.AsyncClient(timeout=10) as client:
        response=await client.post(url.rstrip('/')+path,json=body)
        if response.status_code>=400:
            raise HTTPException(response.status_code,response.text)
        return response.json()


@app.post('/v1/test/world')
async def test_world(body: dict):
    return await virtual_world('/mutate',body)


class ClockTarget(BaseModel):
    timestamp: float


@app.post('/v1/test/clock')
async def test_clock(body: ClockTarget):
    r=app.state.runtime
    if not r.clock.virtual:
        raise HTTPException(404,'not a virtual Home')
    if body.timestamp<r.now():
        raise HTTPException(409,'clock cannot move backwards')
    async with httpx.AsyncClient(timeout=10) as client:
        url=os.getenv('WORLD_URL')
        if not url:raise HTTPException(404,'virtual world not configured')
        while r.now()<body.timestamp:
            world=(await client.get(url+'/world')).json()
            current=r.now()
            step=min(x for x in [r.next_due(),world.get('next_due'),body.timestamp] if x is not None and x>current)
            await virtual_world('/sim/clock',{'timestamp':step})
            for _ in range(100):
                if r.now()>=step:break
                await asyncio.sleep(.02)
            else:raise HTTPException(503,'clock observation unavailable')
            await r.tick()
    return await timeline()

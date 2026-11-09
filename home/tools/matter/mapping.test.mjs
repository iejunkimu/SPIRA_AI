import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fields, validValue, valuesFrom } from './mapping.mjs';
const endpoint={state:{laundryWasherMode:{supportedModes:[{mode:0},{mode:1}]}}};
test('strict Matter value types, supported modes and native units',()=>{
  const field=p=>fields.find(x=>x.property===p);
  assert.equal(validValue(field('power'),'false',endpoint),false);
  assert.equal(validValue(field('brightness'),0,endpoint),false);
  assert.equal(validValue(field('brightness'),254,endpoint),true);
  assert.equal(validValue(field('brightness'),true,endpoint),false);
  assert.equal(validValue(field('mode'),2,endpoint),false);
  assert.equal(validValue(field('mode'),1,endpoint),true);
  assert.deepEqual(valuesFrom({relativeHumidityMeasurement:{measuredValue:5034}},[field('humidity')]),{humidity:50.34});
  assert.deepEqual(valuesFrom({relativeHumidityMeasurement:{measuredValue:null}},[field('humidity')]),{humidity:null});
});
test('no start, color or job identity is manufactured',()=>{
  assert.equal(fields.some(x=>['start','color','job_id'].includes(x.property)),false);
  assert.equal(fields.find(x=>x.property==='run_mode').family,'RUN_JOB');
});

test('partial multi-property protocol rejection cannot advertise safe retry',async()=>{
  const { executeCommands }=await import('./mapping.mjs');
  const first={property:'first',command:async()=>({})};
  const second={property:'second',command:async()=>({status:1})};
  assert.equal((await executeCommands({},[first,second],{first:true,second:true})).status,'UNKNOWN');
  assert.equal((await executeCommands({},[second],{second:true})).status,'REJECTED');
});

test('bounded protocol journal survives controller recreation with native identity',async()=>{
  const { EventStore }=await import('./event-store.mjs');
  const { mkdtempSync, rmSync }=await import('node:fs');
  const { tmpdir }=await import('node:os');const { join }=await import('node:path');
  const dir=mkdtempSync(join(tmpdir(),'spira-event-test-'));
  try {
    const file=join(dir,'events.jsonl');let store=new EventStore(file,2);
    for(let i=0;i<270;i++)store.append({event_id:'native-'+i,at:i});
    assert.equal(store.records.length,2);
    store=new EventStore(file,2);
    assert.equal(store.sequence,270);assert.equal(store.records.at(-1).event.event_id,'native-269');
    assert.equal(store.append({event_id:'next-native',at:271}).sequence,271);
  } finally {rmSync(dir,{recursive:true,force:true});}
});

// Upstream owns commissioning, sessions, subscriptions, reads and protocol writes.
import '@matter/nodejs';
import { ServerNode, Environment, ChangeNotificationService } from '@matter/main';
import { ManualPairingCodeCodec } from '@matter/types';
import { createServer } from 'node:http';
import { randomUUID } from 'node:crypto';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { EventStore } from './event-store.mjs';
import { Read, Write } from '@matter/protocol';
import { FanControl } from '@matter/main/clusters/fan-control';
import { supportedFields, validValue, valuesFrom, executeCommands } from './mapping.mjs';
Environment.default.vars.set('mdns.networkInterface',process.env.SPIRA_MATTER_INTERFACE || 'en0');
const controller = await ServerNode.create(ServerNode.RootEndpointWithoutGroupcast,{id:'spira-controller-'+(process.env.SPIRA_MATTER_NODE_SUFFIX || 'en0'),network:{port:5551}});
const inventory=new Map(), epoch=randomUUID();
const journal=new EventStore(process.env.SPIRA_MATTER_EVENT_JOURNAL || join(homedir(),'.matter','spira-home-events-'+(process.env.SPIRA_MATTER_NODE_SUFFIX || 'en0')+'.jsonl'));
function emit(type,entry,payload={},observation,eventId) {
  const event={event_id:eventId || `matter-${epoch}-${journal.sequence+1}`,type,source:entry.target,at:Date.now()/1000,substrate:'VIRTUAL_MATTER',provenance:'UNKNOWN',payload};
  if(observation) event.observation=observation;
  journal.append(event);
}
function observation(entry,state) {return {target:entry.target,values:valuesFrom(state,entry.fields),observed_at:Date.now()/1000,substrate:'VIRTUAL_MATTER',provenance:'UNKNOWN',available:entry.peer.lifecycle.connectionState===0};}
const fanFields = e => e.fields.filter(f=>f.cluster==='fanControl'&&f.command);
async function fanRead(entry) {
  const selected=fanFields(entry), values={}, revisions={};
  const request={...Read(Read.Attribute({endpoint:entry.endpoint.number,cluster:FanControl,attributes:selected.map(f=>f.attribute)})),includeKnownVersions:true};
  for await(const chunk of entry.peer.interaction.read(request)) for await(const report of chunk) {
    if(report.kind==='attr-status'&&report.status!==0) throw Error('Fan network read failed');
    if(report.kind!=='attr-value') continue;
    const f=selected.find(f=>FanControl.attributes[f.attribute].id===report.path.attributeId);
    if(f) {values[f.property]=report.value;revisions[f.property]='514:'+report.version;}
  }
  if(Object.keys(values).length!==selected.length) throw Error('Incomplete fan network read');
  return {...observation(entry,{}),values,revisions};
}
async function fanConditionalWrite(entry,desired,expected) {
  const keys=Object.keys(desired),selected=keys.map(k=>fanFields(entry).find(f=>f.property===k));
  const versions=keys.map(k=>expected[k]);
  // The upstream server checks each write against the evolving cluster version.
  // Refuse multi-property CAS before any effect rather than partially restoring.
  if(keys.length!==1||!selected.every(Boolean)||Object.keys(expected).length!==keys.length||versions.some(v=>typeof v!=='string'||!/^514:\d+$/.test(v))) return 'UNSUPPORTED';
  const version=Number(versions[0].slice(4));
  if(!Number.isInteger(version)||version<0||version>0xffffffff) return 'UNSUPPORTED';
  const result=await entry.peer.interaction.write(Write(...selected.map(f=>Write.Attribute({endpoint:entry.endpoint.number,cluster:FanControl,attributes:f.attribute,value:desired[f.property],version}))));
  if(result.length===keys.length&&result.every(r=>r.status===0)) return 'ACCEPTED';
  if(result.length&&result.every(r=>r.status===0x92)) return 'CONFLICT'; // native DataVersionMismatch
  return result.some(r=>r.status===0)?'UNKNOWN':'REJECTED';
}
// Listen before peer startup; initial attribute reports are state, never contact edges.
controller.env.get(ChangeNotificationService).change.on(change=>{
  let entry=[...inventory.values()].find(e=>e.endpoint===change.endpoint);
  // Native events can arrive during subscription priming before discovery has
  // installed the descriptor inventory. Keep their real identity/time. State
  // priming still never becomes a synthetic contact edge.
  if(!entry && change.kind==='event') {
    let peer=change.endpoint;while(peer && peer.nodeType!=='client') peer=peer.owner;
    if(peer && supportedFields(change.endpoint).length) {
      const target=peer.id==='plug'&&change.endpoint.number===1?'matter:plug':`matter:${peer.id}/${change.endpoint.number}`;
      entry={target,peer,endpoint:change.endpoint};
    }
  }
  if(!entry) return;
  if(change.kind==='update') emit('state.changed',entry,{cluster:change.behavior.id,properties:change.properties},observation(entry,entry.endpoint.state));
  if(change.kind==='event') {
    // Preserve native event number/time for durable dedup and interpretation in Home.
    const payload={cluster:change.behavior.id,event:change.event.propertyName,number:String(change.number),timestamp:change.timestamp,timestamp_kind:change.timestampKind,data:change.payload};
    emit('matter.event',entry,payload,undefined,`matter:${entry.peer.id}/${entry.endpoint.number}/${change.behavior.id}/${change.event.id}/${change.number}`);
  }
});
await controller.start();
const configured=JSON.parse(process.env.SPIRA_MATTER_PEERS || '[{"id":"plug","passcode":20202021,"discriminator":3840},{"id":"household","passcode":20202022,"discriminator":3841}]');
async function connect(config) {
  let peer=controller.peers.get(config.id);
  if(!peer) peer=await controller.peers.commission({id:config.id,pairingCode:ManualPairingCodeCodec.encode({passcode:config.passcode,discriminator:config.discriminator})});
  await peer.start();
  function* descendants(parent) {for(const ep of parent.parts) {yield ep;yield* descendants(ep);}}
  for(const endpoint of descendants(peer)) {
    const selected=supportedFields(endpoint); if(!selected.length) continue;
    const target=config.id==='plug' && endpoint.number===1?'matter:plug':`matter:${config.id}/${endpoint.number}`;
    const descriptor=await endpoint.get({descriptor:['deviceTypeList','serverList']},{includeKnownVersions:true});
    inventory.set(target,{target,peer,endpoint,fields:selected,descriptor:descriptor.descriptor});
  }
  peer.lifecycle.connectionStateChanged.on(state=>{
    for(const entry of inventory.values()) if(entry.peer===peer) emit('availability.changed',entry,{connection_state:state});
  });
}
// One absent peer never prevents other commissioned nodes or the HTTP surface recovering.
for(const config of configured) {
  const attempt=async()=>{try {await connect(config);return true;} catch(e) {console.error('PEER_UNAVAILABLE',config.id,e.message);return false;}};
  void (async()=>{while(!await attempt()) await new Promise(r=>setTimeout(r,5000).unref());})();
}
function capabilities() {return [...inventory.values()].map(e=>({target:e.target,writable:e.fields.filter(f=>f.command).map(f=>f.property),families:[...new Set(e.fields.filter(f=>f.command).map(f=>f.family || (f.cluster==='fanControl'?'MAINTAIN_CONDITION':'SET_STATE')))].concat(e.fields.some(f=>f.cluster==='fanControl')?['SET_STATE']:[]),available:e.peer.lifecycle.connectionState===0,conditional_write:fanFields(e).length>0&&e.fields.filter(f=>f.command).every(f=>f.cluster==='fanControl'),substrate:'VIRTUAL_MATTER',device_type:e.descriptor.deviceTypeList.map(t=>t.deviceType).join(','),value_schema:Object.fromEntries(e.fields.filter(f=>f.schema).map(f=>[f.property,f.schema])),events:['state.changed','matter.event'],commands:fanFields(e).length?[{name:'conditional_attribute_write',properties:fanFields(e).map(f=>f.property),max_properties:1,revision_scope:'cluster',guard:'Matter DataVersion'}]:[]}));}
const http = createServer(async(req,res)=>{
  try {
    let raw=''; for await(const part of req) {raw+=part;if(raw.length>65536) throw Error('Request too large');}
    const body=raw?JSON.parse(raw):{},url=new URL(req.url,'http://localhost'); let result;
    const target=body.target || url.searchParams.get('target') || 'matter:plug';
    const entry=inventory.get(target);
    if(url.pathname==='/capabilities') result=capabilities();
    else if(url.pathname==='/inventory') result={epoch,subscriptions:[...controller.peers].flatMap(p=>[...p.interaction.subscriptions]).filter((s,i,all)=>all.indexOf(s)===i).map(s=>({id:s.subscriptionId,kind:s.kind,max_interval:s.maxInterval})),peers:configured.map(c=>({id:c.id,commissioned:!!controller.peers.get(c.id)})),targets:[...inventory.values()].map(e=>({target:e.target,peer:e.peer.id,endpoint:e.endpoint.number,descriptor:e.descriptor,fields:e.fields.map(f=>({cluster:f.cluster,attribute:f.attribute,property:f.property,writable:!!f.command}))})),event_buffer_limit:4096};
    else if(url.pathname==='/events') {
      const after=Number(url.searchParams.get('after')||0),same=url.searchParams.get('epoch')===epoch;
      const events=journal.records;
      result={epoch,cursor:journal.sequence,gap:(!same&&after>0)||(events.length>0&&after<events[0].sequence-1),events:events.filter(e=>e.sequence>after || (!same&&after>journal.sequence)).map(e=>e.event)};
    } else if(!entry) {res.statusCode=404;result={error:'Target not commissioned/discovered'};}
    else if(url.pathname==='/read') {
      const selection={}; for(const f of entry.fields) (selection[f.cluster] ||= []).push(f.attribute);
      // Unconditional network ReadRequest, never return a command echo/cache as fresh truth.
      if(fanFields(entry).length) result=await fanRead(entry);
      else {const state=await entry.endpoint.get(selection,{includeKnownVersions:true}); result=observation(entry,state);}
    } else if(req.method==='POST'&&url.pathname==='/command') {
      const desired=body.desired || {},selected=Object.keys(desired).map(k=>entry.fields.find(f=>f.property===k&&f.command));
      result={status:'UNSUPPORTED',operation_id:body.operation_id};
      if(selected.length&&selected.every(Boolean)&&selected.every(f=>validValue(f,desired[f.property],entry.endpoint))) {
        if(body.expected) result.status=await fanConditionalWrite(entry,desired,body.expected);
        else {
        // Validate the entire request before any write. Multi-property commands are not atomic.
        result={...result,...await executeCommands(entry.endpoint,selected,desired)};
        if(selected.some(f=>f.family==='RUN_JOB')) result.detail='Native RVC run mode/state has no job identity; completion attribution unavailable';
        }
      }
    } else {res.statusCode=404;result={error:'Unknown endpoint'};}
    res.setHeader('content-type','application/json');res.end(JSON.stringify(result,(_,v)=>typeof v==='bigint'?v.toString():v));
  } catch(e) {console.error(e);res.statusCode=503;res.end(JSON.stringify({error:e.message}));}
}).listen(8720,'127.0.0.1');
console.log('CONTROLLER_READY 8720');

// Upstream closes protocol/storage on signals; also release this helper listener.
process.once('SIGTERM',()=>http.close());
process.once('SIGINT',()=>http.close());

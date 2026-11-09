// Upstream device/cluster servers; this process owns the independent virtual world.
import '@matter/nodejs';
import { Endpoint, ServerNode, Environment } from '@matter/main';
import { DimmableLightDevice, ContactSensorDevice, GenericSwitchDevice, HumiditySensorDevice, FanDevice, LaundryWasherDevice } from '@matter/main/devices';
import { SwitchServer } from '@matter/main/behaviors/switch';
import { LaundryWasherModeServer } from '@matter/main/behaviors/laundry-washer-mode';
import { addRvc } from './upstream-rvc/RoboticVacuumCleanerDevice.js';
import { createServer } from 'node:http';
Environment.default.vars.set('mdns.networkInterface', process.env.SPIRA_MATTER_INTERFACE || 'en0');
const node = await ServerNode.create({id:'spira-household-'+(process.env.SPIRA_MATTER_NODE_SUFFIX || 'en0'),network:{port:5552},commissioning:{passcode:20202022,discriminator:3841}});
const endpoints = new Map();
async function add(type, options) {
  const ep = new Endpoint(type, options); await node.add(ep); endpoints.set(options.id, ep); return ep;
}
await add(DimmableLightDevice,{id:'light',onOff:{onOff:false},levelControl:{currentLevel:100}});
await add(ContactSensorDevice,{id:'contact',booleanState:{stateValue:true}}); // true = contact detected/closed
await add(GenericSwitchDevice.with(SwitchServer.with('MomentarySwitch','MomentarySwitchRelease')),{id:'bell',switch:{numberOfPositions:2,currentPosition:0}});
await add(HumiditySensorDevice,{id:'humidity',relativeHumidityMeasurement:{measuredValue:7000}});
await add(FanDevice,{id:'fan',fanControl:{fanMode:0,fanModeSequence:0,percentSetting:0,percentCurrent:0}});
await add(LaundryWasherDevice.with(LaundryWasherModeServer),{id:'washer',
  operationalState:{operationalState:0,operationalStateList:[{operationalStateId:0},{operationalStateId:1},{operationalStateId:2},{operationalStateId:3}]},
  laundryWasherMode:{currentMode:0,supportedModes:[{label:'Normal',mode:0,modeTags:[{value:0x4000}]},{label:'Delicate',mode:1,modeTags:[{value:0x4001}]}]}});
await addRvc(node);
// Extra endpoints exercise household-scale fanout without more redundant processes.
for(let i=0;i<5;i++) await add(ContactSensorDevice,{id:'contact-'+i,booleanState:{stateValue:true}});
await node.start();
function snapshot() {return [...node.parts].map(ep=>({id:ep.id,endpoint:ep.number,state:ep.state}));}
const http = createServer(async(req,res)=>{
  try {
    let raw=''; for await(const part of req) raw+=part;
    const body=raw?JSON.parse(raw):{};
    if(req.method==='POST' && req.url==='/mutate') {
      const ep=endpoints.get(body.id); if(!ep) throw Error('Unknown independently mutable endpoint');
      // Only expose supported fixture inputs, never accept Home semantic events or commands.
      const allowed={light:['onOff','levelControl'],contact:['booleanState'],bell:['switch'],humidity:['relativeHumidityMeasurement'],fan:['fanControl'],washer:['laundryWasherMode']};
      const clusters=allowed[body.id] || (body.id.startsWith('contact-')?['booleanState']:[]);
      if(!Object.keys(body.state || {}).length || Object.keys(body.state).some(x=>!clusters.includes(x))) throw Error('Unsupported fixture input');
      await ep.set(body.state);
    } else if(req.method!=='GET' || req.url!=='/state') {res.statusCode=404;res.end('{}');return;}
    res.setHeader('content-type','application/json');res.end(JSON.stringify(snapshot(),(_,v)=>typeof v==='bigint'?v.toString():v));
  } catch(e) {res.statusCode=400;res.end(JSON.stringify({error:e.message}));}
}).listen(8723,'127.0.0.1');
console.log('HOUSEHOLD_READY 8723');

process.once('SIGTERM',()=>http.close());
process.once('SIGINT',()=>http.close());

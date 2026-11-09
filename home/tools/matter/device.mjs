// Upstream OnOffPlugInUnitDevice; HTTP is an independent OOB test actor only.
import '@matter/nodejs';
import { Endpoint, ServerNode, Environment } from '@matter/main';
import { OnOffPlugInUnitDevice } from '@matter/main/devices';
import { createServer } from 'node:http';
Environment.default.vars.set('mdns.networkInterface',process.env.SPIRA_MATTER_INTERFACE || 'en0');
const server = await ServerNode.create({id:'spira-virtual-plug-'+(process.env.SPIRA_MATTER_NODE_SUFFIX || 'en0'),network:{port:5550},commissioning:{passcode:20202021,discriminator:3840}});
const endpoint = new Endpoint(OnOffPlugInUnitDevice,{id:'plug'});
await server.add(endpoint);
endpoint.events.onOff.onOff$Changed.on(value=>console.log('DEVICE_STATE',JSON.stringify({power:value,at:Date.now()})));
await server.start();
const http = createServer(async(req,res)=>{
  if(req.method==='POST' && req.url==='/toggle') await endpoint.set({onOff:{onOff:!endpoint.state.onOff.onOff}});
  res.setHeader('content-type','application/json');res.end(JSON.stringify({power:endpoint.state.onOff.onOff}));
}).listen(8721,'127.0.0.1');
console.log('OOB_READY 8721');

process.once('SIGTERM',()=>http.close());
process.once('SIGINT',()=>http.close());

// Explicit, bounded translations of standard Matter attributes, not household inference.
import { OnOffClient } from '@matter/main/behaviors/on-off';
import { LevelControlClient } from '@matter/main/behaviors/level-control';
import { LaundryWasherModeClient } from '@matter/main/behaviors/laundry-washer-mode';
import { RvcRunModeClient } from '@matter/main/behaviors/rvc-run-mode';
export const fields = [
  {cluster:'onOff',attribute:'onOff',property:'power',schema:{type:'boolean'},command:async(e,v)=>e.commandsOf(OnOffClient)[v?'on':'off']()},
  {cluster:'levelControl',attribute:'currentLevel',property:'brightness',schema:{type:'integer',minimum:1,maximum:254},command:async(e,v)=>e.commandsOf(LevelControlClient).moveToLevel({level:v,transitionTime:0,optionsMask:{executeIfOff:true},optionsOverride:{executeIfOff:true}})},
  {cluster:'booleanState',attribute:'stateValue',property:'closed'},
  {cluster:'relativeHumidityMeasurement',attribute:'measuredValue',property:'humidity',decode:v=>v===null?null:v/100},
  {cluster:'switch',attribute:'currentPosition',property:'position'},
  {cluster:'fanControl',attribute:'fanMode',property:'fan_mode',schema:{type:'integer',minimum:0,maximum:3},command:async(e,v)=>e.set({fanControl:{fanMode:v}})},
  {cluster:'fanControl',attribute:'percentSetting',property:'percent',schema:{type:'integer',minimum:0,maximum:100},command:async(e,v)=>e.set({fanControl:{percentSetting:v}})},
  {cluster:'laundryWasherMode',attribute:'currentMode',property:'mode',schema:{type:'integer',minimum:0,maximum:255},command:async(e,v)=>e.commandsOf(LaundryWasherModeClient).changeToMode({newMode:v}),family:'CONFIGURE_FOR_NEXT_USE'},
  {cluster:'operationalState',attribute:'operationalState',property:'operational_state'},
  {cluster:'rvcRunMode',attribute:'currentMode',property:'run_mode',schema:{type:'integer',minimum:0,maximum:255},command:async(e,v)=>e.commandsOf(RvcRunModeClient).changeToMode({newMode:v}),family:'RUN_JOB'},
  {cluster:'rvcOperationalState',attribute:'operationalState',property:'operational_state'},
  {cluster:'serviceArea',attribute:'currentArea',property:'area'},
];
export function supportedFields(endpoint) {
  return fields.filter(f=>endpoint.behaviors.supported[f.cluster] && endpoint.state[f.cluster][f.attribute]!==undefined);
}
export function validValue(field,value,endpoint) {
  const s=field.schema;
  if(s.type==='boolean') return typeof value==='boolean';
  if(!Number.isInteger(value)||value<s.minimum||value>s.maximum) return false;
  if(['mode','run_mode'].includes(field.property)) return endpoint.state[field.cluster].supportedModes.some(m=>m.mode===value);
  return true;
}
export function valuesFrom(state,selected) {
  const values={}; for(const f of selected) if(state[f.cluster]?.[f.attribute]!==undefined) values[f.property]=f.decode?f.decode(state[f.cluster][f.attribute]):state[f.cluster][f.attribute];
  return values;
}

export async function executeCommands(endpoint,selected,desired) {
  let applied=0;
  for(const f of selected) {
    const response=await f.command(endpoint,desired[f.property]);
    if(response?.status!==undefined&&response.status!==0) {
      // An earlier accepted write may already have changed the world. A later
      // rejection cannot make the entire operation safely retryable.
      return {status:applied?'UNKNOWN':'REJECTED',detail:JSON.stringify(response)};
    }
    applied++;
  }
  return {status:'ACCEPTED'};
}

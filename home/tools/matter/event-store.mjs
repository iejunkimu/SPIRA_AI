// Bounded non-secret protocol observation journal in the private controller bind.
// Fabric state remains wholly owned by upstream matter.js.
import { appendFileSync, existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';
export class EventStore {
  constructor(path,limit=4096) {
    this.path=path;this.limit=limit;this.sequence=0;this.lines=0;
    mkdirSync(dirname(path),{recursive:true});
    const saved=existsSync(path)?readFileSync(path,'utf8'):'';
    this.records=[];
    for(const line of saved.split('\n').filter(Boolean)) {
      const row=JSON.parse(line);
      if(!Number.isSafeInteger(row.sequence)||row.sequence<=this.sequence||!row.event?.event_id) throw Error('Invalid Matter observation journal');
      this.sequence=row.sequence;this.records.push(row);this.lines++;
    }
    this.records=this.records.slice(-limit);
  }
  append(event) {
    const row={sequence:++this.sequence,event};
    appendFileSync(this.path,JSON.stringify(row,(_,v)=>typeof v==='bigint'?v.toString():v)+'\n',{mode:0o600});
    this.records.push(row);if(this.records.length>this.limit)this.records.shift();
    if(++this.lines>this.limit+256) {
      writeFileSync(this.path+'.tmp',this.records.map(r=>JSON.stringify(r,(_,v)=>typeof v==='bigint'?v.toString():v)).join('\n')+'\n',{mode:0o600});
      renameSync(this.path+'.tmp',this.path);this.lines=this.records.length;
    }
    return row;
  }
}

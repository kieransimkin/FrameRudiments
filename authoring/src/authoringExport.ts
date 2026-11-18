/** Shared, export-only authoring bundle. Callers own native graph approval and
 * supply its history-revision fence; this module never writes project files.
 */
import {sha256,zipEntries} from './archive.ts';
import {cueNameValid,serializeLrc,type Document} from './model.ts';
import type {AuthoringSnapshot} from './authoringHistory.ts';

export const AUTHORING_EXPORT_README='FrameRudiments authoring bundle\n'+
  'Extract these files into the ORIGINAL project directory.\n'+
  'Do not move or modify the existing SHA-pinned master, fonts, models or recordings.\n'+
  'Run framerudiments plan and preflight before rendering.\n'+
  'Original project files have not been modified by the authoring preview server.\n';
export const AUTHORING_EXPORT_STALE='The draft changed during export. Wait for its graph check, then export again.';
export interface AuthoringExport {
  project:Document;
  entries:Record<string,string>;
  bytes:Uint8Array;
}
export interface AuthoringExportOptions {
  /** Compare the whole immutable history revision, not its current snapshot:
   * undo followed by redo can revisit a snapshot while invalidating its check.
   */
  isCurrent?:()=>boolean;
  /** The normal path uses browser WebCrypto. The seam permits delayed digest
   * completion in concurrency tests without a second serializer.
   */
  hash?:(text:string)=>Promise<string>;
}

/** JSON.stringify otherwise drops values/keys or replaces them with null. A
 * project loaded from JSON satisfies this boundary, including unknown fields;
 * an invalid programmatic proposal must fail instead of exporting altered data.
 */
function requireProjectJSON(value:unknown,visiting=new WeakSet<object>()):void {
  if(value===null||typeof value==='string'||typeof value==='boolean')return;
  const invalid=()=>{throw Error('Authoring project requires finite JSON data without omitted fields.');};
  if(typeof value==='number'){if(!Number.isFinite(value))invalid();return;}
  if(typeof value!=='object')return invalid();
  const prototype=Object.getPrototypeOf(value);
  if(Array.isArray(value)?prototype!==Array.prototype:prototype!==Object.prototype&&prototype!==null)return invalid();
  if(visiting.has(value))return invalid();
  visiting.add(value);
  const keys=Reflect.ownKeys(value);
  if(Array.isArray(value)){
    // Only indexed entries plus the native length slot survive JSON arrays.
    if(keys.length!==value.length+1)return invalid();
    for(let index=0;index<value.length;index++)
      if(!Object.prototype.hasOwnProperty.call(value,index))return invalid();
  }
  for(const key of keys){
    if(Array.isArray(value)&&key==='length')continue;
    if(typeof key!=='string')return invalid();
    const descriptor=Object.getOwnPropertyDescriptor(value,key)!;
    if(!descriptor.enumerable||!Object.prototype.hasOwnProperty.call(descriptor,'value'))return invalid();
    requireProjectJSON(descriptor.value,visiting);
  }
  visiting.delete(value);
}

function pinnedNames(project:Document):Set<string> {
  const names=new Set<string>();
  const visit=(value:unknown):void=>{
    if(!value||typeof value!=='object')return;
    if(Array.isArray(value)){value.forEach(visit);return;}
    const row=value as Document;
    for(const [path,hash] of [['path','sha256'],['shader_path','shader_sha256']]){
      if(typeof row[path]!=='string'||typeof row[hash]!=='string')continue;
      // The browser has no authority over the original project directory.
      // Conservatively reserve source basenames, including absolute/Windows
      // paths, and compare case-insensitively for portable extraction.
      const basename=row[path].replace(/\\/g,'/').split('/').at(-1);
      if(basename)names.add(basename.toLowerCase());
    }
    Object.values(row).forEach(visit);
  };
  visit(project);
  return names;
}

function sidecarName(kind:string,reserved:Set<string>):string {
  const stem=`authoring-${kind}`;
  let name=`${stem}.lrc`,suffix=2;
  while(reserved.has(name.toLowerCase()))name=`${stem}-${suffix++}.lrc`;
  reserved.add(name.toLowerCase());
  return name;
}

/** Capture the complete project and all sidecars before the first async hash.
 * Only dirty sidecar refs change; native graph, source clocks, pins and unknown
 * project data retain their exact JSON values. No automatic extraction occurs.
 */
export async function buildAuthoringExport(snapshot:AuthoringSnapshot,
  options:AuthoringExportOptions={}):Promise<AuthoringExport> {
  const assertCurrent=()=>{if(options.isCurrent&&!options.isCurrent())throw Error(AUTHORING_EXPORT_STALE);};
  assertCurrent();
  requireProjectJSON(snapshot.project);
  const captured=structuredClone(snapshot),updated=captured.project;
  const reserved=pinnedNames(updated),entries:Record<string,string>={};
  if(reserved.has('project.json')||reserved.has('authoring-readme.txt'))
    throw Error('A pinned input conflicts with a required authoring bundle filename.');
  const pending:{field:string;name:string;text:string}[]=[];
  for(const [kind,field] of [['lines','lyrics'],['words','word_lyrics'],['cues','cues']] as const){
    if(!captured.dirty[kind])continue;
    if(kind==='cues'&&(captured.sidecars.cueFormat==='cue'||captured.sidecars.cues.some(row=>!cueNameValid(row.label))))
      throw Error('Edited cues require conversion to SECTION, ENTRY, DROP or BREAK LRC labels.');
    pending.push({field,name:sidecarName(kind,reserved),text:serializeLrc(captured.sidecars[kind])});
  }
  const hash=options.hash??sha256;
  for(const item of pending){
    assertCurrent();
    const digest=await hash(item.text);
    assertCurrent();
    if(!/^[a-f0-9]{64}$/.test(digest))throw Error('Invalid SHA-256 for an authored sidecar');
    entries[item.name]=item.text;
    const original=updated[item.field];
    updated[item.field]={...(original&&typeof original==='object'&&!Array.isArray(original)?original:{}),
      path:item.name,sha256:digest};
  }
  assertCurrent();
  entries['project.json']=JSON.stringify(updated,null,2)+'\n';
  entries['AUTHORING-README.txt']=AUTHORING_EXPORT_README;
  const bytes=zipEntries(entries);
  assertCurrent();
  return {project:updated,entries,bytes};
}

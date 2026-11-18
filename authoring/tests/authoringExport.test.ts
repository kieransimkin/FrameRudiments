import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {sha256} from '../src/archive.ts';
import {AUTHORING_EXPORT_README,AUTHORING_EXPORT_STALE,buildAuthoringExport} from '../src/authoringExport.ts';
import {commitAuthoring,createAuthoringHistory,undoAuthoring,redoAuthoring,
  type AuthoringSnapshot} from '../src/authoringHistory.ts';
import {editTiming,parseLrc,withKeyframe} from '../src/model.ts';

const pin=(path:string)=>({path,sha256:'a'.repeat(64)});
function snapshot():AuthoringSnapshot {
  return {
    project:{schema:'framerudiments.project.v1',title:'Synthetic export fixture',
      audio:{source:pin('master.wav'),sample_rate:48000,start_sample:48000,end_sample:144000,repeats:2,fade_seconds:0},
      clock:{bpm:120,origin:.125,authority:'synthetic fixture',status:'synthetic',phase_status:'supplied'},
      font:pin('font.ttf'),lyrics:pin('lines.lrc'),word_lyrics:pin('words.lrc'),cues:pin('sections.cue'),
      layers:[{id:'red',asset:pin('red.png'),motion:{kind:'recording',file:pin('recording.json')}}],
      stem_sources:{native:{kind:'recording',file:pin('recording.json')}},
      effect_graph:{nodes:[{id:'layer:red',type:'source'},{id:'dim',type:'opacity',inputs:['layer:red'],opacity:.7}],output:'dim'},
      effect_graph_controls:[{node:'dim',property:'opacity',motion:{kind:'named',pattern:'circle',repeat_clock:'source'},axis:'x',range:[.2,.8]}],
      future_project_extension:{nested:[null,true,3,'été ❤️'],unknown_pin:pin('opaque.bin')}},
    sidecars:{lines:parseLrc('[00:01.100]été ❤️\n[00:02.100]again\n[00:02.900]\n','line'),
      words:parseLrc('[00:01.100]été\n[00:01.600]❤️\n','word'),
      cues:[{id:'cue-0',start:1,label:'Intro'},{id:'cue-1',start:2,label:'Drop'}],cueFormat:'cue'},
    dirty:{lines:false,words:false,cues:false},
  };
}
function authored():AuthoringSnapshot {
  const value=snapshot();
  return {...value,sidecars:{...value.sidecars,cueFormat:'lrc',cues:[
    {id:'cue-0',start:1,label:'[SECTION: INTRO]'},{id:'cue-1',start:2,label:'[DROP: FINISH]'}]},
    dirty:{lines:true,words:true,cues:true}};
}
function deferred<T>() {
  let resolve!:(value:T)=>void;
  const promise=new Promise<T>(done=>{resolve=done;});
  return {promise,resolve};
}

test('clean export preserves complete JSON, native pins/clocks and original CD CUE without mutation',async()=>{
  const source=snapshot(),before=structuredClone(source);
  const first=await buildAuthoringExport(source),second=await buildAuthoringExport(source);
  assert.deepEqual(first.project,source.project);
  assert.deepEqual(JSON.parse(first.entries['project.json']),source.project);
  assert.deepEqual(Object.keys(first.entries).sort(),['AUTHORING-README.txt','project.json']);
  assert.equal(first.entries['AUTHORING-README.txt'],AUTHORING_EXPORT_README);
  assert.match(AUTHORING_EXPORT_README,/ORIGINAL project directory/);
  assert.deepEqual(first.bytes,second.bytes);
  assert.deepEqual(source,before);
  first.project.future_project_extension.nested.push('returned copy');
  assert.deepEqual(source,before);
});

test('dirty sidecars use source-clock LRC, exact UTF-8 digests and retained reference metadata',async()=>{
  const source=authored();
  source.project.lyrics.future_ref_metadata={retain:['yes']};
  source.sidecars.lines=editTiming(source.sidecars.lines,
    {...source.sidecars.lines[0],id:'line-0@1',start:2.625},source.project,'line');
  const before=structuredClone(source),result=await buildAuthoringExport(source);
  for(const [kind,field] of [['lines','lyrics'],['words','word_lyrics'],['cues','cues']] as const){
    const name=`authoring-${kind}.lrc`,text=result.entries[name];
    assert.equal(result.project[field].path,name);
    assert.equal(result.project[field].sha256,createHash('sha256').update(text,'utf8').digest('hex'));
  }
  assert.match(result.entries['authoring-lines.lrc'],/^\[00:01.625\]été ❤️\n/);
  assert.match(result.entries['authoring-lines.lrc'],/\[00:02.900\]\n$/);
  assert.equal(result.entries['authoring-cues.lrc'],'[00:01.000][SECTION: INTRO]\n[00:02.000][DROP: FINISH]\n');
  assert.deepEqual(result.project.lyrics.future_ref_metadata,{retain:['yes']});
  const retained={...result.project,lyrics:source.project.lyrics,word_lyrics:source.project.word_lyrics,cues:source.project.cues};
  assert.deepEqual(retained,source.project);
  assert.deepEqual(source,before);
});

test('generated sidecar names avoid existing pinned source names across portable path forms',async()=>{
  const source=authored();
  source.project.lyrics=pin('./authoring-lines.lrc');
  source.project.font=pin('C:\\Original\\AUTHORING-LINES-2.LRC');
  source.project.layers[0].asset=pin('nested/authoring-words.lrc');
  source.project.future_project_extension.shader={shader_path:'/assets/AUTHORING-CUES.LRC',shader_sha256:'b'.repeat(64)};
  const before=structuredClone(source),first=await buildAuthoringExport(source),second=await buildAuthoringExport(source);
  assert.equal(first.project.lyrics.path,'authoring-lines-3.lrc');
  assert.equal(first.project.word_lyrics.path,'authoring-words-2.lrc');
  assert.equal(first.project.cues.path,'authoring-cues-2.lrc');
  assert.deepEqual(Object.keys(first.entries).sort(),[
    'AUTHORING-README.txt','authoring-cues-2.lrc','authoring-lines-3.lrc','authoring-words-2.lrc','project.json']);
  assert.deepEqual(first.bytes,second.bytes);
  assert.deepEqual(source,before);
});

test('fixed project and README entries cannot overwrite an admitted pinned input',async()=>{
  for(const path of ['./project.json','C:\\Original\\PROJECT.JSON','nested/authoring-readme.TXT']){
    const source=snapshot();source.project.future_project_extension.pin=pin(path);
    const before=structuredClone(source);
    await assert.rejects(buildAuthoringExport(source),/pinned input conflicts with a required authoring bundle filename/);
    assert.deepEqual(source,before);
  }
});

test('non-JSON project values fail instead of being dropped or silently replaced',async()=>{
  const values=[NaN,Infinity,-Infinity,undefined,Symbol('value'),()=>1,1n,new Date(),new Map(),new Set(),new Array(1)];
  for(const value of values){
    const source=snapshot();source.project.future_project_extension.value=value;
    await assert.rejects(buildAuthoringExport(source),/requires finite JSON data/);
  }
  for(const add of [
    (project:Record<string,any>)=>{project[Symbol('key')]='omitted';},
    (project:Record<string,any>)=>Object.defineProperty(project,'hidden',{value:'omitted'}),
    (project:Record<string,any>)=>Object.defineProperty(project,'getter',{enumerable:true,get(){return 'derived';}}),
    (project:Record<string,any>)=>{project.future_project_extension.array=Object.assign([1],{extra:'omitted'});},
    (project:Record<string,any>)=>{project.future_project_extension.cycle=project;},
  ]){
    const source=snapshot();add(source.project);
    await assert.rejects(buildAuthoringExport(source),/requires finite JSON data/);
  }
});

test('CD CUE requires explicit valid conversion before it becomes an edited LRC',async()=>{
  const source=snapshot();
  await assert.rejects(buildAuthoringExport({...source,dirty:{...source.dirty,cues:true}}),/Edited cues require conversion/);
  await assert.rejects(buildAuthoringExport({...source,sidecars:{...source.sidecars,cueFormat:'lrc'},
    dirty:{...source.dirty,cues:true}}),/Edited cues require conversion/);
  const converted=authored(),result=await buildAuthoringExport({...converted,dirty:{lines:false,words:false,cues:true}});
  assert.equal(result.project.cues.path,'authoring-cues.lrc');
  assert.equal(source.project.cues.path,'sections.cue');
  assert.deepEqual(result.project.lyrics,source.project.lyrics);
});

test('export captures every sidecar and project before an asynchronous digest yields',async()=>{
  const source=authored(),before=structuredClone(source),gate=deferred<void>();
  let calls=0;
  const result=buildAuthoringExport(source,{hash:async text=>{if(++calls===1)await gate.promise;return sha256(text);}});
  source.project.audio.start_sample=0;
  source.project.effect_graph.nodes[1].opacity=.1;
  source.project.future_project_extension.nested.push('late mutation');
  source.sidecars.lines[0].label='changed after click';
  source.sidecars.words.push({id:'word-late',start:2.5,label:'late'});
  source.sidecars.cues[0].label='unconverted late title';
  gate.resolve();
  const captured=await result,expected=await buildAuthoringExport(before);
  assert.equal(calls,3);
  assert.deepEqual(captured,expected);
});

test('stale export checks happen before hashing and after each awaited digest',async()=>{
  let calls=0;
  await assert.rejects(buildAuthoringExport(authored(),{isCurrent:()=>false,hash:async text=>{calls++;return sha256(text);}}),
    new Error(AUTHORING_EXPORT_STALE));
  assert.equal(calls,0);
  let current=true;
  await assert.rejects(buildAuthoringExport(authored(),{isCurrent:()=>current,hash:async text=>{
    calls++;current=false;return sha256(text);
  }}),new Error(AUTHORING_EXPORT_STALE));
  assert.equal(calls,1,'A stale first digest prevents later sidecar hashes and any returned bundle');
});

test('undo then redo cannot revive an in-flight export even when the snapshot identity returns',async()=>{
  let history=createAuthoringHistory(authored());
  history=commitAuthoring(history,current=>({...current,project:withKeyframe(current.project,'layer:red','opacity',{time:.5,value:.6})})).history;
  const revision=history,gate=deferred<void>();
  const pending=buildAuthoringExport(history.current,{isCurrent:()=>history===revision,
    hash:async text=>{await gate.promise;return sha256(text);}});
  history=redoAuthoring(undoAuthoring(history));
  assert.equal(history.current,revision.current);
  assert.notEqual(history,revision);
  gate.resolve();
  await assert.rejects(pending,new Error(AUTHORING_EXPORT_STALE));
});

test('reopening identical data invalidates an in-flight export; a semantic no-op leaves it valid',async()=>{
  let history=createAuthoringHistory(authored()),revision=history,gate=deferred<void>();
  const stale=buildAuthoringExport(history.current,{isCurrent:()=>history===revision,
    hash:async text=>{await gate.promise;return sha256(text);}});
  history=createAuthoringHistory(history.current);
  gate.resolve();
  await assert.rejects(stale,new Error(AUTHORING_EXPORT_STALE));
  revision=history;gate=deferred<void>();
  const valid=buildAuthoringExport(history.current,{isCurrent:()=>history===revision,
    hash:async text=>{await gate.promise;return sha256(text);}});
  const result=commitAuthoring(history,current=>structuredClone(current));
  assert.equal(result.disposition,'unchanged');
  history=result.history;
  assert.equal(history,revision);
  gate.resolve();
  assert.deepEqual((await valid).bytes,(await buildAuthoringExport(history.current)).bytes);
});

test('full undo restores original pin-only bundle and full redo restores identical authored ZIP bytes',async()=>{
  let history=createAuthoringHistory(snapshot());
  const initial=await buildAuthoringExport(history.current);
  history=commitAuthoring(history,current=>({...current,project:withKeyframe(current.project,'layer:red','opacity',{time:.25,value:.3})})).history;
  history=commitAuthoring(history,current=>({...current,sidecars:{...current.sidecars,
    lines:editTiming(current.sidecars.lines,{...current.sidecars.lines[0],id:'line-0@1',start:2.5},current.project,'line')},
    dirty:{...current.dirty,lines:true}})).history;
  const changed=await buildAuthoringExport(history.current);
  while(history.past.length)history=undoAuthoring(history);
  assert.deepEqual((await buildAuthoringExport(history.current)).bytes,initial.bytes);
  while(history.future.length)history=redoAuthoring(history);
  assert.deepEqual((await buildAuthoringExport(history.current)).bytes,changed.bytes);
});

test('ZIP local and central records use a valid deterministic 1980-01-01 date',async()=>{
  const {bytes}=await buildAuthoringExport(authored()),view=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength);
  assert.equal(view.getUint16(10,true),0);
  assert.equal(view.getUint16(12,true),0x21);
  const central=view.getUint32(bytes.length-6,true);
  assert.equal(view.getUint32(central,true),0x02014b50);
  assert.equal(view.getUint16(central+12,true),0);
  assert.equal(view.getUint16(central+14,true),0x21);
});

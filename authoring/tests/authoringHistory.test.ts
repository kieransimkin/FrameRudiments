import test from 'node:test';
import assert from 'node:assert/strict';
import {AUTHORING_HISTORY_LIMIT,commitAuthoring,createAuthoringHistory,redoAuthoring,undoAuthoring,
 type AuthoringHistory,type AuthoringSnapshot} from '../src/authoringHistory.ts';
import {editTiming,expandTiming,renameCue,withKeyframe} from '../src/model.ts';
import {withEffectConnection,withEffectOpacity} from '../src/graphEditor.ts';
import {GRAPH_VALIDATION_SCHEMA,GraphValidationFence} from '../src/graphValidation.ts';
import {GraphControlFence} from '../src/graphControls.ts';
import {samePreviewRequest,type NativePreviewRequest} from '../src/graphPreview.ts';
import {selectGraphPort} from '../src/graphInteraction.ts';

const PIN='a'.repeat(64);
function fixture():AuthoringSnapshot {
 return {project:{schema:'framerudiments.project.v1',width:64,height:64,fps:60,
   audio:{source:{path:'master.wav',sha256:PIN},sample_rate:48000,start_sample:48000,end_sample:144000,repeats:2},
   clock:{bpm:120,origin:.125,status:'synthetic',authority:'fixture'},
   font:{path:'font.ttf',sha256:'b'.repeat(64)},
   lyrics:{path:'lines.lrc',sha256:'c'.repeat(64)},word_lyrics:{path:'words.lrc',sha256:'d'.repeat(64)},
   cues:{path:'sections.cue',sha256:'e'.repeat(64)},
   layers:[{id:'voice',type:'rudiment',renderer:'particles',style:{seed:17},
     motion:{kind:'recording',file:{path:'recording.json',sha256:'f'.repeat(64)},axes:['x'],outside:'neutral'}}],
   effect_graph:{nodes:[{id:'frame',type:'source'},
     {id:'a',type:'opacity',inputs:['frame'],opacity:.8},
     {id:'b',type:'copy',inputs:['frame']},
     {id:'mix',type:'transition',inputs:['a','b'],transition_style:'dissolve',progress:.5}],output:'mix'},
   effect_graph_controls:[{id:'motion',node:'mix',property:'progress',range:[0,1],
     motion:{kind:'named',pattern:'bounce',repeat_clock:'source',phase_beats:.25}}],
   // History retains foreign JSON without treating itself as the native validator.
   future_extension:JSON.parse('{"__proto__":{"keep":true},"ordered":[3,null,{"label":"été 🎼"}],"enabled":false}')},
  sidecars:{lines:[{id:'line-0',start:1.2,end:1.8,label:'synthetic line'}],
   words:[{id:'word-0',start:1.25,label:'synthetic',parentId:'line-0'}],
   cues:[{id:'cue-0',start:1,label:'Intro'}],cueFormat:'cue'},
  dirty:{lines:false,words:false,cues:false}};
}
function frozen(value:unknown):void {
 if(value===null||typeof value!=='object')return;
 assert.ok(Object.isFrozen(value));
 for(const child of Object.values(value))frozen(child);
}
function opacity(history:AuthoringHistory,value:number):AuthoringHistory {
 const result=commitAuthoring(history,s=>({...s,project:withEffectOpacity(s.project,'a',value)}));
 assert.equal(result.disposition,'committed');return result.history;
}
const report=()=>({schema:GRAPH_VALIDATION_SCHEMA,valid:true,graph_present:true,
 request_sha256:PIN,graph_sha256:'b'.repeat(64),sources:[{id:'frame',format:'RGBA8'}],
 nodes:[{id:'frame',type:'source',inputs:[],output:{format:'RGBA8'},min_inputs:0,max_inputs:0}],
 errors:[],scope:{rendered:false,gpu_context_opened:false,all_nodes:true,source_names:true,control_bindings:true}});

test('initial history detaches and deeply freezes complete imported JSON, sidecars and dirty flags',()=>{
 const input=fixture(),expected=structuredClone(input),history=createAuthoringHistory(input);
 assert.deepEqual(history.current,expected);assert.deepEqual(history.past,[]);assert.deepEqual(history.future,[]);
 assert.notEqual(history.current,input);assert.notEqual(history.current.project,input.project);
 assert.notEqual(history.current.sidecars.lines,input.sidecars.lines);frozen(history);
 input.project.audio.source.sha256='0'.repeat(64);
 input.project.effect_graph.nodes[1].opacity=.1;
 input.project.future_extension.ordered[2].label='changed';
 input.sidecars.lines[0].label='changed';input.sidecars.cueFormat='lrc';
 (input.dirty as Record<string,boolean>).cues=true;
 assert.deepEqual(history.current,expected);
 assert.throws(()=>{history.current.project.layers[0].motion.file.path='other.json';},TypeError);
 assert.throws(()=>history.current.sidecars.words.push({id:'bad',start:0,label:'bad'}),TypeError);
 assert.throws(()=>{history.current.sidecars.cueFormat='lrc';},TypeError);
 assert.throws(()=>{(history.current.dirty as Record<string,boolean>).lines=true;},TypeError);
 assert.throws(()=>{(history.past as AuthoringSnapshot[]).push(history.current);},TypeError);
});

test('committing a native proposal detaches caller aliases and retains all unrelated JSON and pins',()=>{
 const history=createAuthoringHistory(fixture()),proposal=structuredClone(history.current.project);
 proposal.effect_graph.nodes[3].progress=.75;
 const result=commitAuthoring(history,s=>({...s,project:proposal}),history);
 assert.equal(result.disposition,'committed');assert.notEqual(result.history,history);
 assert.equal(result.history.past[0],history.current);assert.equal(result.history.current.project.effect_graph.nodes[3].progress,.75);
 assert.deepEqual(result.history.current.project.audio,history.current.project.audio);
 assert.deepEqual(result.history.current.project.clock,history.current.project.clock);
 assert.deepEqual(result.history.current.project.layers,history.current.project.layers);
 assert.deepEqual(result.history.current.project.effect_graph_controls,history.current.project.effect_graph_controls);
 assert.deepEqual(result.history.current.project.future_extension,history.current.project.future_extension);
 proposal.effect_graph.nodes[3].progress=0;proposal.audio.source.path='changed.wav';
 assert.equal(result.history.current.project.effect_graph.nodes[3].progress,.75);
 assert.equal(result.history.current.project.audio.source.path,'master.wav');frozen(result.history);
});

test('real graph, keyframe, repeated timing and CUE conversion edits undo and redo every snapshot field',()=>{
 const initial=createAuthoringHistory(fixture()),snapshots=[initial.current];let history=initial;
 history=opacity(history,.3);snapshots.push(history.current);
 history=commitAuthoring(history,s=>({...s,project:withKeyframe(s.project,'layer:voice','opacity',{time:.5,value:.4})})).history;
 snapshots.push(history.current);
 history=commitAuthoring(history,s=>{
   const shown=expandTiming(s.sidecars.lines,s.project).find(item=>item.id==='line-0@1')!;
   return {...s,sidecars:{...s.sidecars,lines:editTiming(s.sidecars.lines,{...shown,start:2.6},s.project,'line')},
     dirty:{...s.dirty,lines:true}};
 }).history;snapshots.push(history.current);
 history=commitAuthoring(history,s=>{
   const cue=expandTiming(s.sidecars.cues,s.project)[0];
   return {...s,sidecars:{...s.sidecars,cues:editTiming(s.sidecars.cues,
     {...cue,label:renameCue(cue.label,'SECTION','SYNTHETIC')},s.project,'cue'),cueFormat:'lrc'},
     dirty:{...s.dirty,cues:true}};
 }).history;snapshots.push(history.current);
 assert.equal(history.current.sidecars.lines[0].start,1.6);
 assert.equal(history.current.sidecars.cueFormat,'lrc');
 assert.deepEqual(history.current.dirty,{lines:true,words:false,cues:true});
 for(const snapshot of snapshots){
   for(const field of ['audio','clock','font','lyrics','word_lyrics','cues','layers','effect_graph_controls','future_extension'])
     assert.deepEqual(snapshot.project[field],initial.current.project[field]);
   assert.deepEqual(snapshot.sidecars.words,initial.current.sidecars.words);frozen(snapshot);
 }
 for(let index=snapshots.length-2;index>=0;index--){
   const previous=history;history=undoAuthoring(history);
   assert.notEqual(history,previous);assert.equal(history.current,snapshots[index]);
 }
 assert.equal(history.current.sidecars.cueFormat,'cue');
 assert.deepEqual(history.current.dirty,{lines:false,words:false,cues:false});
 assert.equal(undoAuthoring(history),history);
 for(let index=1;index<snapshots.length;index++){
   const previous=history;history=redoAuthoring(history);
   assert.notEqual(history,previous);assert.equal(history.current,snapshots[index]);
 }
 assert.equal(redoAuthoring(history),history);frozen(history);
});

test('equal edits preserve current identity, revision and the redo branch even with reordered object keys',()=>{
 const initial=createAuthoringHistory(fixture()),history=undoAuthoring(opacity(initial,.3));
 const changes:[string,(s:AuthoringSnapshot)=>AuthoringSnapshot][]=[
   ['same snapshot',s=>s],['clone',s=>structuredClone(s)],
   ['equal model edit',s=>({...s,project:withEffectOpacity(s.project,'a',.8)})],
   ['reordered keys',s=>({dirty:s.dirty,sidecars:s.sidecars,
     project:Object.fromEntries(Object.entries(s.project).reverse())})],
 ];
 for(const [label,change] of changes){
   const result=commitAuthoring(history,change,history);
   assert.equal(result.disposition,'unchanged',label);assert.equal(result.history,history,label);
   assert.equal(result.history.current,initial.current);assert.equal(result.history.future,history.future);
 }
 assert.equal(redoAuthoring(history).current.project.effect_graph.nodes[1].opacity,.3);
});

test('dirty flags and cue format are independent snapshot data even when project and timing values match',()=>{
 const original=createAuthoringHistory(fixture());
 const dirty=commitAuthoring(original,s=>({...s,dirty:{...s.dirty,words:true}}));
 assert.equal(dirty.disposition,'committed');assert.deepEqual(dirty.history.current.project,original.current.project);
 const converted=commitAuthoring(dirty.history,s=>({...s,sidecars:{...s.sidecars,cueFormat:'lrc'}}));
 assert.equal(converted.disposition,'committed');
 assert.equal(undoAuthoring(converted.history).current,dirty.history.current);
 assert.equal(undoAuthoring(dirty.history).current,original.current);
});

test('a changed edit after undo discards only the abandoned redo branch',()=>{
 const initial=createAuthoringHistory(fixture()),first=opacity(initial,.3),second=opacity(first,.4);
 const undone=undoAuthoring(second),branch=opacity(undone,.6);
 assert.equal(undone.current,first.current);assert.equal(undone.future[0],second.current);
 assert.deepEqual(branch.future,[]);assert.equal(redoAuthoring(branch),branch);
 assert.equal(undoAuthoring(branch).current,first.current);
 assert.equal(undoAuthoring(undoAuthoring(branch)).current,initial.current);
 assert.equal(second.current.project.effect_graph.nodes[1].opacity,.4);
});

test('100 retained operations remain ordered and bounded through complete undo, redo and a new branch',()=>{
 assert.equal(AUTHORING_HISTORY_LIMIT,100);
 let history=createAuthoringHistory(fixture());
 for(let index=1;index<=137;index++){
   history=commitAuthoring(history,s=>({...s,project:{...s.project,notes:`operation ${index}`}})).history;
   assert.equal(history.past.length,Math.min(index,100));
 }
 const final=history.current;assert.equal(history.past[0].project.notes,'operation 37');
 for(let index=136;index>=37;index--){
   history=undoAuthoring(history);assert.equal(history.current.project.notes,`operation ${index}`);
 }
 assert.equal(history.past.length,0);assert.equal(history.future.length,100);
 assert.equal(undoAuthoring(history),history);
 for(let index=38;index<=137;index++){
   history=redoAuthoring(history);assert.equal(history.current.project.notes,`operation ${index}`);
   assert.ok(history.past.length<=100);
 }
 assert.equal(history.current,final);assert.equal(redoAuthoring(history),history);
 const branch=commitAuthoring(undoAuthoring(history),s=>({...s,project:{...s.project,notes:'branch'}})).history;
 assert.equal(branch.past.length,100);assert.equal(branch.future.length,0);
});

test('no available undo or redo leaves the initial revision untouched',()=>{
 const history=createAuthoringHistory(fixture());
 assert.equal(undoAuthoring(history),history);assert.equal(redoAuthoring(history),history);
});

test('validation, mutation and copy failures leave current, past and future unchanged',()=>{
 const original=createAuthoringHistory(fixture()),history=undoAuthoring(opacity(opacity(original,.3),.4));
 const before=JSON.stringify(history),failure=Error('Rejected native proposal');
 assert.throws(()=>commitAuthoring(history,()=>{throw failure;}),error=>error===failure);
 assert.throws(()=>commitAuthoring(history,s=>({...s,project:withEffectConnection(s.project,'a',0,'mix')})),/cycle/i);
 assert.throws(()=>commitAuthoring(history,s=>{s.sidecars.lines[0].start=2;return s;}),TypeError);
 assert.throws(()=>commitAuthoring(history,s=>({...s,project:{...s.project,callback:()=>0}})),/clone/i);
 assert.throws(()=>commitAuthoring(history,s=>({...s,project:{...s.project,hidden:new Map([['a',1]])}})),/plain data/);
 assert.throws(()=>commitAuthoring(history,s=>{
   const proposed=structuredClone(s);proposed.project.cycle=proposed;return proposed;
 }),/cyclic data/);
 assert.equal(JSON.stringify(history),before);assert.equal(history.current.project.effect_graph.nodes[1].opacity,.3);
 assert.equal(redoAuthoring(history).current.project.effect_graph.nodes[1].opacity,.4);frozen(history);
});

test('native proposals require the expected history revision and stale callbacks never run',()=>{
 const initial=createAuthoringHistory(fixture()),edited=opacity(initial,.3);let calls=0;
 const change=(s:AuthoringSnapshot)=>{calls++;return {...s,project:withEffectOpacity(s.project,'a',.7)};};
 for(const stale of [initial,initial.current,edited.current,{}]){
   const result=commitAuthoring(edited,change,stale);
   assert.equal(result.disposition,'stale');assert.equal(result.history,edited);assert.equal(calls,0);
 }
 const accepted=commitAuthoring(edited,change,edited);
 assert.equal(accepted.disposition,'committed');assert.equal(calls,1);
 assert.equal(accepted.history.current.project.effect_graph.nodes[1].opacity,.7);
});

test('undo then redo cannot revive a native proposal, validation ticket, preview request or pending connection',()=>{
 const initial=createAuthoringHistory(fixture()),edited=opacity(initial,.3);
 const validation=new GraphValidationFence<AuthoringHistory>(),controls=new GraphControlFence<AuthoringHistory>();
 const oldValidation=validation.begin(edited),oldControl=controls.begin(edited,'mix',PIN);
 assert.ok(validation.accept(report(),oldValidation,true));assert.ok(controls.current(oldControl));
 const project=edited.current.project,sidecars=edited.current.sidecars;
 const preview:NativePreviewRequest<AuthoringHistory>={snapshot:edited,project,sidecars,frame:5,quality:'fast',node:null};
 const wire=selectGraphPort(project,null,{kind:'output',node:'b'},edited).pending;
 const returned=redoAuthoring(undoAuthoring(edited));
 assert.equal(returned.current,edited.current);assert.notEqual(returned,edited);
 assert.equal(returned.current.project,project);assert.equal(returned.current.sidecars,sidecars);
 const proposal=commitAuthoring(returned,()=>{throw Error('Stale proposal executed');},edited);
 assert.equal(proposal.disposition,'stale');assert.equal(proposal.history,returned);
 assert.equal(samePreviewRequest(preview,{...preview,snapshot:returned}),false);
 const nextWire=selectGraphPort(project,wire,{kind:'input',node:'a',index:0},returned);
 assert.equal(nextWire.connection,null);assert.equal(nextWire.pending?.revision,returned);
 const newValidation=validation.begin(returned),newControl=controls.begin(returned,'mix',PIN);
 assert.equal(validation.accept(report(),oldValidation,true),null);assert.ok(validation.accept(report(),newValidation,true));
 assert.equal(controls.current(oldControl),false);assert.ok(controls.current(newControl));
});

test('a sidecar-only edit also rejects a proposal captured from the unchanged project',()=>{
 const history=createAuthoringHistory(fixture());
 const edited=commitAuthoring(history,s=>({...s,sidecars:{...s.sidecars,
   words:s.sidecars.words.map(word=>({...word,start:1.4}))},dirty:{...s.dirty,words:true}})).history;
 assert.deepEqual(edited.current.project,history.current.project);
 const result=commitAuthoring(edited,s=>({...s,project:withEffectOpacity(s.project,'a',.2)}),history);
 assert.equal(result.disposition,'stale');assert.equal(result.history,edited);
 assert.equal(undoAuthoring(edited).current,history.current);
});

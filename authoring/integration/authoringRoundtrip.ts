/** M10.5: actual shared history/export code, native HTTP proposals and ZIP bytes.
 * Python owns source fixtures, independent expected projects, ZIP extraction,
 * native reopen and pixel comparisons. Stdout contains only this JSON result.
 */
import {readFileSync} from 'node:fs';
import {strict as assert} from 'node:assert';
import {buildAuthoringExport} from '../src/authoringExport.ts';
import {AUTHORING_HISTORY_LIMIT,commitAuthoring,createAuthoringHistory,undoAuthoring,redoAuthoring,
  type AuthoringHistory,type AuthoringSnapshot} from '../src/authoringHistory.ts';
import {editTiming,layerKeyframes,withKeyframe,withLayerRoute,withStageInjection,
  type Document,type Sidecars,type TimingKind} from '../src/model.ts';
import {withInitialEffectGraph,withEffectSource,withEffectNode,withEffectConnection,
  withEffectInputs,withEffectOutput,withoutTerminalEffectNode,type AddableEffect} from '../src/graphEditor.ts';
import {GraphControlFence,readGraphControlResponse,setGraphParameter,putGraphBinding,
  removeGraphBinding,moveGraphBinding,type GraphControlCatalog,type GraphEditOperation} from '../src/graphControls.ts';
import {GraphValidationFence,readGraphValidation} from '../src/graphValidation.ts';

interface Plan extends Document {kind:string;checkpoint?:string;}
interface Fixture {name:string;project:Document;sidecars:Sidecars;operations:Plan[];reopen?:boolean;}
interface Checkpoint extends Document {name:string;archive:string;}
const input=JSON.parse(readFileSync(0,'utf8')) as {origin:string;cases:Fixture[]};
const originalInput=JSON.stringify(input),cases:Document[]=[];
let requests=0;

async function request(route:string,body:Document):Promise<unknown> {
  requests++;
  const response=await fetch(`${input.origin}/api/graph/${route}`,{
    method:'POST',headers:{'Content-Type':'application/json','Origin':input.origin},
    body:JSON.stringify(body),signal:AbortSignal.timeout(30000),
  });
  const result=await (route==='validate'?readGraphValidation(response):readGraphControlResponse(response));
  assert.equal(response.status,200,JSON.stringify(result));
  return result;
}

function dirtySidecars(snapshot:AuthoringSnapshot):Record<string,unknown> {
  return Object.fromEntries((['lines','words','cues'] as const)
    .filter(kind=>snapshot.dirty[kind]).map(kind=>[kind,snapshot.sidecars[kind]]));
}

function nativeOperation(catalog:GraphControlCatalog,plan:Plan):GraphEditOperation {
  if(plan.kind==='set_parameter')return setGraphParameter(catalog,plan.property,plan.value);
  if(plan.kind==='remove_binding')return removeGraphBinding(catalog,plan.index);
  if(plan.kind==='move_binding')return moveGraphBinding(catalog,plan.index,plan.direction);
  assert.equal(plan.kind,'put_binding');
  const motion=catalog.motions.find(row=>row.kind===plan.motion.kind&&
    (plan.motion.pattern===undefined||row.motion.pattern===plan.motion.pattern)&&
    (row.kind!=='named'||Object.keys(row.motion).length===2));
  assert.ok(motion,'The native catalogue must contain the requested released or pinned motion');
  return putGraphBinding(catalog,{index:plan.index??null,property:plan.property,motion_id:motion.id,
    settings:plan.settings,...(plan.motion_settings?{motion_settings:plan.motion_settings}:{})});
}

function localEdit(snapshot:AuthoringSnapshot,plan:Plan):AuthoringSnapshot {
  const project=snapshot.project;
  switch(plan.kind){
    case 'graph_init':return {...snapshot,project:withInitialEffectGraph(project)};
    case 'graph_source':return {...snapshot,project:withEffectSource(project,plan.source)};
    case 'graph_node':return {...snapshot,project:withEffectNode(project,plan.type as AddableEffect,plan.inputs,plan.options)};
    case 'graph_connection':return {...snapshot,project:withEffectConnection(project,plan.target,plan.index,plan.source)};
    case 'graph_inputs':return {...snapshot,project:withEffectInputs(project,plan.target,plan.inputs)};
    case 'graph_output':return {...snapshot,project:withEffectOutput(project,plan.target)};
    case 'graph_remove':return {...snapshot,project:withoutTerminalEffectNode(project,plan.target)};
    case 'keyframe':return {...snapshot,project:withKeyframe(project,plan.target,plan.property,plan.key,plan.oldTime)};
    case 'route':return {...snapshot,project:withLayerRoute(project,plan.target,plan.feed,plan.overlay,plan.durationPips)};
    case 'injection':return {...snapshot,project:withStageInjection(project,plan.target,plan.before,plan.enabled)};
    case 'noop':return structuredClone(snapshot);
    case 'timing':{
      const field=plan.field as 'lines'|'words'|'cues';
      assert.ok(['lines','words','cues'].includes(field));
      const sourceId=String(plan.id).slice(0,String(plan.id).lastIndexOf('@'));
      const item=snapshot.sidecars[field].find(row=>row.id===sourceId);
      assert.ok(item,`Timing fixture must address an existing source event: ${plan.id}`);
      const kind:TimingKind=field==='lines'?'line':field==='words'?'word':'cue';
      const edited=editTiming(snapshot.sidecars[field],{...item,id:plan.id,start:plan.start,label:plan.label??item.label},project,kind);
      return {...snapshot,sidecars:{...snapshot.sidecars,[field]:edited,
        ...(plan.cueFormat===undefined?{}:{cueFormat:plan.cueFormat})},dirty:{...snapshot.dirty,[field]:true}};
    }
    default:throw Error(`Unknown authoring fixture operation ${plan.kind}`);
  }
}

function differentKey(snapshot:AuthoringSnapshot):AuthoringSnapshot {
  const target=`layer:${snapshot.project.layers[0].id}`;
  const previous=layerKeyframes(snapshot.project).find(row=>row.target===target&&row.property==='opacity')?.keys.find(key=>key.time===0);
  return {...snapshot,project:withKeyframe(snapshot.project,target,'opacity',{time:0,value:previous?.value===.25?.75:.25})};
}

/** Qualification assertions use the same public history reducer as App. They
 * operate on an isolated history so the hand-authored checkpoint plan remains
 * suitable for independent native expectations.
 */
async function historyChecks(snapshot:AuthoringSnapshot):Promise<Document> {
  let branch=createAuthoringHistory(snapshot);
  branch=commitAuthoring(branch,differentKey).history;
  branch=commitAuthoring(branch,differentKey).history;
  const obsolete=branch;
  branch=undoAuthoring(branch);
  assert.equal(branch.future.length,1);
  const noop=commitAuthoring(branch,current=>structuredClone(current));
  assert.equal(noop.disposition,'unchanged');
  assert.equal(noop.history,branch);
  assert.equal(noop.history.future.length,1);
  branch=commitAuthoring(branch,differentKey).history;
  assert.equal(branch.future.length,0);
  assert.equal(redoAuthoring(branch),branch);
  let called=false;
  const stale=commitAuthoring(branch,current=>{called=true;return differentKey(current);},obsolete);
  assert.equal(stale.disposition,'stale');assert.equal(stale.history,branch);assert.equal(called,false);

  let bounded=createAuthoringHistory(snapshot);
  const states=[bounded.current],commits=AUTHORING_HISTORY_LIMIT+5;
  for(let index=0;index<commits;index++){
    const result=commitAuthoring(bounded,differentKey);
    assert.equal(result.disposition,'committed');bounded=result.history;states.push(bounded.current);
  }
  assert.equal(bounded.past.length,AUTHORING_HISTORY_LIMIT);
  const final=await buildAuthoringExport(bounded.current);
  let undone=0;
  while(bounded.past.length){bounded=undoAuthoring(bounded);undone++;}
  assert.equal(undone,AUTHORING_HISTORY_LIMIT);
  assert.equal(bounded.current,states[5]);
  assert.equal(undoAuthoring(bounded),bounded);
  while(bounded.future.length)bounded=redoAuthoring(bounded);
  assert.deepEqual((await buildAuthoringExport(bounded.current)).bytes,final.bytes);
  return {noop_preserved_redo:true,branch_cleared_redo:true,stale_proposal_rejected:true,
    history_bound:AUTHORING_HISTORY_LIMIT,bound_commits:commits,bound_undo_steps:undone,bound_export_identity:true};
}

for(const fixture of input.cases){
  let history=createAuthoringHistory({project:fixture.project,sidecars:fixture.sidecars,
    dirty:{lines:false,words:false,cues:false}});
  const checkpoints:Checkpoint[]=[],names=new Set<string>(),dispositions:Document[]=[];
  const validationFence=new GraphValidationFence<AuthoringHistory>();
  let validations=0,nativeEdits=0,rejectedStale=0;
  async function validate():Promise<void> {
    const revision=history,ticket=validationFence.begin(revision);
    const raw=await request('validate',{project:revision.current.project,sidecars:dirtySidecars(revision.current)});
    assert.equal(history,revision);
    const report=validationFence.accept(raw,ticket,revision.current.project.effect_graph!==undefined);
    assert.ok(report,'The native validation report must pass the production TypeScript parser');
    assert.equal(report.valid,true,JSON.stringify(report.errors));
    validations++;
  }
  async function checkpoint(name:string):Promise<Checkpoint> {
    assert.ok(!names.has(name),`Duplicate checkpoint ${name}`);names.add(name);
    const revision=history;
    await validate();
    const result=await buildAuthoringExport(revision.current,{isCurrent:()=>history===revision});
    const row={name,archive:Buffer.from(result.bytes).toString('base64'),project:result.project,
      snapshot_project:revision.current.project,sidecars:revision.current.sidecars,dirty:revision.current.dirty,
      past:revision.past.length,future:revision.future.length};
    checkpoints.push(row);return row;
  }
  const initial=await checkpoint('initial');
  for(const [index,plan] of fixture.operations.entries()){
    if(plan.kind==='undo'||plan.kind==='redo'){
      const previous=history;
      history=plan.kind==='undo'?undoAuthoring(history):redoAuthoring(history);
      dispositions.push({index,kind:plan.kind,disposition:previous===history?'unchanged':'navigated'});
    }else if(plan.kind==='native'){
      const revision=history,snapshot=revision.current,fence=new GraphControlFence<AuthoringHistory>();
      const ticket=fence.begin(revision,plan.node);
      const catalog=fence.acceptCatalog(await request('catalog',{project:snapshot.project,
        sidecars:dirtySidecars(snapshot),node:plan.node}),ticket);
      assert.ok(catalog,'The actual native catalogue must pass the production TypeScript parser');
      const operation=nativeOperation(catalog,plan.operation);
      const editTicket=fence.begin(revision,plan.node,catalog.request_sha256);
      const raw=await request('edit',{project:snapshot.project,sidecars:dirtySidecars(snapshot),node:plan.node,operation});
      const proposed=fence.acceptEdit(raw,editTicket,snapshot.project,operation);
      assert.ok(proposed,'A matching native edit must apply atomically');
      const result=commitAuthoring(history,current=>({...current,project:proposed}),revision);
      assert.equal(result.disposition,plan.expect??'committed');history=result.history;nativeEdits++;
      dispositions.push({index,kind:plan.kind,disposition:result.disposition});
      fence.cancel();assert.equal(fence.acceptEdit(raw,editTicket,snapshot.project,operation),null);rejectedStale++;
    }else{
      const result=commitAuthoring(history,current=>localEdit(current,plan));
      assert.equal(result.disposition,plan.expect??(plan.kind==='noop'?'unchanged':'committed'));
      history=result.history;dispositions.push({index,kind:plan.kind,disposition:result.disposition});
    }
    if(plan.checkpoint)await checkpoint(plan.checkpoint);
  }
  let assertions:Document={input_preserved:true};
  if(!fixture.reopen){
    const beforeUndo=history;
    const final=await buildAuthoringExport(beforeUndo.current,{isCurrent:()=>history===beforeUndo});
    let undoSteps=0,redoSteps=0;
    while(history.past.length){history=undoAuthoring(history);undoSteps++;}
    const fullyUndone=await checkpoint('full_undo');
    assert.equal(fullyUndone.archive,initial.archive,'Full undo must restore the exact original export bytes');
    while(history.future.length){history=redoAuthoring(history);redoSteps++;}
    const fullyRedone=await checkpoint('full_redo');
    assert.equal(fullyRedone.archive,Buffer.from(final.bytes).toString('base64'),
      'Full redo must restore the exact final authored export bytes');
    assert.equal(history.current,beforeUndo.current);
    assert.equal(undoSteps,redoSteps);
    assertions={...assertions,full_undo_export_identity:true,full_redo_export_identity:true,undo_steps:undoSteps,redo_steps:redoSteps,
      ...await historyChecks(history.current)};
  }
  cases.push({name:fixture.name,checkpoints,dispositions,validations,native_edits:nativeEdits,
    rejected_stale_responses:rejectedStale,assertions});
}
assert.equal(JSON.stringify(input),originalInput,'All imported fixtures must remain unchanged');
process.stdout.write(JSON.stringify({schema:'framerudiments.authoring-roundtrip-export.v1',requests,cases}));

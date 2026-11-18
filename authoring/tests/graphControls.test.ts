import test from 'node:test';
import assert from 'node:assert/strict';
import {GRAPH_CONTROLS_SCHEMA,GRAPH_EDIT_SCHEMA,GRAPH_CONTROL_CATALOG_BYTES,GraphControlFence,applyGraphEditResponse,
 moveGraphBinding,parseGraphControlCatalog,parseGraphEditResponse,putGraphBinding,readGraphControlResponse,
 removeGraphBinding,setGraphParameter,type GraphControlCatalog,type GraphEditOperation,type GraphEditResponse,
 type GraphParameterDescriptor} from '../src/graphControls.ts';
import {GRAPH_VALIDATION_SCHEMA} from '../src/graphValidation.ts';
import type {Document} from '../src/model.ts';

const REQUEST='a'.repeat(64),UPDATED='b'.repeat(64),PIN='c'.repeat(64);
const descriptor=(property:string,overrides:Document={}):GraphParameterDescriptor=>({property,label:property,
 type:'number',value:1,default:1,minimum:0,maximum:1,editable:true,bindable:true,group:'filter',active:true,...overrides});
const target=(id='fx',authored_id=id)=>({id,authored_id,type:'filter',label:id});
const named=()=>({id:'motion:basic',label:'Native movement',kind:'named' as const,axes:['x','y','z'],
 motion:{kind:'named',pattern:'bounce'},motion_fields:[
   descriptor('rate',{minimum:.00001,maximum:undefined,group:'motion',bindable:false}),
   descriptor('phase_beats',{minimum:undefined,maximum:undefined,value:0,default:0,group:'motion',bindable:false}),
   descriptor('repeat_clock',{type:'enum',minimum:undefined,maximum:undefined,value:'continuous',default:'continuous',
     choices:['source','continuous'],group:'motion',bindable:false})]});
const binding=(node='fx',id='fade')=>({id,node,property:'filter_mix',motion:{kind:'named',pattern:'bounce'},range:[0,1]});
function catalog():GraphControlCatalog {
 // JSON roundtrip mirrors the native response: optional fields are absent,
 // never present with JavaScript's non-JSON undefined value.
 return JSON.parse(JSON.stringify({schema:GRAPH_CONTROLS_SCHEMA,request_sha256:REQUEST,node:target(),
   targets:[target(),target('other')],parameters:[
     descriptor('filter_mix',{binding_defaults:{range:[0,1]}}),
     descriptor('filter_radius',{value:8,default:0,maximum:128,units:'pixels'}),
     descriptor('iterations',{type:'integer',value:4,default:4,maximum:128,excluded:[1]}),
     descriptor('invert',{type:'boolean',value:false,default:false,minimum:undefined,maximum:undefined,
       choices:[false,true],binding_defaults:{choices:[false,true]}}),
     descriptor('filter_type',{type:'enum',value:'blur',default:'blur',minimum:undefined,maximum:undefined,
       choices:['blur','bloom'],binding_defaults:{choices:['blur','bloom']}}),
     descriptor('style_offset.x',{value:0,default:0,minimum:-65536,maximum:65536,units:'pixels',
       binding_defaults:{range:[-65536,65536]},binding_range:[-65536,65536]}),
     descriptor('gradient_stops.first.color.r',{value:20,default:20,maximum:255,units:'channel_255',
       default_semantics:'configured_base',factory_default:null,constraints:['Keep stable palette stop IDs.']}),
     descriptor('readonly',{editable:false,bindable:false,reason:'Native source identity is preserved.'})
   ],bindings:[{index:0,config:binding()}],motions:[named()],binding_fields:[
     {property:'id',label:'Binding name',type:'text',default:'',max_length:64},
     {property:'axis',label:'Axis',type:'enum',default:'x',choices:['x','y','z']},
     {property:'range',label:'Range',type:'number_pair',default:[-1,1],minimum:-10000,maximum:10000},
     {property:'curve',label:'Curve',type:'enum',default:'linear',choices:['linear','steps','smoothstep']},
     {property:'steps',label:'Steps',type:'integer',default:0,minimum:0,maximum:128,excluded:[1]},
     {property:'invert',label:'Invert',type:'boolean',default:false},
     {property:'weight',label:'Weight',type:'number',default:1,minimum:0,maximum:1},
     {property:'choices',label:'Choices',type:'choices',default:[],min_items:2,max_items:32}
   ],limits:{bindings:64},scope:{rendered:false,gpu_context_opened:false}}));
}
function project():Document {return {schema:'framerudiments.project.v1',audio:{path:'media.wav',sha256:PIN},
 clock:{bpm:120,source_offset_seconds:2},backend:'cpu',layers:[],render_quality:{mode:'exact'},
 effect_graph:{nodes:[{id:'frame',type:'source'},{id:'fx',type:'filter',inputs:['frame'],filter_mix:1},
   {id:'other',type:'opacity',inputs:['fx'],opacity:1}],output:'other'},effect_graph_controls:[binding()]};}
function response(edited=project(),selected=target()):GraphEditResponse {
 const controls=catalog();controls.node=selected;controls.targets=[selected];controls.request_sha256=UPDATED;
 controls.bindings=(edited.effect_graph_controls??[]).flatMap((config:Document,index:number)=>
   config.node===selected.id?[{index,config}]:[]);
 return {schema:GRAPH_EDIT_SCHEMA,request_sha256:REQUEST,project:edited,catalog:controls,
   validation:{schema:GRAPH_VALIDATION_SCHEMA,valid:true,graph_present:true,request_sha256:UPDATED,
     graph_sha256:'d'.repeat(64),sources:[{id:'frame',format:'RGBA8'}],
     nodes:[{id:'frame',type:'source',inputs:[],output:{format:'RGBA8'},min_inputs:0,max_inputs:0}],errors:[],
     scope:{rendered:false,gpu_context_opened:false,all_nodes:true,source_names:true,control_bindings:true}}};
}
const parameterEdit:GraphEditOperation={kind:'set_parameter',property:'filter_mix',value:.5};
function proposedMix():Document {const edited=project();edited.effect_graph.nodes[1].filter_mix=.5;return edited;}

test('catalogue retains native scalar, vector/color paths, applicability and reset semantics as detached data',()=>{
 const raw={...catalog(),private_path:'/private/do-not-display'},parsed=parseGraphControlCatalog(raw)!;
 assert.ok(parsed);assert.equal('private_path' in parsed,false);
 assert.deepEqual(parsed.parameters.find(row=>row.property==='style_offset.x')?.binding_defaults,{range:[-65536,65536]});
 assert.deepEqual(parsed.parameters.find(row=>row.property==='style_offset.x')?.binding_range,[-65536,65536]);
 const color=parsed.parameters.find(row=>row.property==='gradient_stops.first.color.r')!;
 assert.equal(color.units,'channel_255');assert.equal(color.default_semantics,'configured_base');
 assert.equal(color.factory_default,null);assert.deepEqual(color.constraints,['Keep stable palette stop IDs.']);
 assert.deepEqual(parsed.binding_fields.find(row=>row.property==='choices'),
   {property:'choices',label:'Choices',type:'choices',default:[],min_items:2,max_items:32});
 raw.parameters[0].value=0;raw.bindings[0].config.motion.pattern='changed';raw.motions[0].axes[0]='changed';
 assert.equal(parsed.parameters[0].value,1);assert.equal(parsed.bindings[0].config.motion.pattern,'bounce');
 assert.equal(parsed.motions[0].axes[0],'x');
});

test('all admitted native motion kinds and implicit named kind survive without source rewriting',()=>{
 const raw=catalog();delete raw.motions[0].motion.kind;
 for(const kind of ['recording','match','table','composite'] as const)raw.motions.push({
   id:`motion:${kind}`,label:kind,kind,axes:['motion axis'],motion_fields:[],
   motion:{kind,file:{path:`${kind}.json`,sha256:PIN},outside:'hold'}});
 const parsed=parseGraphControlCatalog(raw)!;assert.ok(parsed);assert.equal(parsed.motions.length,5);
 assert.equal('kind' in parsed.motions[0].motion,false);
 assert.deepEqual(parsed.motions[3].motion,raw.motions[3].motion);
 const mismatch=structuredClone(raw);mismatch.motions[1].motion.kind='named';assert.equal(parseGraphControlCatalog(mismatch),null);
 const editable=structuredClone(raw);editable.motions[1].motion_fields=named().motion_fields;assert.equal(parseGraphControlCatalog(editable),null);
});

test('catalogue rejects malformed metadata, duplicated identities and excessive native bounds',()=>{
 const mutate=(change:(raw:GraphControlCatalog)=>void)=>{const raw=catalog();change(raw);return parseGraphControlCatalog(raw);};
 assert.equal(mutate(raw=>raw.targets.push(raw.targets[0])),null);
 assert.equal(mutate(raw=>raw.parameters.push(raw.parameters[0])),null);
 assert.equal(mutate(raw=>raw.bindings.push(raw.bindings[0])),null);
 assert.equal(mutate(raw=>raw.motions.push(raw.motions[0])),null);
 assert.equal(mutate(raw=>raw.binding_fields.push(raw.binding_fields[0])),null);
 assert.equal(mutate(raw=>raw.parameters[0].maximum=NaN),null);
 assert.equal(mutate(raw=>raw.parameters[0].step=0),null);
 assert.equal(mutate(raw=>raw.parameters[4].value='unknown'),null);
 assert.equal(mutate(raw=>raw.parameters[0].binding_defaults={range:[1,1]}),null);
 assert.equal(mutate(raw=>raw.parameters[0].binding_range=[1,1]),null);
 assert.equal(mutate(raw=>raw.bindings[0].index=64),null);
 assert.equal(mutate(raw=>raw.bindings[0].config.node='other'),null);
 assert.equal(mutate(raw=>raw.parameters=Array.from({length:2049},(_,index)=>({...raw.parameters[0],property:`parameter_${index}`}))),null);
 assert.equal(mutate(raw=>raw.targets=[raw.node,...Array.from({length:256},(_,index)=>target(`target_${index}`))]),null);
 assert.equal(mutate(raw=>raw.motions=Array.from({length:4097},(_,index)=>({...raw.motions[0],id:`motion:${index}`}))),null);
 assert.equal(mutate(raw=>raw.motions[0].axes=Array.from({length:1025},(_,i)=>String(i))),null);
 assert.equal(mutate(raw=>(raw.scope as Document).rendered=true),null);
 assert.equal(mutate(raw=>raw.node=target('not-in-targets')),null);
 assert.equal(mutate(raw=>raw.motions[0].motion.self=raw.motions[0].motion),null);
});

test('native string bounds count Unicode characters and nested JSON remains bounded',()=>{
 const raw=catalog(),id='🎼'.repeat(128);raw.node=target(id);raw.targets=[raw.node];raw.bindings=[];
 assert.ok(parseGraphControlCatalog(raw));raw.node.id+='x';assert.equal(parseGraphControlCatalog(raw),null);
 const deep=catalog();let nested:Document=deep.motions[0].motion;
 for(let i=0;i<41;i++){nested.child={};nested=nested.child;}
 assert.equal(parseGraphControlCatalog(deep),null);
 const invalid=catalog();invalid.motions[0].motion.date=new Date();assert.equal(parseGraphControlCatalog(invalid),null);
 const excessive={...catalog(),unknown:'🎼'.repeat(GRAPH_CONTROL_CATALOG_BYTES/4)};
 assert.equal(parseGraphControlCatalog(excessive),null);
});

test('parameter operations follow native primitive types and offered enum choices without patching the catalogue',()=>{
 const native=catalog(),before=structuredClone(native);
 assert.deepEqual(setGraphParameter(native,'style_offset.x',65536),{kind:'set_parameter',property:'style_offset.x',value:65536});
 assert.deepEqual(setGraphParameter(native,'invert',true),{kind:'set_parameter',property:'invert',value:true});
 assert.deepEqual(setGraphParameter(native,'filter_type','bloom'),{kind:'set_parameter',property:'filter_type',value:'bloom'});
 assert.throws(()=>setGraphParameter(native,'filter_mix',Infinity),/type/);
 assert.throws(()=>setGraphParameter(native,'iterations',2.5),/type/);
 assert.throws(()=>setGraphParameter(native,'iterations',1),/excluded/);
 assert.throws(()=>setGraphParameter(native,'filter_radius',129),/bounds/);
 assert.throws(()=>setGraphParameter(native,'filter_type','custom'),/available/);
 assert.throws(()=>setGraphParameter(native,'readonly',0),/identity/);
 assert.throws(()=>setGraphParameter(native,'shader_path','other.glsl'),/editable/);
 assert.deepEqual(native,before);
});

test('numeric bindings preserve source selection, wider native ranges and advertised field contracts',()=>{
 const native=catalog(),settings={id:'offset',axis:'y',range:[-65536,65536],curve:'smoothstep',steps:2};
 const operation=putGraphBinding(native,{index:null,property:'style_offset.x',motion_id:'motion:basic',settings,
   motion_settings:{rate:.5,phase_beats:-2,repeat_clock:'source'}});
 assert.equal(operation.kind,'put_binding');if(operation.kind!=='put_binding')return;
 settings.range[0]=0;assert.deepEqual(operation.settings.range,[-65536,65536]);
 assert.deepEqual(operation.motion_settings,{rate:.5,phase_beats:-2,repeat_clock:'source'});
 const build=(settings:Document)=>putGraphBinding(native,{index:null,property:'filter_mix',motion_id:'motion:basic',settings});
 assert.throws(()=>build({range:[-10001,10001]}),/bounds/);
 assert.throws(()=>build({range:[1,0]}),/increasing/);
 assert.throws(()=>build({axis:3}),/axis/);assert.throws(()=>build({axis:'unknown'}),/axis/);
 assert.throws(()=>build({steps:1}),/excludes/);assert.throws(()=>build({steps:true}),/type/);
 assert.throws(()=>build({weight:2}),/bounds/);assert.throws(()=>build({curve:'external-function'}),/available/);
 assert.throws(()=>build({id:'🎼'.repeat(65)}),/length/);assert.ok(build({id:'🎼'.repeat(64),axis:2}));
 assert.throws(()=>build({motion:{kind:'recording',file:{path:'new.json',sha256:PIN}}}),/Only native/);
 assert.throws(()=>putGraphBinding(native,{index:null,property:'filter_mix',motion_id:'unknown',settings:{}}),/source/);
 assert.throws(()=>putGraphBinding(native,{index:63,property:'filter_mix',motion_id:'motion:basic',settings:{}}),/selected/);
 native.parameters.push(descriptor('time_seconds',{maximum:1e8,binding_range:[-10000,10000]}));
 assert.deepEqual(setGraphParameter(native,'time_seconds',1e8),{kind:'set_parameter',property:'time_seconds',value:1e8});
 assert.throws(()=>putGraphBinding(native,{index:null,property:'time_seconds',motion_id:'motion:basic',
   settings:{range:[0,1e8]}}),/bounds/);
});

test('categorical bindings require ordered distinct admitted values and the native combination contract',()=>{
 const native=catalog(),build=(settings:Document,property='filter_type')=>putGraphBinding(native,
   {index:0,property,motion_id:'motion:basic',settings});
 const operation=build({choices:['bloom','blur'],combine:'set',weight:1});
 assert.equal(operation.kind,'put_binding');if(operation.kind==='put_binding')assert.deepEqual(operation.settings.choices,['bloom','blur']);
 assert.ok(build({choices:[true,false]},'invert'));
 for(const settings of [{choices:['blur']},{choices:['blur','blur']},{choices:['blur','external']},
   {choices:['blur','bloom'],combine:'add'},{choices:['blur','bloom'],weight:.5},
   {choices:['blur','bloom'],range:[0,1]}])assert.throws(()=>build(settings),/categorical|Categorical/);
 assert.throws(()=>build({choices:['blur','bloom']},'filter_mix'),/Numeric/);
});

test('only offered named-motion clock fields are editable; admitted recorded configurations stay opaque',()=>{
 const native=catalog();native.motions.push({id:'motion:recorded',label:'Recorded',kind:'recording',axes:['left'],
   motion:{kind:'recording',file:{path:'recorded.json',sha256:PIN}},motion_fields:[]});
 const build=(motion_id:string,motion_settings:Document)=>putGraphBinding(native,
   {index:0,property:'filter_mix',motion_id,settings:{axis:0},motion_settings});
 assert.ok(build('motion:recorded',{}));
 assert.throws(()=>build('motion:recorded',{rate:2}),/preserved/);
 assert.throws(()=>build('motion:basic',{file:{path:'new.json',sha256:PIN}}),/Only named/);
 assert.throws(()=>build('motion:basic',{rate:0}),/bounds/);
 assert.throws(()=>build('motion:basic',{repeat_clock:'wall'}),/available/);
 assert.throws(()=>build('motion:basic',{interpolate:'yes'}),/read-only/);
});

test('binding ordering addresses selected target rows without rewriting unrelated positions',()=>{
 const native=catalog();native.bindings.push({index:3,config:binding('fx','second')});
 assert.deepEqual(moveGraphBinding(native,3,-1),{kind:'move_binding',index:3,direction:-1});
 assert.throws(()=>moveGraphBinding(native,0,-1),/outside/);assert.throws(()=>moveGraphBinding(native,3,1),/outside/);
 assert.deepEqual(removeGraphBinding(native,3),{kind:'remove_binding',index:3});
 assert.throws(()=>removeGraphBinding(native,1),/selected/);
 const before=project();before.effect_graph_controls.push(binding('other','unrelated'),binding('fx','second'));
 const edited=structuredClone(before);[edited.effect_graph_controls[0],edited.effect_graph_controls[2]]=
   [edited.effect_graph_controls[2],edited.effect_graph_controls[0]];
 const result=applyGraphEditResponse(before,response(edited),REQUEST,{kind:'move_binding',index:2,direction:-1});
 assert.deepEqual(result.effect_graph_controls,edited.effect_graph_controls);
 assert.deepEqual(result.effect_graph_controls[1],before.effect_graph_controls[1]);
});

test('validated parameter proposals preserve source clocks, paths, bindings and all original non-graph data',()=>{
 const before=project(),frozen=structuredClone(before),native=response(proposedMix());
 const next=applyGraphEditResponse(before,native,REQUEST,parameterEdit);
 assert.equal(next.effect_graph.nodes[1].filter_mix,.5);assert.deepEqual(before,frozen);
 for(const key of ['audio','clock','backend','layers','render_quality','effect_graph_controls'])assert.deepEqual(next[key],before[key]);
 next.clock.bpm=1;next.effect_graph.nodes[1].filter_mix=.9;
 assert.equal(before.clock.bpm,120);assert.equal(native.project.effect_graph.nodes[1].filter_mix,.5);
});

test('edit admission requires matching original and updated digests plus native full validation',()=>{
 const valid=response(proposedMix());assert.ok(parseGraphEditResponse(valid));
 const mismatch=structuredClone(valid);mismatch.catalog.request_sha256=REQUEST;assert.equal(parseGraphEditResponse(mismatch),null);
 const failed=structuredClone(valid);failed.validation.valid=false;failed.validation.errors=[{code:'control_binding',message:'Invalid binding'}];
 assert.equal(parseGraphEditResponse(failed),null);
 assert.throws(()=>applyGraphEditResponse(project(),valid,'0'.repeat(64),parameterEdit),/match/);
 assert.throws(()=>applyGraphEditResponse(project(),valid,REQUEST),/identity and operation/);
 assert.throws(()=>applyGraphEditResponse(project(),valid,undefined,parameterEdit),/identity and operation/);
});

test('native proposals cannot change unrelated fields, graph topology, node parameters or binding tables',()=>{
 const check=(change:(edited:Document)=>void)=>{const edited=proposedMix();change(edited);
   return ()=>applyGraphEditResponse(project(),response(edited),REQUEST,parameterEdit);};
 assert.throws(check(edited=>edited.clock.bpm=60),/read-only project/);
 assert.throws(check(edited=>edited.audio.path='other.wav'),/read-only project/);
 assert.throws(check(edited=>edited.effect_graph.output='fx'),/topology/);
 assert.throws(check(edited=>edited.effect_graph.nodes[1].inputs=['other']),/topology/);
 assert.throws(check(edited=>edited.effect_graph.nodes[2].opacity=.5),/unrelated graph/);
 assert.throws(check(edited=>edited.effect_graph.nodes[1].filter_radius=40),/unrelated parameter/);
 assert.throws(check(edited=>edited.effect_graph_controls=[]),/control bindings/);
 assert.throws(check(edited=>edited.effect_graph.nodes[1].shader_path='new.glsl'),/shader authority/);
});

test('GL time edits retain every shader admission field and never grant shader authority',()=>{
 const before=project(),config=before.effect_graph.nodes[1];Object.assign(config,{type:'transition',
   inputs:['frame','frame'],transition_style:'gl_transition',time_seconds:0,shader_path:'shader.glsl',shader_sha256:PIN,
   trusted_shader:true,shader_passes:[],max_pass_bytes:100000,frame_index:4});
 const operation:GraphEditOperation={kind:'set_parameter',property:'time_seconds',value:2};
 const edited=structuredClone(before);edited.effect_graph.nodes[1].time_seconds=2;
 assert.equal(applyGraphEditResponse(before,response(edited),REQUEST,operation).effect_graph.nodes[1].time_seconds,2);
 for(const [key,value] of Object.entries({shader_path:'other.glsl',shader_sha256:REQUEST,trusted_shader:false,
   shader_passes:[{shader_path:'shader.glsl',shader_sha256:PIN}],max_pass_bytes:200000,frame_index:5})){
   const changed=structuredClone(edited);changed.effect_graph.nodes[1][key]=value;
   assert.throws(()=>applyGraphEditResponse(before,response(changed),REQUEST,operation),/shader authority/);
 }
});

test('expanded chain stage/input edits touch only their stable authored component',()=>{
 const before=project();before.effect_graph.nodes[1]={id:'fx',type:'transition_chain',inputs:['frame','frame'],opacity:1,
   stages:[{id:'fade',transition_style:'dissolve',progress:.2}],
   input_effects:[[{id:'soften',type:'filter',filter_radius:2}],[]]};
 const stage=target('fx::stage::fade','fx'),stageEdit:GraphEditOperation={kind:'set_parameter',property:'progress',value:.5};
 const edited=structuredClone(before);edited.effect_graph.nodes[1].stages[0].progress=.5;
 assert.equal(applyGraphEditResponse(before,response(edited,stage),REQUEST,stageEdit).effect_graph.nodes[1].stages[0].progress,.5);
 const changed=structuredClone(edited);changed.effect_graph.nodes[1].input_effects[0][0].filter_radius=8;
 assert.throws(()=>applyGraphEditResponse(before,response(changed,stage),REQUEST,stageEdit),/unrelated chain/);
 const renamed=structuredClone(edited);renamed.effect_graph.nodes[1].stages[0].id='new';
 assert.throws(()=>applyGraphEditResponse(before,response(renamed,stage),REQUEST,stageEdit),/topology/);
 const input=structuredClone(before);input.effect_graph.nodes[1].input_effects[0][0].filter_radius=3;
 assert.equal(applyGraphEditResponse(before,response(input,target('fx::input::0::soften','fx')),REQUEST,
   {kind:'set_parameter',property:'filter_radius',value:3}).effect_graph.nodes[1].input_effects[0][0].filter_radius,3);
 const opacity=structuredClone(before);opacity.effect_graph.nodes[1].opacity=.5;
 assert.equal(applyGraphEditResponse(before,response(opacity),REQUEST,
   {kind:'set_parameter',property:'opacity',value:.5}).effect_graph.nodes[1].opacity,.5);
});

test('binding edits admit only one selected row operation and retain original pin authority',()=>{
 const before=project();before.effect_graph_controls.push(binding('other','unrelated'));
 const edited=structuredClone(before);edited.effect_graph_controls[0].range=[0,.5];
 const operation:GraphEditOperation={kind:'put_binding',index:0,property:'filter_mix',motion_id:'motion:basic',settings:{range:[0,.5]}};
 assert.deepEqual(applyGraphEditResponse(before,response(edited),REQUEST,operation).effect_graph_controls[0].range,[0,.5]);
 const other=structuredClone(edited);other.effect_graph_controls[1].range=[0,.1];
 assert.throws(()=>applyGraphEditResponse(before,response(other),REQUEST,operation),/unrelated bindings/);
 const graph=structuredClone(edited);graph.effect_graph.nodes[1].filter_mix=.2;
 assert.throws(()=>applyGraphEditResponse(before,response(graph),REQUEST,operation),/graph parameters/);
 const injected=structuredClone(edited);injected.effect_graph_controls[0].motion={kind:'recording',file:{path:'unknown.json',sha256:PIN}};
 assert.throws(()=>applyGraphEditResponse(before,response(injected),REQUEST,operation),/unpinned/);
 const removed=structuredClone(before);removed.effect_graph_controls.splice(0,1);
 assert.deepEqual(applyGraphEditResponse(before,response(removed),REQUEST,{kind:'remove_binding',index:0}).effect_graph_controls,
   [before.effect_graph_controls[1]]);
 const added=structuredClone(before);added.effect_graph_controls.push(binding('fx','new'));
 assert.equal(applyGraphEditResponse(before,response(added),REQUEST,{...operation,index:null}).effect_graph_controls.length,3);
});

test('exact snapshot, selection, undo/redo and sidecar revisions invalidate delayed catalogues and proposals',()=>{
 const fence=new GraphControlFence<object>(),original={project:project(),sidecars:{'owned.json':{version:1}}};
 const first=fence.begin(original,'fx'),second=fence.begin({...original,sidecars:{'owned.json':{version:2}}},'fx');
 assert.equal(fence.acceptCatalog(catalog(),first),null);assert.ok(fence.acceptCatalog(catalog(),second));
 const changedSelection=fence.begin(original,'other');assert.equal(fence.acceptCatalog(catalog(),changedSelection),null);
 const edit=fence.begin(original,'fx',REQUEST);assert.ok(fence.acceptEdit(response(proposedMix()),edit,original.project,parameterEdit));
 assert.equal(fence.acceptEdit(response(proposedMix()),edit,original.project),null);
 const undo=fence.begin(original,'fx',REQUEST);assert.equal(fence.acceptEdit(response(proposedMix()),edit,original.project,parameterEdit),null);
 assert.ok(fence.acceptEdit(response(proposedMix()),undo,original.project,parameterEdit));
 const wrongHash=fence.begin(original,'fx',UPDATED);assert.equal(fence.acceptCatalog(catalog(),wrongHash),null);
 assert.throws(()=>fence.acceptEdit(response(proposedMix()),wrongHash,original.project,parameterEdit),/match/);
 fence.cancel();assert.equal(fence.acceptEdit(response(proposedMix()),wrongHash,original.project,parameterEdit),null);
});

test('response reader limits streamed bytes, cancels oversize bodies and requires JSON transport',async()=>{
 const raw=catalog();assert.deepEqual(await readGraphControlResponse(new Response(JSON.stringify(raw),
   {headers:{'content-type':'Application/JSON; charset=utf-8'}})),raw);
 await assert.rejects(readGraphControlResponse(new Response('<html>error</html>',{headers:{'content-type':'text/html'}})),/invalid response/);
 await assert.rejects(readGraphControlResponse(new Response('{}',{headers:{'content-type':'application/json','content-length':String(12*1024*1024+1)}})),/invalid response/);
 let cancelled=false;const body=new ReadableStream<Uint8Array>({start(controller){controller.enqueue(new Uint8Array(12*1024*1024+1));},
   cancel(){cancelled=true;}});
 await assert.rejects(readGraphControlResponse(new Response(body,{headers:{'content-type':'application/json'}})),/limit/);
 assert.equal(cancelled,true);
 await assert.rejects(readGraphControlResponse(new Response('{',{headers:{'content-type':'application/json'}})),SyntaxError);
});

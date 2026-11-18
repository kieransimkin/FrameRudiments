import test from 'node:test';
import assert from 'node:assert/strict';
import {GRAPH_VALIDATION_SCHEMA,GraphValidationFence,parseGraphValidation,readGraphValidation} from '../src/graphValidation.ts';

const report=()=>({schema:GRAPH_VALIDATION_SCHEMA,valid:true,graph_present:true,
 request_sha256:'a'.repeat(64),graph_sha256:'b'.repeat(64),sources:[{id:'frame',format:'RGBA8'}],
 nodes:[{id:'frame',type:'source',inputs:[],output:{format:'RGBA8'},min_inputs:0,max_inputs:0}],
 errors:[],scope:{rendered:false,gpu_context_opened:false,all_nodes:true,source_names:true,control_bindings:true}});

test('native graph report projection retains bindings and excludes unknown response data',()=>{
 const raw={...report(),private_path:'/private/source',token:'discard'};
 const parsed=parseGraphValidation(raw)!;
 assert.equal(parsed.request_sha256,raw.request_sha256);
 assert.equal(parsed.graph_sha256,raw.graph_sha256);
 assert.equal(parsed.scope.rendered,false);
 assert.equal(JSON.stringify(parsed).includes('/private'),false);
 assert.equal('token' in parsed,false);
 raw.nodes[0].id='changed';assert.equal(parsed.nodes[0].id,'frame');
});

test('edit, undo, redo and stale request completion cannot approve the current draft',()=>{
 const gate=new GraphValidationFence<object>(),original={},edit={},undo=original;
 const first=gate.begin(original),second=gate.begin(edit);
 assert.equal(gate.accept(report(),first,true),null);
 assert.ok(gate.accept(report(),second,true));
 const third=gate.begin(undo);
 assert.equal(gate.accept(report(),second,true),null);
 assert.equal(gate.accept(report(),first,true),null);
 assert.ok(gate.accept(report(),third,true));
 assert.equal(gate.accept(report(),third,false),null);
 gate.cancel();assert.equal(gate.accept(report(),third,true),null);
});

test('semantic rejection remains rejection and malformed approval claims fail closed',()=>{
 const invalid={...report(),valid:false,errors:[{code:'graph_schema',message:'Invalid input count'}]};
 assert.equal(parseGraphValidation(invalid)?.valid,false);
 assert.equal(parseGraphValidation({...invalid,valid:true}),null);
 assert.equal(parseGraphValidation({...report(),valid:false}),null);
 assert.equal(parseGraphValidation({...report(),scope:{...report().scope,rendered:true}}),null);
 assert.equal(parseGraphValidation({...report(),graph_sha256:null}),null);
 assert.equal(parseGraphValidation({...report(),request_sha256:'not a digest'}),null);
 assert.equal(parseGraphValidation({...report(),nodes:[]}),null);
 assert.equal(parseGraphValidation({...report(),graph_present:false,graph_sha256:null}),null);
 assert.ok(parseGraphValidation({...report(),graph_present:false,graph_sha256:null,nodes:[]}));
});

test('native report bounds and port order are enforced before display',()=>{
 assert.equal(parseGraphValidation({...report(),nodes:Array(257).fill(report().nodes[0])}),null);
 assert.equal(parseGraphValidation({...report(),sources:[{id:'x'.repeat(129),format:'RGBA8'}]}),null);
 assert.equal(parseGraphValidation({...report(),errors:[{code:'error',message:'x'.repeat(513)}],valid:false}),null);
 const node={id:'blend',type:'composite',min_inputs:2,max_inputs:32,output:{format:'RGBA8'},
   inputs:[{index:1,role:'layer',format:'RGBA8',source:'frame'}]};
 assert.equal(parseGraphValidation({...report(),nodes:[node]}),null);
 assert.equal(parseGraphValidation({...report(),nodes:[{...node,inputs:[{index:0,role:'layer',format:'RGBA8',source:'frame'}]}]}),null);
});

test('validation response reading bounds streamed bytes and requires JSON',async()=>{
 const valid=new Response(JSON.stringify(report()),{headers:{'Content-Type':'application/json'}});
 assert.deepEqual(await readGraphValidation(valid),report());
 await assert.rejects(readGraphValidation(new Response('not JSON',{headers:{'Content-Type':'text/html'}})),/invalid response/);
 await assert.rejects(readGraphValidation(new Response('x'.repeat(2*1024*1024+1),{headers:{'Content-Type':'application/json'}})),/response limit/);
});

test('native Unicode ID bounds and expanded transition-chain references remain valid reports',()=>{
 const id='🎼'.repeat(128),raw=report();
 const unicode={...raw,sources:[{id,format:'RGBA8'}],nodes:[{...raw.nodes[0],id}]};
 assert.ok(parseGraphValidation(unicode));
 assert.equal(parseGraphValidation({...unicode,sources:[{id:id+'x',format:'RGBA8'}]}),null);
 const expanded={...raw,nodes:[...raw.nodes,{id:'copy',type:'copy',min_inputs:1,max_inputs:1,
   inputs:[{index:0,role:'image',format:'RGBA8',source:'chain::stage::fade'}],output:{format:'RGBA8'}}]};
 assert.ok(parseGraphValidation(expanded));
});

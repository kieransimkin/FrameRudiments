import test from 'node:test';
import assert from 'node:assert/strict';
import {availableEffectSources,connectionIssue,effectInputContract,inspectEffectGraph,layoutEffectGraph,
  withEffectConnection,withEffectInputs,withEffectNode,withEffectSource,withInitialEffectGraph,
  withEffectOutput,type EffectNode} from '../src/graphEditor.ts';

const original={schema:'framerudiments.project.v1',fps:60,
 audio:{source:{path:'unchanged.wav',sha256:'a'.repeat(64)},sample_rate:48000,start_sample:12000,end_sample:60000,repeats:3},
 clock:{bpm:120,authority:'synthetic fixture',status:'synthetic'},
 layers:[{id:'signal',type:'sprite',asset:{path:'unchanged.png',sha256:'b'.repeat(64)}}],
 effects:[{id:'external',engine:'projectm',seed:613}],render_quality:{spatial_scale:2,temporal_samples:2},
 effect_graph_controls:[{id:'fade',node:'editor-opacity-1',property:'opacity',motion:{kind:'named',pattern:'bounce'},range:[0,1]}]};
const graph=(nodes:EffectNode[],output=nodes.at(-1)!.id,extra={})=>({...original,...extra,effect_graph:{nodes,output}});
const sources:EffectNode[]=[{id:'frame',type:'source'},{id:'background',type:'source'},{id:'layer:signal',type:'source'}];

test('fork, merge, matte and transition creation uses explicit ordered connections without changing clocks',()=>{
 const initial=withInitialEffectGraph(original);
 const withProvider=withEffectSource(initial,'layer:signal');
 const fork=withEffectNode(withProvider,'opacity',['layer:signal'],{makeOutput:false});
 assert.equal(fork.effect_graph.output,'frame');
 assert.deepEqual(fork.effect_graph.nodes.at(-1),{id:'editor-opacity-1',type:'opacity',inputs:['layer:signal']});
 const merge=withEffectNode(fork,'composite',['frame','editor-opacity-1']);
 const matte=withEffectNode(merge,'matte',['editor-composite-1','layer:signal']);
 const transition=withEffectNode(matte,'transition',['frame','editor-matte-1']);
 const maskTransition=withEffectNode(transition,'matte_transition',['frame','editor-transition-1','layer:signal']);
 assert.deepEqual(maskTransition.effect_graph.nodes.at(-1),{id:'editor-matte_transition-1',type:'transition',
  inputs:['frame','editor-transition-1','layer:signal'],transition_style:'matte',progress:.5});
 assert.equal(maskTransition.effect_graph.output,'editor-matte_transition-1');
 for(const key of ['audio','clock','layers','effects','render_quality','effect_graph_controls'])
  assert.deepEqual(maskTransition[key],original[key]);
 assert.deepEqual(initial.effect_graph.nodes,[{id:'frame',type:'source'}]);
 assert.equal('effect_graph' in original,false);
 assert.equal(JSON.stringify(maskTransition).includes('pixelType'),false);
 assert.equal(JSON.stringify(maskTransition).includes('inputIndex'),false);
});

test('rewiring one named port preserves sibling branches, parameter data and source references',()=>{
 const before=graph([...sources,{id:'branch',type:'filter',inputs:['layer:signal'],filter_type:'blur',filter_radius:2.25},
  {id:'mix',type:'transition',inputs:['frame','branch','background'],transition_style:'matte',progress:.7,mask_gamma:1.4}],
 'mix',{effect_graph_controls:[{node:'mix',property:'progress',id:'progress-binding'}]});
 const frozen=structuredClone(before);
 const result=withEffectConnection(before,'mix',2,'layer:signal');
 assert.deepEqual(result.effect_graph.nodes.at(-1),{...frozen.effect_graph.nodes.at(-1),inputs:['frame','branch','layer:signal']});
 assert.deepEqual(result.effect_graph.nodes.slice(0,-1),frozen.effect_graph.nodes.slice(0,-1));
 assert.deepEqual(result.effect_graph_controls,frozen.effect_graph_controls);
 assert.deepEqual(before,frozen);
 assert.equal(connectionIssue(before,'mix',2,'layer:signal'),null);
});

test('cycles, self edges, absent endpoints and invalid port indices reject atomically',()=>{
 const before=graph([...sources,{id:'a',type:'copy',inputs:['frame']},{id:'b',type:'copy',inputs:['a']},
  {id:'visible',type:'opacity',inputs:['background']}],'visible');
 const snapshot=JSON.stringify(before);
 for(const [node,port,source,pattern] of [
  ['a',0,'b',/cycle/],['a',0,'a',/cycle/],['b',0,'missing',/Unknown/],['missing',0,'frame',/Unknown/],
  ['a',-1,'frame',/port/],['a',1,'frame',/port/],['a',.5,'frame',/port/],['frame',0,'background',/port/],
 ] as const){
  assert.throws(()=>withEffectConnection(before,node,port,source),pattern);
  assert.match(connectionIssue(before,node,port,source)!,pattern);
  assert.equal(JSON.stringify(before),snapshot);
 }
});

test('variable inputs may be inserted, removed and reordered only within native bounds',()=>{
 const before=graph([...sources,{id:'merge',type:'composite',inputs:['frame','layer:signal'],mode:'screen'},
  {id:'matte',type:'matte',inputs:['merge','background'],matte_channel:'luminance',mask_combine:'subtract'}]);
 const ordered=['layer:signal','frame','background'];
 const reordered=withEffectInputs(before,'merge',ordered);
 ordered[0]='mutated-later';
 assert.deepEqual(reordered.effect_graph.nodes.find((n:EffectNode)=>n.id==='merge').inputs,['layer:signal','frame','background']);
 assert.equal(reordered.effect_graph.nodes.find((n:EffectNode)=>n.id==='merge').mode,'screen');
 const masks=withEffectInputs(reordered,'matte',['merge',...Array(8).fill('background')]);
 assert.equal(masks.effect_graph.nodes.at(-1).inputs.length,9);
 assert.throws(()=>withEffectInputs(masks,'matte',['merge',...Array(9).fill('background')]),/port count/);
 assert.throws(()=>withEffectInputs(masks,'matte',['merge']),/port count/);
 assert.throws(()=>withEffectInputs(masks,'merge',Array(33).fill('frame')),/input ports/);
 assert.throws(()=>withEffectInputs(masks,'merge',[]),/port count/);
 assert.throws(()=>withEffectInputs(masks,'frame',[]),/source/);
 assert.throws(()=>withEffectInputs(masks,'merge',['matte','background']),/cycle/);
 assert.deepEqual(withEffectInputs(reordered,'merge',['frame','layer:signal']).effect_graph.nodes.find((n:EffectNode)=>n.id==='merge').inputs,
  ['frame','layer:signal']);
 assert.deepEqual(before.effect_graph.nodes.at(-1).inputs,['merge','background']);
});

test('multi-input templates never invent missing providers or commit unfinished nodes',()=>{
 const before=withInitialEffectGraph(original),snapshot=JSON.stringify(before);
 for(const kind of ['composite','matte','transition','matte_transition'] as const)
  assert.throws(()=>withEffectNode(before,kind),/choose.*ordered input/);
 assert.throws(()=>withEffectNode(before,'transition',['frame']),/port count/);
 assert.throws(()=>withEffectNode(before,'matte_transition',['frame','frame']),/port count/);
 assert.throws(()=>withEffectNode(before,'composite',['frame','missing']),/Unknown/);
 assert.throws(()=>withEffectNode(before,'copy',['frame'],{makeOutput:'false'} as any),/boolean/);
 assert.equal(JSON.stringify(before),snapshot);
 const repeated=withEffectNode(before,'transition',['frame','frame']);
 assert.deepEqual(repeated.effect_graph.nodes.at(-1).inputs,['frame','frame']);
});

test('provider inventory follows native source names and rejects unavailable sources even off-output',()=>{
 const project={...original,identity:[{},{}],lyric_art:{items:[{}]},captions:{box:[0,0,10,10]},
  word_lyrics:{path:'words.lrc',sha256:'c'.repeat(64)},word_captions:{},word_indicator:{}};
 assert.deepEqual(availableEffectSources(project),['frame','background','layer:signal','identity:0','identity:1',
  'lyric-art:0','caption','word-caption','word-indicator']);
 assert.deepEqual(availableEffectSources({...original,captions:{box:[]}}),['frame','background','layer:signal']);
 assert.equal(availableEffectSources({...project,captions:{}}).includes('caption'),false);
 for(const word_lyrics of [null,{},[],false,''])
  assert.equal(availableEffectSources({...original,captions:{box:[]},word_lyrics}).includes('caption'),false);
 assert.throws(()=>inspectEffectGraph(graph([...sources,{id:'missing',type:'source'}],'frame')),/Unavailable/);
 const initial=withInitialEffectGraph(project);
 assert.throws(()=>withEffectSource(initial,'scene'),/Unavailable/);
 assert.throws(()=>withEffectSource(initial,'frame'),/already in use/);
 assert.throws(()=>withEffectSource(initial,'new-mask-alias'),/Unavailable/);
 const added=withEffectSource(initial,'identity:1');
 assert.equal(added.effect_graph.output,'frame');
 assert.equal(added.effect_graph.nodes.at(-1).id,'identity:1');
});

test('Unicode node and provider bounds match native code-point limits including generated chain IDs',()=>{
 const id='🎛'.repeat(128);
 const before=graph([...sources,{id,type:'copy',inputs:['frame']}],id);
 assert.equal(inspectEffectGraph(before)!.output,id);
 assert.deepEqual(withEffectConnection(before,id,0,'background').effect_graph.nodes.at(-1).inputs,['background']);
 assert.throws(()=>inspectEffectGraph(graph([...sources,{id:id+'x',type:'copy',inputs:['frame']}])),/Invalid/);
 const layerId='🎛'.repeat(122),provider=`layer:${layerId}`;
 const project={...original,layers:[{id:layerId},{id:layerId+'x'}]};
 assert.deepEqual(availableEffectSources(project),['frame','background',provider]);
 assert.equal(withEffectSource(withInitialEffectGraph(project),provider).effect_graph.nodes.at(-1).id,provider);
 const chainId='🎛'.repeat(128-'::stage::fade'.length);
 const chain:EffectNode={id:chainId,type:'transition_chain',inputs:['frame','background'],stages:[{id:'fade'}]};
 assert.ok(inspectEffectGraph(graph([...sources,chain])));
 assert.throws(()=>inspectEffectGraph(graph([...sources,{...chain,id:chainId+'x'}])),/generated node ID.*bound/);
});

test('specialised native ports preserve RGBA8 interpretation and enforce configuration-dependent arity',()=>{
 const examples:[EffectNode,number,number,string[]][]=[
  [{id:'t',type:'transition',transition_style:'matte',inputs:['frame','background','layer:signal']},3,3,['image','image','mask']],
  [{id:'w',type:'spatial_warp',warp_type:'displacement',inputs:['frame','background']},2,2,['image','displacement']],
  [{id:'s',type:'layer_style',style_type:'pattern_overlay',pattern_kind:'texture',inputs:['frame','background']},2,2,['image','texture']],
  [{id:'g',type:'layer_style',style_type:'gradient_overlay',inputs:['frame']},1,1,['image']],
  [{id:'f',type:'procedural_field',inputs:['frame']},1,1,['canvas']],
  [{id:'r',type:'raster_effect',inputs:['frame']},1,1,['image']],
 ];
 for(const [node,min,max,roles] of examples){
  const before=graph([...sources,node]);
  assert.ok(inspectEffectGraph(before));
  const contract=effectInputContract(node);
  assert.deepEqual([contract.min,contract.max,contract.roles],[min,max,roles]);
  const layout=layoutEffectGraph(before)!;
  assert.ok(layout.nodes.at(-1)!.inputs.every(port=>port.pixelType==='RGBA8'&&port.editable));
  assert.throws(()=>withEffectInputs(before,node.id,[]),/port count/);
 }
 assert.throws(()=>inspectEffectGraph(graph([...sources,{id:'w',type:'spatial_warp',warp_type:'wave',inputs:['frame','background']}])),/port count/);
 assert.throws(()=>inspectEffectGraph(graph([...sources,{id:'s',type:'layer_style',style_type:'pattern_overlay',inputs:['frame','background']}])),/port count/);
 assert.throws(()=>inspectEffectGraph(graph([...sources,{id:'unsupported',type:'future_renderer',inputs:['frame']}],'frame')),/unsupported/);
});

test('transition shorthand retains nested matte dependencies without rewriting hidden stage data',()=>{
 const chain:EffectNode={id:'chain',type:'transition_chain',inputs:['frame','layer:signal'],
  stages:[{id:'reveal',transition_style:'matte',matte_input:'background',progress:.4}],
  input_effects:[[{id:'fade',type:'opacity',opacity:.8}],[]],opacity:.9};
 const before=graph([...sources,chain]);
 const view=layoutEffectGraph(before)!;
 assert.deepEqual(view.nodes.at(-1)!.inputs.map(port=>[port.label,port.editable]),
  [['image 1',true],['image 2',true],['stage reveal matte',false]]);
 assert.equal(view.edges.at(-1)!.readOnly,true);
 const changed=withEffectConnection(before,'chain',1,'background');
 assert.deepEqual(changed.effect_graph.nodes.at(-1).stages,chain.stages);
 assert.deepEqual(changed.effect_graph.nodes.at(-1).input_effects,chain.input_effects);
 assert.equal(effectInputContract(chain).variable,false);
 assert.throws(()=>withEffectInputs(before,'chain',['frame','background','layer:signal']),/one stage/);
 assert.throws(()=>withEffectConnection(before,'chain',2,'frame'),/input port/);
 const cycle=graph([...sources,{...chain,stages:[{id:'reveal',transition_style:'matte',matte_input:'later'}]},
  {id:'later',type:'copy',inputs:['chain']}],'frame');
 assert.throws(()=>inspectEffectGraph(cycle),/cycle/);
 assert.throws(()=>inspectEffectGraph(graph([...sources,{...chain,stages:[{id:'reveal',transition_style:'matte',matte_input:'absent'}]}],'frame')),/Unknown/);
});

test('expanded chain bounds and generated names are checked before an edit can be committed',()=>{
 const chain:EffectNode={id:'chain',type:'transition_chain',inputs:['frame','background'],stages:[{id:'fade'}]};
 assert.throws(()=>inspectEffectGraph(graph([...sources,chain,{id:'chain::stage::fade',type:'copy',inputs:['frame']}])),/collision/);
 const large=graph([...sources,...Array.from({length:127},(_,i)=>({...chain,id:`chain-${i}`}))],'frame');
 assert.throws(()=>inspectEffectGraph(large),/Expanded.*limit/);
 const exact=graph([...sources,...Array.from({length:126},(_,i)=>({...chain,id:`chain-${i}`})),
  {id:'last',type:'copy',inputs:['frame']}],'frame');
 assert.equal(inspectEffectGraph(exact)!.nodes.length,130);
 assert.throws(()=>withEffectNode(exact,'copy',['frame'],{makeOutput:false}),/Expanded.*limit/);
});

test('output selection cannot activate an invalid or unknown disconnected subgraph',()=>{
 const before=graph([...sources,{id:'a',type:'opacity',inputs:['frame']},{id:'b',type:'copy',inputs:['background']}],'a');
 const changed=withEffectOutput(before,'b');
 assert.equal(changed.effect_graph.output,'b');
 assert.equal(before.effect_graph.output,'a');
 assert.throws(()=>withEffectOutput(before,'none'),/Unknown/);
 const invalid=graph([...sources,{id:'a',type:'copy',inputs:['frame']},{id:'b',type:'raster_effect',inputs:[]}],'a');
 assert.throws(()=>withEffectOutput(invalid,'b'),/port count/);
});

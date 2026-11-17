import test from 'node:test';
import assert from 'node:assert/strict';
import {inspectEffectGraph,layoutEffectGraph,withInitialEffectGraph,withEffectNode,
  withEffectOpacity,withEffectProgress,withEffectBlurRadius,withEffectOutput,
  withoutTerminalEffectNode} from '../src/graphEditor.ts';
const base={schema:'framerudiments.project.v1',audio:{source:{path:'song.wav',sha256:'a'.repeat(64)},
 sample_rate:48000,end_sample:48000},layers:[{id:'voice',type:'rudiment'}]};
const withGraph=(nodes:any[],output:string)=>({...base,effect_graph:{nodes,output}});

test('graph initialisation and unary editing preserve original source identities',()=>{
 const initial=withInitialEffectGraph(base);
 assert.equal(initial.effect_graph.output,'frame');
 assert.equal(base.effect_graph,undefined);
 const first=withEffectNode(initial,'filter');
 assert.deepEqual(first.effect_graph.nodes[1],{id:'editor-filter-1',type:'filter',inputs:['frame'],filter_type:'blur',filter_radius:1.5});
 const second=withEffectNode(first,'opacity');
 const third=withEffectOpacity(withEffectBlurRadius(second,'editor-filter-1',2),'editor-opacity-1',0.35);
 assert.equal(third.effect_graph.nodes[1].filter_radius,2);
 assert.equal(third.effect_graph.nodes[2].opacity,0.35);
 assert.equal(third.audio.source.sha256,base.audio.source.sha256);
 assert.equal(initial.effect_graph.nodes.length,1);
 assert.equal(withoutTerminalEffectNode(third,'editor-opacity-1').effect_graph.output,'editor-filter-1');
});

test('layout displays separate input ports and preserves fork identities',()=>{
 const graph=withGraph([
  {id:'frame',type:'source'},
  {id:'a',type:'copy',inputs:['frame']},
  {id:'b',type:'filter',inputs:['frame'],filter_type:'blur'},
  {id:'mix',type:'transition',inputs:['a','b'],progress:.5},
 ],'mix');
 const view=layoutEffectGraph(graph)!;
 assert.equal(view.edges.length,4);
 assert.deepEqual(view.nodes.find(n=>n.id==='mix')!.inputs.map(p=>p.label),['from','to']);
 assert.equal(view.nodes.find(n=>n.id==='mix')!.isOutput,true);
 assert.equal(new Set(view.nodes.map(n=>n.id)).size,4);
 assert(view.width>400 && view.height>100);
 assert.equal(inspectEffectGraph(graph)!.output,'mix');
});

test('existing 3-input matte transition and unsupported specialised nodes are displayed without rewriting',()=>{
 const graph=withGraph([
  {id:'frame',type:'source'}, {id:'mask',type:'source'},
  {id:'key',type:'chroma_key',inputs:['frame'],key_color:'#00ff00'},
  {id:'mix',type:'transition',inputs:['frame','key','mask'],transition_style:'matte',progress:.5},
 ],'mix');
 const view=layoutEffectGraph(graph)!;
 assert.deepEqual(view.nodes.find(n=>n.id==='mix')!.inputs.map(x=>x.label),['from','to','matte']);
 assert.deepEqual(withEffectProgress(graph,'mix',.75).effect_graph.nodes[3].progress,.75);
 assert.deepEqual(graph.effect_graph.nodes[3].progress,.5);
});

test('invalid graph structures and port references fail without silent rewrite',()=>{
 assert.throws(()=>inspectEffectGraph(withGraph([{id:'a',type:'copy',inputs:['missing']}],'a')),/Unknown/);
 assert.throws(()=>inspectEffectGraph(withGraph([{id:'a',type:'copy',inputs:['a']}],'a')),/cycle/);
 assert.throws(()=>inspectEffectGraph(withGraph([{id:'a',type:'source'},{id:'a',type:'source'}],'a')),/duplicate/);
 assert.throws(()=>inspectEffectGraph(withGraph([{id:'a',type:'source'},{id:'b',type:'transition',inputs:['a']}],'b')),/port count/);
 assert.throws(()=>inspectEffectGraph(withGraph([{id:'a',type:'source'}],'absent')),/output/);
 assert.throws(()=>withEffectNode(base,'opacity'),/Create/);
 assert.throws(()=>withInitialEffectGraph(withInitialEffectGraph(base)),/already exists/);
 assert.throws(()=>withEffectNode(withInitialEffectGraph(base),'transition' as any),/Unsupported/);
});

test('bounds, output and terminal removal are checked against references',()=>{
 const graph=withGraph([{id:'frame',type:'source'}, {id:'o',type:'opacity',inputs:['frame']},
   {id:'render',type:'copy',inputs:['o']}],'render');
 assert.throws(()=>withEffectOpacity(graph,'o',NaN),/finite/);
 assert.throws(()=>withEffectOpacity(graph,'o',1.01),/finite/);
 assert.throws(()=>withEffectBlurRadius(graph,'o',2),/filter node/);
 assert.throws(()=>withEffectProgress(graph,'o',.5),/transition/);
 assert.throws(()=>withoutTerminalEffectNode(graph,'o'),/terminal/);
 assert.throws(()=>withoutTerminalEffectNode(graph,'frame'),/terminal/);
 assert.throws(()=>withEffectOutput(graph,'absent'),/Unknown/);
 assert.equal(withEffectOutput(graph,'o').effect_graph.output,'o');
 assert.equal(withoutTerminalEffectNode(graph,'render').effect_graph.output,'o');
 const bound={...graph,effect_graph_controls:[{node:'render',property:'opacity'}]};
 assert.throws(()=>withoutTerminalEffectNode(bound,'render'),/terminal/);
});

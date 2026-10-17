import test from 'node:test';
import assert from 'node:assert/strict';
import {cueNameValid, editTiming, expandTiming, formatStamp, layerKeyframes, parseCueSheet,
  parseLrc, projectTiming, renameCue, serializeLrc, withKeyframe, withLayerRoute, withPreset} from '../src/model.ts';
const project = {schema:'framerudiments.project.v1', audio:{sample_rate:48000, start_sample:48000,
  end_sample:144000, repeats:2},layers:[{id:'haze',type:'rudiment',renderer:'particles',style:{seed:4}}],
  effects:[{id:'gpu',engine:'butterchurn'}]};
test('source clock and repeated excerpt use distinct output positions',()=>{
  assert.deepEqual(projectTiming(project), {start:1,end:3,slice:2,repeats:2,duration:4,rate:48000});
  const events = parseLrc('[00:01.200]hello\n[00:02.400]again\n','line');
  const shown = expandTiming(events,project);
  assert.deepEqual(shown.map(x=>x.start),[.2,1.4,2.2,3.4].map(x=>Number(x.toFixed(10))));
  const edited = editTiming(events,{...shown[2],start:2.7},project,'line');
  assert.equal(edited[0].start,1.7);
  assert.equal(expandTiming(edited,project)[0].start,0.7);
});
test('LRC preserves Unicode, leading clears and source seconds',()=>{
  const events=parseLrc('[offset:100]\n[00:00.000]hello\n[00:00.750]été ❤️\n[00:01.250]\n','line');
  assert.equal(events[1].label,'été ❤️');
  assert.equal(events[0].start,0.1);
  assert.match(serializeLrc(events),/\[00:00.850\]été ❤️/);
  assert.equal(formatStamp(61.007),'[01:01.007]');
});
test('cue names use established section conventions and CD CUE stays readable',()=>{
  assert(cueNameValid(renameCue('','DROP','VERSE 2')));
  assert(!cueNameValid('[FAKE: CHORUS]'));
  const cue=parseCueSheet('TRACK 01 AUDIO\nTITLE "Intro"\nINDEX 01 00:01:15');
  assert.equal(cue[0].start,1.2);
  assert.throws(()=>editTiming([{id:'cue-0',start:1.2,label:'[DROP: OK]'}],
     {id:'cue-0@0',start:.5,label:'[FAKE: NO]'},project,'cue'));
});
test('keyframes apply valid sorted normalized values without editing input',()=>{
  const first=withKeyframe(project,'layer:haze','opacity',{time:1,value:.4});
  const second=withKeyframe(first,'layer:haze','opacity',{time:0,value:.8});
  assert.equal(project.layer_animations,undefined);
  assert.deepEqual(layerKeyframes(second)[0].keys.map(k=>k.time),[0,1]);
  assert.throws(()=>withKeyframe(second,'layer:haze','opacity',{time:5,value:.4}));
});
test('GPU routing is declarative and rejects unknown sources',()=>{
  const edited=withLayerRoute(project,'layer:haze','first_pips',true,32);
  assert.equal(edited.visualiser_routing.elements['layer:haze'].feed.duration_pips,32);
  assert.equal(project.visualiser_routing,undefined);
  assert.throws(()=>withLayerRoute(project,'layer:unknown','never',true));
});
test('style presets preserve motion and recording identity',()=>{
  const updated=withPreset(project,'layer:haze',{
   schema:'framerudiments.authoring-style-preset.v1',style:{seed:22,glow:.3}});
  assert.equal(updated.layers[0].style.seed,22);
  assert.equal(project.layers[0].style.seed,4);
});
import {graphPreset,withGraphPreset,withStageInjection} from '../src/model.ts';
test('stage injections and reusable effect graphs preserve known layer ordering',()=>{
 const base={...project,effects:[{engine:'butterchurn'},{engine:'projectm'}]};
 const routed=withLayerRoute(base,'layer:haze','continuous',false);
 const injected=withStageInjection(routed,'layer:haze',1,true);
 assert.deepEqual(injected.visualiser_stage_injections[0].elements,['layer:haze']);
 const restored=withGraphPreset(routed,graphPreset(injected));
 assert.deepEqual(restored.visualiser_stage_injections,injected.visualiser_stage_injections);
 assert.throws(()=>withGraphPreset(routed,{schema:'framerudiments.authoring-effect-graph.v1',
   layer_masks:{'layer:haze':{source:'layer:unknown'}}}));
});

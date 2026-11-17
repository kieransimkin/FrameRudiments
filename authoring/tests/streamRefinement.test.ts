import test from 'node:test';
import assert from 'node:assert/strict';
import {StoppedCursor,observeTimelinePlayback} from '../src/streamRefinement.ts';

test('latest stopped frame wins after scrub settling, with no duplicate pauses',()=>{
  const state=new StoppedCursor(0,0);
  for(let n=1;n<=1000;n++)assert.equal(state.observe(n,false,n),'seek');
  assert.equal(state.observe(1000,false,1119),null);
  assert.equal(state.observe(1000,false,1120),'pause');
  for(let t=1121;t<1500;t++)assert.equal(state.observe(1000,false,t),null);
});
test('a stationary callback during actual audio playback must never fake a stop',()=>{
  const state=new StoppedCursor(9,0);
  assert.equal(state.observe(9,true,1),'play');
  for(let t=2;t<10000;t+=100)assert.equal(state.observe(9,true,t),null);
  assert.equal(state.observe(10,false,10000),'seek');
  assert.equal(state.observe(10,false,10120),'pause');
  assert.equal(state.observe(10,true,10121),'play');
});
test('initial paused mount refines exact current frame and boundaries are validated',()=>{
  const state=new StoppedCursor(400,100);
  assert.equal(state.observe(400,false,219),null);
  assert.equal(state.observe(400,false,220),'pause');
  assert.throws(()=>state.observe(-1,false,300));
  assert.throws(()=>state.observe(0,false,NaN));
});
test('native audio capture observes pause/play/ended and removes all listeners',()=>{
  const audio={paused:true,ended:false,error:null};
  const root=new EventTarget();
  const element=Object.assign(root,{querySelector:()=>audio});
  const states:boolean[]=[];
  const cleanup=observeTimelinePlayback(element as unknown as Element,state=>states.push(state));
  audio.paused=false;root.dispatchEvent(new Event('play'));
  audio.paused=true;root.dispatchEvent(new Event('pause'));
  audio.paused=false;audio.ended=true;root.dispatchEvent(new Event('ended'));
  cleanup();audio.ended=false;root.dispatchEvent(new Event('play'));
  assert.deepEqual(states,[false,true,false,false]);
});

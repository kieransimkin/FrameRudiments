import test from 'node:test';
import assert from 'node:assert/strict';
import {PresentationFence,previewFrame,validFrameUrl} from '../src/streamAdaptive.ts';

test('1000 seek intents fence reordered completions and image decode',()=>{
 const gate=new PresentationFence(0),requests:{seq:number,frame:number}[]=[];
 for(let n=0;n<1000;n++){
   const frame=n*137%1001;requests.push({seq:gate.request(frame),frame});
 }
 for(const row of requests.slice(0,-1))assert.equal(gate.accepts(row.seq,row.frame),false);
 const last=requests.at(-1)!;assert(gate.accepts(last.seq,last.frame));
 gate.request(last.frame);assert(!gate.accepts(last.seq,last.frame));
});
test('frame clock uses absolute output position and clamps final frame',()=>{
 assert.equal(previewFrame(1.5,60,3),90);
 assert.equal(previewFrame(3,60,3),179);
 for(const x of [-1,NaN,Infinity])assert.throws(()=>previewFrame(x,60,3));
 assert.throws(()=>new PresentationFence(.5));
});
test('frame URLs are restricted to the active local stream',()=>{
 assert(validFrameUrl('/api/stream/frame?session=one&frame=1','one'));
 assert(!validFrameUrl('/api/stream/frame?session=other','one'));
 assert(!validFrameUrl('https://example.com/api/stream/frame?session=one','one'));
 assert(!validFrameUrl('//example.com/api/stream/frame?session=one','one'));
 assert(!validFrameUrl('javascript:alert(1)','one'));
});

import test from 'node:test';
import assert from 'node:assert/strict';
import {pendingConnectionText,selectGraphPort} from '../src/graphInteraction.ts';

const draft=()=>({effect_graph:{nodes:[{id:'frame',type:'source'},{id:'a',type:'copy',inputs:['frame']},
 {id:'b',type:'opacity',inputs:['frame']}],output:'b'}});
test('direct connections work in both endpoint orders using the same port index',()=>{
 const project=draft();
 const start=selectGraphPort(project,null,{kind:'output',node:'a'});
 const end=selectGraphPort(project,start.pending,{kind:'input',node:'b',index:0});
 assert.deepEqual(end.connection,{source:'a',target:'b',index:0});assert.equal(end.pending,null);
 const backwards=selectGraphPort(project,null,{kind:'input',node:'b',index:0});
 assert.deepEqual(selectGraphPort(project,backwards.pending,{kind:'output',node:'a'}).connection,end.connection);
 assert.deepEqual(project.effect_graph.nodes[2].inputs,['frame']);
});
test('invalid direct wire is retained as a gesture without mutating the draft',()=>{
 const project=draft(),start=selectGraphPort(project,null,{kind:'output',node:'b'});
 const failed=selectGraphPort(project,start.pending,{kind:'input',node:'b',index:0});
 assert.ok(failed.error);assert.equal(failed.connection,null);assert.equal(failed.pending,start.pending);
 assert.deepEqual(project.effect_graph.nodes[2].inputs,['frame']);
});
test('an edit or undo discards stale endpoints and same-port activation cancels',()=>{
 const before=draft(),after=draft();
 const pending=selectGraphPort(before,null,{kind:'output',node:'a'}).pending;
 const next=selectGraphPort(after,pending,{kind:'input',node:'b',index:0});
 assert.equal(next.connection,null);assert.equal(next.pending?.snapshot,after);
 const cancelled=selectGraphPort(after,next.pending,{kind:'input',node:'b',index:0});
 assert.equal(cancelled.pending,null);
 assert.match(pendingConnectionText(pending),/Escape cancels/);
});
test('sidecar-only history changes clear gestures even when the graph document is unchanged',()=>{
 const project=draft(),before={project,sidecars:{}},after={project,sidecars:{lines:[]}};
 const pending=selectGraphPort(project,null,{kind:'output',node:'a'},before).pending;
 const action=selectGraphPort(project,pending,{kind:'input',node:'b',index:0},after);
 assert.equal(action.connection,null);assert.equal(action.pending?.revision,after);
});

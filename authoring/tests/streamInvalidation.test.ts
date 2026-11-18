import test from 'node:test';
import assert from 'node:assert/strict';
import {nextDraftRevision,invalidationSummary,invalidatesFrame,sameDraftImage} from '../src/streamInvalidation.ts';

test('one page keeps a stable edit client and strictly increasing revisions',()=>{
  const first=nextDraftRevision();
  assert.match(first.client_id,/^[0-9a-f]{32}$/);
  for(let n=1;n<=1000;n++){
    const next=nextDraftRevision();
    assert.equal(next.client_id,first.client_id);
    assert.equal(next.edit_sequence,first.edit_sequence+n);
  }
});
test('receipts show counts, not private dependency contents',()=>{
  const receipt={schema:'framerudiments.preview-invalidation.v1',revision:7,cause:'section_dependencies_changed',
    invalidated_frames:40,affected_sections:[{segment_id:'private-label'}],retained_sections:['b','c'],
    changed_dependencies:['private/file.wav']};
  assert.equal(invalidationSummary(receipt),'Revision 7: 1 section records invalidated · 40 frames cleared · 2 sections retained');
  for(const value of [null,{},'bad',{...receipt,revision:-1},{...receipt,invalidated_frames:Infinity},
    {...receipt,retained_sections:[]}]){
    if(value&&typeof value==='object'&&'schema' in value&&(value as any).retained_sections?.length===0)
      assert(invalidationSummary(value));
    else assert.equal(invalidationSummary(value),null);
  }
});
test('old decoded pixels are hidden on the very render that receives a draft change',()=>{
  const project={layers:[]},sidecars={};
  const image={project,sidecars,refine:true};
  assert(sameDraftImage(image,project,sidecars,true));
  assert(!sameDraftImage(image,{...project},sidecars,true));
  assert(!sameDraftImage(image,project,{},true));
  assert(!sameDraftImage(image,project,sidecars,false));
  assert(!sameDraftImage(null,project,sidecars,true));
});

test('invalidation uses entire half-open old/new ranges, including boundary moves',()=>{
  const receipt={schema:'framerudiments.preview-invalidation.v1',affected_sections:[
    {old_range:[10,20],new_range:[12,20]}]};
  for(const frame of [10,11,12,19])assert(invalidatesFrame(receipt,frame));
  for(const frame of [0,9,20,21])assert(!invalidatesFrame(receipt,frame));
  assert(invalidatesFrame({schema:receipt.schema,affected_sections:[{old_range:[9,9]}]},10));
  assert(invalidatesFrame(null,10));
});

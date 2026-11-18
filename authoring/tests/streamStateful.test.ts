import test from 'node:test';
import assert from 'node:assert/strict';
import {PresentationFence} from '../src/streamAdaptive.ts';
import {currentStatefulEvent,parseStateful,statefulSummary} from '../src/streamStateful.ts';

const snapshot={enabled:true,mode:'verified_prefix_replay',phase:'verifying',requested_frame:19,
  verified_prefix_frames:10,live_next_frame:12,current_request_frames:32,replayed_frames:32,
  max_prefix_frames:1800,max_request_frames:3600,last_reason:null};

test('verified replay diagnostics distinguish reconstruction from target frame rate',()=>{
  const status=parseStateful(snapshot)!;
  assert.equal(statefulSummary(status),
    'Verifying an independent reset for frame 19 · 12 / 20 frames · 32 / 3600 work frames');
  assert.equal(statefulSummary({...status,phase:'replaying'}),
    'Replaying feedback from frame zero for frame 19 · 12 / 20 frames · 32 / 3600 work frames');
  assert.equal(statefulSummary({...status,phase:'ready',verified_prefix_frames:20}),
    'Feedback history verified for frame 19 · 20 verified frames');
  assert.equal(statefulSummary({...status,mode:'bounded_history'}),
    'Temporal history is reconstructed on the authored frame clock.');
  assert.equal(statefulSummary({...status,mode:'independent'}),null);
  assert.equal(statefulSummary({...status,enabled:false}),null);
});

test('invalid or unbounded backend data cannot become replay progress',()=>{
  for(const value of [null,[],{},'bad',{...snapshot,enabled:'true'},
    {...snapshot,mode:'private/path.wav'},{...snapshot,phase:'<img>'},
    {...snapshot,requested_frame:-1},{...snapshot,requested_frame:10_000_000},
    {...snapshot,live_next_frame:NaN},{...snapshot,current_request_frames:Infinity},
    {...snapshot,replayed_frames:1.5},{...snapshot,verified_prefix_frames:1801},
    {...snapshot,live_next_frame:1801},{...snapshot,current_request_frames:3601},
    {...snapshot,max_prefix_frames:0},{...snapshot,max_request_frames:21_000_000}])
    assert.equal(parseStateful(value),null);
  const initial=parseStateful({enabled:true,mode:'verified_prefix_replay',phase:'idle'});
  assert(initial);
  assert.equal(initial.requested_frame,null);
  assert.equal(initial.current_request_frames,0);
  assert.equal(statefulSummary(initial),'Preparing feedback history.');
});

test('failure reasons use fixed user text and never expose source names or backend messages',()=>{
  for(const reason of ['private/song.wav','<script>bad</script>','constructor','__proto__',{},1]){
    const status=parseStateful({...snapshot,phase:'unavailable',last_reason:reason,secret:'private/song.wav'})!;
    assert.equal(status.last_reason,'unknown');
    assert.equal(statefulSummary(status),'Stateful seek unavailable. Reopen the preview or seek earlier.');
    assert(!('secret' in status));
  }
  assert.match(statefulSummary(parseStateful({...snapshot,phase:'unavailable',last_reason:'replay_mismatch'}))!,
    /different pixels after reset/);
  assert.match(statefulSummary(parseStateful({...snapshot,phase:'unavailable',last_reason:'prefix_limit_exceeded'}))!,
    /history limit. Seek earlier/);
  assert.match(statefulSummary(parseStateful({...snapshot,phase:'unavailable',last_reason:'native_runtime_unavailable'}))!,
    /runtime is unavailable/);
});

test('1000 reordered seek diagnostics cannot clear the current presentation',()=>{
  const fence=new PresentationFence(0);
  const events=[];
  for(let i=0;i<1000;i++){
    const frame=(i*137)%1001;
    events.push({type:'stateful-seek-unavailable',cursor_seq:fence.request(frame),frame,
      reason:'prefix_limit_exceeded',stateful:{...snapshot,requested_frame:frame}});
  }
  for(const event of events.slice(0,-1)) assert.equal(currentStatefulEvent(event,fence),null);
  const latest=events.at(-1)!;
  const accepted=currentStatefulEvent(latest,fence)!;
  assert.equal(accepted.phase,'unavailable');
  assert.equal(accepted.requested_frame,latest.frame);
  assert.equal(accepted.last_reason,'prefix_limit_exceeded');
  assert.equal(currentStatefulEvent({...latest,frame:latest.frame+1},fence),null);
  assert.equal(currentStatefulEvent({...latest,stateful:{...snapshot,requested_frame:latest.frame+1}},fence),null);
  // Seeking to the same frame is a new intent, including after an image decode begins.
  fence.request(latest.frame);
  assert.equal(currentStatefulEvent(latest,fence),null);
});

test('only current typed progress may update replay status',()=>{
  const fence=new PresentationFence(19);
  const event={type:'stateful-progress',cursor_seq:0,frame:19,stateful:snapshot};
  assert.equal(currentStatefulEvent(event,fence)?.phase,'verifying');
  for(const value of [null,{...event,type:'frame-ready'},{...event,cursor_seq:NaN},
    {...event,cursor_seq:-1},{...event,frame:-1},{...event,stateful:null}])
    assert.equal(currentStatefulEvent(value,fence),null);
});

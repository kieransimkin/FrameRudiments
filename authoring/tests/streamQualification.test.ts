import test from 'node:test';
import assert from 'node:assert/strict';
import {QUALIFICATION_SCHEMA,QualificationFence,formatBytes,formatRate,frameLabel,
  parseQualification,qualificationReceipt,readQualificationResponse,targetFPS} from '../src/streamQualification.ts';

const distribution={p50:5,p95:12,samples:20};
const summary={schema:QUALIFICATION_SCHEMA,sample_sequence:3,elapsed_ms:2500,target_fps:48,frame_count:300,
  rates:{fresh_fps:40,decoded_fps:48,window_seconds:3,acknowledgement_source:'client_decode_ack',
    fresh_fps_p50:35,fresh_fps_p95:45,decoded_fps_p50:40,decoded_fps_p95:50},
  latency_ms:{render:{...distribution},queue:{...distribution},seek_ack:{...distribution},
    seek_first_frame:{...distribution},seek_decode:{p50:15,p95:25,samples:10}},
  cache:{frames:80,q5_frames:60,bytes:100000,quota_bytes:1000000,hits:100,misses:20,upgrades:60,evictions:2},
  resources:{scope:'preview_server_process',cpu_seconds:.9,rss_bytes:32*1024*1024,gpu_memory_bytes:null}};
const digest='a'.repeat(64);
function receipt(){return {...structuredClone(summary),
  identity:{project_sha256:digest,pipeline_sha256:digest,renderer_abi_sha256:digest,
    profile_ladder_sha256:digest,implementation_sha256:digest},
  versions:{python:'3.12.14',framerudiments:'0.4.33',dancerudiments:'0.3.0',pillow:'13.0.0'},
  segments:[{start_frame:0,end_frame_exclusive:100,generation:1,dependency_sha256:digest},
    {start_frame:100,end_frame_exclusive:300,generation:0,dependency_sha256:digest}],
  transitions:[{at_ms:300,from_profile:'Q5',to_profile:'Q4',reason:'p95_over_budget'}],
  invalidations:[{at_ms:0,cause:'section_dependencies_changed',affected_sections:1,
    invalidated_frames:100,invalidated_bytes:3000,retained_frames:60,revision:1}],
  counters:{commands:120,coalesced_requests:3,stale_results:2,render_errors:0,quota_rejections:0,
    foreground_completed:20,background_completed:60,decoded_acknowledgements:100,repeated_acknowledgements:2},
  bounds:{timing_samples:512,history_events:128,sections:2048},
  measurement:{physical_display:false,production_approval:false,seek_ack_scope:'server_command_processing',
    seek_first_frame_scope:'server_frame_ready',seek_decode_scope:'server_seek_to_decode_ack',
    render_scope:'foreground_service_including_setup_and_cache_publish',rate_scope:'rolling_3s_current_session',
    rate_percentile_scope:'observations_at_least_250ms_apart',cache_scope:'current_indexed_png_bytes',resource_scope:'server_process_only'},
  history_dropped:{transitions:1,invalidations:0},
  release_gates:{exact_change_ci:false,native_engine_matrix:false,physical_gpu:false,encoded_output:false,perceptual_approval:false}};}
const envelope=(qualification:unknown=summary)=>({session:'current-session',cursor_seq:7,frame:70,qualification});

test('manual target accepts every whole FPS in the contract without changing the source FPS',()=>{
  for(let fps=15;fps<=120;fps++)assert.equal(targetFPS(String(fps)),fps);
  assert.equal(targetFPS(' 48 '),48);
  for(const value of ['','14','121','48.5','1e2','Infinity','NaN','60 FPS','-1','+60','60\n15'])
    assert.equal(targetFPS(value),null,value);
});

test('displayed frame and quality are distinct from the requested timeline position',()=>{
  assert.equal(frameLabel(null,81),'Requested frame 81 · awaiting image');
  assert.equal(frameLabel(80,81),'Showing frame 80 · requested 81 · stale preview');
  assert.equal(frameLabel(81,81),'Frame 81 · current');
  assert.equal(formatRate(0),'0.0');assert.equal(formatRate(null),'measuring');
  assert.equal(formatBytes(0),'0 B');assert.equal(formatBytes(null),'not measured');
  assert.equal(formatBytes(32*1024*1024),'32.0 MiB');
});

test('measurement projection preserves distinct rates, coverage and explicitly unmeasured GPU memory',()=>{
  const value=parseQualification(summary)!;
  assert(value);assert.equal(value.rates.fresh_fps,40);assert.equal(value.rates.decoded_fps,48);
  assert.equal(value.cache.q5_frames/value.frame_count,.2);
  assert.equal(value.resources.scope,'preview_server_process');assert.equal(value.resources.gpu_memory_bytes,null);
  assert.equal(value.rates.fresh_fps_p95,45);
  assert.equal(value.latency_ms.seek_decode?.p95,25);
  const empty=structuredClone(summary) as any;
  empty.rates.fresh_fps=null;empty.rates.decoded_fps=0;
  for(const name of ['render','queue','seek_ack','seek_first_frame'])empty.latency_ms[name]={p50:null,p95:null,samples:0};
  assert.equal(parseQualification(empty)?.rates.fresh_fps,null);
  assert.equal(parseQualification(empty)?.rates.decoded_fps,0);
});

test('UI observations ignore arbitrary text and reject impossible numeric measurements',()=>{
  const withSecrets={...structuredClone(summary),song:'DO-NOT-EXPORT',
    rates:{...summary.rates,message:'DO-NOT-EXPORT'},cache:{...summary.cache,path:'DO-NOT-EXPORT'},
    resources:{...summary.resources,command_line:'DO-NOT-EXPORT'}};
  assert(!JSON.stringify(parseQualification(withSecrets)).includes('DO-NOT-EXPORT'));
  for(const change of [
    {schema:'other'}, {sample_sequence:Infinity}, {elapsed_ms:-1}, {target_fps:1}, {frame_count:0},
    {rates:{...summary.rates,fresh_fps:NaN}}, {rates:{...summary.rates,decoded_fps:'60'}},
    {rates:{...summary.rates,acknowledgement_source:'physical_display'}},
    {rates:{...summary.rates,window_seconds:0}}, {rates:{...summary.rates,fresh_fps_p95:-1}},
    {cache:{...summary.cache,q5_frames:81}}, {cache:{...summary.cache,frames:301}},
    {cache:{...summary.cache,hits:1.2}}, {resources:{...summary.resources,scope:'all_processes'}},
    {resources:{...summary.resources,rss_bytes:Infinity}},
    {latency_ms:{...summary.latency_ms,render:{p50:12,p95:5,samples:3}}},
    {latency_ms:{...summary.latency_ms,queue:{p50:0,p95:0,samples:0}}},
    {latency_ms:{...summary.latency_ms,seek_ack:{p50:null,p95:null,samples:1}}},
  ])assert.equal(parseQualification({...summary,...change}),null,JSON.stringify(change));
});

test('current-session observations remain monotonic while the timeline advances',()=>{
  const fence=new QualificationFence(),epoch=fence.open('current-session');
  assert(fence.accept(envelope(),epoch));
  assert(fence.accept({...envelope({...summary,sample_sequence:4}),cursor_seq:500,frame:200},epoch));
  assert.equal(fence.accept(envelope({...summary,sample_sequence:3}),epoch),null);
  assert.equal(fence.accept({...envelope(),session:'old-session'},epoch),null);
  assert.equal(fence.accept({...envelope(),frame:300},epoch),null);
  assert.equal(fence.accept({...envelope(),cursor_seq:-1},epoch),null);
});

test('a replaced or closed stream cannot finish a pending poll or download into the new draft',()=>{
  const fence=new QualificationFence(),old=fence.open('current-session');
  const current=fence.open('next-session');
  assert.equal(fence.accept(envelope({...summary,sample_sequence:999}),old),null);
  assert.equal(fence.current(old,'current-session'),false);
  assert(fence.accept({...envelope(),session:'next-session'},current));
  fence.close();
  assert.equal(fence.current(current,'next-session'),false);
  assert.equal(fence.accept({...envelope(),session:'next-session'},current),null);
  const beforeInvalidation=fence.open('current-session');
  const afterInvalidation=fence.open('current-session');
  assert.equal(fence.accept(envelope(),beforeInvalidation),null);
  assert(fence.accept(envelope(),afterInvalidation));
});

test('complete export preserves identities and bounded evidence while removing tokens and private fields',()=>{
  const input=receipt() as any;
  input.project={title:'DO-NOT-EXPORT'};input.private_path='DO-NOT-EXPORT';
  for(const key of ['identity','versions','bounds','measurement','release_gates','counters'])input[key].secret='DO-NOT-EXPORT';
  input.segments[0].path='DO-NOT-EXPORT';input.transitions[0].message='DO-NOT-EXPORT';
  input.invalidations[0].source='DO-NOT-EXPORT';
  const exported=qualificationReceipt({...envelope(input),private_path:'DO-NOT-EXPORT'},'current-session')!;
  assert(exported);assert.equal(exported.schema,QUALIFICATION_SCHEMA);
  assert.deepEqual(exported.identity,receipt().identity);
  assert.deepEqual(exported.segments,receipt().segments);
  assert.deepEqual(exported.transitions,receipt().transitions);
  assert.deepEqual(exported.invalidations,receipt().invalidations);
  assert.deepEqual(exported.measurement,receipt().measurement);
  assert.deepEqual(exported.history_dropped,{transitions:1,invalidations:0});
  assert(!JSON.stringify(exported).includes('current-session'));
  assert(!JSON.stringify(exported).includes('DO-NOT-EXPORT'));
  input.identity.project_sha256='b'.repeat(64);
  assert.equal((exported.identity as any).project_sha256,digest);
  input.versions.dancerudiments=null;
  assert.equal((qualificationReceipt(envelope(input),'current-session')!.versions as any).dancerudiments,null);
});

test('export refuses missing evidence, secret strings, unsupported schemas and falsely approved live measurements',()=>{
  assert.equal(qualificationReceipt(envelope(receipt()),'other-session'),null);
  assert.equal(qualificationReceipt(envelope(summary),'current-session'),null);
  const changes:((value:any)=>void)[]=[
    row=>{row.schema='other';},row=>{row.identity.project_sha256='private/path';},
    row=>{row.versions.python='/private/python';},row=>{row.versions.python='x'.repeat(65);},
    row=>{row.segments[1].start_frame=101;},row=>{row.segments[1].end_frame_exclusive=299;},
    row=>{row.segments[0].generation=-1;},row=>{row.segments=Array(2049).fill(row.segments[0]);},
    row=>{row.transitions=Array(129).fill(row.transitions[0]);},
    row=>{row.transitions[0].reason='private/path';},row=>{row.transitions[0].to_profile='Q6';},
    row=>{row.invalidations=Array(129).fill(row.invalidations[0]);},
    row=>{row.invalidations[0].cause='lyrics';},row=>{row.counters.commands=Infinity;},
    row=>{row.bounds.history_events=1000000;},row=>{row.measurement.physical_display=true;},
    row=>{row.measurement.seek_first_frame_scope='physical_screen';},row=>{row.history_dropped.invalidations=-1;},
    row=>{row.release_gates.physical_gpu=true;},
  ];
  for(const change of changes){const value=receipt();change(value);assert.equal(qualificationReceipt(envelope(value),'current-session'),null);}
});

test('report response parser requires JSON success and handles UTF-8 split across chunks',async()=>{
  const source=new TextEncoder().encode(JSON.stringify({label:'observed ✓'}));
  const stream=new ReadableStream({start(controller){
    for(const byte of source)controller.enqueue(Uint8Array.of(byte));controller.close();
  }});
  const response=new Response(stream,{headers:{'content-type':'application/json; charset=utf-8'}});
  assert.deepEqual(await readQualificationResponse(response),{label:'observed ✓'});
  for(const response of [new Response('{}',{status:400,headers:{'content-type':'application/json'}}),
    new Response('<html>private error</html>',{headers:{'content-type':'text/html'}}),
    new Response('{}',{headers:{'content-type':'application/json','content-length':String(3*1024*1024)}}),
    new Response('invalid',{headers:{'content-type':'application/json'}})])
    await assert.rejects(readQualificationResponse(response));
});

test('oversized streamed reports are cancelled before JSON parsing',async()=>{
  let cancelled=false;
  const stream=new ReadableStream({start(controller){controller.enqueue(new Uint8Array(2*1024*1024+1));},
    cancel(){cancelled=true;}});
  await assert.rejects(readQualificationResponse(new Response(stream,{headers:{'content-type':'application/json'}})),/size limit/);
  assert.equal(cancelled,true);
});

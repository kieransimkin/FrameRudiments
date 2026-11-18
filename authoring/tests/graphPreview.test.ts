import test from 'node:test';
import assert from 'node:assert/strict';
import {setImmediate as nextTurn} from 'node:timers/promises';
import {NativePreviewController,PREVIEW_IMAGE_BYTES,PREVIEW_PIXELS,PREVIEW_RESPONSE_BYTES,
  TRANSITION_PREVIEW_SCHEMA,parseTransitionPreview,readNativePreviewResponse,samePreviewRequest,
  type NativePreviewRequest,type NativePreviewState,type TransitionPreviewReport} from '../src/graphPreview.ts';

const PIN='a'.repeat(64),OTHER='b'.repeat(64);
// Actual 16x16 RGB Pillow PNG; transport tests do not substitute image decoding evidence.
const PNG='iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAIAAACQkWg2AAAAGUlEQVR4nGOs0AhgIAUwkaR6VMOohiGlAQARhQEQ+TT5QwAAAABJRU5ErkJggg==';
const PNG_SHA='e49ae890fb1d51306d60103327c3b760bcb10bf3a36a94beac55fcc1a3caa974';
const bytes=()=>Uint8Array.from(atob(PNG),character=>character.charCodeAt(0));
const normal=(overrides:Partial<NativePreviewRequest>={}):NativePreviewRequest=>({snapshot:{},
 project:{width:16,height:16,fps:60},sidecars:{},frame:0,quality:'fast',node:null,...overrides});
const selected=(overrides:Partial<NativePreviewRequest>={}):NativePreviewRequest=>
 normal({node:'fade',request_sha256:PIN,...overrides});
function report(request=selected()):TransitionPreviewReport{
 return {schema:TRANSITION_PREVIEW_SCHEMA,request_sha256:request.request_sha256!,
   node:{id:request.node!,authored_id:'chain',label:'Fade stage',type:'transition',transition_style:'dissolve'},
   frame:request.frame,quality:request.quality,width:16,height:16,
   clock:{frame:request.frame,seconds:request.frame/60,source_seconds:1+request.frame/60,beat:-1,source_beat:-1},
   render_quality:{enabled:request.quality==='full',spatial_scale:request.quality==='full'?2:1,
     temporal_samples:request.quality==='full'?4:1,antialias:request.quality==='full'?.5:0,
     antialias_radius:.75,antialias_threshold:.04,antialias_passes:3,
     spatial_method:'Native vectors',temporal_method:'Native source clock',final_method:'Native AA',
     optical_flow:false,gpu_feedback_subframes:false},
   scope:{pipeline:'native_graph_before_sequential_effects',sequential_effects_excluded:false,
     native_temporal_post_effects:true,black_matte:true,production_approved:false},
   images:['from','result','to'].map((role,index)=>({role:role as 'from'|'result'|'to',
     node:index===1?request.node!:`source:${index}`,label:['From','Result','To'][index],width:16,height:16,
     mime:'image/png',png_base64:PNG,sha256:PNG_SHA,pixel_sha256:OTHER}))};
}
const json=(value:unknown,status=200)=>new Response(JSON.stringify(value),{status,headers:{'Content-Type':'application/json; charset=utf-8'}});
const png=()=>new Response(bytes(),{headers:{'Content-Type':'image/png'}});
function deferred<T>(){
 let resolve!:(value:T)=>void,reject!:(reason?:unknown)=>void;
 const promise=new Promise<T>((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};
}
async function until(predicate:()=>boolean):Promise<void>{
 const expires=Date.now()+3000;
 while(!predicate()){if(Date.now()>expires)throw Error('Expected asynchronous preview state was not reached');await nextTurn();}
}
function harness(decode?:(url:string,signal:AbortSignal)=>Promise<void>){
 const states:NativePreviewState[]=[],calls:{path:string;init:RequestInit;reply:ReturnType<typeof deferred<Response>>}[]=[];
 const revoked:string[]=[],created:string[]=[],decoded:string[]=[];
 const timers=new Map<symbol,()=>void>();
 const controller=new NativePreviewController(state=>states.push(state),{
   fetch:(path,init)=>{const reply=deferred<Response>();calls.push({path,init,reply});return reply.promise;},
   decode:async(url,signal)=>{decoded.push(url);if(decode)await decode(url,signal);},
   createObjectURL:()=>{const url=`blob:owned-${created.length}`;created.push(url);return url;},
   revokeObjectURL:url=>revoked.push(url),
   setTimer:callback=>{const handle=Symbol();timers.set(handle,callback);return handle;},
   clearTimer:handle=>{timers.delete(handle as symbol);},
 });
 return {controller,states,calls,revoked,created,decoded,timers,
   last:()=>states.at(-1)!,body:(index:number)=>JSON.parse(calls[index].init.body as string)};
}

test('request identity fences every snapshot, sidecar, target, frame, quality and digest dimension',()=>{
 const a=selected();assert.ok(samePreviewRequest(a,{...a}));
 for(const change of [{snapshot:{}},{project:{...a.project}},{sidecars:{}},{node:'other'},
   {frame:1},{quality:'full' as const},{request_sha256:OTHER}])
   assert.equal(samePreviewRequest(a,{...a,...change}),false);
 assert.equal(samePreviewRequest(null,a),false);
});

test('native transition metadata preserves source clocks and explicit render scope without retaining aliases',()=>{
 const raw=report(),parsed=parseTransitionPreview(raw)!;assert.ok(parsed);
 assert.equal(parsed.clock.beat,-1);assert.equal(parsed.scope.native_temporal_post_effects,true);
 assert.equal(parsed.scope.production_approved,false);assert.equal(parsed.images[1].node,'fade');
 raw.clock.frame=9;raw.node.label='Changed';raw.images[0].label='Changed';
 assert.equal(parsed.clock.frame,0);assert.equal(parsed.node.label,'Fade stage');assert.equal(parsed.images[0].label,'From');
 assert.ok(parseTransitionPreview(report(selected({quality:'full'}))));
 const unicode=report();unicode.node.id='🎼'.repeat(128);unicode.images[1].node=unicode.node.id;
 assert.ok(parseTransitionPreview(unicode));unicode.node.id+='x';assert.equal(parseTransitionPreview(unicode),null);
});

test('native response rejects malformed clocks, authority claims, order, quality evidence and bounds',()=>{
 const rejects=(change:(raw:any)=>void)=>{const raw=report();change(raw);assert.equal(parseTransitionPreview(raw),null);};
 for(const change of [
   (r:any)=>r.schema='other',(r:any)=>r.request_sha256='short',(r:any)=>r.frame=.5,
   (r:any)=>r.clock.frame=1,(r:any)=>r.clock.seconds=NaN,(r:any)=>r.clock.source_seconds=-1,
   (r:any)=>r.clock.beat=Infinity,(r:any)=>r.node.type='filter',(r:any)=>r.node.label='',
   (r:any)=>r.images.reverse(),(r:any)=>r.images.pop(),(r:any)=>r.images[1].node='other',
   (r:any)=>r.images[0].width=18,(r:any)=>r.images[0].mime='image/svg+xml',(r:any)=>r.images[0].sha256='bad',
   (r:any)=>r.images[0].pixel_sha256='bad',(r:any)=>r.images[0].png_base64='AB==',
   (r:any)=>r.images[0].png_base64='AAAA!',(r:any)=>r.images[0].png_base64='',
   (r:any)=>r.scope.black_matte=false,(r:any)=>r.scope.production_approved=true,
   (r:any)=>r.scope.pipeline='replacement_renderer',(r:any)=>r.render_quality.enabled=true,
   (r:any)=>r.render_quality.temporal_samples=9,(r:any)=>r.render_quality.spatial_scale=2,
   (r:any)=>r.render_quality.antialias=1,(r:any)=>r.render_quality.optical_flow=true,
   (r:any)=>r.width=7680,(r:any)=>r.width=17,
 ])rejects(change);
 const tooLarge=report();tooLarge.images[0].png_base64='A'.repeat(Math.ceil(PREVIEW_IMAGE_BYTES/3)*4+4);
 assert.equal(parseTransitionPreview(tooLarge),null);
 const area=report();area.width=area.height=4096;assert.ok(area.width*area.height>PREVIEW_PIXELS);
 assert.equal(parseTransitionPreview(area),null);
});

test('real PNG transport verifies digest, dimensions and exact selected request before exposing payload',async()=>{
 const request=selected({frame:17,quality:'full'});
 const payload=await readNativePreviewResponse(json(report(request)),request);
 assert.equal(payload.images.length,3);assert.deepEqual(payload.images[0].bytes,bytes());
 assert.equal(payload.report?.clock.frame,17);assert.equal(payload.report?.quality,'full');
 assert.equal('images' in payload.report!,false);assert.equal('png_base64' in payload.images[0],false);
 for(const change of [{frame:18},{quality:'fast' as const},{node:'other'},{request_sha256:OTHER},
   {project:{width:18,height:16}}])
   await assert.rejects(readNativePreviewResponse(json(report(request)),{...request,...change}),/invalid/);
});

test('PNG SHA mismatch, malformed PNG and dimensions are rejected independently of metadata claims',async()=>{
 const request=selected();
 const wrong=report();wrong.images[1].sha256=OTHER;
 await assert.rejects(readNativePreviewResponse(json(wrong),request),/invalid/);
 const malformed=report();malformed.images[0].png_base64=btoa('This is not a PNG');
 await assert.rejects(readNativePreviewResponse(json(malformed),request),/invalid/);
 const short=bytes().slice(0,32);
 await assert.rejects(readNativePreviewResponse(new Response(short,{headers:{'content-type':'image/png'}}),normal()),/invalid/);
 const changed=bytes();new DataView(changed.buffer).setUint32(16,18);
 await assert.rejects(readNativePreviewResponse(new Response(changed,{headers:{'content-type':'image/png'}}),normal()),/invalid/);
 const payload=await readNativePreviewResponse(png(),normal());assert.equal(payload.images[0].role,'composition');
 assert.equal(payload.report,null);assert.deepEqual(payload.images[0].bytes,bytes());
});

test('reader rejects excess bytes, bad framing and wrong MIME while cancelling its owned body',async()=>{
 const request=normal();let cancelled=false;
 const stream=new ReadableStream<Uint8Array>({start(c){c.enqueue(new Uint8Array(PREVIEW_IMAGE_BYTES+1));},cancel(){cancelled=true;}});
 await assert.rejects(readNativePreviewResponse(new Response(stream,{headers:{'content-type':'image/png'}}),request),/limit/);
 assert.equal(cancelled,true);
 for(const headers of [{'content-type':'text/html'},{'content-type':'image/png','content-length':'-1'},
   {'content-type':'image/png','content-length':String(PREVIEW_IMAGE_BYTES+1)},
   {'content-type':'image/png','content-length':'1'}] as Record<string,string>[])
   await assert.rejects(readNativePreviewResponse(new Response(bytes(),{headers}),request),/invalid/);
 await assert.rejects(readNativePreviewResponse(new Response('{}',{headers:{'content-type':'application/json',
   'content-length':String(PREVIEW_RESPONSE_BYTES+1)}}),selected()),/invalid/);
 await assert.rejects(readNativePreviewResponse(new Response('{private-source-path',{headers:{'content-type':'application/json'}}),selected()),
   error=>error instanceof Error&&!error.message.includes('private-source-path')&&/invalid/.test(error.message));
});

test('cancellation interrupts an owned streaming body and rejects late reader completion',async()=>{
 const abort=new AbortController();let cancelled=false,started=false;
 const stream=new ReadableStream<Uint8Array>({pull(){started=true;},cancel(){cancelled=true;}});
 const pending=readNativePreviewResponse(new Response(stream,{headers:{'content-type':'image/png'}}),normal(),abort.signal);
 await until(()=>started);abort.abort();await assert.rejects(pending,{name:'AbortError'});assert.equal(cancelled,true);
});

test('1000 scrub requests keep one active render and only the latest pending frame',async()=>{
 const h=harness(),base=normal();h.controller.request(base);assert.equal(h.calls.length,1);
 for(let n=1;n<=1000;n++)h.controller.request({...base,frame:n});
 assert.equal(h.calls.length,1);assert.equal(h.last().status,'loading');assert.equal(h.last().request?.frame,1000);
 assert.equal(h.calls[0].init.signal?.aborted,false);
 h.calls[0].reply.resolve(png());await until(()=>h.calls.length===2);assert.equal(h.body(1).frame,1000);
 h.calls[1].reply.resolve(png());await until(()=>h.last().status==='ready');
 assert.deepEqual(h.states.filter(x=>x.status==='ready').map(x=>x.request?.frame),[1000]);
 assert.equal(h.decoded.length,1);assert.equal(h.calls[1].path,'/api/preview');
 assert.equal(h.calls[1].init.credentials,'same-origin');assert.equal(h.calls[1].init.cache,'no-store');
 assert.equal(h.timers.size,0);h.controller.dispose();assert.deepEqual(h.revoked,h.created);
});

test('quality changes immediately invalidate the old displayed image; identical accepted requests are reused',async()=>{
 const h=harness(),request=normal();h.controller.request(request);h.calls[0].reply.resolve(png());
 await until(()=>h.last().status==='ready');h.controller.request({...request});assert.equal(h.calls.length,1);
 const full={...request,quality:'full' as const};h.controller.request(full);
 assert.equal(h.last().status,'loading');assert.equal(h.last().result,null);assert.deepEqual(h.revoked,['blob:owned-0']);
 await until(()=>h.calls.length===2);assert.equal(h.body(1).quality,'full');h.calls[1].reply.resolve(png());
 await until(()=>h.last().status==='ready');assert.ok(samePreviewRequest(h.last().result!.request,full));
 h.controller.dispose();assert.deepEqual(h.revoked,h.created);
});

test('a changed snapshot during decode prevents the old frame from ever publishing',async()=>{
 const firstDecode=deferred<void>();let decodes=0;
 const h=harness(async()=>{if(++decodes===1)await firstDecode.promise;}),first=normal();
 h.controller.request(first);h.calls[0].reply.resolve(png());await until(()=>h.decoded.length===1);
 const latest={...first,snapshot:{},sidecars:{lines:[{start:0,label:'Changed'}]}};
 h.controller.request(latest);assert.equal(h.last().result,null);assert.equal(h.calls.length,1);
 firstDecode.resolve();await until(()=>h.calls.length===2);assert.deepEqual(h.revoked,['blob:owned-0']);
 h.calls[1].reply.resolve(png());await until(()=>h.last().status==='ready');
 assert.deepEqual(h.states.filter(x=>x.status==='ready').map(x=>x.request?.snapshot),[latest.snapshot]);
 h.controller.dispose();assert.deepEqual(h.revoked,h.created);
});

test('returning to an earlier undo snapshot does not revive an earlier in-flight ticket',async()=>{
 const h=harness(),old=normal(),other={...old,snapshot:{}};
 h.controller.request(old);h.controller.request(other);h.controller.request(old);
 h.calls[0].reply.resolve(png());await until(()=>h.calls.length===2);
 assert.equal(h.created.length,0);h.calls[1].reply.resolve(png());await until(()=>h.last().status==='ready');
 assert.equal(h.states.filter(x=>x.status==='ready').length,1);h.controller.dispose();
});

test('cancel revokes a partially decoded image immediately and drops queued frames even if decode ignores abort',async()=>{
 const decode=deferred<void>(),h=harness(()=>decode.promise),request=normal();
 h.controller.request(request);h.calls[0].reply.resolve(png());await until(()=>h.created.length===1);
 h.controller.request({...request,frame:9});h.controller.cancel();
 assert.equal(h.last().status,'idle');assert.equal(h.calls[0].init.signal?.aborted,true);
 assert.deepEqual(h.revoked,['blob:owned-0']);decode.resolve();await until(()=>h.timers.size===0);
 assert.equal(h.calls.length,1);assert.equal(h.states.some(x=>x.status==='ready'),false);
 assert.deepEqual(h.revoked,h.created);h.controller.dispose();
});

test('disposed controllers ignore fetch completion and a StrictMode replacement can request the same frame',async()=>{
 const old=harness(),fresh=harness(),request=normal();old.controller.request(request);
 const count=old.states.length;old.controller.dispose();assert.equal(old.calls[0].init.signal?.aborted,true);
 fresh.controller.request(request);old.calls[0].reply.resolve(png());fresh.calls[0].reply.resolve(png());
 await until(()=>fresh.last().status==='ready');assert.equal(old.states.length,count);assert.equal(old.created.length,0);
 old.controller.request(request);assert.equal(old.calls.length,1);fresh.controller.dispose();
 assert.deepEqual(fresh.revoked,fresh.created);
});

test('transition responses require all three successful decodes before one atomic presentation',async()=>{
 const second=deferred<void>();let count=0;
 const h=harness(async()=>{if(++count===2)await second.promise;}),request=selected();
 h.controller.request(request);assert.equal(h.calls[0].path,'/api/graph/preview');assert.equal(h.body(0).node,'fade');
 assert.equal('snapshot' in h.body(0),false);assert.equal('request_sha256' in h.body(0),false);
 h.calls[0].reply.resolve(json(report(request)));await until(()=>h.decoded.length===2);
 assert.equal(h.last().status,'loading');assert.equal(h.last().result,null);
 second.resolve();await until(()=>h.last().status==='ready');
 assert.deepEqual(h.last().result?.images.map(x=>x.role),['from','result','to']);
 assert.equal(h.last().result?.report?.node.id,'fade');assert.equal(h.states.filter(x=>x.status==='ready').length,1);
 h.controller.dispose();assert.deepEqual(h.revoked,h.created);
});

test('failed comparison decode releases every created URL and exposes no partial comparison',async()=>{
 let count=0;const h=harness(async()=>{if(++count===2)throw Error('Could not decode image');});
 const request=selected();h.controller.request(request);h.calls[0].reply.resolve(json(report(request)));
 await until(()=>h.last().status==='error');assert.equal(h.last().result,null);
 assert.equal(h.created.length,2);assert.deepEqual(h.revoked,h.created);assert.equal(h.timers.size,0);h.controller.dispose();
});

test('busy and rejected responses use fixed messages, do not loop, and permit explicit retry',async()=>{
 const h=harness(),request=selected();h.controller.request(request);
 h.calls[0].reply.resolve(json({code:'busy',error:'secret file path'},429));await until(()=>h.last().status==='error');
 assert.match(h.last().message,/busy/);assert.equal(h.last().message.includes('secret'),false);assert.equal(h.calls.length,1);
 h.controller.request(request);await until(()=>h.calls.length===2);h.calls[1].reply.resolve(json(report(request)));
 await until(()=>h.last().status==='ready');h.controller.dispose();
 for(const [code,status,expected] of [['preview_limit',400,/limit/],['input_changed',400,/pinned source/i],
   ['graph_invalid',400,/rejected/],['render_failed',500,/could not complete/]] as const)
   await assert.rejects(readNativePreviewResponse(json({code,error:'secret file path'},status),request),expected);
});

test('timeout aborts, revokes partial URLs immediately and cannot later publish the timed-out frame',async()=>{
 const decode=deferred<void>(),h=harness(()=>decode.promise);h.controller.request(normal());
 h.calls[0].reply.resolve(png());await until(()=>h.created.length===1);
 const expire=[...h.timers.values()][0];expire();assert.equal(h.last().status,'error');assert.match(h.last().message,/timed out/);
 assert.equal(h.calls[0].init.signal?.aborted,true);assert.deepEqual(h.revoked,h.created);
 decode.resolve();await until(()=>h.timers.size===0);assert.equal(h.states.some(x=>x.status==='ready'),false);
 assert.deepEqual(h.revoked,h.created);h.controller.dispose();
});

test('stale failures cannot replace the pending current-frame status',async()=>{
 const h=harness(),old=normal(),latest={...old,frame:7};h.controller.request(old);h.controller.request(latest);
 h.calls[0].reply.reject(Error('Old render failed'));await until(()=>h.calls.length===2);
 assert.equal(h.last().status,'loading');assert.equal(h.last().request?.frame,7);
 h.calls[1].reply.resolve(png());await until(()=>h.last().status==='ready');
 assert.equal(h.states.some(x=>x.status==='error'),false);h.controller.dispose();
});

test('invalid identities, oversized projects and oversized draft bodies fail before fetching',async()=>{
 const h=harness();
 for(const request of [normal({frame:-1}),normal({frame:NaN}),normal({quality:'other' as any}),
   selected({request_sha256:undefined}),normal({project:{width:7680,height:7680}})]){
   h.controller.request(request);assert.equal(h.last().status,'error');assert.equal(h.calls.length,0);
 }
 h.controller.request(normal({project:{width:16,height:16,notes:'x'.repeat(2*1024*1024)}}));
 await until(()=>h.last().status==='error');assert.equal(h.calls.length,0);assert.match(h.last().message,/request limit/);
 assert.equal(h.timers.size,0);h.controller.dispose();
});

/** Real HTTP/native preview qualification. Node verifies containers; Python decodes the pixels. */
import {readFileSync} from 'node:fs';
import {Buffer} from 'node:buffer';
import assert from 'node:assert/strict';
import {parseGraphControlCatalog,readGraphControlResponse} from '../src/graphControls.ts';
import {NativePreviewController,readNativePreviewResponse,samePreviewRequest,
  type NativePreviewPayload,type NativePreviewRequest,type NativePreviewResult,
  type PreviewQuality,type TransitionPreviewReport} from '../src/graphPreview.ts';

type Cursor={frame:number;quality:PreviewQuality};
type Case={name:string;project:Record<string,any>;sidecars:Record<string,unknown>;node:string;requests:Cursor[]};
const input=JSON.parse(readFileSync(0,'utf8')) as {origin:string;cases:Case[]};
const origin=new URL(input.origin);
assert.equal(origin.protocol,'http:');assert.ok(['127.0.0.1','localhost','[::1]'].includes(origin.hostname));
assert.equal(origin.origin,input.origin);assert.equal(origin.username,'');assert.equal(origin.password,'');
assert.ok(Array.isArray(input.cases)&&input.cases.length>0&&input.cases.length<=64);
let httpRequests=0;
async function actualFetch(path:string,init:RequestInit):Promise<Response>{
 const headers=new Headers(init.headers);headers.set('Origin',input.origin);httpRequests++;
 return await fetch(new URL(path,input.origin),{...init,headers});
}
function deferred<T>(){
 let resolve!:(value:T)=>void,reject!:(reason?:unknown)=>void;
 const promise=new Promise<T>((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};
}
function wireReceipt(payload:NativePreviewPayload):TransitionPreviewReport{
 assert.ok(payload.report);assert.equal(payload.images.length,3);
 return {...payload.report,images:payload.images.map(image=>{
   assert.ok(image.node);assert.notEqual(image.role,'composition');
   return {role:image.role as 'from'|'result'|'to',node:image.node,label:image.label,
     width:image.width,height:image.height,mime:'image/png' as const,
     png_base64:Buffer.from(image.bytes).toString('base64'),sha256:image.sha256!,pixel_sha256:image.pixel_sha256!};
 })};
}
function runner(base:NativePreviewRequest,holdFirst=false){
 const pending=new Map<string,Blob>();let serial=0;
 const requested:Cursor[]=[],presented:Cursor[]=[],receipts:TransitionPreviewReport[]=[];
 const held=deferred<void>(),release=deferred<void>();
 let awaiting:ReturnType<typeof deferred<NativePreviewResult>>|null=null;
 const controller=new NativePreviewController(state=>{
   if(state.status==='ready'){
     assert.ok(state.result);assert.ok(samePreviewRequest(state.result.request,state.request));
     presented.push({frame:state.result.request.frame,quality:state.result.request.quality});
     awaiting?.resolve(state.result);awaiting=null;
   }else if(state.status==='error'){
     awaiting?.reject(Error(state.message));awaiting=null;
   }
 },{
   fetch:async(path,init)=>{
     assert.equal(path,base.node===null?'/api/preview':'/api/graph/preview');
     const body=JSON.parse(init.body as string),request={...base,frame:body.frame,quality:body.quality};
     const index=requested.length;requested.push({frame:body.frame,quality:body.quality});
     const response=await actualFetch(path,init);
     if(holdFirst&&index===0){held.resolve();await release.promise;}
     const payload=await readNativePreviewResponse(response.clone(),request,init.signal??undefined);
     if(payload.report)receipts.push(wireReceipt(payload));return response;
   },
   createObjectURL:blob=>{assert.equal(blob.type,'image/png');const id=`blob:qualification-${serial++}`;pending.set(id,blob);return id;},
   revokeObjectURL:url=>{assert.ok(pending.has(url),'An image URL must be released exactly once');pending.delete(url);},
   // This deliberately does not impersonate browser Image.decode. The shared
   // reader has verified PNG framing/dimensions/hash; Python checks every pixel.
   decode:async(url,signal)=>{assert.equal(signal.aborted,false);assert.ok(pending.has(url));},
 });
 return {controller,requested,presented,receipts,held,release,pending,
   wait:()=>{assert.equal(awaiting,null);awaiting=deferred<NativePreviewResult>();return awaiting.promise;}};
}

const output:{name:string;previews:{frame:number;quality:PreviewQuality;receipt:TransitionPreviewReport}[]}[]=[];
let coalescingBase:NativePreviewRequest|null=null,coalescingCursors:Cursor[]=[];
for(const item of input.cases){
 assert.ok(typeof item.name==='string'&&item.name.length<=128);
 assert.ok(Array.isArray(item.requests)&&item.requests.length>0&&item.requests.length<=64);
 const catalogResponse=await actualFetch('/api/graph/catalog',{method:'POST',headers:{'Content-Type':'application/json'},
   body:JSON.stringify({project:item.project,sidecars:item.sidecars,node:item.node})});
 assert.ok(catalogResponse.ok,'Native catalogue request failed');
 const catalog=parseGraphControlCatalog(await readGraphControlResponse(catalogResponse));
 assert.ok(catalog&&catalog.node.id===item.node&&catalog.node.type==='transition');
 const base:NativePreviewRequest={snapshot:{project:item.project,sidecars:item.sidecars},project:item.project,
   sidecars:item.sidecars,node:item.node,request_sha256:catalog.request_sha256,...item.requests[0]};
 if(!coalescingBase){coalescingBase=base;coalescingCursors=item.requests;}
 const run=runner(base),previews:typeof output[number]['previews']=[];
 try{
   for(const cursor of item.requests){
     run.controller.cancel();const wait=run.wait(),request={...base,...cursor};run.controller.request(request);
     const result=await wait;assert.ok(samePreviewRequest(result.request,request));
     const receipt=run.receipts.at(-1)!;assert.equal(receipt.frame,cursor.frame);assert.equal(receipt.quality,cursor.quality);
     assert.deepEqual(result.images.map(image=>image.sha256),receipt.images.map(image=>image.sha256));
     previews.push({...cursor,receipt});
   }
 }finally{run.controller.dispose();assert.equal(run.pending.size,0);}
 assert.equal(run.requested.length,item.requests.length);assert.equal(run.presented.length,item.requests.length);
 output.push({name:item.name,previews});
}

assert.ok(coalescingBase);
const first=coalescingCursors[0],last=coalescingCursors.at(-1)!;
const alternate={frame:first.frame,quality:first.quality==='fast'?'full' as const:'fast' as const};
const run=runner(coalescingBase,true);let submitted=1;
try{
 const completed=run.wait();run.controller.request({...coalescingBase,...first});await run.held.promise;
 for(let n=0;n<100;n++){
   run.controller.request({...coalescingBase,...alternate});submitted++;
   run.controller.request({...coalescingBase,...first});submitted++;
 }
 run.controller.request({...coalescingBase,...last});submitted++;
 assert.equal(run.requested.length,1);assert.equal(run.presented.length,0);
 run.release.resolve();const result=await completed;
 assert.equal(result.request.frame,last.frame);assert.equal(result.request.quality,last.quality);
 assert.deepEqual(run.requested,[first,last]);assert.deepEqual(run.presented,[last]);
}finally{run.release.resolve();run.controller.dispose();assert.equal(run.pending.size,0);}
const timed=input.cases.find(item=>item.name==='timed_continuous');assert.ok(timed);
const normalBase:NativePreviewRequest={snapshot:{project:timed.project,sidecars:timed.sidecars},
 project:timed.project,sidecars:timed.sidecars,node:null,frame:5,quality:'fast'};
const direct=runner(normalBase),normal:{name:string;frame:number;quality:PreviewQuality;png_base64:string}[]=[];
try{
 for(const quality of ['fast','full'] as const){
   direct.controller.cancel();const completed=direct.wait();direct.controller.request({...normalBase,quality});
   const result=await completed;assert.equal(result.images.length,1);assert.equal(result.images[0].role,'composition');
   assert.equal(result.report,null);const blob=direct.pending.get(result.images[0].url);assert.ok(blob);
   normal.push({name:timed.name,frame:5,quality,png_base64:Buffer.from(await blob.arrayBuffer()).toString('base64')});
 }
}finally{direct.controller.dispose();assert.equal(direct.pending.size,0);}
assert.equal(direct.requested.length,2);assert.equal(direct.presented.length,2);
console.log(JSON.stringify({schema:'framerudiments.authoring-transition-preview-export.v1',requests:httpRequests,
 scope:{actual_http:true,actual_native_catalog:true,actual_preview_controller:true,
   png_container_dimensions_verified:true,transition_png_sha256_verified:true,
   browser_decode_observed:false},cases:output,normal,
 coalescing:{submitted_intents:submitted,requested:run.requested,presented:run.presented,
   only_latest_presented:true,owned_urls_removed:true}}));

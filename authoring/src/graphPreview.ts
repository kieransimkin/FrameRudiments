/** M10.4 native preview transport. The shared timeline owns every requested frame. */
import type {Document} from './model.ts';

export const TRANSITION_PREVIEW_SCHEMA='framerudiments.authoring-transition-preview.v1';
export const PREVIEW_REQUEST_BYTES=2*1024*1024;
export const PREVIEW_RESPONSE_BYTES=64*1024*1024;
export const PREVIEW_IMAGE_BYTES=24*1024*1024;
export const PREVIEW_PIXELS=8388608;
export type PreviewQuality='fast'|'full';
export type PreviewRole='composition'|'from'|'result'|'to';
export interface NativePreviewRequest<T extends object=object> {
 snapshot:T;project:Document;sidecars:Record<string,unknown>;frame:number;quality:PreviewQuality;
 node:string|null;request_sha256?:string;
}
export interface PreviewRenderQuality {
 enabled:boolean;spatial_scale:number;temporal_samples:number;antialias:number;
 antialias_radius:number;antialias_threshold:number;antialias_passes:number;
 spatial_method:string;temporal_method:string;final_method:string;
 optical_flow:false;gpu_feedback_subframes:false;
}
export interface TransitionPreviewMetadata {
 schema:typeof TRANSITION_PREVIEW_SCHEMA;request_sha256:string;
 node:{id:string;authored_id:string;label:string;type:'transition';transition_style:string};
 frame:number;quality:PreviewQuality;width:number;height:number;
 clock:{frame:number;seconds:number;source_seconds:number;beat:number;source_beat:number};
 render_quality:PreviewRenderQuality;
 scope:{pipeline:'native_graph_before_sequential_effects';sequential_effects_excluded:boolean;
   native_temporal_post_effects:boolean;black_matte:true;production_approved:false};
}
export interface TransitionPreviewImage {
 role:'from'|'result'|'to';node:string;label:string;width:number;height:number;mime:'image/png';
 png_base64:string;sha256:string;pixel_sha256:string;
}
export interface TransitionPreviewReport extends TransitionPreviewMetadata {images:TransitionPreviewImage[];}
export interface NativePreviewImage {
 role:PreviewRole;node:string|null;label:string;width:number;height:number;url:string;
 sha256?:string;pixel_sha256?:string;
}
export interface NativePreviewResult<T extends object=object> {
 request:NativePreviewRequest<T>;images:NativePreviewImage[];report:TransitionPreviewMetadata|null;
}
export interface NativePreviewState<T extends object=object> {
 status:'idle'|'loading'|'ready'|'error';request:NativePreviewRequest<T>|null;
 result:NativePreviewResult<T>|null;message:string;
}
export interface NativePreviewDependencies {
 fetch:(path:string,init:RequestInit)=>Promise<Response>;
 decode:(url:string,signal:AbortSignal)=>Promise<void>;
 createObjectURL:(blob:Blob)=>string;revokeObjectURL:(url:string)=>void;
 timeoutMs:number;setTimer:(callback:()=>void,delay:number)=>unknown;clearTimer:(handle:unknown)=>void;
}
const object=(value:unknown):Record<string,unknown>|null=>{
 if(!value||typeof value!=='object'||Array.isArray(value))return null;
 const prototype=Object.getPrototypeOf(value);
 return prototype===Object.prototype||prototype===null?value as Record<string,unknown>:null;
};
const text=(value:unknown,max:number):value is string=>typeof value==='string'&&value.length>0&&Array.from(value).length<=max;
const digest=(value:unknown):value is string=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
const number=(value:unknown,min=-Infinity,max=Infinity):value is number=>
 typeof value==='number'&&Number.isFinite(value)&&value>=min&&value<=max;
const integer=(value:unknown,min=0,max=Number.MAX_SAFE_INTEGER):value is number=>
 number(value,min,max)&&Number.isSafeInteger(value);
const quality=(value:unknown):value is PreviewQuality=>value==='fast'||value==='full';
const dimensions=(width:unknown,height:unknown):boolean=>integer(width,16,7680)&&integer(height,16,7680)&&
 width%2===0&&height%2===0&&width*height<=PREVIEW_PIXELS;
const invalid=()=>Error('The native preview response is invalid. Retry the current frame.');
const abortError=()=>new DOMException('Preview cancelled','AbortError');
function aborted(signal?:AbortSignal):void{if(signal?.aborted)throw abortError();}

/** Identity includes sidecars, selection, quality and complete undo/redo snapshot. */
export function samePreviewRequest(a:NativePreviewRequest|null|undefined,b:NativePreviewRequest|null|undefined):boolean {
 return !!a&&!!b&&a.snapshot===b.snapshot&&a.project===b.project&&a.sidecars===b.sidecars&&
   a.node===b.node&&a.frame===b.frame&&a.quality===b.quality&&a.request_sha256===b.request_sha256;
}
function checkedRequest<T extends object>(value:NativePreviewRequest<T>):NativePreviewRequest<T>{
 if(!value.snapshot||typeof value.snapshot!=='object'||!object(value.project)||!object(value.sidecars)||
   !integer(value.frame)||!quality(value.quality)||
   (value.node!==null&&(!text(value.node,128)||!digest(value.request_sha256))))
   throw Error('Choose a valid native preview target and timeline frame.');
 if(!dimensions(value.project.width??1080,value.project.height??1920))
   throw Error('This frame exceeds the one-shot preview pixel limit. Use adaptive preview or a smaller project.');
 return Object.freeze({...value});
}
function parseRenderQuality(value:unknown):PreviewRenderQuality|null{
 const row=object(value);
 if(!row||typeof row.enabled!=='boolean'||!integer(row.spatial_scale,1,4)||!integer(row.temporal_samples,1,8)||
   !number(row.antialias,0,1)||!number(row.antialias_radius,.25,3)||!number(row.antialias_threshold,0,.95)||
   !integer(row.antialias_passes,1,3)||!text(row.spatial_method,512)||!text(row.temporal_method,512)||
   !text(row.final_method,512)||row.optical_flow!==false||row.gpu_feedback_subframes!==false||
   (!row.enabled&&(row.spatial_scale!==1||row.temporal_samples!==1||row.antialias!==0)))return null;
 return {enabled:row.enabled,spatial_scale:row.spatial_scale,temporal_samples:row.temporal_samples,
   antialias:row.antialias,antialias_radius:row.antialias_radius,antialias_threshold:row.antialias_threshold,
   antialias_passes:row.antialias_passes,spatial_method:row.spatial_method,temporal_method:row.temporal_method,
   final_method:row.final_method,optical_flow:false,gpu_feedback_subframes:false};
}
function base64Bytes(value:unknown):number|null{
 if(typeof value!=='string'||!value.length||value.length%4||value.length>Math.ceil(PREVIEW_IMAGE_BYTES/3)*4||
   !/^[A-Za-z0-9+/]*={0,2}$/.test(value))return null;
 const pad=value.endsWith('==')?2:value.endsWith('=')?1:0;
 const alphabet='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
 const last=alphabet.indexOf(value[value.length-pad-1]);
 if((pad===2&&(last&15)!==0)||(pad===1&&(last&3)!==0))return null;
 const bytes=value.length/4*3-pad;
 return bytes<=PREVIEW_IMAGE_BYTES?bytes:null;
}

/** Metadata is native evidence. PNG hashes and decoder acceptance are checked separately. */
export function parseTransitionPreview(value:unknown):TransitionPreviewReport|null{
 const row=object(value),node=object(row?.node),clock=object(row?.clock),scope=object(row?.scope);
 const render=parseRenderQuality(row?.render_quality);
 if(!row||row.schema!==TRANSITION_PREVIEW_SCHEMA||!digest(row.request_sha256)||!node||
   !text(node.id,128)||!text(node.authored_id,128)||!text(node.label,256)||node.type!=='transition'||
   !text(node.transition_style,64)||!integer(row.frame)||!quality(row.quality)||
   !dimensions(row.width,row.height)||!clock||clock.frame!==row.frame||!number(clock.seconds,0)||
   !number(clock.source_seconds,0)||!number(clock.beat)||!number(clock.source_beat)||!render||
   (row.quality==='fast'&&render.enabled)||!scope||scope.pipeline!=='native_graph_before_sequential_effects'||
   typeof scope.sequential_effects_excluded!=='boolean'||typeof scope.native_temporal_post_effects!=='boolean'||
   scope.black_matte!==true||scope.production_approved!==false||!Array.isArray(row.images)||row.images.length!==3)return null;
 const images:TransitionPreviewImage[]=[];let encoded=0;
 for(const [index,value] of row.images.entries()){
   const image=object(value);
   if(!image||image.role!==['from','result','to'][index]||!text(image.node,128)||!text(image.label,256)||
     image.width!==row.width||image.height!==row.height||image.mime!=='image/png'||
     base64Bytes(image.png_base64)===null||!digest(image.sha256)||!digest(image.pixel_sha256)||
     (index===1&&image.node!==node.id))return null;
   encoded+=(image.png_base64 as string).length;if(encoded>PREVIEW_RESPONSE_BYTES)return null;
   images.push({role:image.role as TransitionPreviewImage['role'],node:image.node,label:image.label,
     width:row.width as number,height:row.height as number,mime:'image/png',png_base64:image.png_base64 as string,
     sha256:image.sha256,pixel_sha256:image.pixel_sha256});
 }
 return {schema:TRANSITION_PREVIEW_SCHEMA,request_sha256:row.request_sha256,
   node:{id:node.id,authored_id:node.authored_id,label:node.label,type:'transition',transition_style:node.transition_style},
   frame:row.frame,quality:row.quality,width:row.width as number,height:row.height as number,
   clock:{frame:row.frame,seconds:clock.seconds,source_seconds:clock.source_seconds,beat:clock.beat,source_beat:clock.source_beat},
   render_quality:render,scope:{pipeline:'native_graph_before_sequential_effects',
     sequential_effects_excluded:scope.sequential_effects_excluded,native_temporal_post_effects:scope.native_temporal_post_effects,
     black_matte:true,production_approved:false},images};
}

async function readBytes(response:Response,maximum:number,mime:string,signal?:AbortSignal):Promise<Uint8Array<ArrayBuffer>>{
 aborted(signal);
 const length=response.headers.get('content-length');
 if(response.headers.get('content-type')?.split(';')[0].trim().toLowerCase()!==mime||!response.body||
   (length!==null&&(!/^\d+$/.test(length)||!Number.isSafeInteger(Number(length))||Number(length)>maximum))){
   await response.body?.cancel();throw invalid();
 }
 const reader=response.body.getReader(),chunks:Uint8Array[]=[];let size=0,finished=false;
 const cancel=()=>{void reader.cancel().catch(()=>{});};signal?.addEventListener('abort',cancel,{once:true});
 try{
   while(true){aborted(signal);const next=await reader.read();aborted(signal);if(next.done){finished=true;break;}
     size+=next.value.byteLength;if(size>maximum)throw Error('The native preview exceeded its response limit.');
     chunks.push(next.value);}
   if(length!==null&&Number(length)!==size)throw invalid();
   const bytes=new Uint8Array(size);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.length;}
   return bytes;
 }finally{
   signal?.removeEventListener('abort',cancel);if(!finished)await reader.cancel().catch(()=>{});reader.releaseLock();
 }
}
function pngDimensions(bytes:Uint8Array,width:number,height:number):void{
 const signature=[137,80,78,71,13,10,26,10];
 if(bytes.length<45||!signature.every((value,index)=>bytes[index]===value))throw invalid();
 const data=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength);
 if(data.getUint32(8)!==13||String.fromCharCode(...bytes.subarray(12,16))!=='IHDR'||
   data.getUint32(16)!==width||data.getUint32(20)!==height||bytes[24]!==8||![2,6].includes(bytes[25])||
   bytes[26]!==0||bytes[27]!==0||bytes[28]!==0||data.getUint32(bytes.length-12)!==0||
   String.fromCharCode(...bytes.subarray(bytes.length-8,bytes.length-4))!=='IEND')throw invalid();
}
async function byteDigest(bytes:Uint8Array<ArrayBuffer>):Promise<string>{
 if(!globalThis.crypto?.subtle)throw Error('WebCrypto is required to verify native preview images.');
 const result=await globalThis.crypto.subtle.digest('SHA-256',bytes);
 return [...new Uint8Array(result)].map(value=>value.toString(16).padStart(2,'0')).join('');
}
type ImagePayload=Omit<NativePreviewImage,'url'> & {bytes:Uint8Array<ArrayBuffer>};
export interface NativePreviewPayload {images:ImagePayload[];report:TransitionPreviewMetadata|null;}
async function responseError(response:Response,signal?:AbortSignal):Promise<Error>{
 let code='';
 try{const bytes=await readBytes(response,4096,'application/json',signal);
   const value=object(JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes)));code=String(value?.code??'');}
 catch{aborted(signal);}
 if(code==='busy'||response.status===429)return Error('The native preview renderer is busy. Retry when the current work finishes.');
 if(code==='preview_limit')return Error('This frame exceeds the one-shot preview limit. Use adaptive preview or a smaller project.');
 if(code==='input_changed')return Error('A pinned source changed. Reload the project before previewing.');
 if(['request_invalid','draft_rejected','graph_invalid','node_invalid','frame_invalid','quality_invalid'].includes(code)||response.status===400)
   return Error('The server rejected this preview. Check the current graph, target and timeline frame.');
 return Error('The native preview could not complete. Retry the current frame.');
}

/** No response may choose its own frame, target, dimensions or quality. */
export async function readNativePreviewResponse(response:Response,request:NativePreviewRequest,
    signal?:AbortSignal):Promise<NativePreviewPayload>{
 checkedRequest(request);if(!response.ok)throw await responseError(response,signal);
 const width=request.project.width??1080,height=request.project.height??1920;
 if(request.node===null){
   const bytes=await readBytes(response,PREVIEW_IMAGE_BYTES,'image/png',signal);
   pngDimensions(bytes,width,height);aborted(signal);
   return {images:[{role:'composition',node:null,label:'Composited frame',width,height,bytes}],report:null};
 }
 const json=await readBytes(response,PREVIEW_RESPONSE_BYTES,'application/json',signal);
 let value:unknown;
 try{value=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(json));}catch{throw invalid();}
 const parsed=parseTransitionPreview(value);
 if(!parsed||parsed.request_sha256!==request.request_sha256||parsed.node.id!==request.node||
   parsed.frame!==request.frame||parsed.quality!==request.quality||parsed.width!==width||parsed.height!==height)throw invalid();
 const images:ImagePayload[]=[];
 for(const image of parsed.images){
   aborted(signal);const raw=atob(image.png_base64),bytes=new Uint8Array(raw.length);
   for(let index=0;index<raw.length;index++)bytes[index]=raw.charCodeAt(index);
   pngDimensions(bytes,width,height);
   if(await byteDigest(bytes)!==image.sha256)throw invalid();aborted(signal);
   images.push({role:image.role,node:image.node,label:image.label,width,height,bytes,
     sha256:image.sha256,pixel_sha256:image.pixel_sha256});
 }
 const {images:_images,...report}=parsed;void _images;
 return {images,report};
}

/** A decoded PNG is required before it can replace the displayed result. */
export function decodePreviewImage(url:string,signal:AbortSignal):Promise<void>{
 return new Promise((resolve,reject)=>{
   aborted(signal);const image=new Image();
   const cancel=()=>{image.src='';reject(abortError());};
   signal.addEventListener('abort',cancel,{once:true});image.src=url;
   image.decode().then(()=>{aborted(signal);resolve();},reject)
     .catch(reject).finally(()=>signal.removeEventListener('abort',cancel));
 });
}
type Job<T extends object>={serial:number;request:NativePreviewRequest<T>};

/** One active native render and one replaceable pending cursor; never a playback clock. */
export class NativePreviewController<T extends object=object> {
 private serial=0;private wanted:Job<T>|null=null;private active:Job<T>|null=null;
 private abort:AbortController|null=null;private closed=false;private urls=new Set<string>();
 private decodingUrls=new Set<string>();
 private accepted:NativePreviewRequest<T>|null=null;
 private dependencies:NativePreviewDependencies;
 private update:(state:NativePreviewState<T>)=>void;
 constructor(update:(state:NativePreviewState<T>)=>void,dependencies:Partial<NativePreviewDependencies>={}){
   this.update=update;
   this.dependencies={fetch:(path,init)=>fetch(path,init),decode:decodePreviewImage,
     createObjectURL:blob=>URL.createObjectURL(blob),revokeObjectURL:url=>URL.revokeObjectURL(url),
     timeoutMs:60000,setTimer:(callback,delay)=>setTimeout(callback,delay),
     clearTimer:handle=>clearTimeout(handle as ReturnType<typeof setTimeout>),...dependencies};
   if(!number(this.dependencies.timeoutMs,1,300000))throw Error('Invalid preview timeout');
 }
 private emit(state:NativePreviewState<T>):void{if(!this.closed)this.update(state);}
 private release():void{for(const url of this.urls)this.dependencies.revokeObjectURL(url);this.urls.clear();this.accepted=null;}
 private current(job:Job<T>):boolean{return !this.closed&&this.wanted===job&&job.serial===this.serial;}
 request(value:NativePreviewRequest<T>):void{
   if(this.closed)return;
   let request:NativePreviewRequest<T>;
   try{request=checkedRequest(value);}catch(error){
     this.serial++;this.wanted=null;this.release();
     this.emit({status:'error',request:value,result:null,message:(error as Error).message});return;
   }
   if((this.wanted&&samePreviewRequest(this.wanted.request,request))||samePreviewRequest(this.accepted,request))return;
   const job={serial:++this.serial,request};this.wanted=job;this.release();
   this.emit({status:'loading',request,result:null,message:'Rendering the requested frame…'});
   if(!this.active)void this.run(job);
 }
 cancel():void{
   this.serial++;this.wanted=null;this.abort?.abort();this.release();
   for(const url of this.decodingUrls)this.dependencies.revokeObjectURL(url);this.decodingUrls.clear();
   this.emit({status:'idle',request:null,result:null,message:'Preview cancelled.'});
 }
 dispose():void{this.closed=true;this.cancel();}
 private async run(job:Job<T>):Promise<void>{
   this.active=job;const controller=new AbortController();this.abort=controller;
   const pending=new Set<string>();this.decodingUrls=pending;let timedOut=false;
   const timer=this.dependencies.setTimer(()=>{
     timedOut=true;controller.abort();
     for(const url of pending)this.dependencies.revokeObjectURL(url);pending.clear();
     if(this.current(job)){
       this.wanted=null;this.emit({status:'error',request:job.request,result:null,
         message:'The native preview timed out. Retry the current frame or use Fast quality.'});
     }
   },this.dependencies.timeoutMs);
   try{
     const request=job.request;
     const body=JSON.stringify({project:request.project,sidecars:request.sidecars,frame:request.frame,
       quality:request.quality,...(request.node===null?{}:{node:request.node})});
     if(new TextEncoder().encode(body).byteLength>PREVIEW_REQUEST_BYTES)throw Error('The preview draft exceeds its request limit.');
     const response=await this.dependencies.fetch(request.node===null?'/api/preview':'/api/graph/preview',
       {method:'POST',headers:{'Content-Type':'application/json'},body,signal:controller.signal,
         credentials:'same-origin',cache:'no-store'});
     if(!this.current(job)){await response.body?.cancel();return;}
     const payload=await readNativePreviewResponse(response,request,controller.signal);
     if(!this.current(job))return;
     const images:NativePreviewImage[]=[];
     for(const image of payload.images){
       if(!this.current(job))return;
       const {bytes,...metadata}=image,url=this.dependencies.createObjectURL(new Blob([bytes],{type:'image/png'}));
       pending.add(url);await this.dependencies.decode(url,controller.signal);
       if(!this.current(job)||controller.signal.aborted)return;
       images.push({...metadata,url});
     }
     if(!this.current(job))return;
     this.release();this.urls=pending;this.accepted=request;
     this.emit({status:'ready',request,result:{request,images,report:payload.report},message:''});
   }catch(error){
     if(this.current(job)&&!timedOut){
       this.wanted=null;
       this.emit({status:'error',request:job.request,result:null,message:controller.signal.aborted?
         'Preview cancelled.':error instanceof Error?error.message:'The native preview could not complete.'});
     }
   }finally{
     this.dependencies.clearTimer(timer);
     if(this.urls!==pending)for(const url of pending)this.dependencies.revokeObjectURL(url);
     if(this.abort===controller)this.abort=null;
     this.active=null;
     const next=this.wanted;
     if(next&&next!==job&&!this.closed)void this.run(next);
   }
 }
}

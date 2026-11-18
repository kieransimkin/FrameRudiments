/** Exact timeline-frame views. Rendering, decoding and URL ownership live in the shared controller. */
import {useLayoutEffect,useMemo,useRef,useState} from 'react';
import type {Document} from './model.ts';
import {NativePreviewController,samePreviewRequest,type NativePreviewRequest,type NativePreviewState,
  type PreviewQuality} from './graphPreview.ts';
import './native-frame-preview.css';

const EMPTY_SIDECARS:Record<string,unknown>={};
const EMPTY_STATE:NativePreviewState={status:'idle',request:null,result:null,message:''};
const qualityLabel=(quality:PreviewQuality)=>quality==='fast'?'Fast · 1×':'Authored quality';
const roleLabel=(role:string)=>role==='from'?'From':role==='to'?'To':role==='result'?'Result':'Composition';
type View='all'|'from'|'result'|'to';

export interface NativeFramePreviewProps {
 project:Document;revision:object;sidecars?:Record<string,unknown>;frame:number;quality:PreviewQuality;
 target?:{id:string;label:string;request_sha256:string};enabled?:boolean;blockedMessage?:string;
}
export interface NativeFramePreviewViewProps {
 request:NativePreviewRequest;state:NativePreviewState;label?:string;enabled?:boolean;blockedMessage?:string;
 cancelled?:boolean;view?:View;onView?:(view:View)=>void;onRetry?:()=>void;onCancel?:()=>void;
}

/** Every seek is an integer on the existing project frame grid. */
export function FramePreviewControls({frame,lastFrame,fps,quality,showQuality,onFrame,onQuality}:{
 frame:number;lastFrame:number;fps:number;quality:PreviewQuality;showQuality:boolean;
 onFrame:(frame:number)=>void;onQuality:(quality:PreviewQuality)=>void;
}){
 const seek=(next:number)=>{if(Number.isSafeInteger(next)&&next>=0&&next<=lastFrame)onFrame(next);};
 return <>
   <label className="fr-scrub">Preview position <input aria-label="Seek draft preview" type="range" min="0" max={lastFrame/fps} step={1/fps}
     value={frame/fps} onChange={event=>seek(Math.round(event.target.valueAsNumber*fps))}/></label>
   {showQuality?<label>Preview quality <select aria-label="Frame preview quality" value={quality}
     onChange={event=>{if(event.target.value==='fast'||event.target.value==='full')onQuality(event.target.value);}}>
       <option value="fast">Fast (1×)</option><option value="full">Authored quality</option></select></label>:
     <span className="fr-preview-adaptive-label">Quality follows the adaptive preview controls.</span>}
   <div className="fr-preview-frame-controls"><button type="button" aria-label="Previous preview frame" disabled={frame===0}
     onClick={()=>seek(frame-1)}>←</button>
     <label>Preview frame <input aria-label="Exact preview frame" type="number" min="0" max={lastFrame} step="1" value={frame}
       onChange={event=>seek(event.target.valueAsNumber)}/></label>
     <button type="button" aria-label="Next preview frame" disabled={frame===lastFrame} onClick={()=>seek(frame+1)}>→</button>
   </div>
 </>;
}

/** A current request is checked during render, before effects can retire older work. */
export function NativeFramePreviewView({request,state,label,enabled=true,blockedMessage,cancelled=false,
 view='all',onView,onRetry,onCancel}:NativeFramePreviewViewProps){
 const current=state.request&&samePreviewRequest(state.request,request)?state:null;
 const result=enabled&&!cancelled&&current?.status==='ready'&&current.result&&
   samePreviewRequest(current.result.request,request)?current.result:null;
 const busy=enabled&&!cancelled&&(!current||current.status==='loading');
 const failed=enabled&&!cancelled&&current?.status==='error';
 const images=result?.images??[],transition=request.node!==null;
 const selection=images.some(image=>image.role===view)?view:'all';
 const shown=selection==='all'?images:images.filter(image=>image.role===selection);
 const status=!enabled?blockedMessage||'Wait for a successful native graph check before previewing.':
   cancelled?'Preview cancelled. Choose Retry preview to render this position.':
   failed?current!.message:busy?`Rendering frame ${request.frame}…`:
   result?`Frame ${request.frame} ready.`:'Preparing preview…';
 return <section className={`fr-native-preview${transition?' fr-transition-preview':''}`} aria-label={transition?'Selected transition preview':'Native frame preview'}
   aria-busy={busy}>
   <div className="fr-native-preview-heading"><div><strong>{transition?'Transition preview':'Composited frame'}</strong>
     {transition&&<span className="fr-native-target">{label||request.node}</span>}</div>
     <span>Frame {request.frame} · {qualityLabel(request.quality)}</span></div>
   {transition&&result&&<div className="fr-native-preview-views" role="group" aria-label="Transition comparison view">
     {(['all','from','result','to'] as const).map(value=><button key={value} type="button" aria-pressed={selection===value}
       onClick={()=>onView?.(value)}>{value==='all'?'Compare':roleLabel(value)}</button>)}
   </div>}
   <div className={`fr-native-images${transition&&selection==='all'?' fr-native-comparison':''}`}>
     {result?shown.map(image=><figure key={image.role}>
       {transition&&<figcaption>{roleLabel(image.role)}</figcaption>}
       <img src={image.url} width={image.width} height={image.height} alt={`${roleLabel(image.role)} at frame ${request.frame}`}/>
     </figure>):<div className="fr-native-placeholder" role={failed?'alert':'status'}>{status}</div>}
   </div>
   <div className="fr-native-preview-status">
     {result&&<span role="status">{status}</span>}
     {busy&&onCancel&&<button type="button" onClick={onCancel}>Cancel preview</button>}
     {(failed||cancelled)&&onRetry&&<button type="button" onClick={onRetry}>Retry preview</button>}
   </div>
   {transition&&<small className="fr-native-preview-note">{request.quality==='fast'?
     'Fast skips authored supersampling and finishing.':'Uses the project’s authored quality settings.'}
     {' '}The timeline controls the frame; this preview does not change the transition.</small>}
   {result?.report?.scope.sequential_effects_excluded&&<p className="fr-native-preview-scope" role="status">
     Sequential visualiser stages are excluded from this graph comparison.</p>}
   {transition&&result&&<small className="fr-native-preview-note">Transparent pixels are shown over black.</small>}
 </section>;
}

export function NativeFramePreview({project,revision,sidecars=EMPTY_SIDECARS,frame,quality,target,
 enabled=true,blockedMessage}:NativeFramePreviewProps){
 const request=useMemo<NativePreviewRequest>(()=>({snapshot:revision,project,sidecars,frame,quality,
   node:target?.id??null,...(target?{request_sha256:target.request_sha256}:{})}),
 [revision,project,sidecars,frame,quality,target?.id,target?.request_sha256]);
 const [state,setState]=useState<NativePreviewState>(EMPTY_STATE);
 const [cancelled,setCancelled]=useState<NativePreviewRequest|null>(null),[retry,setRetry]=useState(0);
 const [view,setView]=useState<View>('all');
 const controllerRef=useRef<NativePreviewController|null>(null);
 useLayoutEffect(()=>{
   const controller=new NativePreviewController(next=>{
     if(controllerRef.current===controller)setState(next);
   });
   controllerRef.current=controller;
   return ()=>{controllerRef.current=null;controller.dispose();};
 },[]);
 useLayoutEffect(()=>{
   setCancelled(null);
   if(enabled)controllerRef.current?.request(request);else controllerRef.current?.cancel();
   // Keep the controller alive across seeks: it coalesces one latest pending
   // request instead of abandoning a native render for every cursor movement.
 },[request,enabled,retry]);
 const isCancelled=!!cancelled&&samePreviewRequest(cancelled,request);
 return <NativeFramePreviewView request={request} state={state} label={target?.label} enabled={enabled}
   blockedMessage={blockedMessage} cancelled={isCancelled} view={view} onView={setView}
   onCancel={()=>{controllerRef.current?.cancel();setCancelled(request);}}
   onRetry={()=>{setCancelled(null);setRetry(value=>value+1);}}/>;
}

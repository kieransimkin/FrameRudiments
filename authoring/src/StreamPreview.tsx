/** Opt-in S3 adaptive image stream. Never advances or time-stretches audio. */
import {useEffect,useRef,useState} from 'react';
import {PresentationFence,previewFrame,validFrameUrl} from './streamAdaptive.ts';
import {StoppedCursor,observeTimelinePlayback} from './streamRefinement.ts';
import {invalidationSummary,invalidatesFrame,nextDraftRevision,sameDraftImage} from './streamInvalidation.ts';
import {currentStatefulEvent,parseStateful,statefulSummary,type StatefulStatus} from './streamStateful.ts';

type Props={project:Record<string,any>;sidecars:Record<string,unknown[]>;
  time:number;fps:number;duration:number};
type Metrics={profile:string;quality_floor:string;fresh_fps:number|null;
  acknowledged_fps:number|null;render_ms_p95:number|null;target_unattainable:boolean};
type Refinement={enabled:boolean;state:string;anchor_frame:number|null;frame:number|null;
  completed:number;skipped:number;epoch:number;error_type?:string|null};
type FrameEvent={cursor_seq:number;frame:number;url?:string;profile_id?:string;
  image_sha256:string;cache_hit?:boolean;adaptive?:Metrics;origin?:string;refinement?:Refinement;
  stateful?:unknown;type:string};
async function post(path:string,data:Record<string,unknown>,signal?:AbortSignal) {
  const result=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(data),signal,credentials:'same-origin'});
  const body=await result.json();
  if(!result.ok)throw Error(body?.error||`HTTP ${result.status}`);
  return body;
}
const rate=(n:number|null|undefined)=>n==null?'measuring':n.toFixed(1);
export function StreamPreview({project,sidecars,time,fps,duration}:Props){
  const [picture,setPicture]=useState('');
  const [pictureSource,setPictureSource]=useState<{project:Props['project'];sidecars:Props['sidecars'];refine:boolean}|null>(null);
  const [invalidation,setInvalidation]=useState('');
  const [status,setStatus]=useState('Connecting adaptive stream…');
  const [target,setTarget]=useState(Math.max(15,Math.min(60,fps)));
  const [tier,setTier]=useState('');
  const [metrics,setMetrics]=useState<Metrics|null>(null);
  const [displayed,setDisplayed]=useState<number|null>(null);
  const [refine,setRefine]=useState(true);
  const [refinement,setRefinement]=useState<Refinement|null>(null);
  const [stateful,setStateful]=useState<StatefulStatus|null>(null);
  const playing=useRef(false);
  useEffect(()=>observeTimelinePlayback(document.querySelector('.fr-timeline'),
    active=>{playing.current=active;}),[]);
  const targetRef=useRef(target);targetRef.current=target;
  const timeRef=useRef(time);timeRef.current=time;
  const request=useRef<((frame:number,command?:string)=>void)|null>(null);
  useEffect(()=>{
    const revision=nextDraftRevision();
    let disposed=false,events:EventSource|null=null,token='',busy=true;
    let fence=new PresentationFence(previewFrame(timeRef.current,fps,duration));
    let desired=fence.frame,lastObserved=desired,nextSendAt=0,decodeId=0;
    let stopped=new StoppedCursor(desired,performance.now());
    let fetching:AbortController|null=null,pictureUrl='',retryTimer:number|undefined;
    let restarting=false,pictureFrame:number|null=null;
    setPicture('');setPictureSource(null);setInvalidation('');
    setDisplayed(null);setMetrics(null);setTier('');setRefinement(null);setStateful(null);
    setStatus('Connecting adaptive stream…');
    const send=(frame:number,command='advance')=>{
      if(!token||disposed)return;
      if(command==='advance'&&(busy||frame===fence.frame))return;
      busy=true;fetching?.abort();decodeId++;
      const sequence=fence.request(frame);
      setStateful(previous=>previous?{...previous,requested_frame:frame,phase:'idle',last_reason:null}:null);
      const now=performance.now(),interval=1000/targetRef.current;
      nextSendAt=command==='advance'?Math.max(now,nextSendAt+interval):now+interval;
      if(command==='seek'){
        setPicture('');setDisplayed(null);
        if(pictureUrl){URL.revokeObjectURL(pictureUrl);pictureUrl='';}
      }
      void post('/api/stream/control',{session:token,command,sequence,frame,
        ...(command==='target_fps'?{target_fps:targetRef.current}:{})})
        .catch(error=>{if(!disposed&&fence.accepts(sequence,frame)){
          busy=false;setStatus(String(error));
        }});
    };
    request.current=send;
    const receive=async(event:Event)=>{
      if(!(event instanceof MessageEvent)||disposed)return;
      let row:FrameEvent;
      try{row=JSON.parse(event.data) as FrameEvent;}catch{setStatus('Invalid stream event');return;}
      if(!fence.accepts(row.cursor_seq,row.frame)||!validFrameUrl(row.url,token))return;
      fetching?.abort();fetching=new AbortController();
      const serial=++decodeId,session=token,sequence=row.cursor_seq;
      let url='';
      try{
        const response=await fetch(row.url,{signal:fetching.signal,credentials:'same-origin',cache:'no-store'});
        if(!response.ok)throw Error(`Frame unavailable (${response.status}); seek to retry`);
        url=URL.createObjectURL(await response.blob());
        const image=new Image();image.src=url;await image.decode();
        if(disposed||serial!==decodeId||token!==session||!fence.accepts(sequence,row.frame)){
          URL.revokeObjectURL(url);return;
        }
        const old=pictureUrl;pictureUrl=url;pictureFrame=row.frame;setPicture(url);setDisplayed(row.frame);
        setPictureSource({project,sidecars,refine});
        if(old)URL.revokeObjectURL(old);
        setTier(`${row.profile_id} · ${row.origin==='refinement'?'background refinement':row.cache_hit?'cache hit':'fresh render'}`);
        if(row.refinement)setRefinement(row.refinement);
        const replay=parseStateful(row.stateful);
        setStateful(replay);
        setMetrics(row.adaptive??null);
        setStatus(replay?.mode==='verified_prefix_replay'?'Verified feedback preview':
          row.adaptive?.target_unattainable?'At quality floor: target cannot be sustained':'Adaptive preview');
        requestAnimationFrame(()=>{
          if(disposed||serial!==decodeId||!fence.accepts(sequence,row.frame))return;
          // A decoded-image acknowledgement, not a physical-display measurement.
          void post('/api/stream/presented',{session,sequence,frame:row.frame,sha256:row.image_sha256}).catch(()=>{});
          busy=false;
        });
      }catch(error){
        if(url&&url!==pictureUrl)URL.revokeObjectURL(url);
        if(!disposed&&serial===decodeId&&(error as Error).name!=='AbortError'){
          busy=false;setStatus(String(error));
        }
      }
    };
    const connect=async()=>{
      if(disposed)return;
      try{
        const initial=previewFrame(timeRef.current,fps,duration);
        const reply=await post('/api/stream/start',{project,sidecars,frame:initial,
          target_fps:targetRef.current,adaptive:true,refine,...revision});
        if(disposed){void post('/api/stream/close',{session:reply.session}).catch(()=>{});return;}
        token=reply.session;fence=new PresentationFence(initial);
        stopped=new StoppedCursor(initial,performance.now());
        setRefinement(reply.refinement??null);
        setStateful(parseStateful(reply.stateful));
        setInvalidation(invalidationSummary(reply.invalidation)??'');
        desired=lastObserved=previewFrame(timeRef.current,fps,duration);
        events=new EventSource('/api/stream/events?session='+encodeURIComponent(token)+'&after=0&continuous=1');
        events.addEventListener('frame-ready',receive);
        for(const kind of ['stateful-progress','stateful-seek-unavailable'])events.addEventListener(kind,event=>{
          if(disposed||!(event instanceof MessageEvent))return;
          try{
            const update=currentStatefulEvent(JSON.parse(event.data),fence);
            if(!update)return;
            setStateful(update);
            if(update.phase==='unavailable'){
              fetching?.abort();decodeId++;busy=false;
              setPicture('');setPictureSource(null);setDisplayed(null);setTier('');
              if(pictureUrl){URL.revokeObjectURL(pictureUrl);pictureUrl='';}pictureFrame=null;
              setStatus('Stateful seek unavailable');
            }else if(update.phase==='replaying'||update.phase==='verifying'){
              setStatus('Reconstructing feedback history');
            }
          }catch{setStatus('Invalid stateful preview event');}
        });
        events.addEventListener('cache-invalidated',event=>{
          if(disposed||!(event instanceof MessageEvent))return;
          try{
            const receipt=JSON.parse(event.data).invalidation;
            setInvalidation(invalidationSummary(receipt)??'');
            if(invalidatesFrame(receipt,pictureFrame??fence.frame)){
              fetching?.abort();decodeId++;setPicture('');setPictureSource(null);setDisplayed(null);
              if(pictureUrl){URL.revokeObjectURL(pictureUrl);pictureUrl='';}pictureFrame=null;
              setStatus('Section invalidated; awaiting a current frame');
            }
          }catch{setStatus('Invalid cache revision event');}
        });
        events.addEventListener('refinement-progress',event=>{
          if(disposed||!(event instanceof MessageEvent))return;
          try{const row=JSON.parse(event.data);if(row.refinement)setRefinement(row.refinement);}
          catch{setStatus('Invalid refinement event');}
        });
        events.addEventListener('resync',()=>{
          stopped=new StoppedCursor(desired,performance.now());busy=false;send(desired,'seek');
        });
        for(const kind of ['render-error','overload'])events.addEventListener(kind,(event)=>{
          if(!(event instanceof MessageEvent))return;
          try{const row=JSON.parse(event.data);
            if(row.cursor_seq===fence.sequence){busy=false;setStatus(`${kind}: ${row.reason||row.error_type||'seek to retry'}`);}
          }catch{setStatus('Invalid stream diagnostic');}
        });
        setStatus('Following source-clocked timeline');
        if(desired!==initial)send(desired,'seek');
      }catch(error){
        if(disposed)return;
        setStatus(String(error));
        if(String(error).includes('retiring')&&!restarting){
          restarting=true;retryTimer=window.setTimeout(()=>{restarting=false;void connect();},300);
        }
      }
    };
    void connect();
    const timer=window.setInterval(()=>{
      if(!token)return;
      desired=previewFrame(timeRef.current,fps,duration);
      const intent=stopped.observe(desired,playing.current,performance.now());
      if(intent){lastObserved=desired;send(desired,intent);return;}
      if(desired!==lastObserved){
        const jump=desired<lastObserved||Math.abs(desired-lastObserved)>Math.max(2,Math.ceil(fps/4));
        lastObserved=desired;
        if(jump){send(desired,'seek');return;}
      }
      if(playing.current&&performance.now()>=nextSendAt)send(desired);
    },8);
    return ()=>{
      disposed=true;events?.close();fetching?.abort();decodeId++;
      clearInterval(timer);clearTimeout(retryTimer);request.current=null;
      if(pictureUrl)URL.revokeObjectURL(pictureUrl);
      if(token)void post('/api/stream/close',{session:token}).catch(()=>{});
    };
  },[project,sidecars,fps,duration,refine]);
  const changeFPS=(n:number)=>{
    targetRef.current=n;setTarget(n);
    request.current?.(previewFrame(timeRef.current,fps,duration),'target_fps');
  };
  const desiredFrame=previewFrame(time,fps,duration);
  const replaying=stateful?.enabled&&stateful.mode==='verified_prefix_replay';
  const replayStatus=statefulSummary(stateful);
  return <div className="fr-stream-preview" role="region" aria-label="Adaptive cached streaming preview">
    {picture&&sameDraftImage(pictureSource,project,sidecars,refine)?<img src={picture} alt={`Cached preview frame ${displayed}`}/>:
      <span>{stateful?.phase==='unavailable'?'Stateful seek unavailable':'Rendering seek position…'}</span>}
    <small role="status">{status} · {tier} · {displayed!==null&&displayed!==desiredFrame?'late frame':'current request'} · preview only</small>
    {invalidation&&<small role="status">{invalidation}</small>}
    {replayStatus&&<small role="status">{replayStatus}</small>}
    {replaying?<small>Feedback uses authored Q5. Replay and verification time is separate from the target FPS.
      {' '}Acknowledged FPS {rate(metrics?.acknowledged_fps)}</small>:
      <small>Fresh render FPS {rate(metrics?.fresh_fps)} · acknowledged FPS {rate(metrics?.acknowledged_fps)} ·
        p95 {rate(metrics?.render_ms_p95)} ms · floor {metrics?.quality_floor??'checking'}</small>}
    <label><input type="checkbox" checked={refine} onChange={event=>setRefine(event.target.checked)}/>
      Refine paused frames at authored Q5</label>
    {refine&&<small role="status">Q5 refinement: {refinement?.state??'waiting'} ·
      anchor {refinement?.anchor_frame??'not stopped'} · {refinement?.completed??0} completed ·
      {refinement?.skipped??0} already cached{refinement?.error_type?` · ${refinement.error_type}`:''}</small>}
    <label>Target FPS<select value={target} onChange={event=>changeFPS(Number(event.target.value))}>
      {[15,24,30,60,120].map(n=><option key={n} value={n}>{n}</option>)}
    </select></label>
  </div>;
}

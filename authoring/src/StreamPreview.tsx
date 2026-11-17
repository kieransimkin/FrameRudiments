/** Opt-in S3 adaptive image stream. Never advances or time-stretches audio. */
import {useEffect,useRef,useState} from 'react';
import {PresentationFence,previewFrame,validFrameUrl} from './streamAdaptive.ts';

type Props={project:Record<string,any>;sidecars:Record<string,unknown[]>;
  time:number;fps:number;duration:number};
type Metrics={profile:string;quality_floor:string;fresh_fps:number|null;
  acknowledged_fps:number|null;render_ms_p95:number|null;target_unattainable:boolean};
type FrameEvent={cursor_seq:number;frame:number;url?:string;profile_id?:string;
  image_sha256:string;cache_hit?:boolean;adaptive?:Metrics;type:string};
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
  const [status,setStatus]=useState('Connecting adaptive stream…');
  const [target,setTarget]=useState(Math.max(15,Math.min(60,fps)));
  const [tier,setTier]=useState('');
  const [metrics,setMetrics]=useState<Metrics|null>(null);
  const [displayed,setDisplayed]=useState<number|null>(null);
  const targetRef=useRef(target);targetRef.current=target;
  const timeRef=useRef(time);timeRef.current=time;
  const request=useRef<((frame:number,command?:string)=>void)|null>(null);
  useEffect(()=>{
    let disposed=false,events:EventSource|null=null,token='',busy=true;
    let fence=new PresentationFence(previewFrame(timeRef.current,fps,duration));
    let desired=fence.frame,lastObserved=desired,nextSendAt=0,decodeId=0;
    let fetching:AbortController|null=null,pictureUrl='',retryTimer:number|undefined;
    let restarting=false;
    setPicture('');setDisplayed(null);setMetrics(null);setTier('');
    setStatus('Connecting adaptive stream…');
    const send=(frame:number,command='advance')=>{
      if(!token||disposed)return;
      if(command==='advance'&&(busy||frame===fence.frame))return;
      busy=true;fetching?.abort();decodeId++;
      const sequence=fence.request(frame);
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
        const old=pictureUrl;pictureUrl=url;setPicture(url);setDisplayed(row.frame);
        if(old)URL.revokeObjectURL(old);
        setTier(`${row.profile_id} · ${row.cache_hit?'cache hit':'fresh render'}`);
        setMetrics(row.adaptive??null);
        setStatus(row.adaptive?.target_unattainable?'At quality floor: target cannot be sustained':'Adaptive preview');
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
          target_fps:targetRef.current,adaptive:true});
        if(disposed){void post('/api/stream/close',{session:reply.session}).catch(()=>{});return;}
        token=reply.session;fence=new PresentationFence(initial);
        desired=lastObserved=previewFrame(timeRef.current,fps,duration);
        events=new EventSource('/api/stream/events?session='+encodeURIComponent(token)+'&after=0&continuous=1');
        events.addEventListener('frame-ready',receive);
        events.addEventListener('resync',()=>{busy=false;send(desired,'seek');});
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
      desired=previewFrame(timeRef.current,fps,duration);
      if(desired!==lastObserved){
        const jump=desired<lastObserved||Math.abs(desired-lastObserved)>Math.max(2,Math.ceil(fps/4));
        lastObserved=desired;
        if(jump){send(desired,'seek');return;}
      }
      if(performance.now()>=nextSendAt)send(desired);
    },8);
    return ()=>{
      disposed=true;events?.close();fetching?.abort();decodeId++;
      clearInterval(timer);clearTimeout(retryTimer);request.current=null;
      if(pictureUrl)URL.revokeObjectURL(pictureUrl);
      if(token)void post('/api/stream/close',{session:token}).catch(()=>{});
    };
  },[project,sidecars,fps,duration]);
  const changeFPS=(n:number)=>{
    targetRef.current=n;setTarget(n);
    request.current?.(previewFrame(timeRef.current,fps,duration),'target_fps');
  };
  const desiredFrame=previewFrame(time,fps,duration);
  return <div className="fr-stream-preview" role="region" aria-label="Adaptive cached streaming preview">
    {picture?<img src={picture} alt={`Cached preview frame ${displayed}`}/>:<span>Rendering seek position…</span>}
    <small role="status">{status} · {tier} · {displayed!==null&&displayed!==desiredFrame?'late frame':'current request'} · preview only</small>
    <small>Fresh render FPS {rate(metrics?.fresh_fps)} · acknowledged FPS {rate(metrics?.acknowledged_fps)} ·
      p95 {rate(metrics?.render_ms_p95)} ms · floor {metrics?.quality_floor??'checking'}</small>
    <label>Target FPS<select value={target} onChange={event=>changeFPS(Number(event.target.value))}>
      {[15,24,30,60,120].map(n=><option key={n} value={n}>{n}</option>)}
    </select></label>
  </div>;
}

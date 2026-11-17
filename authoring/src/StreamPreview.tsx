/** Opt-in S2 image stream; shared timeline remains authoritative for audio. */
import {useEffect,useRef,useState} from 'react';

type Props={project:Record<string,any>;sidecars:Record<string,unknown[]>;
  time:number;fps:number;duration:number};
type FrameEvent={cursor_seq:number;frame:number;url?:string;profile_id?:string;
  cache_hit?:boolean;type:string};
async function post(path:string,data:Record<string,unknown>,signal?:AbortSignal) {
  const result=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(data),signal,credentials:'same-origin'});
  const body=await result.json();
  if(!result.ok)throw Error(body?.error||`HTTP ${result.status}`);
  return body;
}
export function StreamPreview({project,sidecars,time,fps,duration}:Props){
  const [token,setToken]=useState('');
  const [picture,setPicture]=useState('');
  const [status,setStatus]=useState('Connecting seekable stream…');
  const [fpsTarget,setFpsTarget]=useState(30);
  const [tier,setTier]=useState('Q0 · not production equivalent');
  const state=useRef<{token:string,seq:number}|null>(null);
  const desired=useRef(0),last=useRef<number|null>(null),busy=useRef(false);
  const timeRef=useRef(time);timeRef.current=time;
  const frame=(t:number)=>Math.max(0,Math.min(Math.ceil(duration*fps)-1,Math.round(t*fps)));
  const seek=(n:number,force=false)=>{
    const active=state.current;
    if(!active || (busy.current&&!force))return;
    busy.current=true;
    const seq=++active.seq;
    void post('/api/stream/control',{session:active.token,command:'seek',sequence:seq,frame:n})
      .catch(error=>{if(state.current?.seq===seq){busy.current=false;setStatus(String(error));}});
  };
  useEffect(()=>{
    const abort=new AbortController();let disposed=false;let events:EventSource|null=null;
    state.current=null;busy.current=false;last.current=null;
    setToken('');setPicture('');setStatus('Connecting seekable stream…');
    void post('/api/stream/start',{project,sidecars,frame:frame(timeRef.current),target_fps:30},abort.signal)
      .then((reply:{session:string})=>{
        if(disposed){void post('/api/stream/close',{session:reply.session}).catch(()=>{});return;}
        state.current={token:reply.session,seq:0};
        setToken(reply.session);
        setStatus('Following timeline · requested FPS is not adaptive yet');
        events=new EventSource('/api/stream/events?session='+encodeURIComponent(reply.session)+'&after=0');
        events.addEventListener('frame-ready',event=>{
          if(!(event instanceof MessageEvent))return;
          const row=JSON.parse(event.data) as FrameEvent;
          if(state.current?.token!==reply.session || state.current.seq!==row.cursor_seq)return;
          if(!row.url?.startsWith('/api/stream/frame?'))return;
          busy.current=false;
          setPicture(row.url);
          setTier(`${row.profile_id||'Q0'} · ${row.cache_hit?'cache hit':'new frame'} · not production equivalent`);
          setStatus('Seekable preview');
          if(desired.current!==row.frame)seek(desired.current);
        });
        events.addEventListener('resync',()=>{busy.current=false;seek(desired.current,true);});
        events.addEventListener('render-error',()=>{busy.current=false;setStatus('Render failed; seek to retry');});
      }).catch(error=>{if(!disposed&&(error as Error).name!=='AbortError')setStatus(String(error));});
    return ()=>{
      disposed=true;abort.abort();events?.close();
      const active=state.current;state.current=null;busy.current=false;
      if(active)void post('/api/stream/close',{session:active.token}).catch(()=>{});
    };
    // Draft changes create a fresh content fingerprint and stream revision.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[project,sidecars]);
  useEffect(()=>{
    if(!token || !state.current)return;
    const next=frame(time),previous=last.current;
    desired.current=next;last.current=next;
    if(previous===next)return;
    const jump=previous===null||Math.abs(next-previous)>Math.max(2,Math.ceil(fps/4));
    if(jump)setPicture(''); // explicit seek must not show an old frame
    seek(next,jump);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[time,token,fps,duration]);
  const changeFPS=(n:number)=>{
    setFpsTarget(n);
    const active=state.current;if(!active)return;
    const seq=++active.seq;
    void post('/api/stream/control',{session:active.token,command:'target_fps',sequence:seq,target_fps:n})
      .catch(error=>setStatus(String(error)));
    busy.current=false;seek(desired.current,true);
  };
  return <div className="fr-stream-preview" role="region" aria-label="Cached streaming preview">
    {picture?<img src={picture} alt="Cached low-resolution frame"/>:<span>Rendering seek position…</span>}
    <small role="status">{status} · {tier}</small>
    <label>Requested FPS (adaptive quality starts in S3)
      <select value={fpsTarget} onChange={(event:{target:HTMLSelectElement})=>changeFPS(Number(event.target.value))}>
        {[15,24,30,60].map(n=><option key={n} value={n}>{n}</option>)}
      </select></label>
  </div>;
}

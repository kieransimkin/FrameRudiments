/** Opt-in adaptive image stream and S7 observations. The timeline owns playback. */
import {useEffect,useRef,useState} from 'react';
import {PresentationFence,previewFrame,validFrameUrl} from './streamAdaptive.ts';
import {StoppedCursor,observeTimelinePlayback} from './streamRefinement.ts';
import {invalidationSummary,invalidatesFrame,nextDraftRevision,sameDraftImage} from './streamInvalidation.ts';
import {currentStatefulEvent,parseStateful,statefulSummary,type StatefulStatus} from './streamStateful.ts';
import {QualificationFence,formatBytes,formatRate,frameLabel,parseQualification,qualificationReceipt,
  readQualificationResponse,targetFPS,type QualificationSummary} from './streamQualification.ts';

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
export function StreamPreview({project,sidecars,time,fps,duration}:Props){
  const [picture,setPicture]=useState('');
  const [pictureSource,setPictureSource]=useState<{project:Props['project'];sidecars:Props['sidecars'];refine:boolean}|null>(null);
  const [invalidation,setInvalidation]=useState('');
  const [status,setStatus]=useState('Connecting adaptive stream…');
  const [target,setTarget]=useState(Math.max(15,Math.min(60,fps)));
  const [targetDraft,setTargetDraft]=useState(String(Math.max(15,Math.min(60,fps))));
  const [acceptedTarget,setAcceptedTarget]=useState<number|null>(null);
  const [tier,setTier]=useState('');
  const [metrics,setMetrics]=useState<Metrics|null>(null);
  const [displayed,setDisplayed]=useState<number|null>(null);
  const [refine,setRefine]=useState(true);
  const [refinement,setRefinement]=useState<Refinement|null>(null);
  const [stateful,setStateful]=useState<StatefulStatus|null>(null);
  const [qualification,setQualification]=useState<QualificationSummary|null>(null);
  const [observationSource,setObservationSource]=useState<typeof pictureSource>(null);
  const [observationError,setObservationError]=useState('');
  const [canDownload,setCanDownload]=useState(false);
  const [downloading,setDownloading]=useState(false);
  const [downloadStatus,setDownloadStatus]=useState('');
  const playing=useRef(false);
  useEffect(()=>observeTimelinePlayback(document.querySelector('.fr-timeline'),
    active=>{playing.current=active;}),[]);
  const targetRef=useRef(target);targetRef.current=target;
  const timeRef=useRef(time);timeRef.current=time;
  const request=useRef<((frame:number,command?:string)=>void)|null>(null);
  const exportReceipt=useRef<(()=>Promise<void>)|null>(null);
  useEffect(()=>{
    const revision=nextDraftRevision();
    let disposed=false,events:EventSource|null=null,token='',busy=true;
    let fence=new PresentationFence(previewFrame(timeRef.current,fps,duration));
    let desired=fence.frame,lastObserved=desired,nextSendAt=0,decodeId=0;
    let stopped=new StoppedCursor(desired,performance.now());
    let fetching:AbortController|null=null,pictureUrl='',retryTimer:number|undefined;
    let observing:AbortController|null=null,exporting:AbortController|null=null,observationEpoch=0;
    const observations=new QualificationFence(),downloadUrls=new Set<string>();
    let restarting=false,pictureFrame:number|null=null;
    setPicture('');setPictureSource(null);setInvalidation('');
    setDisplayed(null);setMetrics(null);setTier('');setRefinement(null);setStateful(null);
    setQualification(null);setObservationSource(null);setObservationError('');setAcceptedTarget(null);
    setCanDownload(false);setDownloading(false);setDownloadStatus('');
    setStatus('Connecting adaptive stream…');
    const observe=(value:unknown,epoch=observationEpoch)=>{
      if(disposed)return false;
      const next=observations.accept(value,epoch);
      if(next){setQualification(next);setObservationSource({project,sidecars,refine});setObservationError('');
        setAcceptedTarget(next.target_fps);}
      return next!==null;
    };
    const refreshObservations=async()=>{
      if(!token||disposed||observing)return;
      const controller=new AbortController();observing=controller;
      const epoch=observationEpoch,session=token;
      const timeout=window.setTimeout(()=>controller.abort(),5000);
      try{
        const response=await fetch('/api/stream/status?session='+encodeURIComponent(session),
          {credentials:'same-origin',cache:'no-store',signal:controller.signal});
        if(!response.ok)throw Error('Observations unavailable');
        const value=await readQualificationResponse(response);
        if(!disposed&&observations.current(epoch,session)){
          const row=value as Record<string,unknown>;
          if(!row||row.session!==session||!parseQualification(row.qualification))throw Error('Invalid observations');
          // A control response can overtake this poll; a valid older snapshot is harmless.
          observe(value,epoch);
        }
      }catch{
        if(!disposed&&observations.current(epoch,session))setObservationError('Live measurements unavailable; last snapshot retained.');
      }finally{clearTimeout(timeout);if(observing===controller)observing=null;}
    };
    const send=(frame:number,command='advance')=>{
      if(!token||disposed)return;
      if(command==='advance'&&(busy||frame===fence.frame))return;
      busy=true;fetching?.abort();decodeId++;
      const sequence=fence.request(frame);
      const epoch=observationEpoch;
      setStateful(previous=>previous?{...previous,requested_frame:frame,phase:'idle',last_reason:null}:null);
      const now=performance.now(),interval=1000/targetRef.current;
      nextSendAt=command==='advance'?Math.max(now,nextSendAt+interval):now+interval;
      if(command==='seek'){
        setPicture('');setDisplayed(null);
        if(pictureUrl){URL.revokeObjectURL(pictureUrl);pictureUrl='';}
      }
      void post('/api/stream/control',{session:token,command,sequence,frame,
        ...(command==='target_fps'?{target_fps:targetRef.current}:{})})
        .then(reply=>{if(!disposed){observe(reply,epoch);if(fence.accepts(sequence,frame)){
          const accepted=targetFPS(String(reply.target_fps));if(accepted!==null)setAcceptedTarget(accepted);
        }}})
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
        observationEpoch=observations.open(token);observe(reply);setCanDownload(true);
        const accepted=targetFPS(String(reply.target_fps));if(accepted!==null)setAcceptedTarget(accepted);
        exportReceipt.current=async()=>{
          if(disposed||exporting)return;
          const controller=new AbortController();exporting=controller;setDownloading(true);setDownloadStatus('');
          const session=token,epoch=observationEpoch,timeout=window.setTimeout(()=>controller.abort(),15000);
          try{
            const response=await fetch('/api/stream/qualification?session='+encodeURIComponent(session),
              {credentials:'same-origin',cache:'no-store',signal:controller.signal});
            const value=await readQualificationResponse(response);
            if(disposed||!observations.current(epoch,session))return;
            const receipt=qualificationReceipt(value,session);
            if(!receipt)throw Error('Invalid qualification receipt');
            const blob=new Blob([JSON.stringify(receipt,null,2)+'\n'],{type:'application/json'});
            const url=URL.createObjectURL(blob);downloadUrls.add(url);
            const link=document.createElement('a');link.href=url;link.download='framerudiments-preview-qualification.json';
            document.body.append(link);link.click();link.remove();
            window.setTimeout(()=>{URL.revokeObjectURL(url);downloadUrls.delete(url);},1000);
            setDownloadStatus('Measurement report downloaded. Release approval is separate.');observe(value);
          }catch{
            if(!disposed&&observations.current(epoch,session))setDownloadStatus('Could not download the current measurement report. Retry after the stream reconnects.');
          }finally{
            clearTimeout(timeout);if(exporting===controller)exporting=null;
            if(!disposed)setDownloading(false);
          }
        };
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
            // A response sampled before invalidation must not repopulate old cache coverage.
            observationEpoch=observations.open(token);observing?.abort();exporting?.abort();
            setQualification(null);setObservationSource(null);setDownloadStatus('');
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
    const observationTimer=window.setInterval(()=>{void refreshObservations();},1000);
    return ()=>{
      disposed=true;events?.close();fetching?.abort();decodeId++;
      observing?.abort();exporting?.abort();observations.close();exportReceipt.current=null;
      clearInterval(timer);clearInterval(observationTimer);clearTimeout(retryTimer);request.current=null;
      for(const url of downloadUrls)URL.revokeObjectURL(url);downloadUrls.clear();
      if(pictureUrl)URL.revokeObjectURL(pictureUrl);
      if(token)void post('/api/stream/close',{session:token}).catch(()=>{});
    };
  },[project,sidecars,fps,duration,refine]);
  const changeFPS=(n:number)=>{
    if(targetFPS(String(n))===null)return;
    targetRef.current=n;setTarget(n);
    request.current?.(previewFrame(timeRef.current,fps,duration),'target_fps');
  };
  const desiredFrame=previewFrame(time,fps,duration);
  const replaying=stateful?.enabled&&stateful.mode==='verified_prefix_replay';
  const replayStatus=statefulSummary(stateful);
  const currentObservation=sameDraftImage(observationSource,project,sidecars,refine)?qualification:null;
  const visible=sameDraftImage(pictureSource,project,sidecars,refine)&&!!picture;
  const draftTarget=targetFPS(targetDraft);
  const latency=currentObservation?.latency_ms,cache=currentObservation?.cache,resources=currentObservation?.resources;
  const q5Percent=currentObservation?100*currentObservation.cache.q5_frames/currentObservation.frame_count:null;
  return <div className="fr-stream-preview" role="region" aria-label="Adaptive cached streaming preview">
    <div className="fr-stream-image">
      {visible?<img src={picture} alt={`Cached preview frame ${displayed}`}/>:
        <span>{stateful?.phase==='unavailable'?'Stateful seek unavailable':'Rendering seek position…'}</span>}
      <small className={visible&&displayed!==desiredFrame?'fr-stream-stale':''}>
        {frameLabel(visible?displayed:null,desiredFrame)}{visible&&tier?` · ${tier}`:''}</small>
    </div>
    <div className="fr-stream-observations" aria-label="Preview measurements and controls">
      <div className="fr-stream-status" role="status">{status} <span>Preview only</span></div>
      {invalidation&&<small role="status">{invalidation}</small>}
      {replayStatus&&<small role="status">{replayStatus}</small>}
      <form className="fr-stream-target" onSubmit={event=>{event.preventDefault();if(draftTarget!==null)changeFPS(draftTarget);}}>
        <label>Target FPS<input type="number" min="15" max="120" step="1" value={targetDraft}
          aria-invalid={draftTarget===null} onChange={event=>setTargetDraft(event.target.value)}/></label>
        <button type="submit" disabled={draftTarget===null||draftTarget===acceptedTarget}>Apply</button>
        <span>{acceptedTarget===null?'Connecting':`Accepted ${acceptedTarget} FPS`}
          {acceptedTarget!==null&&target!==acceptedTarget?` · requesting ${target}`:''}</span>
      </form>
      {draftTarget===null&&<small role="status">Enter a whole number from 15 to 120.</small>}
      <dl className="fr-stream-metrics">
        <div><dt>Fresh render FPS</dt><dd>{formatRate(currentObservation?currentObservation.rates.fresh_fps:visible?metrics?.fresh_fps:null)}</dd></div>
        <div><dt>Decoded FPS</dt><dd>{formatRate(currentObservation?currentObservation.rates.decoded_fps:visible?metrics?.acknowledged_fps:null)}</dd></div>
        <div><dt>Foreground service p50 / p95</dt><dd>{formatRate(latency?.render.p50)} / {formatRate(latency?.render.p95)} ms</dd></div>
        <div><dt>Queue p50 / p95</dt><dd>{formatRate(latency?.queue.p50)} / {formatRate(latency?.queue.p95)} ms</dd></div>
        <div><dt>Q5 cache coverage</dt><dd>{q5Percent===null?'measuring':`${q5Percent.toFixed(1)}%`}
          {cache?` · ${cache.q5_frames} / ${currentObservation!.frame_count}`:''}</dd></div>
        <div><dt>PNG cache</dt><dd>{cache?`${formatBytes(cache.bytes)} / ${formatBytes(cache.quota_bytes)}`:'measuring'}</dd></div>
      </dl>
      <small>{replaying?'Feedback stays at authored Q5; replay and verification consume the reconstruction budget.':
        `Quality floor ${metrics?.quality_floor??'checking'}. Fresh FPS excludes cache hits and background refinement.`}
        {' '}Decoded FPS counts client acknowledgements, not physical display refreshes.</small>
      <label><input type="checkbox" checked={refine} onChange={event=>setRefine(event.target.checked)}/>
        Refine paused frames at authored Q5</label>
      {refine&&<small role="status">Q5 refinement: {refinement?.state??'waiting'} ·
        anchor {refinement?.anchor_frame??'not stopped'} · {refinement?.completed??0} completed ·
        {refinement?.skipped??0} already cached{refinement?.error_type?` · ${refinement.error_type}`:''}</small>}
      <details>
        <summary>Seek, cache and resource measurements</summary>
        <dl className="fr-stream-metrics">
          <div><dt>Seek processing p95</dt><dd>{formatRate(latency?.seek_ack.p95)} ms</dd></div>
          <div><dt>Seek to frame-ready p95</dt><dd>{formatRate(latency?.seek_first_frame.p95)} ms</dd></div>
          <div><dt>Seek to decoded ACK p95</dt><dd>{formatRate(latency?.seek_decode?.p95)} ms</dd></div>
          <div><dt>Cache hits / misses</dt><dd>{cache?`${cache.hits} / ${cache.misses}`:'measuring'}</dd></div>
          <div><dt>Upgrades / evictions</dt><dd>{cache?`${cache.upgrades} / ${cache.evictions}`:'measuring'}</dd></div>
          <div><dt>Server CPU time</dt><dd>{resources?.cpu_seconds==null?'not measured':`${resources.cpu_seconds.toFixed(2)} s`}</dd></div>
          <div><dt>Server memory</dt><dd>{formatBytes(resources?.rss_bytes)}</dd></div>
          <div><dt>GPU memory</dt><dd>{formatBytes(resources?.gpu_memory_bytes)}</dd></div>
        </dl>
        <small>Foreground service includes setup and cache publication. Seek timings end at the named server/decoded-ACK step.
          {' '}Server CPU and memory exclude renderer children. PNG bytes exclude the index and temporary files.
          {' '}Measurements describe this stream; hardware and encoded-output approval are separate.</small>
      </details>
      {observationError&&<small role="status">{observationError}</small>}
      <button type="button" disabled={!canDownload||!currentObservation||downloading} onClick={()=>{void exportReceipt.current?.();}}>
        {downloading?'Preparing report…':'Download measurement report'}</button>
      {downloadStatus&&<small role="status">{downloadStatus}</small>}
    </div>
  </div>;
}

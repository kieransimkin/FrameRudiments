import {useEffect, useMemo, useRef, useState} from 'react';
import {TimelineSequence, type TimelineLane, type TimelineBeatClock, type TimingEvent} from 'react-timeline-sequence';
import {type Document, type Sidecars, type TimedItem, type TimingKind, CUE_TYPES,
  editTiming, expandTiming, layerKeyframes, normalizedKeys, projectTiming, renameCue, serializeLrc,
  stylePreset, withKeyframe, withLayerRoute, withPreset, withStageInjection, graphPreset, withGraphPreset} from './model.ts';
import {sha256, zipEntries} from './archive.ts';

interface Session { project:Document; lines:TimedItem[]; words:TimedItem[]; cues:TimedItem[];
  cueFormat:'lrc'|'cue'; beatTimeline?:Document|null; audioAvailable:boolean; notes:string[]; }
interface Snapshot { project:Document; sidecars:Sidecars; dirty:Record<'lines'|'words'|'cues',boolean>; }
interface History {current:Snapshot; past:Snapshot[]; future:Snapshot[]}
const fmt=(n:number)=>Number(n).toFixed(3);

function download(bytes:BlobPart, filename:string, mime:string) {
  const blob=new Blob([bytes],{type:mime}),url=URL.createObjectURL(blob);
  const a=document.createElement('a');a.href=url;a.download=filename;document.body.append(a);a.click();a.remove();
  setTimeout(()=>URL.revokeObjectURL(url),30000);
}
function message(error:unknown) {return error instanceof Error ? error.message : String(error);}
function beatModel(session:Session|null):TimelineBeatClock|null {
  if (!session) return null;
  const source=projectTiming(session.project),cfg=session.project.clock||{};
  // Repeated excerpts must not claim a continuous source-frame beat grid.
  if(source.repeats>1)return null;
  const timeline=session.beatTimeline;
  if(timeline?.schema==='stemlab.beat-timeline.v1' && Array.isArray(timeline.beats)){
    const sr=timeline.source_audio?.sample_rate;
    if(!Number.isSafeInteger(sr) || sr!==source.rate)return null;
    const beats=timeline.beats.filter((r:Document)=>r.frame>=(session.project.audio.start_sample??0) &&
      r.frame<session.project.audio.end_sample).map((r:Document)=>({beat:r.beat,frame:r.frame-(session.project.audio.start_sample??0)}));
    if(beats.length>=2 && beats.some((r:Document)=>r.beat===timeline.origin_beat))
      return {mode:'variable',pips_per_beat:64,source_audio:{sample_rate:sr,num_frames:session.project.audio.end_sample-session.project.audio.start_sample},
       origin_beat:timeline.origin_beat,beats};
    return null;
  }
  if(typeof cfg.bpm==='number' && cfg.bpm>=20 && cfg.bpm<=400){
    const period=60/cfg.bpm,origin=(cfg.origin??0)-source.start;
    const firstBeatSeconds=((origin%period)+period)%period;
    return {mode:'fixed',bpm:cfg.bpm,firstBeatSeconds};
  }
  return null;
}
export function App() {
 const [session,setSession]=useState<Session|null>(null);
 const [history,setHistory]=useState<History|null>(null);
 const [error,setError]=useState('');
 const [info,setInfo]=useState('');
 const [time,setTime]=useState(0);
 const [selected,setSelected]=useState('');
 const [selectedTiming,setSelectedTiming]=useState<{kind:TimingKind,id:string}|null>(null);
 const [presetFile,setPresetFile]=useState<File|null>(null);
 const [graphFile,setGraphFile]=useState<File|null>(null);
 const [injectStage,setInjectStage]=useState(1);
 const [localAudio,setLocalAudio]=useState<File|null>(null);
 const [audioUrl,setAudioUrl]=useState('');
 const [preview,setPreview]=useState('');
 const [quality,setQuality]=useState<'fast'|'full'>('fast');
 const [previewError,setPreviewError]=useState('');
 const [cueType,setCueType]=useState('SECTION');
 const [cueTitle,setCueTitle]=useState('');
 const [editProperty,setEditProperty]=useState('opacity');
 const [editValue,setEditValue]=useState(.5);
 const requestCount=useRef(0);
 const timeRef=useRef(0);
 const [previewAt,setPreviewAt]=useState(0);
 useEffect(()=>{timeRef.current=time;},[time]);
 useEffect(()=>{const task=window.setInterval(()=>setPreviewAt(timeRef.current),230);
   return ()=>window.clearInterval(task);},[]);
 const current=history?.current;
 const project=current?.project;
 const duration=project?projectTiming(project).duration:0;

 useEffect(()=>{
   let alive=true;
   fetch('/api/project').then(async r=>{if(!r.ok)throw Error(`Server returned ${r.status}`);return await r.json() as Session;})
   .then(value=>{if(!alive)return;setSession(value);
      setHistory({past:[],future:[],current:{project:value.project,sidecars:{lines:value.lines,words:value.words,cues:value.cues,cueFormat:value.cueFormat},
        dirty:{lines:false,words:false,cues:false}}});
      setSelected(`layer:${value.project.layers?.[0]?.id||''}`);
   }).catch(e=>{if(alive)setError(`Unable to load project: ${message(e)}`);});
   return ()=>{alive=false;};
 },[]);
 useEffect(()=>{
   if(!localAudio){setAudioUrl(session?.audioAvailable?'/api/audio':'');return;}
   const url=URL.createObjectURL(localAudio);setAudioUrl(url);
   return ()=>URL.revokeObjectURL(url);
 },[localAudio,session?.audioAvailable]);

 const commit=(change:(s:Snapshot)=>Snapshot)=>{
   setHistory(previous=>{if(!previous)return previous;
      try{const updated=change(previous.current);setError('');
        return {current:updated,past:[...previous.past,previous.current].slice(-100),future:[]};
      }catch(exc){setError(message(exc));return previous;}
   });
 };
 const undo=()=>setHistory(previous=>!previous?.past.length?previous:{
   current:previous.past[previous.past.length-1],past:previous.past.slice(0,-1),future:[previous.current,...previous.future]});
 const redo=()=>setHistory(previous=>!previous?.future.length?previous:{
   current:previous.future[0],past:[...previous.past,previous.current],future:previous.future.slice(1)});

 const onTimingEdit=(kind:TimingKind,item:TimingEvent)=>{
   const key=kind==='line'?'lines':kind==='word'?'words':'cues';
   commit(s=>({...s,sidecars:{...s.sidecars,[key]:editTiming(s.sidecars[key],item,s.project,kind)},dirty:{...s.dirty,[key]:true}}));
 };
 const onKeyEdit=(target:string,prop:string,at:number,item:TimingEvent)=>commit(s=>{
   const currentKey=layerKeyframes(s.project).find(x=>x.target===target&&x.property===prop)?.keys.find(x=>x.time===at);
   if(!currentKey)throw Error('Keyframe no longer exists');
   return {...s,project:withKeyframe(s.project,target,prop,{...currentKey,time:item.start},at)};
 });
 const keys=useMemo(()=>project?layerKeyframes(project):[],[project]);
 const timelineClock=useMemo(()=>beatModel(session),[session]);
 const lanes=useMemo<TimelineLane[]>(()=>{
  if(!project||!current)return[];
  const sidecars=current.sidecars;
  const views:TimelineLane[]=[];
  const addTimed=(id:string,title:string,kind:TimingKind,items:TimedItem[])=>{
    if(!items.length)return;
    let output=expandTiming(items,project);
    if(!output.length)return;
    const parentLines=expandTiming(sidecars.lines,project);
    const limits=kind==='word'?parentLines.map((x,i,all)=>({id:x.id,start:x.start,end:all[i+1]?.start??duration})) : undefined;
    if(kind==='word'&&limits?.length)output=output.map(row=>{
      const parent=limits.filter(line=>line.start<=row.start && row.start<line.end).at(-1);
      return parent?{...row,parentId:parent.id}:row;
    });
    views.push({id,title,kind,content:{type:'timing',kind,items:output,editable:kind!=='cue'||sidecars.cueFormat!=='cue',
      clock:timelineClock,snap:false,parents:limits,
      selectedId:selectedTiming?.id,onSelect:id=>setSelectedTiming({kind,id}),
      onEdit:item=>onTimingEdit(kind,item)}});
  };
  addTimed('lines','Lyric lines','line',sidecars.lines);
  addTimed('words','Words','word',sidecars.words);
  addTimed('cues','Section cues','cue',sidecars.cues);
  for(const layer of project.layers||[]){
    const start=layer.start_seconds??0,end=layer.end_seconds??duration;
    views.push({id:`layer-${layer.id}`,title:`${layer.id} · ${layer.renderer||layer.type||'sprite'}`,
      kind:'layer',content:{type:'blocks',items:[{start:Math.max(0,start),end:Math.min(duration,end),label:layer.id}]}});
  }
  for(const key of keys){
    views.push({id:`key-${key.target}-${key.property}`,title:`${key.target} · ${key.property}`,
      kind:'animation',content:{type:'timing',kind:'cue',editable:true,
      items:key.keys.map((point,i)=>({id:`key:${key.target}:${key.property}:${i}`,start:point.time,
        label:`${key.property} ${fmt(point.value)}`,className:'fr-keyframe'})),
      onSelect:id=>{setSelected(key.target);const part=Number(id.split(':').pop());setEditProperty(key.property);
        setEditValue(key.keys[part]?.value??.5);},
      onEdit:item=>{const index=Number(item.id.split(':').pop());if(key.keys[index])onKeyEdit(key.target,key.property,key.keys[index].time,item);}}});
  }
  for(const [stem,source] of Object.entries(project.stem_sources||{}))
    views.push({id:`stem-${stem}`,title:`StemLab · ${stem}`,kind:'stem',
       content:{type:'text',text:`Pinned ${(source as Document).kind||'recording'} · source clock preserved`}});
  return views;
 },[project,current,keys,session,duration,selectedTiming]);

 useEffect(()=>{
   if(!project||!current)return;
   const index=++requestCount.current;
   const controller=new AbortController();
   const job=setTimeout(()=>{
     const frame=Math.max(0,Math.min(Math.ceil(duration*(project.fps||60))-1,Math.round(previewAt*(project.fps||60))));
     const sidecars:Record<string,TimedItem[]>={};
     for(const key of ['lines','words','cues'] as const)if(current.dirty[key])sidecars[key]=current.sidecars[key];
     fetch('/api/preview',{method:'POST',headers:{'Content-Type':'application/json'},
       body:JSON.stringify({project,frame,quality,sidecars}),signal:controller.signal})
       .then(async r=>{if(!r.ok){let err;try{err=await r.json();}catch{}throw Error(err?.error||`Preview status ${r.status}`);}return r.blob();})
       .then(blob=>{if(index===requestCount.current){const url=URL.createObjectURL(blob);setPreview(old=>{if(old)URL.revokeObjectURL(old);return url;});setPreviewError('');}})
       .catch(e=>{if(e.name!=='AbortError'&&index===requestCount.current)setPreviewError(message(e));});
   },30);
   return ()=>{clearTimeout(job);controller.abort();};
 },[project,current,previewAt,quality,duration]);
 useEffect(()=>()=>{if(preview)URL.revokeObjectURL(preview);},[preview]);

 const changeKey=()=>{
   if(!project)return;
   commit(s=>({...s,project:withKeyframe(s.project,selected,editProperty,{time:Math.min(duration-0.001,Math.max(0,time)),value:editValue})}));
 };
 const renameSelectedCue=()=>{
   if(!current||!project||selectedTiming?.kind!=='cue')return;
   const item=expandTiming(current.sidecars.cues,project).find(x=>x.id===selectedTiming.id);
   if(!item)return;
   const label=renameCue(item.label,cueType,cueTitle);
   commit(s=>({...s,sidecars:{...s.sidecars,cues:editTiming(s.sidecars.cues,{...item,label},s.project,'cue'),cueFormat:'lrc'},
     dirty:{...s.dirty,cues:true}}));
 };
 const saveStyle=()=>{
   const layer=project?.layers?.find((row:Document)=>`layer:${row.id}`===selected);
   if(!layer)return setError('Choose a Rudiment layer');
   try{download(JSON.stringify(stylePreset(layer),null,2),'framerudiments-style-preset.json','application/json');}catch(e){setError(message(e));}
 };
 const loadStyle=async()=>{
   if(!presetFile)return;
   try{const preset=JSON.parse(await presetFile.text());commit(s=>({...s,project:withPreset(s.project,selected,preset)}));}
   catch(e){setError(message(e));}
 };
 const saveGraph=()=>{
   if(!project)return;
   try{download(JSON.stringify(graphPreset(project),null,2),'framerudiments-effect-graph.json','application/json');}
   catch(e){setError(message(e));}
 };
 const loadGraph=async()=>{
   if(!graphFile)return;
   try{const preset=JSON.parse(await graphFile.text());
     commit(s=>({...s,project:withGraphPreset(s.project,preset)}));
   }catch(e){setError(message(e));}
 };
 const exportWork=async()=>{
   if(!project||!current)return;
   try{
    const updated=structuredClone(project),rows:Record<string,string>={};
    for(const [kind,field] of [['lines','lyrics'],['words','word_lyrics'],['cues','cues']] as const){
      if(!current.dirty[kind])continue;
      const file=`authoring-${kind}.lrc`,value=serializeLrc(current.sidecars[kind]);
      rows[file]=value;
      updated[field]={path:file,sha256:await sha256(value)};
    }
    rows['project.json']=JSON.stringify(updated,null,2)+'\n';
    rows['AUTHORING-README.txt']='FrameRudiments authoring bundle\nExtract these files into the ORIGINAL project directory.\n'+
      'Do not move or modify the existing SHA-pinned master, fonts, models or recordings.\n'+
      'Run framerudiments plan and preflight before rendering.\n'+
      'Original project files have not been modified by the authoring preview server.\n';
    const bytes=zipEntries(rows);
    download(bytes as BlobPart,'framerudiments-authoring-export.zip','application/zip');setInfo('Exported a portable project + edited sidecar bundle');
   }catch(e){setError(message(e));}
 };
 const selectedLayer=project?.layers?.find((l:Document)=>`layer:${l.id}`===selected);
 const route=project?.visualiser_routing?.elements?.[selected];
 const routeMode=typeof route?.feed==='object'?route.feed.mode:route?.feed??'continuous';
 const routePips=typeof route?.feed==='object'?route.feed.duration_pips:64;
 return <div className="fr-app">
  <header className="fr-top"><div><small>DanceFlow / FrameRudiments</small><h1>Visual Authoring</h1></div>
    <div className="fr-actions"><button onClick={undo} disabled={!history?.past.length}>Undo</button>
      <button onClick={redo} disabled={!history?.future.length}>Redo</button>
      <button className="primary" onClick={exportWork} disabled={!project}>Export project & sidecars</button></div></header>
  {error&&<div className="fr-alert" role="alert">{error} <button onClick={()=>setError('')}>Dismiss</button></div>}
  {info&&<div className="fr-note" role="status">{info}</div>}
  {!project?<main className="fr-empty">Loading the pinned project…<p>Run through <code>scripts/authoring_preview.py</code> after building <code>authoring/</code>.</p></main>:
  <main className="fr-columns">
   <section className="fr-center">
    <div className="fr-preview-head"><strong>Renderer preview</strong><label className="fr-scrub">Position <input aria-label="Seek draft preview" type="range" min="0" max={duration} step={1/(project.fps||60)}
      value={time} onChange={e=>setTime(Number(e.target.value))}/></label><label>Quality <select value={quality} onChange={e=>setQuality(e.target.value as 'fast'|'full')}>
      <option value="fast">Draft (1×)</option><option value="full">Production AA</option></select></label>
      <span>Frame {Math.round(time*(project.fps||60))}</span></div>
    <div className="fr-preview"><div className="fr-canvas">{preview?<img src={preview} alt="FrameRudiments composited draft frame"/>:<span>Preparing preview…</span>}</div>
      {previewError&&<p className="fr-alert">{previewError}</p>}</div>
    <div className="fr-transport-extra"><label>Exact excerpt audio <input type="file" accept="audio/*,.wav,.mp3,.flac,.m4a,.ogg"
      onChange={e=>setLocalAudio(e.target.files?.[0]??null)}/></label>
      {!session?.audioAvailable&&<span>Choose a rendered excerpt for sample-aligned playback.</span>}
      {session?.notes?.map((note,i)=><span key={i}>{note}</span>)}</div>
    <div className="fr-timeline">
     <TimelineSequence audioSrc={audioUrl||'/api/audio'} duration={duration} lanes={lanes} title="Project timeline"
       subtitle={`${project.title||'Untitled'} · ${project.fps||60} FPS · ${lanes.length} tracks`}
       beatClock={timelineClock} showBeatGrid={!!timelineClock} initialZoom={0} laneControls wheelNavigation
       onTimeChange={t=>setTime(Math.min(duration,Math.max(0,t)))}
       onPlaybackError={e=>setInfo('Audio unavailable: load an exact rendered excerpt to enable transport')}
       footer={<span>Drag or nudge timing/keyframes; source files remain unchanged until export.</span>}/>
    </div>
   </section>
   <aside className="fr-side">
    <section><h2>Layers & effect graph</h2><p>Choose a layer to edit its clean-feed routing, keyframes or style preset.</p>
     <div className="fr-graph"><div className="fr-node-title">Scene inputs</div>
       {(project.layers||[]).map((l:Document)=><button key={l.id} className={'fr-node '+(selected===`layer:${l.id}`?'chosen':'')}
         onClick={()=>setSelected(`layer:${l.id}`)}>{l.id}<small>{l.renderer||l.type||'sprite'}</small></button>)}
       {(project.effects||[]).length>0&&<><div className="fr-arrow">↓ composited input</div>
        {(project.effects||[]).map((fx:Document,i:number)=><div key={i} className="fr-node fr-fx">{fx.engine} <small>{fx.id||`stage ${i+1}`}</small></div>)}</>}
       <div className="fr-arrow">↓</div><div className="fr-node fr-out">Final frame</div>
     </div></section>
    <section><h2>Selected layer</h2><label>Layer <select value={selected} onChange={e=>setSelected(e.target.value)}>
      {(project.layers||[]).map((l:Document)=><option key={l.id} value={`layer:${l.id}`}>{l.id}</option>)}</select></label>
      {selectedLayer?.type==='rudiment'&&<p>Renderer: <code>{selectedLayer.renderer}</code></p>}
      {(project.effects||[]).length>0&&<div className="fr-grid-row"><label>Visualizer feed <select value={routeMode} onChange={e=>commit(s=>({...s,project:withLayerRoute(s.project,selected,e.target.value,route?.overlay??false,routePips)}))}>
        <option value="continuous">Continuous</option><option value="first_pips">First N pips</option><option value="never">Never</option></select></label>
        {routeMode==='first_pips'&&<label>Native pips <input type="number" min="0" max="65536" value={routePips}
          onChange={e=>commit(s=>({...s,project:withLayerRoute(s.project,selected,routeMode,route?.overlay??false,Number(e.target.value))}))}/></label>}
        <label><input type="checkbox" checked={route?.overlay??false} onChange={e=>commit(s=>({...s,project:withLayerRoute(s.project,selected,routeMode,e.target.checked,routePips)}))}/>
          Clean overlay after GPU</label>
        {(project.effects||[]).length>1&&<div className="fr-stage-controls"><label>Inject before stage <select value={injectStage} onChange={e=>setInjectStage(Number(e.target.value))}>
          {(project.effects||[]).slice(1).map((fx:Document,i:number)=><option key={i} value={i+1}>{i+2}: {fx.id||fx.engine}</option>)}
         </select></label><label><input type="checkbox"
           checked={!!project.visualiser_stage_injections?.find((row:Document)=>row.before===injectStage)?.elements?.includes(selected)}
           onChange={e=>commit(s=>({...s,project:withStageInjection(s.project,selected,injectStage,e.target.checked)}))}/>
           Feed original layer into stage {injectStage+1}</label></div>}
       </div>}
    </section>
    <section><h2>Animation keyframes</h2><p>Output-time position and normalized property value (0–1).</p>
      <div className="fr-grid-row"><label>Property<select value={editProperty} onChange={e=>setEditProperty(e.target.value)}>
        {['opacity','x','y','rotation'].map(x=><option key={x}>{x}</option>)}</select></label>
      <label>Value <input type="number" min="0" max="1" step="0.01" value={editValue} onChange={e=>setEditValue(Number(e.target.value))}/></label></div>
      <button onClick={changeKey}>Add/update key at {fmt(time)} s</button>
      <div className="fr-keys">{keys.filter(x=>x.target===selected).map(row=><div key={row.property}>
       <strong>{row.property}</strong> {row.keys.map((point,i)=><button key={i} onClick={()=>{setEditProperty(row.property);setEditValue(point.value);setTime(point.time);}}>{fmt(point.time)}s / {fmt(point.value)}</button>)}</div>)}</div>
    </section>
    <section><h2>Section cue names</h2><p>Rename before moving CD-CUE titles; standard section names are enforced.</p>
      <label>Prefix <select value={cueType} onChange={e=>setCueType(e.target.value)}>{CUE_TYPES.map(x=><option key={x}>{x}</option>)}</select></label>
      <label>Label <input value={cueTitle} onChange={e=>setCueTitle(e.target.value)} placeholder="INTRO / DROP 1"/></label>
      <button onClick={renameSelectedCue} disabled={selectedTiming?.kind!=='cue'}>Rename selected cue</button></section>
    <section><h2>Reusable effect graphs</h2><p>Export existing routing, inter-stage injection, matte and displacement edges without source files.</p>
      <button onClick={saveGraph}>Export graph</button>
      <label>Import graph <input type="file" accept="application/json,.json" onChange={e=>setGraphFile(e.target.files?.[0]||null)}/></label>
      <button onClick={loadGraph} disabled={!graphFile}>Apply validated graph</button></section>
    <section><h2>Style presets</h2><button onClick={saveStyle} disabled={!selectedLayer||selectedLayer.type!=='rudiment'}>Export selected style</button>
      <label>Import preset <input type="file" accept="application/json,.json" onChange={e=>setPresetFile(e.target.files?.[0]||null)}/></label>
      <button onClick={loadStyle} disabled={!presetFile}>Apply style preset</button></section>
   </aside>
  </main>}
  <footer className="fr-footer">Built on <a href="https://github.com/kieransimkin/react-timeline-sequence">react-timeline-sequence 0.4.0</a> · Native StemLab/DanceRudiments timing · Local preview only · No server-side project mutations</footer>
 </div>;
}

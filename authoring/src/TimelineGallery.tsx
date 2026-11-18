/** Browser-only public demonstration using the exact shared timeline control.
 * No native preview, local-file access, server API, remote tracks, or secret inputs.
 * Every annotation and audio sample is synthetic and intentionally releasable.
 */
import {useMemo, useState} from 'react';
import {TimelineSequence, type TimelineBeatClock, type TimelineLane, type TimingEvent} from 'react-timeline-sequence';
import 'react-timeline-sequence/styles.css';
import './timeline-gallery.css';

const DURATION = 16;
const BPM = 120;
const clock: TimelineBeatClock = {mode: 'fixed', bpm: BPM, firstBeatSeconds: 0};
const LINES: TimingEvent[] = [
  {id:'line-0',start:0,label:'An empty canvas, a light in time'},
  {id:'line-1',start:4,label:'A pulse becomes a moving line'},
  {id:'line-2',start:8,label:'The colours bloom, then fall away'},
  {id:'line-3',start:12,label:'And every frame is ours to play'},
];
const INITIAL_WORDS: TimingEvent[] = [
  {id:'word-0',parentId:'line-0',start:0,label:'An'},
  {id:'word-1',parentId:'line-0',start:0.5,label:'empty'},
  {id:'word-2',parentId:'line-0',start:1,label:'canvas'},
  {id:'word-3',parentId:'line-0',start:2,label:'light'},
  {id:'word-4',parentId:'line-1',start:4,label:'A'},
  {id:'word-5',parentId:'line-1',start:4.5,label:'pulse'},
  {id:'word-6',parentId:'line-1',start:5,label:'becomes'},
  {id:'word-7',parentId:'line-1',start:6,label:'moving'},
  {id:'word-8',parentId:'line-2',start:8,label:'The'},
  {id:'word-9',parentId:'line-2',start:8.5,label:'colours'},
  {id:'word-10',parentId:'line-2',start:9,label:'bloom'},
  {id:'word-11',parentId:'line-3',start:12,label:'And'},
  {id:'word-12',parentId:'line-3',start:12.5,label:'every'},
  {id:'word-13',parentId:'line-3',start:13,label:'frame'},
  {id:'word-14',parentId:'line-3',start:14,label:'play'},
];
const CUES: TimingEvent[] = [
  {id:'cue-0',start:0,label:'INTRO'},
  {id:'cue-1',start:4,label:'VERSE'},
  {id:'cue-2',start:8,label:'DROP'},
  {id:'cue-3',start:12,label:'OUTRO'},
];
const KEYFRAMES: TimingEvent[] = [
  {id:'key-0',start:0,label:'Opacity 0'},
  {id:'key-1',start:2,label:'Opacity 1'},
  {id:'key-2',start:8,label:'Opacity 1'},
  {id:'key-3',start:15,label:'Opacity 0'},
];

export function TimelineGallery() {
  const [words,setWords]=useState(INITIAL_WORDS);
  const [time,setTime]=useState(0);
  const [selected,setSelected]=useState('');
  const [showWords,setShowWords]=useState(true);
  const [showKeys,setShowKeys]=useState(true);
  const lanes=useMemo<TimelineLane[]>(()=>{
    const out:TimelineLane[]=[
      {id:'lines',title:'Lyric lines',kind:'line',content:{type:'timing',kind:'line',items:LINES,editable:false,clock,snap:false}},
    ];
    if(showWords)out.push({id:'words',title:'Word timings',kind:'word',content:{type:'timing',kind:'word',
      items:words,editable:true,clock,snap:false,
      parents:LINES.map((line,i)=>({id:line.id,start:line.start,end:LINES[i+1]?.start??DURATION})),
      selectedId:selected,
      onSelect:id=>setSelected(id),
      onEdit:item=>setWords(previous=>previous.map(word=>word.id===item.id?{...word,start:item.start}:word)),
    }});
    out.push(
      {id:'cues',title:'Section cues',kind:'cue',content:{type:'timing',kind:'cue',items:CUES,editable:false}},
      {id:'visual',title:'Visualiser & particles',kind:'layer',content:{type:'blocks',items:[
        {start:0,end:8,label:'Circular wave'}, {start:8,end:16,label:'Spectral particles'},
      ]}},
      {id:'character',title:'Character overlay',kind:'layer',content:{type:'blocks',items:[
        {start:4,end:14,label:'Character layer'},
      ]}},
    );
    if(showKeys)out.push({id:'keys',title:'Animation · opacity',kind:'animation',content:{
      type:'timing',kind:'cue',items:KEYFRAMES,editable:false}});
    out.push({id:'stem',title:'Source provenance',kind:'stem',content:{type:'text',
      text:'Original synthetic 120 BPM test tone · no actual StemLab analysis'}});
    return out;
  },[words,selected,showWords,showKeys]);
  return <div className="fr-gallery-app">
    <header className="fr-gallery-head">
      <div className="fr-gallery-heading">
        <span className="fr-gallery-eyebrow">FrameRudiments / Interactive demo</span>
        <h1>Timeline viewer</h1>
        <p>Explore the same <strong>react-timeline-sequence</strong> control used by the native FrameRudiments authoring workspace. Play, scrub, zoom and navigate; drag a word marker to change its local timing.</p>
      </div>
      <nav aria-label="Gallery navigation"><a href="../">Visualisation atlas</a><a href="../m11-5/">Character showcase</a><a href="./example.otio" download>Download OTIO example</a><a href="https://github.com/kieransimkin/FrameRudiments/tree/main/authoring">Editor source</a></nav>
    </header>
    <section className="fr-gallery-toolbox" aria-label="Timeline settings">
      <div className="fr-gallery-stat"><span>Current playhead</span><strong>{time.toFixed(2)} s</strong></div>
      <div className="fr-gallery-stat"><span>Native beat grid</span><strong>{BPM} BPM · 64 pips / beat</strong></div>
      <label><input type="checkbox" checked={showWords} onChange={e=>setShowWords(e.target.checked)}/> Word timings</label>
      <label><input type="checkbox" checked={showKeys} onChange={e=>setShowKeys(e.target.checked)}/> Keyframes</label>
      <button type="button" onClick={()=>{setWords(INITIAL_WORDS);setSelected('');}}>Reset markers</button>
    </section>
    <main className="fr-gallery-timeline" aria-label="Shared native timeline component">
      <TimelineSequence audioSrc="./synthetic-click.wav" duration={DURATION} lanes={lanes}
        title="Synthetic Clock Study" subtitle={`16-second sample · ${BPM} BPM · ${lanes.length} tracks`}
        beatClock={clock} showBeatGrid initialZoom={0} laneControls wheelNavigation
        onTimeChange={t=>setTime(Math.max(0,Math.min(DURATION,t)))}
        onPlaybackError={()=>setSelected('Audio playback unavailable in this browser')}
        footer={<span>All timing and audio are original synthetic fixtures. Edits affect this tab only; no project or source file is changed.</span>}/>
    </main>
    <footer className="fr-gallery-foot"><p><strong>About this demo.</strong> This is a real browser build of the timeline component, not a screenshot. It is a standalone Pages sample and cannot connect to the native renderer, read private projects or preview Butterchurn/projectM. For full local project editing and native preview, use <a href="https://github.com/kieransimkin/FrameRudiments/tree/main/authoring">the authoring workspace</a>.</p><p><a href="./catalogue.json">Generated audio and timing manifest</a> · <a href="./example.otio">OTIO companion (fixed fixture, not browser edits)</a> · <a href="https://github.com/kieransimkin/FrameRudiments/blob/main/docs/authoring-workspace.md">Editor documentation</a></p></footer>
  </div>;
}

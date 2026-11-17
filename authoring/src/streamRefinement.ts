/** S4 observes the released timeline's media element; it never drives its clock. */
export type CursorCommand='seek'|'play'|'pause';
export class StoppedCursor {
  private frame:number;
  private playing=false;
  private changedAt:number;
  private stopped=false;
  private settleMs:number;
  constructor(frame:number,now:number,settleMs=120){
    if(!Number.isInteger(frame)||frame<0||!Number.isFinite(now)||!Number.isFinite(settleMs)||settleMs<0)
      throw Error('Invalid stopped cursor');
    this.frame=frame;this.changedAt=now;this.settleMs=settleMs;
  }
  observe(frame:number,playing:boolean,now:number):CursorCommand|null{
    if(!Number.isInteger(frame)||frame<0||!Number.isFinite(now)||now<this.changedAt)
      throw Error('Invalid cursor sample');
    const moved=frame!==this.frame,wasPlaying=this.playing;
    this.frame=frame;this.playing=playing;
    if(moved||playing!==wasPlaying){this.changedAt=now;this.stopped=false;}
    if(playing&&!wasPlaying)return 'play';
    if(!playing&&moved)return 'seek';
    if(!playing&&!this.stopped&&now-this.changedAt>=this.settleMs){
      this.stopped=true;return 'pause';
    }
    return null;
  }
}

/** Local wrapper belongs to FrameRudiments, not a private timeline API. */
export function observeTimelinePlayback(root:Element|null,update:(playing:boolean)=>void):()=>void{
  const refresh=()=>{
    const audio=root?.querySelector('audio');
    update(!!audio&&!audio.paused&&!audio.ended&&!audio.error);
  };
  const capture={capture:true};
  const names=['play','playing','pause','ended','emptied','error'];
  for(const name of names)root?.addEventListener(name,refresh,capture);
  refresh();
  return ()=>{for(const name of names)root?.removeEventListener(name,refresh,capture);};
}

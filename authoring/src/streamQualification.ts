/** Bounded, content-free S7 observations. These values never drive the source clock. */
export const QUALIFICATION_SCHEMA = 'framerudiments.preview-stream-qualification.v1';
type Distribution = {p50:number|null;p95:number|null;samples:number};
export type QualificationSummary = {
  schema:typeof QUALIFICATION_SCHEMA;sample_sequence:number;elapsed_ms:number;target_fps:number;frame_count:number;
  rates:{fresh_fps:number|null;decoded_fps:number|null;
    acknowledgement_source:'client_decode_ack'|'harness_png_decode';window_seconds:number;
    fresh_fps_p50?:number|null;fresh_fps_p95?:number|null;decoded_fps_p50?:number|null;decoded_fps_p95?:number|null};
  latency_ms:{render:Distribution;queue:Distribution;seek_ack:Distribution;seek_first_frame:Distribution;seek_decode?:Distribution};
  cache:{frames:number;q5_frames:number;bytes:number;quota_bytes:number;
    hits:number;misses:number;upgrades:number;evictions:number};
  resources:{scope:'preview_server_process';cpu_seconds:number|null;rss_bytes:number|null;gpu_memory_bytes:number|null};
};

const record = (value:unknown):Record<string,unknown>|null =>
  value!==null&&typeof value==='object'&&!Array.isArray(value)?value as Record<string,unknown>:null;
const integer = (value:unknown,max=Number.MAX_SAFE_INTEGER):value is number =>
  Number.isSafeInteger(value)&&(value as number)>=0&&(value as number)<=max;
const finite = (value:unknown,max=1e12):value is number =>
  typeof value==='number'&&Number.isFinite(value)&&value>=0&&value<=max;
const optional = (value:unknown,max=1e12):value is number|null =>value===null||finite(value,max);
const target = (value:unknown):value is number=>integer(value,120)&&(value as number)>=15;

/** Any integer 15–120 is valid, including authored rates absent from quick presets. */
export function targetFPS(value:string):number|null {
  if(!/^\d{1,3}$/.test(value.trim()))return null;
  const result=Number(value.trim());return target(result)?result:null;
}

function distribution(value:unknown):Distribution|null {
  const row=record(value);
  if(!row||!integer(row.samples,4096)||!optional(row.p50)||!optional(row.p95)||
      (row.samples===0&&(row.p50!==null||row.p95!==null))||
      (row.samples>0&&(row.p50===null||row.p95===null))||
      (typeof row.p50==='number'&&typeof row.p95==='number'&&row.p95<row.p50))return null;
  return {samples:row.samples,p50:row.p50,p95:row.p95};
}

/** Copy only measured numeric fields and fixed enums; never render arbitrary report text. */
export function parseQualification(value:unknown):QualificationSummary|null {
  const row=record(value);
  if(!row||row.schema!==QUALIFICATION_SCHEMA||!integer(row.sample_sequence)||!finite(row.elapsed_ms)||
      !target(row.target_fps)||!integer(row.frame_count,10_000_000)||row.frame_count===0)return null;
  const rates=record(row.rates),latency=record(row.latency_ms),cache=record(row.cache),resources=record(row.resources);
  if(!rates||!latency||!cache||!resources||!optional(rates.fresh_fps,1e7)||!optional(rates.decoded_fps,1e7)||
      !finite(rates.window_seconds,60)||rates.window_seconds===0||
      !['client_decode_ack','harness_png_decode'].includes(rates.acknowledgement_source as string))return null;
  const render=distribution(latency.render),queue=distribution(latency.queue),
    seek_ack=distribution(latency.seek_ack),seek_first_frame=distribution(latency.seek_first_frame);
  if(!render||!queue||!seek_ack||!seek_first_frame)return null;
  const seek_decode=latency.seek_decode===undefined?undefined:distribution(latency.seek_decode);
  if(seek_decode===null)return null;
  const percentiles:Partial<QualificationSummary['rates']>={};
  for(const name of ['fresh_fps_p50','fresh_fps_p95','decoded_fps_p50','decoded_fps_p95'] as const){
    if(rates[name]!==undefined){
      if(!optional(rates[name],1e7))return null;
      percentiles[name]=rates[name];
    }
  }
  for(const name of ['frames','q5_frames','bytes','quota_bytes','hits','misses','upgrades','evictions'])
    if(!integer(cache[name]))return null;
  if((cache.q5_frames as number)>(cache.frames as number)||(cache.frames as number)>row.frame_count||
      resources.scope!=='preview_server_process'||!optional(resources.cpu_seconds)||
      !optional(resources.rss_bytes,Number.MAX_SAFE_INTEGER)||!optional(resources.gpu_memory_bytes,Number.MAX_SAFE_INTEGER))return null;
  return {
    schema:QUALIFICATION_SCHEMA,sample_sequence:row.sample_sequence,elapsed_ms:row.elapsed_ms,
    target_fps:row.target_fps,frame_count:row.frame_count,
    rates:{fresh_fps:rates.fresh_fps,decoded_fps:rates.decoded_fps,window_seconds:rates.window_seconds,
      acknowledgement_source:rates.acknowledgement_source as QualificationSummary['rates']['acknowledgement_source'],...percentiles},
    latency_ms:{render,queue,seek_ack,seek_first_frame,...(seek_decode?{seek_decode}:{})},
    cache:{frames:cache.frames as number,q5_frames:cache.q5_frames as number,bytes:cache.bytes as number,
      quota_bytes:cache.quota_bytes as number,hits:cache.hits as number,misses:cache.misses as number,
      upgrades:cache.upgrades as number,evictions:cache.evictions as number},
    resources:{scope:'preview_server_process',cpu_seconds:resources.cpu_seconds,
      rss_bytes:resources.rss_bytes,gpu_memory_bytes:resources.gpu_memory_bytes},
  };
}

/** Accept only this session's observations, monotonically, independently of cursor motion. */
export class QualificationFence {
  private token='';
  private serial=-1;
  private epoch=0;
  open(token:string):number {this.token=token;this.serial=-1;return ++this.epoch;}
  close():void {this.token='';this.serial=-1;this.epoch++;}
  current(epoch:number,token:string):boolean {return !!token&&epoch===this.epoch&&token===this.token;}
  accept(value:unknown,epoch:number):QualificationSummary|null {
    const row=record(value);
    if(!row||!this.current(epoch,row.session as string)||!integer(row.cursor_seq)||!integer(row.frame,9_999_999))return null;
    const summary=parseQualification(row.qualification);
    if(!summary||row.frame>=summary.frame_count||summary.sample_sequence<this.serial)return null;
    this.serial=summary.sample_sequence;return summary;
  }
}

export function formatRate(value:number|null|undefined):string {
  return value==null?'measuring':value.toFixed(1);
}
export function formatBytes(value:number|null|undefined):string {
  if(value==null)return 'not measured';
  const units=['B','KiB','MiB','GiB','TiB'];let index=0,amount=value;
  while(amount>=1024&&index<units.length-1){amount/=1024;index++;}
  return `${amount.toFixed(index?1:0)} ${units[index]}`;
}

export function frameLabel(displayed:number|null,requested:number):string {
  return displayed===null?`Requested frame ${requested} · awaiting image`:
    displayed===requested?`Frame ${displayed} · current`:
      `Showing frame ${displayed} · requested ${requested} · stale preview`;
}

const hash = (value:unknown):value is string=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
const profile = (value:unknown):value is string=>typeof value==='string'&&/^Q[0-5]$/.test(value);
const transitionReasons=new Set(['p95_over_budget','persistent_backlog','catastrophic_service_time',
  'sustained_headroom','allocation_failure','target_changed']);
const invalidationCauses=new Set(['initial','unchanged','boundaries_changed','quality_or_renderer_changed',
  'shared_pipeline_changed','section_dependencies_changed','source_changed','stateful_revoked','unknown']);

/** Strip the bearer token and every unknown field from a complete, bounded receipt. */
export function qualificationReceipt(value:unknown,session:string):Record<string,unknown>|null {
  const envelope=record(value),row=record(envelope?.qualification),summary=parseQualification(row);
  if(!session||!envelope||envelope.session!==session||!integer(envelope.cursor_seq)||
      !integer(envelope.frame,9_999_999)||!summary||envelope.frame>=summary.frame_count||!row)return null;
  const identity=record(row.identity),versions=record(row.versions),bounds=record(row.bounds),
    counters=record(row.counters),measurement=record(row.measurement),gates=record(row.release_gates);
  if(!identity||!versions||!bounds||!counters||!measurement||!gates)return null;
  const identityCopy:Record<string,unknown>={},versionCopy:Record<string,string|null>={},counterCopy:Record<string,number>={};
  for(const name of ['project_sha256','pipeline_sha256','renderer_abi_sha256','profile_ladder_sha256']){
    if(!hash(identity[name]))return null;identityCopy[name]=identity[name];
  }
  if(identity.implementation_sha256!==null&&!hash(identity.implementation_sha256))return null;
  identityCopy.implementation_sha256=identity.implementation_sha256;
  for(const name of ['python','framerudiments','dancerudiments','pillow']){
    const text=versions[name];
    if(text!==null&&(typeof text!=='string'||!/^[0-9][A-Za-z0-9.+_-]{0,63}$/.test(text)))return null;
    versionCopy[name]=text;
  }
  for(const name of ['commands','coalesced_requests','stale_results','render_errors','quota_rejections',
    'foreground_completed','background_completed','decoded_acknowledgements','repeated_acknowledgements']){
    if(!integer(counters[name]))return null;counterCopy[name]=counters[name];
  }
  if(bounds.timing_samples!==512||bounds.history_events!==128||bounds.sections!==2048||
      measurement.physical_display!==false||measurement.production_approval!==false)return null;
  const measurementCopy:Record<string,unknown>={physical_display:false,production_approval:false};
  const scopes={seek_ack_scope:'server_command_processing',seek_first_frame_scope:'server_frame_ready',
    seek_decode_scope:'server_seek_to_decode_ack',render_scope:'foreground_service_including_setup_and_cache_publish',
    rate_scope:'rolling_3s_current_session',rate_percentile_scope:'observations_at_least_250ms_apart',
    cache_scope:'current_indexed_png_bytes',resource_scope:'server_process_only'};
  for(const [name,scope] of Object.entries(scopes))if(measurement[name]!==undefined){
    if(measurement[name]!==scope)return null;measurementCopy[name]=scope;
  }
  let historyDropped:Record<string,number>|undefined;
  if(row.history_dropped!==undefined){
    const history=record(row.history_dropped);if(!history)return null;historyDropped={};
    for(const name of ['transitions','invalidations']){
      if(!integer(history[name]))return null;historyDropped[name]=history[name];
    }
  }
  const gateCopy:Record<string,false>={};
  for(const name of ['exact_change_ci','native_engine_matrix','physical_gpu','encoded_output','perceptual_approval']){
    if(gates[name]!==false)return null;gateCopy[name]=false;
  }
  if(!Array.isArray(row.segments)||row.segments.length>2048||!Array.isArray(row.transitions)||row.transitions.length>128||
      !Array.isArray(row.invalidations)||row.invalidations.length>128)return null;
  const segments:Record<string,unknown>[]=[],transitions:Record<string,unknown>[]=[],invalidations:Record<string,unknown>[]=[];
  let previousEnd=0;
  for(const value of row.segments){
    const item=record(value);
    if(!item||!integer(item.start_frame,summary.frame_count)||!integer(item.end_frame_exclusive,summary.frame_count)||
        item.start_frame!==previousEnd||item.end_frame_exclusive<=item.start_frame||
        !integer(item.generation)||!hash(item.dependency_sha256))return null;
    previousEnd=item.end_frame_exclusive;
    segments.push({start_frame:item.start_frame,end_frame_exclusive:item.end_frame_exclusive,
      generation:item.generation,dependency_sha256:item.dependency_sha256});
  }
  if(previousEnd!==summary.frame_count)return null;
  for(const value of row.transitions){
    const item=record(value);
    if(!item||!finite(item.at_ms)||!profile(item.from_profile)||!profile(item.to_profile)||
        !transitionReasons.has(item.reason as string))return null;
    transitions.push({at_ms:item.at_ms,from_profile:item.from_profile,to_profile:item.to_profile,reason:item.reason});
  }
  for(const value of row.invalidations){
    const item=record(value);
    if(!item||!finite(item.at_ms)||!invalidationCauses.has(item.cause as string))return null;
    const copy:Record<string,unknown>={at_ms:item.at_ms,cause:item.cause};
    for(const key of ['affected_sections','invalidated_frames','invalidated_bytes','retained_frames','revision']){
      if(!integer(item[key]))return null;copy[key]=item[key];
    }
    invalidations.push(copy);
  }
  return {...summary,identity:identityCopy,versions:versionCopy,segments,transitions,invalidations,counters:counterCopy,
    bounds:{timing_samples:512,history_events:128,sections:2048},measurement:measurementCopy,
    release_gates:gateCopy,...(historyDropped?{history_dropped:historyDropped}:{})};
}

/** Bound response allocation before parsing; no remote URLs or response-provided filenames. */
export async function readQualificationResponse(response:Response):Promise<unknown> {
  const limit=2*1024*1024;
  if(!response.ok||!response.headers.get('content-type')?.startsWith('application/json')||
      Number(response.headers.get('content-length')??0)>limit||!response.body)
    throw Error('Qualification receipt unavailable');
  const reader=response.body.getReader(),decoder=new TextDecoder();let size=0,text='';
  try{
    while(true){
      const chunk=await reader.read();if(chunk.done)break;
      size+=chunk.value.byteLength;
      if(size>limit){await reader.cancel();throw Error('Qualification receipt exceeds the size limit');}
      text+=decoder.decode(chunk.value,{stream:true});
    }
    text+=decoder.decode();return JSON.parse(text);
  }finally{reader.releaseLock();}
}

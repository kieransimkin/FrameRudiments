/** Pure, browser-independent FrameRudiments authoring model.
 * Source-timed LRC/CUE values never inherit video frame or DanceRudiment pips.
 * The shared react-timeline-sequence control owns transport/timing gestures.
 */
export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
export type Document = Record<string, any>;
export type TimingKind = "line" | "word" | "cue";
export interface TimedItem { id: string; start: number; end?: number; label: string; parentId?: string; }
export interface Sidecars { lines: TimedItem[]; words: TimedItem[]; cues: TimedItem[]; cueFormat?: "lrc" | "cue"; }
export interface Key { time: number; value: number; ease?: "linear" | "hold" | "smoothstep"; }
export const CUE_TYPES = ["SECTION", "ENTRY", "DROP", "BREAK"] as const;

export function projectTiming(project: Document) {
  if (!project || project.schema !== "framerudiments.project.v1") throw Error("Expected FrameRudiments project.v1");
  const audio = project.audio || {};
  const rate = audio.sample_rate, start = audio.start_sample ?? 0, end = audio.end_sample;
  const repeats = audio.repeats ?? 1;
  if (![rate, start, end, repeats].every(Number.isSafeInteger) || rate < 1 || start < 0 || end <= start || repeats < 1 || repeats > 1000) {
    throw Error("Invalid pinned audio sample window");
  }
  const slice = (end - start) / rate;
  return { start: start / rate, end: end / rate, slice, repeats, duration: slice * repeats, rate };
}

function stampTime(m: RegExpMatchArray) {
  const minutes = Number(m[1]), seconds = Number(m[2]);
  if (seconds >= 60 || !Number.isFinite(minutes)) throw Error("Invalid LRC timestamp");
  return minutes * 60 + seconds;
}

export function parseLrc(content: string, kind: TimingKind): TimedItem[] {
  if (content.length > 4 * 1024 * 1024) throw Error("Timing file exceeds 4 MiB");
  let offset = 0, order = 0;
  const items: TimedItem[] = [];
  for (const raw of content.replace(/^\uFEFF/, "").split(/\r?\n/)) {
    const line = raw.trim();
    if (!line) continue;
    const setting = line.match(/^\[offset:([+-]?\d+)\]$/i);
    if (setting) { offset = Number(setting[1]) / 1000; continue; }
    if (/^\[[a-z][a-z0-9_-]*:[^\]]*\]$/i.test(line) && !/^\[\d/.test(line)) continue;
    // Preserve multiple line timestamps as separate events, as in FrameRudiments.
    const stamps = [...line.matchAll(/\[(\d+):([0-5]?\d(?:\.\d+)?)\]/g)];
    if (!stamps.length || stamps[0].index !== 0) throw Error(`Invalid timing line: ${line.slice(0, 80)}`);
    let cursor = 0;
    for (const stamp of stamps) {
      if (stamp.index !== cursor) throw Error("Noncontiguous LRC stamps");
      cursor += stamp[0].length;
    }
    const label = line.slice(cursor).trim();
    if (kind === "word" && label.includes("<")) {
      if (stamps.length !== 1) throw Error("Enhanced word timing requires one parent line stamp");
      const tags = [...label.matchAll(/<(\d+):([0-5]?\d(?:\.\d+)?)>/g)];
      if (!tags.length || tags[0].index !== 0) throw Error("Malformed enhanced word timing");
      for (let j = 0; j < tags.length; j++) {
        const tag = tags[j], stop = j+1 < tags.length ? tags[j+1].index! : label.length;
        const word = label.slice(tag.index! + tag[0].length, stop).trim();
        if (word && word.split(/\s+/).length > 1) throw Error("Enhanced word marker needs one word");
        const start = stampTime(tag) + offset;
        if (start < 0) throw Error("Invalid enhanced word timing");
        items.push({id:`${kind}-${order++}`,start,label:word});
      }
      continue;
    }
    if (kind === "word" && label && label.split(/\s+/).length > 1) throw Error("Words must be individually timed");
    for (const stamp of stamps) {
      const start = stampTime(stamp) + offset;
      if (!Number.isFinite(start) || start < 0) throw Error("Negative or nonfinite source time");
      items.push({ id: `${kind}-${order++}`, start, label });
      if (items.length > 100000) throw Error("Too many timing items");
    }
  }
  return items.sort((a, b) => a.start - b.start || Number(a.id.split("-").pop()) - Number(b.id.split("-").pop()));
}

/** Parse CUE INDEX 01 as *read-only* timing; never rewrite an original CD CUE sheet. */
export function parseCueSheet(content: string): TimedItem[] {
  let title = "", index = 0;
  const results: TimedItem[] = [];
  for (const line of content.split(/\r?\n/)) {
    const heading = line.trim().match(/^TITLE\s+"?(.*?)"?$/i);
    if (heading) { title = heading[1].replace(/"$/, ""); continue; }
    const mark = line.trim().match(/^INDEX\s+01\s+(\d+):([0-5]\d):([0-7]\d)$/i);
    if (mark) {
      if (Number(mark[3]) >= 75) throw Error("Invalid CD cue subframe");
      results.push({ id: `cue-${index++}`, start: Number(mark[1])*60 + Number(mark[2]) + Number(mark[3])/75, label:title });
    }
  }
  return results;
}

export function cueNameValid(label: string): boolean {
  return /^\[(?:SECTION|ENTRY|DROP|BREAK):\s*[^\]\r\n]{1,120}\]$/.test(label);
}

export function renameCue(label: string, type: string, name: string): string {
  const value = `[${type}: ${name.trim()}]`;
  if (!cueNameValid(value)) throw Error("Cue names must use SECTION, ENTRY, DROP or BREAK");
  return value;
}

export function formatStamp(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0 || seconds >= 360000) throw Error("Invalid timestamp");
  const ms = Math.round(seconds * 1000);
  const mins = Math.floor(ms / 60000);
  const sec = Math.floor(ms / 1000) % 60;
  return `[${String(mins).padStart(2, "0")}:${String(sec).padStart(2, "0")}.${String(ms % 1000).padStart(3, "0")}]`;
}

export function serializeLrc(items: readonly TimedItem[]): string {
  const rows = items.map(item => {
    if (item.label.includes("\n") || item.label.includes("\r")) throw Error("Invalid newline in timed text");
    return { ...item, label: item.label.trim() };
  }).sort((a, b) => a.start - b.start || a.id.localeCompare(b.id));
  return rows.map(row => formatStamp(row.start) + row.label).join("\n") + "\n";
}

/** Timeline times are the output excerpt, but LRC sidecars remain source-clocked. */
export function expandTiming(items: readonly TimedItem[], project: Document): TimedItem[] {
  const timing = projectTiming(project);
  const result: TimedItem[] = [];
  for (const row of items) {
    if (row.start < timing.start || row.start >= timing.end) continue;
    for (let cycle = 0; cycle < timing.repeats; cycle++) {
      result.push({ ...row, id: `${row.id}@${cycle}`, start: Math.round((row.start - timing.start + cycle * timing.slice) * 1000) / 1000,
        ...(row.end === undefined ? {} : {end: Math.min(timing.slice, row.end - timing.start) + cycle * timing.slice}) });
    }
  }
  return result.sort((a,b) => a.start - b.start);
}

export function editTiming(items: readonly TimedItem[], edit: TimedItem, project: Document, kind: TimingKind): TimedItem[] {
  const timing = projectTiming(project);
  const pos = edit.id.lastIndexOf("@");
  if (pos < 0) throw Error("Unknown timeline event identity");
  const id = edit.id.slice(0, pos), cycle = Number(edit.id.slice(pos+1));
  if (!Number.isSafeInteger(cycle) || cycle < 0 || cycle >= timing.repeats) throw Error("Invalid repeat cycle");
  const at = edit.start - cycle * timing.slice + timing.start;
  if (!Number.isFinite(at) || at < timing.start || at >= timing.end || edit.start < 0) throw Error("Event outside source excerpt");
  if (kind === "cue" && !cueNameValid(edit.label)) throw Error("Nonstandard cue section label");
  if (kind === "word" && edit.label.split(/\s+/).length > 1) throw Error("Word must contain one word");
  if (!items.some(row => row.id === id)) throw Error("Unknown event");
  return items.map(row => row.id === id ? { ...row, start: Math.round(at * 1000) / 1000, label:edit.label } : row)
    .sort((a,b) => a.start - b.start || a.id.localeCompare(b.id));
}

export function normalizedKeys(config: unknown): Key[] {
  if (!config || typeof config !== "object" || !Array.isArray((config as any).keys)) return [];
  const keys = (config as {keys: Key[]}).keys;
  if (keys.length > 128) throw Error("Too many keyframes");
  for (let i = 0; i < keys.length; i++) {
    const k = keys[i];
    if (!Number.isFinite(k.time) || k.time < 0 || !Number.isFinite(k.value) || k.value < 0 || k.value > 1 ||
        (i && k.time <= keys[i-1].time) || ![undefined,"linear","hold","smoothstep"].includes(k.ease)) {
      throw Error("Invalid normalized keyframe envelope");
    }
  }
  return keys;
}

export function layerKeyframes(project: Document): {target: string; property: string; keys: Key[]}[] {
  const rows: {target: string; property: string; keys: Key[]}[] = [];
  for (const [target, entry] of Object.entries(project.layer_animations || {})) {
    if (!target.startsWith("layer:") || typeof entry !== "object" || !entry) continue;
    for (const property of ["opacity", "x", "y", "rotation"]) {
      const config = (entry as Document)[property];
      if (config && typeof config === "object" && "keys" in config) rows.push({target, property, keys:normalizedKeys(config)});
    }
  }
  return rows;
}

export function withKeyframe(project: Document, target: string, property: string, key: Key, oldTime?: number): Document {
  const t = projectTiming(project);
  if (!target.startsWith("layer:") || !(project.layers || []).some((layer: Document) => `layer:${layer.id}` === target)) throw Error("Unknown layer");
  if (!["opacity", "x", "y", "rotation"].includes(property)) throw Error("Unsupported property");
  if (!Number.isFinite(key.time) || key.time < 0 || key.time >= t.duration || !Number.isFinite(key.value) || key.value < 0 || key.value > 1 ||
      ![undefined, "linear", "hold", "smoothstep"].includes(key.ease)) throw Error("Keyframe outside range");
  const copy = structuredClone(project);
  copy.layer_animations ||= {};
  copy.layer_animations[target] ||= {};
  const previous = normalizedKeys(copy.layer_animations[target][property]);
  const other = previous.filter(x => oldTime === undefined ? Math.abs(x.time-key.time) > .0001 : Math.abs(x.time-oldTime) > .0001);
  copy.layer_animations[target][property] = { keys: [...other, { ...key, time:Math.round(key.time * 1000) / 1000 }].sort((a,b)=>a.time-b.time) };
  normalizedKeys(copy.layer_animations[target][property]);
  return copy;
}

export function withLayerRoute(project: Document, layer: string, feed: string, overlay: boolean, durationPips = 64): Document {
  if (!(project.effects || []).length) throw Error("GPU routing requires effects");
  if (!Array.isArray(project.layers) || !project.layers.some((l: Document) => `layer:${l.id}` === layer)) throw Error("Unknown layer");
  if (!["never", "continuous", "first_pips"].includes(feed) || typeof overlay !== "boolean") throw Error("Invalid routing");
  if (!Number.isSafeInteger(durationPips) || durationPips < 0 || durationPips > 65536) throw Error("Invalid pip window");
  const copy = structuredClone(project);
  copy.visualiser_routing ||= {default:{feed:"continuous",overlay:false}};
  copy.visualiser_routing.elements ||= {};
  copy.visualiser_routing.elements[layer] = {feed:feed === "first_pips" ? {mode:feed,duration_pips:durationPips} : feed, overlay};
  return copy;
}

export function withPreset(project: Document, target: string, preset: Document): Document {
  if (preset.schema !== "framerudiments.authoring-style-preset.v1" ||
      typeof preset.style !== "object" || !preset.style || Array.isArray(preset.style)) throw Error("Invalid style preset");
  const copy = structuredClone(project);
  const layer = copy.layers?.find((row: Document) => `layer:${row.id}` === target);
  if (!layer || layer.type !== "rudiment") throw Error("Presets require a rudiment layer");
  if (preset.renderer && preset.renderer !== layer.renderer) throw Error('Style preset renderer differs from target');
  // Only presentation data; never replace a pinned motion/StemLab source.
  layer.style = structuredClone(preset.style);
  if (preset.visual !== undefined) layer.visual = structuredClone(preset.visual);
  return copy;
}

export function stylePreset(layer: Document): Document {
  if (layer.type !== "rudiment") throw Error("Only rudiment layers have style presets");
  return {schema:"framerudiments.authoring-style-preset.v1", renderer:layer.renderer,
    style:structuredClone(layer.style || {}), visual:structuredClone(layer.visual || {})};
}

export function withStageInjection(project: Document, layer: string, before: number, enabled: boolean): Document {
  const copy=structuredClone(project);
  if (!(copy.effects||[]).length || !copy.layers?.some((row:Document)=>`layer:${row.id}`===layer)) {
    throw Error('Stage injection requires a known layer and visualiser chain');
  }
  copy.visualiser_routing ||= {default:{feed:'continuous',overlay:false}};
  if (!Number.isInteger(before) || before<1 || before>=copy.effects.length) throw Error('Intermediate stage out of range');
  const old:Document[]=Array.isArray(copy.visualiser_stage_injections)?copy.visualiser_stage_injections:[];
  const entries=old.filter(row=>row.before!==before).map(row=>structuredClone(row));
  const found=old.find(row=>row.before===before);
  const elements=found?.elements?.filter((name:string)=>name!==layer)||[];
  if (enabled) elements.push(layer);
  if (elements.length)entries.push({before,elements,opacity:found?.opacity??1});
  copy.visualiser_stage_injections=entries.sort((a,b)=>a.before-b.before);
  return copy;
}

/** Reusable declarative effects graph; never contains sources or external script. */
export function graphPreset(project: Document): Document {
  return {schema:'framerudiments.authoring-effect-graph.v1',
    visualiser_routing:structuredClone(project.visualiser_routing||null),
    visualiser_stage_injections:structuredClone(project.visualiser_stage_injections||[]),
    layer_masks:structuredClone(project.layer_masks||{}),
    layer_displacement:structuredClone(project.layer_displacement||{})};
}
export function withGraphPreset(project: Document, preset: Document): Document {
  if(!preset || preset.schema!=='framerudiments.authoring-effect-graph.v1' ||
    Object.keys(preset).some(key=>!['schema','visualiser_routing','visualiser_stage_injections','layer_masks','layer_displacement'].includes(key)))
    throw Error('Unsupported effect graph preset');
  const copy=structuredClone(project);
  const nodes=(copy.layers||[]).map((row:Document)=>`layer:${row.id}`);
  const allowed=new Set(['background','scene','caption','word-caption','word-indicator',...nodes]);
  const routes=preset.visualiser_routing;
  if(routes!=null){
    if(!(copy.effects||[]).length || typeof routes!=='object' || Array.isArray(routes))throw Error('Routing needs an effect chain');
    for(const key of Object.keys(routes.elements||{}))if(!allowed.has(key))throw Error(`Unknown route ${key}`);
  }
  const inserts=preset.visualiser_stage_injections||[];
  if(!Array.isArray(inserts)||inserts.length>32)throw Error('Invalid intermediate stages');
  for(const injection of inserts){
    if(!Number.isInteger(injection.before)||injection.before<1||injection.before>=copy.effects?.length||
      !Array.isArray(injection.elements)||injection.elements.some((x:string)=>!allowed.has(x)))throw Error('Invalid stage injection');
  }
  for(const field of ['layer_masks','layer_displacement']){
    const mapping=preset[field]||{};
    if(typeof mapping!=='object'||Array.isArray(mapping))throw Error('Invalid graph layer mapping');
    for(const [target,config] of Object.entries(mapping)){
      if(!nodes.includes(target)||!config||typeof config!=='object'||!allowed.has((config as Document).source))
        throw Error('Unknown layer matte/displacement reference');
      if(allowed.has((config as Document).source) && nodes.includes((config as Document).source) &&
        nodes.indexOf((config as Document).source)>=nodes.indexOf(target))throw Error('Graph must follow layer order');
    }
  }
  for(const key of ['visualiser_routing','visualiser_stage_injections','layer_masks','layer_displacement']){
    if(preset[key]!=null)copy[key]=structuredClone(preset[key]);
  }
  return copy;
}

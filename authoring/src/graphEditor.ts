/** M10.2 atomic connection authoring, independent of React and source clocks.
 * Local checks cover native topology/provider contracts for every authored node.
 * Python remains the authority for effect parameters, controls and preparation.
 */
import type {Document} from './model.ts';

export interface EffectNode { id:string; type:string; inputs?:string[]; [field:string]:unknown; }
export interface EffectGraphSpec { nodes:EffectNode[]; output:string; }
export type PortRole = 'image'|'layer'|'mask'|'displacement'|'texture'|'canvas';
export interface Port { id:string; label:string; role:PortRole; pixelType:'RGBA8'; editable:boolean; inputIndex?:number; }
export interface InputContract { min:number; max:number; variable:boolean; editable:boolean; labels:string[]; roles:PortRole[]; }
export interface PlacedNode {
  id:string; type:string; x:number; y:number; width:number; height:number;
  inputs:Port[]; output:Port; isOutput:boolean;
}
export interface PlacedEdge { from:string; to:string; port:number; path:string; readOnly?:boolean; }
export interface GraphCanvas { nodes:PlacedNode[]; edges:PlacedEdge[]; width:number; height:number; output:string; }
export const ADDABLE_EFFECTS = ['copy','opacity','filter','composite','matte','transition','matte_transition'] as const;
export type AddableEffect = typeof ADDABLE_EFFECTS[number];
const WIDTH=196, X_STEP=244, LEFT=32, TOP=30, V_GAP=25;
const UNARY=new Set(['copy','opacity','chroma_key','chromatic_displacement','spectral_displacement','filter','raster_effect','procedural_field']);
const KINDS=new Set([...UNARY,'source','transition','composite','matte','spatial_warp','layer_style','transition_chain']);
const SOURCE='source';
// Python's native len(str) counts code points, not UTF-16 code units.
const nativeIdLength=(value:string):number=>Array.from(value).length;

function object(value:unknown):value is Record<string,unknown> {
  return value!==null && typeof value==='object' && !Array.isArray(value);
}
function finite01(value:unknown, name:string):number {
  if(typeof value!=='number'||!Number.isFinite(value)||value<0||value>1)throw Error(`${name} must be finite between 0 and 1`);
  return value;
}
/** Source nodes use the compositor's provider ID directly, never a new alias.
 * This document-only authoring route has no external Python scene hook.
 */
export function availableEffectSources(project:Document):string[] {
  const sources=['frame','background'];
  for(const layer of Array.isArray(project.layers)?project.layers:[])
    if(object(layer)&&typeof layer.id==='string'&&layer.id)sources.push(`layer:${layer.id}`);
  if(Array.isArray(project.identity))project.identity.forEach((_:unknown,i:number)=>sources.push(`identity:${i}`));
  if(Array.isArray(project.lyric_art?.items))project.lyric_art.items.forEach((_:unknown,i:number)=>sources.push(`lyric-art:${i}`));
  const hasWordLyrics=object(project.word_lyrics)&&Object.keys(project.word_lyrics).length>0;
  if(object(project.captions)&&Object.keys(project.captions).length&&('lyrics' in project||hasWordLyrics))sources.push('caption');
  if('word_captions' in project)sources.push('word-caption');
  if('word_indicator' in project)sources.push('word-indicator');
  return [...new Set(sources)].filter(source=>nativeIdLength(source)<=128);
}

function role(node:EffectNode,index:number):PortRole {
  if(node.type==='matte')return index===0?'image':'mask';
  if(node.type==='transition'&&index===2)return 'mask';
  if(node.type==='spatial_warp'&&index===1)return 'displacement';
  if(node.type==='layer_style'&&index===1)return 'texture';
  if(node.type==='procedural_field')return 'canvas';
  return node.type==='composite'?'layer':'image';
}
function portLabel(node:EffectNode,index:number):string {
  if(node.type==='transition')return ['from','to','matte'][index]??`image ${index+1}`;
  if(node.type==='transition_chain')return `image ${index+1}`;
  if(node.type==='matte')return index===0?'image':`mask ${index}`;
  if(node.type==='spatial_warp')return index===0?'image':'displacement';
  if(node.type==='composite')return `layer ${index+1}`;
  if(node.type==='layer_style')return index===0?'image':'texture';
  if(node.type==='procedural_field')return 'canvas / coverage';
  return index===0?'image':`input ${index+1}`;
}

/** RGBA8 is the common format; role labels describe interpretation, not casts. */
export function effectInputContract(node:EffectNode):InputContract {
  if(!KINDS.has(node.type))throw Error(`${node.id}: unsupported effect graph node type ${node.type}`);
  let min=1,max=1,variable=false;
  if(node.type==='source')min=max=0;
  else if(node.type==='transition')min=max=node.transition_style==='matte'?3:2;
  else if(node.type==='composite'){min=2;max=32;variable=true;}
  else if(node.type==='matte'){min=2;max=9;variable=true;}
  else if(node.type==='transition_chain'){min=2;max=8;}
  else if(node.type==='spatial_warp')min=max=node.warp_type==='displacement'?2:1;
  else if(node.type==='layer_style')min=max=node.style_type==='pattern_overlay'&&node.pattern_kind==='texture'?2:1;
  const count=node.inputs?.length??min;
  return {min,max,variable,editable:min>0,
    labels:Array.from({length:count},(_,i)=>portLabel(node,i)),
    roles:Array.from({length:count},(_,i)=>role(node,i))};
}

function validateArity(node:EffectNode,inputs:string[]) {
  const {min,max}=effectInputContract(node);
  if(inputs.length<min||inputs.length>max)
    throw Error(`${node.id}: ${node.type} port count requires ${min===max?min:`${min}..${max}`} input${max===1?'':'s'}`);
}

type Dependency={source:string; label:string; role:PortRole; inputIndex?:number};
function dependencies(node:EffectNode):Dependency[] {
  const edges=(node.inputs??[]).map((source,i)=>({source,label:portLabel(node,i),role:role(node,i),inputIndex:i})) as Dependency[];
  if(node.type==='transition_chain'&&Array.isArray(node.stages))
    for(const stage of node.stages)if(object(stage)&&stage.transition_style==='matte'&&typeof stage.matte_input==='string')
      edges.push({source:stage.matte_input,label:`stage ${String(stage.id)} matte`,role:'mask'});
  return edges;
}

/** Mirror only the shorthand's topology rules; do not expand or rewrite it. */
function validateChain(node:EffectNode,reserved:Set<string>,generated:Set<string>):number {
  if(node.type!=='transition_chain')return 1;
  const count=node.inputs!.length,stages=node.stages,filters=node.input_effects??Array.from({length:count},()=>[]);
  if(!Array.isArray(stages)||stages.length!==count-1)throw Error(`${node.id}: transition chain needs one stage per additional input`);
  if(!Array.isArray(filters)||filters.length!==count||filters.some(rows=>!Array.isArray(rows)||rows.length>4))
    throw Error(`${node.id}: transition chain preprocessing must match its inputs`);
  let expanded=1;
  const add=(name:string)=>{
    if(nativeIdLength(name)>128||reserved.has(name)||generated.has(name))throw Error(`${node.id}: transition chain generated node ID collision or bound`);
    generated.add(name);expanded++;
  };
  const stable=(value:unknown):value is string=>typeof value==='string'&&/^[a-z][a-z0-9_-]{0,31}$/.test(value);
  filters.forEach((rows:unknown[],index:number)=>rows.forEach(entry=>{
    if(!object(entry)||!stable(entry.id)||typeof entry.type!=='string'||
      !new Set([...UNARY,'layer_style','spatial_warp']).has(entry.type)||'inputs' in entry)
      throw Error(`${node.id}: transition chain preprocessing requires named unary effects`);
    const effect={...entry,id:entry.id,inputs:['reference']} as EffectNode;
    validateArity(effect,effect.inputs!);
    add(`${node.id}::input::${index}::${entry.id}`);
  }));
  const names=new Set<string>();
  for(const stage of stages){
    if(!object(stage)||!stable(stage.id)||names.has(stage.id)||'type' in stage||'inputs' in stage)
      throw Error(`${node.id}: invalid or duplicate transition chain stage`);
    names.add(stage.id);
    if(stage.transition_style==='matte'){
      if(typeof stage.matte_input!=='string'||!stage.matte_input)throw Error(`${node.id}: matte stage needs a graph input`);
    }else if('matte_input' in stage)throw Error(`${node.id}: only matte stages accept matte_input`);
    add(`${node.id}::stage::${stage.id}`);
  }
  return expanded;
}
/** Validate structure for *every* node, including currently unreachable nodes.
 * Includes nested shorthand dependencies and unavailable source providers.
 * Complete parameter/control validation remains the Python executor's job.
 */
export function inspectEffectGraph(project:Document):EffectGraphSpec|null {
  const graph=project.effect_graph;
  if(graph===undefined)return null;
  if(!object(graph)||Object.keys(graph).some(key=>!['nodes','output'].includes(key))||
     !Array.isArray(graph.nodes)||graph.nodes.length<1||graph.nodes.length>256||typeof graph.output!=='string')
    throw Error('Effect graph requires bounded nodes and output');
  const all=new Map<string,EffectNode>();
  for(const row of graph.nodes){
    if(!object(row)||typeof row.id!=='string'||!row.id||nativeIdLength(row.id)>128||typeof row.type!=='string'||!row.type||all.has(row.id))
      throw Error('Invalid or duplicate effect graph node');
    const node=row as unknown as EffectNode;
    const inputs=node.inputs??[];
    if(!Array.isArray(inputs)||inputs.length>32||inputs.some(name=>typeof name!=='string'||!name))
      throw Error(`${node.id}: invalid input ports`);
    validateArity(node,inputs);
    all.set(node.id,node);
  }
  if(!all.has(graph.output))throw Error('Effect graph output does not exist');
  const available=new Set(availableEffectSources(project));
  const reserved=new Set(all.keys()),generated=new Set<string>();
  let expanded=0;
  for(const node of all.values()){
    if(node.type===SOURCE&&!available.has(node.id))throw Error(`Unavailable effect graph source: ${node.id}`);
    expanded+=validateChain(node,reserved,generated);
  }
  if(expanded>256)throw Error('Expanded effect graph node limit exceeded');
  const visits=new Map<string,number>();
  const visit=(id:string):void=>{
    if(!all.has(id))throw Error(`Unknown effect graph input: ${id}`);
    if(visits.get(id)===1)throw Error('Effect graph contains a cycle');
    if(visits.get(id)===2)return;
    visits.set(id,1);
    for(const edge of dependencies(all.get(id)!))visit(edge.source);
    visits.set(id,2);
  };
  for(const id of all.keys())visit(id);
  return graph as unknown as EffectGraphSpec;
}

/** Stable topological columns, separate port endpoints, and no persisted layout. */
export function layoutEffectGraph(project:Document):GraphCanvas|null {
  const graph=inspectEffectGraph(project);
  if(!graph)return null;
  const lookup=new Map(graph.nodes.map(node=>[node.id,node]));
  const depths=new Map<string,number>();
  const depth=(id:string):number=>{
    const cached=depths.get(id);if(cached!==undefined)return cached;
    const inputs=dependencies(lookup.get(id)!).map(edge=>edge.source);
    const result=inputs.length?1+Math.max(...inputs.map(depth)):0;
    depths.set(id,result);return result;
  };
  for(const node of graph.nodes)depth(node.id);
  const columns=new Map<number,number>();
  const placed=graph.nodes.map(node=>{
    const col=depth(node.id),y=columns.get(col)??TOP;
    const ports=dependencies(node),count=ports.length;
    const height=Math.max(91,52+count*23);
    columns.set(col,y+height+V_GAP);
    return {id:node.id,type:node.type,x:LEFT+col*X_STEP,y,width:WIDTH,height,
      inputs:ports.map((edge,i)=>({id:`${node.id}:in:${i}`,label:edge.label,role:edge.role,
        pixelType:'RGBA8' as const,editable:edge.inputIndex!==undefined,...(edge.inputIndex===undefined?{}:{inputIndex:edge.inputIndex})})),
      output:{id:`${node.id}:out`,label:'RGBA8',role:'image' as const,pixelType:'RGBA8' as const,editable:true},isOutput:node.id===graph.output};
  });
  const positions=new Map(placed.map(node=>[node.id,node]));
  const edges:PlacedEdge[]=[];
  for(const node of graph.nodes){
    const target=positions.get(node.id)!;
    for(const [port,edge] of dependencies(node).entries()){
      const from=edge.source;
      const origin=positions.get(from)!;
      const x1=origin.x+origin.width,y1=origin.y+37;
      const x2=target.x,y2=target.y+53+port*23;
      const mid=Math.max(24,(x2-x1)/2);
      edges.push({from,to:node.id,port,path:`M${x1} ${y1} C${x1+mid} ${y1},${x2-mid} ${y2},${x2} ${y2}`,
        ...(edge.inputIndex===undefined?{readOnly:true}:{})});
    }
  }
  return {nodes:placed,edges,output:graph.output,
    width:Math.max(680,...placed.map(node=>node.x+node.width+LEFT)),
    height:Math.max(220,...placed.map(node=>node.y+node.height+TOP))};
}

/** Make a safe CPU graph only on explicit user action; do not alter originals. */
export function withInitialEffectGraph(project:Document):Document {
  if(project.effect_graph!==undefined)throw Error('Effect graph already exists');
  const copy=structuredClone(project);
  copy.effect_graph={nodes:[{id:'frame',type:'source'}],output:'frame'};
  return copy;
}
export function withEffectNode(project:Document,kind:AddableEffect,inputs?:string[],options:{makeOutput?:boolean}={}):Document {
  if(!ADDABLE_EFFECTS.includes(kind))throw Error('Unsupported effect graph node template');
  const graph=inspectEffectGraph(project);
  if(!graph)throw Error('Create an effect graph first');
  if(graph.nodes.length>=256)throw Error('Effect graph node limit exceeded');
  const used=new Set(graph.nodes.map(node=>node.id));
  let index=1;
  while(used.has(`editor-${kind}-${index}`))index++;
  const id=`editor-${kind}-${index}`;
  if(inputs===undefined&&!['copy','opacity','filter'].includes(kind))
    throw Error(`${kind}: choose its ordered input connections before creating the node`);
  if(options.makeOutput!==undefined&&typeof options.makeOutput!=='boolean')throw Error('makeOutput must be boolean');
  const node:EffectNode={id,type:kind==='matte_transition'?'transition':kind,inputs:inputs??[graph.output]};
  if(kind==='filter')Object.assign(node,{filter_type:'blur',filter_radius:1.5});
  if(kind==='composite')node.mode='over';
  if(kind==='matte')Object.assign(node,{matte_channel:'alpha',mask_combine:'multiply'});
  if(kind==='transition'||kind==='matte_transition')Object.assign(node,{transition_style:kind==='matte_transition'?'matte':'dissolve',progress:.5});
  const copy=structuredClone(project);
  copy.effect_graph.nodes.push(structuredClone(node));
  if(options.makeOutput!==false)copy.effect_graph.output=id;
  inspectEffectGraph(copy);
  return copy;
}

/** Register a real isolated provider; source node IDs are fixed by the runtime. */
export function withEffectSource(project:Document,sourceId:string):Document {
  const graph=inspectEffectGraph(project);
  if(!graph)throw Error('Create an effect graph first');
  if(!availableEffectSources(project).includes(sourceId))throw Error('Unavailable effect graph source');
  if(graph.nodes.some(node=>node.id===sourceId))throw Error('Effect graph node ID is already in use');
  const copy=structuredClone(project);
  copy.effect_graph.nodes.push({id:sourceId,type:'source'});
  inspectEffectGraph(copy);
  return copy;
}

/** Replace ordered inputs in one immutable transaction, never persist holes. */
export function withEffectInputs(project:Document,targetId:string,inputs:string[]):Document {
  const graph=inspectEffectGraph(project);
  const node=graph?.nodes.find(row=>row.id===targetId);
  if(!node)throw Error('Unknown effect graph target');
  if(node.type===SOURCE)throw Error('A source has no input ports');
  if(!Array.isArray(inputs))throw Error('Ordered input connections must be an array');
  const copy=structuredClone(project);
  copy.effect_graph.nodes.find((row:EffectNode)=>row.id===targetId).inputs=structuredClone(inputs);
  inspectEffectGraph(copy);
  return copy;
}

/** Build a read-only candidate without copying unrelated project/media metadata.
 * Compatibility checks run for each candidate port; clone only on actual commit.
 */
function connectionDraft(project:Document,targetId:string,inputIndex:number,sourceId:string):Document {
  const graph=inspectEffectGraph(project);
  const node=graph?.nodes.find(row=>row.id===targetId);
  if(!node)throw Error('Unknown effect graph target');
  if(!Number.isSafeInteger(inputIndex)||inputIndex<0||inputIndex>=(node.inputs?.length??0))
    throw Error('Unknown effect graph input port');
  if(typeof sourceId!=='string'||!sourceId)throw Error('Choose a graph output for this input');
  const inputs=[...node.inputs!];inputs[inputIndex]=sourceId;
  const candidate={...project,effect_graph:{...graph,
    nodes:graph!.nodes.map(row=>row.id===targetId?{...row,inputs}:row)}};
  inspectEffectGraph(candidate);
  return candidate;
}

/** Reconnect one existing port. Creating/removing ports uses withEffectInputs. */
export function withEffectConnection(project:Document,targetId:string,inputIndex:number,sourceId:string):Document {
  return structuredClone(connectionDraft(project,targetId,inputIndex,sourceId));
}

/** Actionable rejection text for a proposed wire; null means locally valid. */
export function connectionIssue(project:Document,targetId:string,inputIndex:number,sourceId:string):string|null {
  try{connectionDraft(project,targetId,inputIndex,sourceId);return null;}
  catch(error){return error instanceof Error?error.message:'Invalid effect graph connection';}
}
function editNode(project:Document,id:string,property:string,value:number):Document {
  const graph=inspectEffectGraph(project);
  if(!graph)throw Error('No effect graph');
  const found=graph.nodes.find(node=>node.id===id);
  if(!found)throw Error('Unknown effect graph node');
  if(property==='opacity')finite01(value,'opacity');
  else if(property==='progress'){
    if(found.type!=='transition')throw Error('Progress requires transition node');
    finite01(value,'progress');
  }else if(property==='filter_radius'){
    if(found.type!=='filter'||typeof value!=='number'||!Number.isFinite(value)||value<0||value>4)
      throw Error('Blur radius must be between 0 and 4 on a filter node');
  }else throw Error('Unsupported graph property');
  const copy=structuredClone(project);
  copy.effect_graph.nodes.find((node:EffectNode)=>node.id===id)[property]=value;
  return copy;
}
export function withEffectOpacity(project:Document,id:string,value:number):Document{return editNode(project,id,'opacity',value);}
export function withEffectProgress(project:Document,id:string,value:number):Document{return editNode(project,id,'progress',value);}
export function withEffectBlurRadius(project:Document,id:string,value:number):Document{return editNode(project,id,'filter_radius',value);}
export function withEffectOutput(project:Document,id:string):Document {
  const graph=inspectEffectGraph(project);
  if(!graph?.nodes.some(node=>node.id===id))throw Error('Unknown effect graph output');
  const copy=structuredClone(project);copy.effect_graph.output=id;
  return copy;
}
/** M10.1 allows removal only of a terminal unary node without dependants. */
export function withoutTerminalEffectNode(project:Document,id:string):Document {
  const graph=inspectEffectGraph(project);
  if(!graph)throw Error('No effect graph');
  const node=graph.nodes.find(node=>node.id===id);
  if(!node||node.type==='source'||node.inputs?.length!==1||graph.output!==id||
     graph.nodes.some(other=>other.id!==id&&other.inputs?.includes(id))||
     project.effect_graph_controls?.some((row:Document)=>row.node===id))
    throw Error('Only an unreferenced terminal unary node can be removed');
  const copy=structuredClone(project);
  copy.effect_graph.nodes=copy.effect_graph.nodes.filter((row:EffectNode)=>row.id!==id);
  copy.effect_graph.output=node.inputs[0];
  inspectEffectGraph(copy);
  return copy;
}

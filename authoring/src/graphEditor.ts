/** M10.1 effect-graph node/port authoring, independent of React and clocks.
 * The executor owns complete semantic validation. These operations preserve the
 * authored DAG and reject malformed edges before offering export or preview.
 */
import type {Document} from './model.ts';

export interface EffectNode { id:string; type:string; inputs?:string[]; [field:string]:unknown; }
export interface EffectGraphSpec { nodes:EffectNode[]; output:string; }
export interface Port { id:string; label:string; }
export interface PlacedNode {
  id:string; type:string; x:number; y:number; width:number; height:number;
  inputs:Port[]; output:Port; isOutput:boolean;
}
export interface PlacedEdge { from:string; to:string; port:number; path:string; }
export interface GraphCanvas { nodes:PlacedNode[]; edges:PlacedEdge[]; width:number; height:number; output:string; }
export const ADDABLE_EFFECTS = ['copy','opacity','filter'] as const;
export type AddableEffect = typeof ADDABLE_EFFECTS[number];
const WIDTH=196, X_STEP=244, LEFT=32, TOP=30, V_GAP=25;
const UNARY=new Set(['copy','opacity','chroma_key','chromatic_displacement','spectral_displacement','filter']);
const SOURCE='source';

function object(value:unknown):value is Record<string,unknown> {
  return value!==null && typeof value==='object' && !Array.isArray(value);
}
function finite01(value:unknown, name:string):number {
  if(typeof value!=='number'||!Number.isFinite(value)||value<0||value>1)throw Error(`${name} must be finite between 0 and 1`);
  return value;
}
function validateArity(node:EffectNode, inputs:string[]) {
  if(node.type===SOURCE && inputs.length)throw Error(`${node.id}: source has no input ports`);
  if(UNARY.has(node.type)&&inputs.length!==1)throw Error(`${node.id}: expected one input port`);
  if(node.type==='transition'&&inputs.length!==(node.transition_style==='matte'?3:2))throw Error(`${node.id}: transition port count`);
  if(node.type==='composite'&&inputs.length<2)throw Error(`${node.id}: composite needs two inputs`);
  if(node.type==='matte'&&(inputs.length<2||inputs.length>9))throw Error(`${node.id}: matte port count`);
}
/** Validate structure for *every* node, including currently unreachable nodes.
 * Does not claim to implement the Python executor's effect-specific schema.
 */
export function inspectEffectGraph(project:Document):EffectGraphSpec|null {
  const graph=project.effect_graph;
  if(graph===undefined)return null;
  if(!object(graph)||Object.keys(graph).some(key=>!['nodes','output'].includes(key))||
     !Array.isArray(graph.nodes)||graph.nodes.length<1||graph.nodes.length>256||typeof graph.output!=='string')
    throw Error('Effect graph requires bounded nodes and output');
  const all=new Map<string,EffectNode>();
  for(const row of graph.nodes){
    if(!object(row)||typeof row.id!=='string'||!row.id||row.id.length>128||typeof row.type!=='string'||!row.type||all.has(row.id))
      throw Error('Invalid or duplicate effect graph node');
    const node=row as unknown as EffectNode;
    const inputs=node.inputs??[];
    if(!Array.isArray(inputs)||inputs.length>32||inputs.some(name=>typeof name!=='string'||!name))
      throw Error(`${node.id}: invalid input ports`);
    validateArity(node,inputs);
    all.set(node.id,node);
  }
  if(!all.has(graph.output))throw Error('Effect graph output does not exist');
  const visits=new Map<string,number>();
  const visit=(id:string):void=>{
    if(!all.has(id))throw Error(`Unknown effect graph input: ${id}`);
    if(visits.get(id)===1)throw Error('Effect graph contains a cycle');
    if(visits.get(id)===2)return;
    visits.set(id,1);
    for(const input of all.get(id)!.inputs??[])visit(input);
    visits.set(id,2);
  };
  for(const id of all.keys())visit(id);
  return graph as unknown as EffectGraphSpec;
}

function portLabel(node:EffectNode, index:number) {
  if(node.type==='transition')return ['from','to','matte'][index]??`image ${index+1}`;
  if(node.type==='matte')return index===0?'image':`mask ${index}`;
  if(node.type==='spatial_warp')return index===0?'image':'displacement';
  if(node.type==='composite')return `layer ${index+1}`;
  if(node.type==='layer_style')return index===0?'image':'overlay';
  return index===0?'image':`input ${index+1}`;
}
/** Stable topological columns, separate port endpoints, and no persisted layout. */
export function layoutEffectGraph(project:Document):GraphCanvas|null {
  const graph=inspectEffectGraph(project);
  if(!graph)return null;
  const lookup=new Map(graph.nodes.map(node=>[node.id,node]));
  const depths=new Map<string,number>();
  const depth=(id:string):number=>{
    const cached=depths.get(id);if(cached!==undefined)return cached;
    const inputs=lookup.get(id)!.inputs??[];
    const result=inputs.length?1+Math.max(...inputs.map(depth)):0;
    depths.set(id,result);return result;
  };
  for(const node of graph.nodes)depth(node.id);
  const columns=new Map<number,number>();
  const placed=graph.nodes.map(node=>{
    const col=depth(node.id),y=columns.get(col)??TOP;
    const count=node.inputs?.length??0;
    const height=Math.max(91,52+count*23);
    columns.set(col,y+height+V_GAP);
    return {id:node.id,type:node.type,x:LEFT+col*X_STEP,y,width:WIDTH,height,
      inputs:(node.inputs??[]).map((_,i)=>({id:`${node.id}:in:${i}`,label:portLabel(node,i)})),
      output:{id:`${node.id}:out`,label:'RGBA8'},isOutput:node.id===graph.output};
  });
  const positions=new Map(placed.map(node=>[node.id,node]));
  const edges:PlacedEdge[]=[];
  for(const node of graph.nodes){
    const target=positions.get(node.id)!;
    for(const [port,from] of (node.inputs??[]).entries()){
      const origin=positions.get(from)!;
      const x1=origin.x+origin.width,y1=origin.y+37;
      const x2=target.x,y2=target.y+53+port*23;
      const mid=Math.max(24,(x2-x1)/2);
      edges.push({from,to:node.id,port,path:`M${x1} ${y1} C${x1+mid} ${y1},${x2-mid} ${y2},${x2} ${y2}`});
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
export function withEffectNode(project:Document, kind:AddableEffect):Document {
  if(!ADDABLE_EFFECTS.includes(kind))throw Error('Unsupported M10.1 node template');
  const graph=inspectEffectGraph(project);
  if(!graph)throw Error('Create an effect graph first');
  if(graph.nodes.length>=256)throw Error('Effect graph node limit exceeded');
  const used=new Set(graph.nodes.map(node=>node.id));
  let index=1;
  while(used.has(`editor-${kind}-${index}`))index++;
  const id=`editor-${kind}-${index}`;
  const node:EffectNode={id,type:kind,inputs:[graph.output]};
  if(kind==='filter')Object.assign(node,{filter_type:'blur',filter_radius:1.5});
  const copy=structuredClone(project);
  copy.effect_graph.nodes.push(node);
  copy.effect_graph.output=id;
  inspectEffectGraph(copy);
  return copy;
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

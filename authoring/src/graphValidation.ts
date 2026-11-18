/** M10.2 native validation reports. Draft identity stays outside the wire format. */
export const GRAPH_VALIDATION_SCHEMA='framerudiments.authoring-graph-validation.v1';
export interface GraphValidationReport {
 schema:typeof GRAPH_VALIDATION_SCHEMA;valid:boolean;graph_present:boolean;
 request_sha256:string;graph_sha256:string|null;
 sources:{id:string;format:'RGBA8'}[];
 nodes:{id:string;type:string;inputs:{index:number;role:string;format:'RGBA8';source:string}[];
   output:{format:'RGBA8'};min_inputs:number;max_inputs:number}[];
 errors:{code:string;message:string}[];
 scope:{rendered:false;gpu_context_opened:false;all_nodes:true;source_names:true;control_bindings:true};
}
const object=(value:unknown):Record<string,unknown>|null=>
 value!==null&&typeof value==='object'&&!Array.isArray(value)?value as Record<string,unknown>:null;
const bounded=(value:unknown,max:number):value is string=>typeof value==='string'&&value.length>0&&Array.from(value).length<=max;
const integer=(value:unknown,max:number):value is number=>Number.isSafeInteger(value)&&(value as number)>=0&&(value as number)<=max;
const digest=(value:unknown):value is string=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
const roles=new Set(['image','mask','from','to','layer','displacement','texture','canvas']);
const errorCodes=new Set(['graph_schema','graph_topology','source_unavailable','control_binding','project_invalid','input_changed']);

/** Copy only the declared report. Native diagnostics are data, never markup. */
export function parseGraphValidation(value:unknown):GraphValidationReport|null {
 const row=object(value),scope=object(row?.scope);
 if(!row||row.schema!==GRAPH_VALIDATION_SCHEMA||typeof row.valid!=='boolean'||typeof row.graph_present!=='boolean'||
   !digest(row.request_sha256)||(row.graph_sha256!==null&&!digest(row.graph_sha256))||
   (row.graph_present&&row.graph_sha256===null)||(!row.graph_present&&row.graph_sha256!==null)||
   !scope||scope.rendered!==false||scope.gpu_context_opened!==false||scope.all_nodes!==true||
   scope.source_names!==true||scope.control_bindings!==true||
   !Array.isArray(row.sources)||row.sources.length>2048||!Array.isArray(row.nodes)||row.nodes.length>256||
   !Array.isArray(row.errors)||row.errors.length>1)return null;
 const sources:GraphValidationReport['sources']=[],nodes:GraphValidationReport['nodes']=[],errors:GraphValidationReport['errors']=[];
 const sourceIds=new Set<string>(),nodeIds=new Set<string>();
 for(const value of row.sources){
   const source=object(value);
   if(!source||!bounded(source.id,128)||source.format!=='RGBA8'||sourceIds.has(source.id))return null;
   sourceIds.add(source.id);sources.push({id:source.id,format:'RGBA8'});
 }
 for(const value of row.nodes){
   const node=object(value),output=object(node?.output);
   if(!node||!bounded(node.id,128)||!bounded(node.type,64)||nodeIds.has(node.id)||!output||output.format!=='RGBA8'||
     !integer(node.min_inputs,32)||!integer(node.max_inputs,32)||node.min_inputs>node.max_inputs||
     !Array.isArray(node.inputs)||node.inputs.length>32)return null;
   nodeIds.add(node.id);const inputs:GraphValidationReport['nodes'][number]['inputs']=[];
   for(const [index,value] of node.inputs.entries()){
     const port=object(value);
     if(!port||port.index!==index||!bounded(port.role,64)||!roles.has(port.role)||port.format!=='RGBA8'||!bounded(port.source,128))return null;
     inputs.push({index,role:port.role,format:'RGBA8',source:port.source});
   }
   nodes.push({id:node.id,type:node.type,inputs,output:{format:'RGBA8'},min_inputs:node.min_inputs,max_inputs:node.max_inputs});
 }
 for(const value of row.errors){
   const error=object(value);
   if(!error||!bounded(error.code,64)||!errorCodes.has(error.code)||!bounded(error.message,160))return null;
   errors.push({code:error.code,message:error.message});
 }
 if((row.valid&&errors.length)||(!row.valid&&!errors.length)||(!row.graph_present&&nodes.length)||
   (row.valid&&row.graph_present&&!nodes.length))return null;
 if(row.valid)for(const node of nodes){
   // Native transition chains may expose generated dependency IDs outside the
   // authored node list. Their semantic resolution belongs to the native check.
   if(node.inputs.length<node.min_inputs||node.inputs.length>node.max_inputs||
     (node.type==='source'&&!sourceIds.has(node.id)))return null;
 }
 return {schema:GRAPH_VALIDATION_SCHEMA,valid:row.valid,graph_present:row.graph_present,
   request_sha256:row.request_sha256,graph_sha256:row.graph_sha256,sources,nodes,errors,
   scope:{rendered:false,gpu_context_opened:false,all_nodes:true,source_names:true,control_bindings:true}};
}

export interface ValidationTicket<T extends object>{serial:number;snapshot:T;}
/** A previous request cannot approve a newer edit, undo, redo or sidecar snapshot. */
export class GraphValidationFence<T extends object>{
 private serial=0;
 private snapshot:T|null=null;
 begin(snapshot:T):ValidationTicket<T>{this.snapshot=snapshot;return {serial:++this.serial,snapshot};}
 cancel():void{this.snapshot=null;this.serial++;}
 current(ticket:ValidationTicket<T>):boolean{return ticket.serial===this.serial&&ticket.snapshot===this.snapshot;}
 accept(value:unknown,ticket:ValidationTicket<T>,graphPresent:boolean):GraphValidationReport|null{
   if(!this.current(ticket))return null;
   const report=parseGraphValidation(value);
   return report&&report.graph_present===graphPresent?report:null;
 }
}

export async function readGraphValidation(response:Response):Promise<unknown>{
 const maximum=2*1024*1024;
 if(!response.headers.get('content-type')?.startsWith('application/json')||
   Number(response.headers.get('content-length')??0)>maximum||!response.body)
   throw Error('Native graph validation returned an invalid response');
 const reader=response.body.getReader(),decoder=new TextDecoder();let size=0,text='';
 try{
   while(true){
     const next=await reader.read();if(next.done)break;
     size+=next.value.byteLength;
     if(size>maximum){await reader.cancel();throw Error('Native graph validation exceeded the response limit');}
     text+=decoder.decode(next.value,{stream:true});
   }
   text+=decoder.decode();return JSON.parse(text);
 }finally{reader.releaseLock();}
}

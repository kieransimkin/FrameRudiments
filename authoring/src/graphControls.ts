/** M10.3 bounded native authoring contracts. Parameter semantics stay in Python. */
import type {Document,Json} from './model.ts';
import {parseGraphValidation,type GraphValidationReport} from './graphValidation.ts';

export const GRAPH_CONTROLS_SCHEMA='framerudiments.authoring-controls.v1';
export const GRAPH_EDIT_SCHEMA='framerudiments.authoring-edit.v1';
export const GRAPH_CONTROL_CATALOG_BYTES=8*1024*1024;
export const GRAPH_CONTROL_RESPONSE_BYTES=12*1024*1024;
export type GraphControlScalar=number|string|boolean;
export type GraphParameterType='number'|'integer'|'boolean'|'enum';
export interface GraphControlTarget {id:string;type:string;label:string;authored_id:string;}
export interface GraphParameterDescriptor {
 property:string;label:string;type:GraphParameterType;value:GraphControlScalar;default:GraphControlScalar|null;
 minimum?:number;maximum?:number;step?:number;units?:string;choices?:GraphControlScalar[];excluded?:GraphControlScalar[];
 editable:boolean;bindable:boolean;reason?:string;group:string;active:boolean;
 binding_defaults?:{range:[number,number]}|{choices:GraphControlScalar[]};
 binding_range?:[number,number];
 constraints?:string[];factory_default?:GraphControlScalar|null;default_semantics?:'factory_default'|'configured_base';
}
export interface GraphBindingFieldDescriptor {
 property:string;label:string;type:GraphParameterType|'number_pair'|'text'|'choices';
 default:GraphControlScalar|GraphControlScalar[]|null;
 minimum?:number;maximum?:number;step?:number;units?:string;choices?:GraphControlScalar[];excluded?:GraphControlScalar[];
 max_length?:number;min_items?:number;max_items?:number;
}
export interface GraphControlBinding {index:number;config:Document;}
export interface GraphControlMotion {
 id:string;label:string;kind:'named'|'recording'|'match'|'table'|'composite';axes:string[];motion:Document;
 motion_fields:GraphParameterDescriptor[];
}
export interface GraphControlCatalog {
 schema:typeof GRAPH_CONTROLS_SCHEMA;request_sha256:string;node:GraphControlTarget;targets:GraphControlTarget[];
 parameters:GraphParameterDescriptor[];bindings:GraphControlBinding[];motions:GraphControlMotion[];
 binding_fields:GraphBindingFieldDescriptor[];limits:{bindings:64};
 scope:{rendered:false;gpu_context_opened:false};
}
export type GraphEditOperation=
 |{kind:'set_parameter';property:string;value:GraphControlScalar}
 |{kind:'put_binding';index:number|null;property:string;motion_id:string;settings:Document;motion_settings?:Document}
 |{kind:'remove_binding';index:number}
 |{kind:'move_binding';index:number;direction:-1|1};
export interface GraphEditResponse {
 schema:typeof GRAPH_EDIT_SCHEMA;request_sha256:string;project:Document;
 validation:GraphValidationReport;catalog:GraphControlCatalog;
}

const object=(value:unknown):Record<string,unknown>|null=>{
 if(value===null||typeof value!=='object'||Array.isArray(value))return null;
 const prototype=Object.getPrototypeOf(value);
 return prototype===Object.prototype||prototype===null?value as Record<string,unknown>:null;
};
const bounded=(value:unknown,max:number,empty=false):value is string=>
 typeof value==='string'&&(empty||value.length>0)&&Array.from(value).length<=max;
const digest=(value:unknown):value is string=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
const finite=(value:unknown):value is number=>typeof value==='number'&&Number.isFinite(value);
const integer=(value:unknown,max:number):value is number=>Number.isSafeInteger(value)&&(value as number)>=0&&(value as number)<=max;
const scalar=(value:unknown):value is GraphControlScalar=>typeof value==='boolean'||finite(value)||bounded(value,256,true);
const has=(row:Record<string,unknown>,key:string)=>Object.prototype.hasOwnProperty.call(row,key);
const parameterTypes=new Set(['number','integer','boolean','enum']);
const settingsFields=new Set(['id','axis','range','curve','steps','power','invert','combine','weight',
 'lag_frames','smoothing_frames','deadzone','sample_stride','choices']);
const motionFields=new Set(['rate','phase_beats','origin_beat','repeat_clock','interpolate']);
const motionKinds=new Set(['named','recording','match','table','composite']);

/** Clone only bounded JSON; reject class instances, cycles, nonfinite numbers and deep payloads. */
function boundedJSON(value:unknown):Json {
 let entries=0;
 const seen=new Set<object>();
 const visit=(item:unknown,depth:number):Json=>{
   if(++entries>250000||depth>40)throw Error('Native graph data exceeds its JSON bound');
   if(item===null||typeof item==='boolean'||finite(item))return item;
   if(typeof item==='string'){
     if(item.length>2*1024*1024)throw Error('Native graph string exceeds its bound');
     return item;
   }
   if(typeof item!=='object'||seen.has(item))throw Error('Native graph data must be finite JSON');
   seen.add(item);
   try{
     if(Array.isArray(item))return item.map(entry=>visit(entry,depth+1));
     const row=object(item);if(!row)throw Error('Native graph data must be JSON objects');
     return Object.fromEntries(Object.entries(row).map(([key,entry])=>[key,visit(entry,depth+1)]));
   }finally{seen.delete(item);}
 };
 return visit(value,0);
}
function sameJSON(left:unknown,right:unknown):boolean {
 if(left===right)return true;
 if(Array.isArray(left)||Array.isArray(right))return Array.isArray(left)&&Array.isArray(right)&&
   left.length===right.length&&left.every((value,index)=>sameJSON(value,right[index]));
 const a=object(left),b=object(right);if(!a||!b)return false;
 const keys=Object.keys(a);return keys.length===Object.keys(b).length&&keys.every(key=>has(b,key)&&sameJSON(a[key],b[key]));
}
function parseTarget(value:unknown):GraphControlTarget|null {
 const row=object(value);
 return row&&bounded(row.id,128)&&bounded(row.type,64)&&bounded(row.label,256)&&bounded(row.authored_id,128)?
   {id:row.id,type:row.type,label:row.label,authored_id:row.authored_id}:null;
}
function parseBounds(row:Record<string,unknown>):Pick<GraphParameterDescriptor,'minimum'|'maximum'|'step'|'units'|'choices'|'excluded'>|null {
 const result:Pick<GraphParameterDescriptor,'minimum'|'maximum'|'step'|'units'|'choices'|'excluded'>={};
 for(const key of ['minimum','maximum','step'] as const)if(has(row,key)){
   if(!finite(row[key])||(key==='step'&&row[key]<=0))return null;
   result[key]=row[key];
 }
 if(result.minimum!==undefined&&result.maximum!==undefined&&result.minimum>result.maximum)return null;
 if(has(row,'units')){if(!bounded(row.units,64,true))return null;result.units=row.units;}
 for(const key of ['choices','excluded'] as const)if(has(row,key)){
   const values=row[key];
   if(!Array.isArray(values)||values.length>256||!values.every(scalar)||
     new Set(values.map(value=>JSON.stringify(value))).size!==values.length)return null;
   result[key]=[...values];
 }
 return result;
}
function matchesType(type:string,value:unknown):boolean {
 if(type==='number')return finite(value);
 if(type==='integer')return Number.isSafeInteger(value);
 if(type==='boolean')return typeof value==='boolean';
 if(type==='enum')return scalar(value);
 if(type==='text')return bounded(value,256,true);
 if(type==='choices')return Array.isArray(value)&&value.length<=32&&value.every(scalar);
 return type==='number_pair'&&Array.isArray(value)&&value.length===2&&value.every(finite);
}
function parseParameter(value:unknown):GraphParameterDescriptor|null {
 const row=object(value);if(!row||!bounded(row.property,256)||!bounded(row.label,256)||
   !parameterTypes.has(String(row.type))||!matchesType(String(row.type),row.value)||
   !has(row,'default')||(row.default!==null&&!matchesType(String(row.type),row.default))||
   typeof row.editable!=='boolean'||typeof row.bindable!=='boolean'||typeof row.active!=='boolean'||
   !bounded(row.group,128))return null;
 const bounds=parseBounds(row);if(!bounds)return null;
 if(row.type==='enum'&&(!bounds.choices?.length||!bounds.choices.some(choice=>choice===row.value)))return null;
 if(has(row,'reason')&&!bounded(row.reason,512,true))return null;
 if(has(row,'constraints')&&(!Array.isArray(row.constraints)||row.constraints.length>32||
   !row.constraints.every(value=>bounded(value,512))))return null;
 if(has(row,'factory_default')&&row.factory_default!==null&&!matchesType(String(row.type),row.factory_default))return null;
 if(has(row,'default_semantics')&&!['factory_default','configured_base'].includes(String(row.default_semantics)))return null;
 let binding_defaults:GraphParameterDescriptor['binding_defaults'],binding_range:GraphParameterDescriptor['binding_range'];
 if(has(row,'binding_range')){
   if(!['number','integer'].includes(String(row.type))||!matchesType('number_pair',row.binding_range)||
     (row.binding_range as number[])[0]>=(row.binding_range as number[])[1])return null;
   binding_range=[...(row.binding_range as [number,number])];
 }
 if(has(row,'binding_defaults')){
   const defaults=object(row.binding_defaults);if(!defaults||Object.keys(defaults).length!==1)return null;
   if(has(defaults,'range')&&matchesType('number_pair',defaults.range)&&
     (defaults.range as number[])[0]<(defaults.range as number[])[1])
     binding_defaults={range:[...(defaults.range as [number,number])]};
   else if(has(defaults,'choices')&&Array.isArray(defaults.choices)&&defaults.choices.length>=2&&
     matchesType('choices',defaults.choices)&&new Set(defaults.choices.map(value=>JSON.stringify(value))).size===defaults.choices.length)
     binding_defaults={choices:[...defaults.choices] as GraphControlScalar[]};
   else return null;
 }
 return {property:row.property,label:row.label,type:row.type as GraphParameterType,
   value:row.value as GraphControlScalar,default:row.default as GraphControlScalar|null,...bounds,
   editable:row.editable,bindable:row.bindable,active:row.active,group:row.group,
   ...(typeof row.reason==='string'?{reason:row.reason}:{}),...(binding_defaults?{binding_defaults}:{}),
   ...(binding_range?{binding_range}:{}),
   ...(Array.isArray(row.constraints)?{constraints:[...row.constraints] as string[]}:{}),
   ...(has(row,'factory_default')?{factory_default:row.factory_default as GraphControlScalar|null}:{}),
   ...(has(row,'default_semantics')?{default_semantics:row.default_semantics as GraphParameterDescriptor['default_semantics']}:{})};
}
function parseBindingField(value:unknown):GraphBindingFieldDescriptor|null {
 const row=object(value);if(!row||!bounded(row.property,64)||!settingsFields.has(row.property)||
   ![...parameterTypes,'number_pair','text','choices'].includes(String(row.type))||!has(row,'default')||
   (row.default!==null&&!matchesType(String(row.type),row.default)))return null;
 const bounds=parseBounds(row);if(!bounds)return null;
 if(has(row,'label')&&!bounded(row.label,256))return null;
 if(has(row,'max_length')&&!integer(row.max_length,256))return null;
 for(const key of ['min_items','max_items'])if(has(row,key)&&!integer(row[key],32))return null;
 if(typeof row.min_items==='number'&&typeof row.max_items==='number'&&row.min_items>row.max_items)return null;
 return {property:row.property,label:typeof row.label==='string'?row.label:row.property,
   type:row.type as GraphBindingFieldDescriptor['type'],
   default:boundedJSON(row.default) as GraphBindingFieldDescriptor['default'],...bounds,
   ...(typeof row.max_length==='number'?{max_length:row.max_length}:{}),
   ...(typeof row.min_items==='number'?{min_items:row.min_items}:{}),
   ...(typeof row.max_items==='number'?{max_items:row.max_items}:{})};
}

/** The response is data; discard undeclared fields and never render HTML from it. */
export function parseGraphControlCatalog(value:unknown):GraphControlCatalog|null {
 try{
   const wire=JSON.stringify(value);
   if(wire===undefined||new TextEncoder().encode(wire).byteLength>GRAPH_CONTROL_CATALOG_BYTES)return null;
   const row=object(value),scope=object(row?.scope),limits=object(row?.limits);
   if(!row||row.schema!==GRAPH_CONTROLS_SCHEMA||!digest(row.request_sha256)||
     !scope||scope.rendered!==false||scope.gpu_context_opened!==false||limits?.bindings!==64||
     !Array.isArray(row.targets)||!row.targets.length||row.targets.length>256||
     !Array.isArray(row.parameters)||row.parameters.length>2048||
     !Array.isArray(row.bindings)||row.bindings.length>64||
     !Array.isArray(row.motions)||row.motions.length>4096||
     !Array.isArray(row.binding_fields)||row.binding_fields.length>32)return null;
   const node=parseTarget(row.node);if(!node)return null;
   const targets:GraphControlTarget[]=[],parameters:GraphParameterDescriptor[]=[],bindings:GraphControlBinding[]=[],
     motions:GraphControlMotion[]=[],binding_fields:GraphBindingFieldDescriptor[]=[];
   const targetIds=new Set<string>(),properties=new Set<string>(),bindingIndices=new Set<number>(),
     motionIds=new Set<string>(),fieldIds=new Set<string>();
   for(const item of row.targets){const target=parseTarget(item);if(!target||targetIds.has(target.id))return null;
     targets.push(target);targetIds.add(target.id);}
   if(!targets.some(target=>sameJSON(target,node)))return null;
   for(const item of row.parameters){const parameter=parseParameter(item);if(!parameter||properties.has(parameter.property))return null;
     parameters.push(parameter);properties.add(parameter.property);}
   for(const item of row.bindings){const binding=object(item),config=object(binding?.config);
     if(!binding||!integer(binding.index,63)||bindingIndices.has(binding.index)||!config||
       config.node!==node.id||!bounded(config.property,256))return null;
     bindings.push({index:binding.index,config:boundedJSON(config) as Document});bindingIndices.add(binding.index);}
   for(const item of row.motions){const motion=object(item),config=object(motion?.motion);
     if(!motion||!bounded(motion.id,128)||motionIds.has(motion.id)||!bounded(motion.label,256)||
       !motionKinds.has(String(motion.kind))||!config||(config.kind??'named')!==motion.kind||
       !Array.isArray(motion.axes)||!motion.axes.length||motion.axes.length>1024||
       motion.axes.some(axis=>!bounded(axis,128))||new Set(motion.axes).size!==motion.axes.length||
       !Array.isArray(motion.motion_fields)||motion.motion_fields.length>5||
       motion.kind!=='named'&&motion.motion_fields.length>0)return null;
     const motion_fields:GraphParameterDescriptor[]=[],properties=new Set<string>();
     for(const item of motion.motion_fields){const field=parseParameter(item);
       if(!field||!motionFields.has(field.property)||properties.has(field.property))return null;
       motion_fields.push(field);properties.add(field.property);}
     motions.push({id:motion.id,label:motion.label,kind:motion.kind as GraphControlMotion['kind'],
       axes:[...motion.axes] as string[],motion:boundedJSON(config) as Document,motion_fields});motionIds.add(motion.id);}
   for(const item of row.binding_fields){const field=parseBindingField(item);if(!field||fieldIds.has(field.property))return null;
     binding_fields.push(field);fieldIds.add(field.property);}
   return {schema:GRAPH_CONTROLS_SCHEMA,request_sha256:row.request_sha256,node,targets,parameters,bindings,motions,
     binding_fields,limits:{bindings:64},scope:{rendered:false,gpu_context_opened:false}};
 }catch{return null;}
}

export function parseGraphEditResponse(value:unknown):GraphEditResponse|null {
 try{
   const row=object(value);if(!row||row.schema!==GRAPH_EDIT_SCHEMA||!digest(row.request_sha256)||!object(row.project))return null;
   const validation=parseGraphValidation(row.validation),catalog=parseGraphControlCatalog(row.catalog);
   if(!validation?.valid||!validation.graph_present||!catalog||catalog.request_sha256!==validation.request_sha256)return null;
   const project=boundedJSON(row.project) as Document;
   if(project.schema!=='framerudiments.project.v1'||!object(project.effect_graph))return null;
   return {schema:GRAPH_EDIT_SCHEMA,request_sha256:row.request_sha256,project,validation,catalog};
 }catch{return null;}
}

function parameter(catalog:GraphControlCatalog,property:string,mode:'editable'|'bindable'):GraphParameterDescriptor {
 const found=catalog.parameters.find(row=>row.property===property);
 if(!found||!found[mode])throw Error(found?.reason||`This parameter is not ${mode==='editable'?'editable':'bindable'}`);
 return found;
}
export function setGraphParameter(catalog:GraphControlCatalog,property:string,value:unknown):GraphEditOperation {
 const descriptor=parameter(catalog,property,'editable');
 if(!matchesType(descriptor.type,value))throw Error('Choose a value of the native parameter type');
 if(descriptor.type==='enum'&&!descriptor.choices?.some(choice=>choice===value))throw Error('Choose an available native parameter value');
 if(descriptor.excluded?.some(choice=>choice===value))throw Error('This value is excluded by the native parameter contract');
 if(finite(value)&&((descriptor.minimum!==undefined&&value<descriptor.minimum)||
   (descriptor.maximum!==undefined&&value>descriptor.maximum)))throw Error('Value is outside the native parameter bounds');
 return {kind:'set_parameter',property,value:value as GraphControlScalar};
}
function selectedBinding(catalog:GraphControlCatalog,index:number):GraphControlBinding {
 const binding=catalog.bindings.find(row=>row.index===index);
 if(!binding)throw Error('This binding is no longer selected');
 return binding;
}
export interface PutGraphBindingInput {
 index:number|null;property:string;motion_id:string;settings:Document;motion_settings?:Document;
}
export function putGraphBinding(catalog:GraphControlCatalog,input:PutGraphBindingInput):GraphEditOperation {
 const descriptor=parameter(catalog,input.property,'bindable');
 const motion=catalog.motions.find(row=>row.id===input.motion_id);
 if(!motion)throw Error('Choose an available native motion source');
 if(input.index!==null)selectedBinding(catalog,input.index);
 else if(catalog.bindings.length>=catalog.limits.bindings)throw Error('The native binding limit has been reached');
 const settings=object(input.settings);if(!settings||Object.keys(settings).some(key=>!settingsFields.has(key)))
   throw Error('Only native binding settings may be edited');
 const copied=boundedJSON(settings) as Document;
 if(has(settings,'axis')&&!(integer(settings.axis,motion.axes.length-1)||
   typeof settings.axis==='string'&&motion.axes.includes(settings.axis)))throw Error('Choose a native motion axis');
 if(descriptor.type==='enum'||descriptor.type==='boolean'){
   const choices=descriptor.choices??[false,true];
   if(!Array.isArray(settings.choices)||settings.choices.length<2||settings.choices.length>32||
     new Set(settings.choices.map(choice=>JSON.stringify(choice))).size!==settings.choices.length||
     settings.choices.some(choice=>!choices.some(available=>available===choice)))throw Error('Choose at least two distinct native categorical values');
   if(has(settings,'range')||(settings.combine!==undefined&&settings.combine!=='set')||
     (settings.weight!==undefined&&settings.weight!==1))throw Error('Categorical bindings require set combination and weight 1');
 }else{
   if(has(settings,'choices'))throw Error('Numeric bindings use a range');
   if(has(settings,'range')&&(!matchesType('number_pair',settings.range)||
     (settings.range as number[])[0]>=(settings.range as number[])[1]))throw Error('Binding range must contain two increasing finite values');
 }
 for(const field of catalog.binding_fields){
   if(!has(settings,field.property)||field.property==='axis'||field.property==='choices')continue;
   const value=settings[field.property];
   if(!matchesType(field.type,value))throw Error(`${field.label} has an invalid native value type`);
   if(field.excluded?.some(choice=>choice===value))throw Error(`${field.label} excludes this native value`);
   if(field.type==='text'&&field.max_length!==undefined&&!bounded(value,field.max_length,true))
     throw Error(`${field.label} exceeds its native length`);
   if(field.type==='enum'&&!field.choices?.some(choice=>choice===value))throw Error(`${field.label} is not an available native value`);
   const numbers=field.type==='number_pair'?value as number[]:finite(value)?[value]:[];
   // The native driver's domain differs from the static parameter's range.
   // For example, GL time can be 1e8 while its driver range remains ±10000.
   const minimum=field.property==='range'&&descriptor.binding_range?descriptor.binding_range[0]:field.minimum;
   const maximum=field.property==='range'&&descriptor.binding_range?descriptor.binding_range[1]:field.maximum;
   if(numbers.some(number=>(minimum!==undefined&&number<minimum)||(maximum!==undefined&&number>maximum)))
     throw Error(`${field.label} is outside its native bounds`);
 }
 let motion_settings:Document|undefined;
 if(input.motion_settings!==undefined){
   const values=object(input.motion_settings);
   if(!values||Object.keys(values).some(key=>!motionFields.has(key)))throw Error('Only named-motion timing settings may be edited');
   if(motion.kind!=='named'&&Object.keys(values).length)throw Error('Existing native motion settings are preserved');
   for(const [property,value] of Object.entries(values)){
     const field=motion.motion_fields.find(row=>row.property===property);
     if(!field||!field.editable)throw Error('This native motion setting is read-only');
     setGraphParameter({...catalog,parameters:motion.motion_fields},property,value);
   }
   motion_settings=boundedJSON(values) as Document;
 }
 return {kind:'put_binding',index:input.index,property:input.property,motion_id:input.motion_id,settings:copied,
   ...(motion_settings===undefined?{}:{motion_settings})};
}
export function removeGraphBinding(catalog:GraphControlCatalog,index:number):GraphEditOperation {
 selectedBinding(catalog,index);return {kind:'remove_binding',index};
}
export function moveGraphBinding(catalog:GraphControlCatalog,index:number,direction:-1|1):GraphEditOperation {
 selectedBinding(catalog,index);
 if(direction!==-1&&direction!==1)throw Error('Binding movement must be one position');
 const positions=catalog.bindings.map(row=>row.index).sort((left,right)=>left-right);
 if(!integer(positions.indexOf(index)+direction,positions.length-1))throw Error('Binding movement is outside the selected target');
 return {kind:'move_binding',index,direction};
}

function pinnedReferences(value:unknown):Set<string>{
 const refs=new Set<string>();
 const visit=(item:unknown)=>{if(Array.isArray(item)){item.forEach(visit);return;}
   const row=object(item);if(!row)return;
   for(const [path,hash] of [['path','sha256'],['shader_path','shader_sha256']])
     if(typeof row[path]==='string'&&typeof row[hash]==='string')refs.add(JSON.stringify([row[path],row[hash]]));
   Object.values(row).forEach(visit);
 };
 visit(value);return refs;
}
const omit=(row:Record<string,unknown>,keys:Set<string>)=>Object.fromEntries(Object.entries(row).filter(([key])=>!keys.has(key)));
const shaderAuthority=new Set(['shader_path','shader_sha256','trusted_shader','shader_passes','max_pass_bytes','frame_index']);

/** Expand only stable authoring locations, never evaluate native parameters here. */
function selectedConfigs(before:Document,after:Document,target:GraphControlTarget):[Document,Document]{
 if(before.type!=='transition_chain')return [before,after];
 if(target.id===target.authored_id){
   if(!sameJSON(omit(before,new Set(['opacity'])),omit(after,new Set(['opacity']))))
     throw Error('The native proposal changed an unrelated chain component');
   return [before,after];
 }
 const stagePrefix=`${target.authored_id}::stage::`,inputPrefix=`${target.authored_id}::input::`;
 const selectedPair=(oldRows:unknown,newRows:unknown,id:string):[Document,Document]=>{
   if(!Array.isArray(oldRows)||!Array.isArray(newRows)||oldRows.length!==newRows.length)
     throw Error('A parameter edit cannot change chain topology');
   let pair:[Document,Document]|null=null;
   for(let index=0;index<oldRows.length;index++){
     const oldRow=object(oldRows[index]),newRow=object(newRows[index]);
     if(!oldRow||!newRow||oldRow.id!==newRow.id||oldRow.type!==newRow.type||
       !sameJSON(oldRow.inputs,newRow.inputs)||!sameJSON(oldRow.matte_input,newRow.matte_input))
       throw Error('A parameter edit cannot change chain topology');
     if(oldRow.id===id){if(pair)throw Error('Ambiguous chain component');pair=[oldRow,newRow];}
     else if(!sameJSON(oldRow,newRow))throw Error('The native proposal changed an unrelated chain component');
   }
   if(!pair)throw Error('The native proposal targets an unknown chain component');
   return pair;
 };
 if(target.id.startsWith(stagePrefix)){
   if(!sameJSON(omit(before,new Set(['stages'])),omit(after,new Set(['stages']))))
     throw Error('The native proposal changed an unrelated chain component');
   return selectedPair(before.stages,after.stages,target.id.slice(stagePrefix.length));
 }
 const match=target.id.startsWith(inputPrefix)?/^(\d+)::(.+)$/.exec(target.id.slice(inputPrefix.length)):null;
 if(!match||!sameJSON(omit(before,new Set(['input_effects'])),omit(after,new Set(['input_effects'])))||
   !Array.isArray(before.input_effects)||!Array.isArray(after.input_effects)||
   before.input_effects.length!==after.input_effects.length)
   throw Error('A parameter edit cannot change chain topology');
 const ordinal=Number(match[1]);
 if(!integer(ordinal,before.input_effects.length-1))throw Error('Unknown chain input');
 for(let index=0;index<before.input_effects.length;index++)
   if(index!==ordinal&&!sameJSON(before.input_effects[index],after.input_effects[index]))
     throw Error('The native proposal changed an unrelated chain component');
 return selectedPair(before.input_effects[ordinal],after.input_effects[ordinal],match[2]);
}

function verifyBindingProposal(original:Document,edited:Document,target:GraphControlTarget,operation:GraphEditOperation):void {
 if(operation.kind==='set_parameter')return;
 const before=original.effect_graph_controls??[],after=edited.effect_graph_controls??[];
 if(!Array.isArray(before)||!Array.isArray(after))throw Error('Invalid native binding proposal');
 const expected=structuredClone(before);
 if(operation.kind==='put_binding'){
   const index=operation.index??before.length,row=object(after[index]);
   if(!row||row.node!==target.id||row.property!==operation.property||
     operation.index!==null&&(!object(before[index])||before[index].node!==target.id))
     throw Error('The native proposal targets an unrelated binding');
   if(operation.index===null)expected.push(row);else expected[index]=row;
 }else{
   const index=operation.index;
   if(!object(before[index])||before[index].node!==target.id)throw Error('The native proposal targets an unrelated binding');
   if(operation.kind==='remove_binding')expected.splice(index,1);
   else{
     const positions=before.flatMap((row:unknown,index:number)=>object(row)?.node===target.id?[index]:[]);
     const position=positions.indexOf(index)+operation.direction;
     if(!integer(position,positions.length-1))throw Error('The native proposal moves outside the binding table');
     const destination=positions[position];
     [expected[index],expected[destination]]=[expected[destination],expected[index]];
   }
 }
 if(!sameJSON(expected,after))throw Error('The native proposal changed unrelated bindings');
}

/** Accept only a native graph/control edit, never an arbitrary project replacement. */
export function applyGraphEditResponse(original:Document,response:GraphEditResponse,
 expectedRequestSha256?:string,operation?:GraphEditOperation):Document {
 if(!expectedRequestSha256||!digest(expectedRequestSha256)||!operation)
   throw Error('A native edit needs its original request identity and operation');
 const checked=parseGraphEditResponse(response);
 if(!checked||(expectedRequestSha256!==undefined&&checked.request_sha256!==expectedRequestSha256))
   throw Error('The native edit response does not match this request');
 const edited=checked.project;
 const protectedFields=(project:Document)=>Object.fromEntries(Object.entries(project)
   .filter(([key])=>key!=='effect_graph'&&key!=='effect_graph_controls'));
 if(!sameJSON(protectedFields(original),protectedFields(edited)))throw Error('The native proposal changed a read-only project field');
 const before=object(original.effect_graph),after=object(edited.effect_graph);
 if(!before||!after||!Array.isArray(before.nodes)||!Array.isArray(after.nodes)||
   !sameJSON(Object.fromEntries(Object.entries(before).filter(([key])=>key!=='nodes')),
             Object.fromEntries(Object.entries(after).filter(([key])=>key!=='nodes')))||before.nodes.length!==after.nodes.length)
   throw Error('A parameter edit cannot change graph topology');
 const owner=checked.catalog.node.authored_id;
 for(let index=0;index<before.nodes.length;index++){
   const oldNode=object(before.nodes[index]),newNode=object(after.nodes[index]);
   if(!oldNode||!newNode||oldNode.id!==newNode.id||oldNode.type!==newNode.type||
     !sameJSON(oldNode.inputs??[],newNode.inputs??[]))throw Error('A parameter edit cannot change graph topology');
   if(oldNode.id!==owner&&!sameJSON(oldNode,newNode))throw Error('The native proposal changed an unrelated graph node');
   if(oldNode.id===owner){
     const [oldConfig,newConfig]=selectedConfigs(oldNode,newNode,checked.catalog.node);
     for(const key of shaderAuthority)if(has(oldConfig,key)!==has(newConfig,key)||!sameJSON(oldConfig[key],newConfig[key]))
       throw Error('The native proposal changed read-only shader authority');
     if(operation?.kind==='set_parameter'){
       const roots=new Set([operation.property.split('.')[0]]);
       // Native key_color.* is an alias for the first key_colors entry when the
       // authored configuration contains only the plural colour collection.
       if(roots.has('key_color')&&!has(oldConfig,'key_color')&&Array.isArray(oldConfig.key_colors)&&oldConfig.key_colors.length)
         roots.add('key_colors');
       if(!sameJSON(omit(oldConfig,roots),omit(newConfig,roots)))
         throw Error('The native proposal changed an unrelated parameter');
     }
   }
 }
 if(!before.nodes.some(value=>object(value)?.id===owner))throw Error('The selected native node has no authored owner');
 if(operation?.kind==='set_parameter'&&!sameJSON(original.effect_graph_controls??[],edited.effect_graph_controls??[]))
   throw Error('A parameter edit cannot replace control bindings');
 if(operation&&operation.kind!=='set_parameter'&&!sameJSON(original.effect_graph,edited.effect_graph))
   throw Error('A binding edit cannot replace graph parameters');
 if(operation)verifyBindingProposal(original,edited,checked.catalog.node,operation);
 const trusted=pinnedReferences(original);
 if([...pinnedReferences(edited)].some(ref=>!trusted.has(ref)))throw Error('The native proposal introduced an unpinned input');
 const result=structuredClone(original);
 result.effect_graph=structuredClone(edited.effect_graph);
 if(has(edited,'effect_graph_controls'))result.effect_graph_controls=structuredClone(edited.effect_graph_controls);
 else delete result.effect_graph_controls;
 return result;
}

export interface GraphControlTicket<T extends object> {serial:number;snapshot:T;node:string;request_sha256?:string;}
/** Selection changes, edits, undo and sidecars all invalidate pending requests. */
export class GraphControlFence<T extends object> {
 private serial=0;
 private currentTicket:GraphControlTicket<T>|null=null;
 begin(snapshot:T,node:string,requestSha256?:string):GraphControlTicket<T>{
   if(!bounded(node,128)||(requestSha256!==undefined&&!digest(requestSha256)))throw Error('Invalid native control request identity');
   const ticket={serial:++this.serial,snapshot,node,...(requestSha256===undefined?{}:{request_sha256:requestSha256})};
   this.currentTicket=ticket;return ticket;
 }
 cancel():void{this.currentTicket=null;this.serial++;}
 current(ticket:GraphControlTicket<T>):boolean{return this.currentTicket===ticket&&ticket.serial===this.serial;}
 acceptCatalog(value:unknown,ticket:GraphControlTicket<T>):GraphControlCatalog|null {
   if(!this.current(ticket))return null;
   const catalog=parseGraphControlCatalog(value);
   return catalog&&catalog.node.id===ticket.node&&
     (ticket.request_sha256===undefined||ticket.request_sha256===catalog.request_sha256)?catalog:null;
 }
 acceptEdit(value:unknown,ticket:GraphControlTicket<T>,original:Document,operation?:GraphEditOperation):Document|null {
   if(!this.current(ticket)||!ticket.request_sha256||!operation)return null;
   const response=parseGraphEditResponse(value);
   if(!response||response.catalog.node.id!==ticket.node)return null;
   return applyGraphEditResponse(original,response,ticket.request_sha256,operation);
 }
}

export async function readGraphControlResponse(response:Response):Promise<unknown>{
 // A catalogue includes every released movement plus admitted configured
 // sources (8 MiB), followed by the bounded draft and native validation report.
 const maximum=GRAPH_CONTROL_RESPONSE_BYTES;
 if(!response.headers.get('content-type')?.toLowerCase().startsWith('application/json')||
   Number(response.headers.get('content-length')??0)>maximum||!response.body){
   await response.body?.cancel();
   throw Error('Native parameter editing returned an invalid response');
 }
 const reader=response.body.getReader(),decoder=new TextDecoder();let size=0,text='';
 try{
   while(true){const next=await reader.read();if(next.done)break;size+=next.value.byteLength;
     if(size>maximum){await reader.cancel();throw Error('Native parameter response exceeded its limit');}
     text+=decoder.decode(next.value,{stream:true});}
   text+=decoder.decode();return JSON.parse(text);
 }finally{reader.releaseLock();}
}

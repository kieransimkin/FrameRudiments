/** Native metadata forms. Staged values never enter the draft before validation. */
import {useEffect,useLayoutEffect,useMemo,useRef,useState,type FormEvent,type ReactNode} from 'react';
import type {Document} from './model.ts';
import {GraphControlFence,moveGraphBinding,putGraphBinding,readGraphControlResponse,removeGraphBinding,
  setGraphParameter,type GraphBindingFieldDescriptor,type GraphControlBinding,type GraphControlCatalog,
  type GraphControlScalar,type GraphEditOperation,type GraphParameterDescriptor} from './graphControls.ts';
import './graph-parameter-editor.css';

type FieldDescriptor=GraphParameterDescriptor|GraphBindingFieldDescriptor;
type FieldDraft=string|boolean|[string,string];
type DraftFields=Record<string,FieldDraft>;
type OperationHandler=(operation:GraphEditOperation)=>void;
const EMPTY_SIDECARS:Record<string,unknown>={};
const scalarLabel=(value:unknown)=>typeof value==='boolean'?(value?'True':'False'):String(value??'');
const htmlToken=(value:string)=>value.replace(/[^a-zA-Z0-9-]/gu,character=>`_${character.codePointAt(0)!.toString(16)}_`);
const constraintText=(value:string)=>value.replaceAll('_',' ');
const sameValue=(left:unknown,right:unknown):boolean=>{
 if(left===right)return true;
 if(Array.isArray(left)||Array.isArray(right))return Array.isArray(left)&&Array.isArray(right)&&left.length===right.length&&left.every((value,index)=>sameValue(value,right[index]));
 if(!left||!right||typeof left!=='object'||typeof right!=='object')return false;
 const a=left as Document,b=right as Document,keys=Object.keys(a);
 return keys.length===Object.keys(b).length&&keys.every(key=>Object.hasOwn(b,key)&&sameValue(a[key],b[key]));
};
const errorText=(value:unknown)=>value instanceof Error?value.message:'The native proposal could not complete.';

/** Text stays text while the user types; finite native values are made on Apply. */
export function graphFieldDraft(field:FieldDescriptor,value:unknown):FieldDraft {
 if(field.type==='boolean')return value===true;
 if(field.type==='number_pair')return Array.isArray(value)?[String(value[0]??''),String(value[1]??'')]:['',''];
 if(field.type==='enum')return JSON.stringify(value??field.default);
 return String(value??'');
}
export function graphFieldValue(field:FieldDescriptor,draft:FieldDraft):GraphControlScalar|[number,number] {
 if(field.type==='boolean'){
   if(typeof draft!=='boolean')throw Error(`${field.label} needs a boolean value.`);
   return draft;
 }
 if(field.type==='enum'){
   const value=typeof draft==='string'?JSON.parse(draft):null;
   if(!field.choices?.some(choice=>choice===value))throw Error(`Choose an available ${field.label.toLowerCase()}.`);
   return value as GraphControlScalar;
 }
 if(field.type==='text')return String(draft);
 if(field.type==='choices')throw Error('Ordered choices use the native choice list.');
 const number=(value:string)=>{
   if(!value.trim()||!Number.isFinite(Number(value)))throw Error(`${field.label} needs a finite number.`);
   const parsed=Number(value);
   if(field.type==='integer'&&!Number.isSafeInteger(parsed))throw Error(`${field.label} needs a whole number.`);
   if((field.minimum!==undefined&&parsed<field.minimum)||(field.maximum!==undefined&&parsed>field.maximum))
     throw Error(`${field.label} is outside its native bounds.`);
   if(field.excluded?.includes(parsed))throw Error(`${field.label} does not admit ${parsed}.`);
   return parsed;
 };
 if(field.type==='number_pair'){
   if(!Array.isArray(draft))throw Error(`${field.label} needs two values.`);
   const pair:[number,number]=[number(draft[0]),number(draft[1])];
   if(pair[0]>=pair[1])throw Error(`${field.label} must increase from lower to upper.`);
   return pair;
 }
 return number(String(draft));
}

/** Exported for structural SSR coverage; no renderer or motion sampler lives here. */
export function GeneratedParameterField({field,draft,onChange,disabled=false,prefix='native'}:{
 field:FieldDescriptor;draft:FieldDraft;onChange:(value:FieldDraft)=>void;disabled?:boolean;prefix?:string;
}){
 const id=`${htmlToken(prefix)}-${htmlToken(field.property)}`,help=`${id}-help`,units=field.units?` (${field.units})`:'';
 const bounds=[field.minimum===undefined?'':`Minimum ${field.minimum}`,field.maximum===undefined?'':`maximum ${field.maximum}`].filter(Boolean).join(', ');
 const defaultLabel='default_semantics' in field&&field.default_semantics==='configured_base'?'Configured base':'Default';
 const hint=[bounds,field.default===null?'':`${defaultLabel} ${Array.isArray(field.default)?field.default.join(' to '):scalarLabel(field.default)}`,
   field.excluded?.length?`Unavailable: ${field.excluded.map(scalarLabel).join(', ')}`:'',
   'constraints' in field?field.constraints?.map(constraintText).join(' · '):''].filter(Boolean).join(' · ');
 if(field.type==='number_pair')return <fieldset className="fr-param-pair" disabled={disabled} aria-describedby={help}>
   <legend>{field.label}{units}</legend><div>
     {(['Lower','Upper'] as const).map((label,index)=><label key={label}>{label}<input type="number" required
       aria-label={`${field.label} ${label.toLowerCase()}${units}`} min={field.minimum} max={field.maximum} step={field.step??'any'}
       value={Array.isArray(draft)?draft[index]:''} onChange={event=>{
         const pair:[string,string]=Array.isArray(draft)?[...draft]:['',''];pair[index]=event.target.value;onChange(pair);
       }}/></label>)}
   </div><small id={help}>{hint}</small></fieldset>;
 return <div className={`fr-param-field${field.type==='boolean'?' fr-param-boolean':''}`}>
   <label htmlFor={id}>{field.label}{units}</label>
   {field.type==='boolean'?<input id={id} type="checkbox" checked={draft===true} disabled={disabled} aria-describedby={help}
     onChange={event=>onChange(event.target.checked)}/>:
    field.type==='enum'?<select id={id} value={String(draft)} disabled={disabled} aria-describedby={help}
     onChange={event=>onChange(event.target.value)}>
       {field.choices?.map(choice=><option key={JSON.stringify(choice)} value={JSON.stringify(choice)}>{scalarLabel(choice)}</option>)}
     </select>:
    field.type==='text'?<input id={id} type="text" value={String(draft)} maxLength={'max_length' in field?field.max_length??256:256}
     disabled={disabled} aria-describedby={help} onChange={event=>onChange(event.target.value)}/>:
    <input id={id} type="number" required value={String(draft)} min={field.minimum} max={field.maximum}
     step={field.type==='integer'?field.step??1:field.step??'any'} disabled={disabled} aria-describedby={help}
     onChange={event=>onChange(event.target.value)}/>}
   <small id={help}>{hint}</small>
 </div>;
}

function ParameterRow({catalog,field,pending,onOperation}:{catalog:GraphControlCatalog;field:GraphParameterDescriptor;
 pending:boolean;onOperation:OperationHandler}){
 const [draft,setDraft]=useState<FieldDraft>(()=>graphFieldDraft(field,field.value)),[error,setError]=useState('');
 const admitted=(value:unknown)=>{
   if(value===undefined||value===null)return false;
   try{setGraphParameter(catalog,field.property,value);return true;}catch{return false;}
 };
 const factoryAdmitted=admitted(field.factory_default);
 const resetValue=factoryAdmitted?field.factory_default!:admitted(field.default)?field.default:null;
 const resetLabel=factoryAdmitted?'Use factory default':
   field.default_semantics==='configured_base'?'Use configured base':'Use default';
 const changed=(()=>{try{return !sameValue(graphFieldValue(field,draft),field.value);}catch{return true;}})();
 const submit=(event:FormEvent)=>{event.preventDefault();try{
   onOperation(setGraphParameter(catalog,field.property,graphFieldValue(field,draft)));setError('');
 }catch(value){setError(errorText(value));}};
 if(!field.editable||!field.active)return <div className="fr-param-readonly" data-property={field.property}>
   <strong>{field.label}</strong><output aria-label={`${field.label} current value`}>{scalarLabel(field.value)}</output>
   <small>{field.reason||(!field.active?'Not applicable to the current parameter choices.':'Read-only native parameter.')}</small>
 </div>;
 return <form className="fr-param-row" data-property={field.property} onSubmit={submit}>
   <GeneratedParameterField field={field} draft={draft} disabled={pending} prefix={`parameter-${catalog.node.id}`}
     onChange={value=>{setDraft(value);setError('');}}/>
   <div className="fr-param-actions"><button type="submit" disabled={pending||!changed} aria-label={`Apply ${field.label}`}>Apply</button>
     {resetValue!==null&&<button type="button" disabled={pending||sameValue(graphFieldDraft(field,resetValue),draft)}
       aria-label={`${resetLabel} ${field.label}`} onClick={()=>{setDraft(graphFieldDraft(field,resetValue));setError('');}}>{resetLabel}</button>}
     {field.bindable&&<span title="This native parameter can use a motion binding">Motion available</span>}
   </div>{error&&<p role="alert" className="fr-param-error">{error}</p>}
 </form>;
}

const categorical=(parameter:GraphParameterDescriptor|undefined)=>parameter?.type==='enum'||parameter?.type==='boolean';
const bindingChoices=(parameter:GraphParameterDescriptor|undefined):GraphControlScalar[]=>
 parameter?.binding_defaults&&'choices' in parameter.binding_defaults?[...parameter.binding_defaults.choices]:[...parameter?.choices??(parameter?.type==='boolean'?[false,true]:[])];
const rangeField=(field:GraphBindingFieldDescriptor,parameter:GraphParameterDescriptor|undefined):GraphBindingFieldDescriptor=>{
 if(field.property!=='range'||!parameter)return field;
 // Mapping defaults and static-value bounds are distinct from the native driver interval.
 return parameter.binding_range?{...field,minimum:parameter.binding_range[0],maximum:parameter.binding_range[1]}:field;
};
const motionMatch=(catalog:GraphControlCatalog,binding?:GraphControlBinding)=>binding?
 catalog.motions.find(motion=>sameValue(motion.motion,binding.config.motion)):catalog.motions[0];
const fieldDrafts=(fields:FieldDescriptor[],values:Document):DraftFields=>Object.fromEntries(fields.map(field=>
 [field.property,graphFieldDraft(field,values[field.property]??field.default)]));

export function createGraphBindingProposal(catalog:GraphControlCatalog,input:{index:number|null;property:string;
 motionId:string;axis:GraphControlScalar;settings:DraftFields;choices:GraphControlScalar[];motionSettings:DraftFields}):GraphEditOperation {
 const parameter=catalog.parameters.find(row=>row.property===input.property),motion=catalog.motions.find(row=>row.id===input.motionId);
 if(!parameter||!motion)throw Error('Choose a native parameter and motion source.');
 const isCategory=categorical(parameter);
 const fields=catalog.binding_fields.filter(field=>!['axis','choices'].includes(field.property)&&
   !(isCategory&&['range','combine','weight'].includes(field.property))).map(field=>rangeField(field,parameter));
 const values:Document=Object.fromEntries(fields.map(field=>[field.property,
   graphFieldValue(field,input.settings[field.property]??graphFieldDraft(field,field.default))]));
 values.axis=input.axis;
 if(isCategory){values.choices=[...input.choices];values.combine='set';values.weight=1;}
 const source:Document=Object.fromEntries(motion.motion_fields.map(field=>[field.property,
   graphFieldValue(field,input.motionSettings[field.property]??graphFieldDraft(field,field.value))]));
 return putGraphBinding(catalog,{index:input.index,property:input.property,motion_id:input.motionId,settings:values,
   ...(motion.motion_fields.length?{motion_settings:source}:{})});
}

export function GraphBindingForm({catalog,binding,pending=false,onOperation,onCancel}:{catalog:GraphControlCatalog;
 binding?:GraphControlBinding;pending?:boolean;onOperation:OperationHandler;onCancel:()=>void}){
 const bindable=catalog.parameters.filter(parameter=>parameter.bindable);
 const [property,setProperty]=useState(String(binding?.config.property??bindable[0]?.property??''));
 const [motionId,setMotionId]=useState(motionMatch(catalog,binding)?.id??'');
 const [motionSearch,setMotionSearch]=useState('');
 const parameter=catalog.parameters.find(row=>row.property===property),motion=catalog.motions.find(row=>row.id===motionId);
 const matchingMotions=catalog.motions.filter(row=>`${row.label} ${row.kind} ${row.axes.join(' ')}`.toLocaleLowerCase().includes(motionSearch.trim().toLocaleLowerCase()));
 const shownMotions=matchingMotions.slice(0,100);
 if(motion&&!shownMotions.some(row=>row.id===motion.id))shownMotions.unshift(motion);
 const [axis,setAxis]=useState<GraphControlScalar>(binding?.config.axis??0);
 const [settings,setSettings]=useState<DraftFields>(()=>{
   const selected=catalog.parameters.find(row=>row.property===(binding?.config.property??bindable[0]?.property));
   const defaults=selected?.binding_defaults&&'range' in selected.binding_defaults?selected.binding_defaults.range:undefined;
   return fieldDrafts(catalog.binding_fields.filter(field=>field.property!=='choices'&&field.property!=='axis'),
     {...binding?.config,...(!binding&&defaults?{range:defaults}:{})});
 });
 const [choices,setChoices]=useState<GraphControlScalar[]>(()=>Array.isArray(binding?.config.choices)?[...binding.config.choices]:bindingChoices(parameter).slice(0,32));
 const [motionSettings,setMotionSettings]=useState<DraftFields>(()=>motion?fieldDrafts(motion.motion_fields,motion.motion):{});
 const [error,setError]=useState('');
 const choiceField=catalog.binding_fields.find(field=>field.property==='choices');
 const minChoices=choiceField?.min_items??2,maxChoices=choiceField?.max_items??32;
 const availableChoices=parameter?.choices??(parameter?.type==='boolean'?[false,true]:[]),isCategory=categorical(parameter);
 const fields=catalog.binding_fields.filter(field=>!['axis','choices'].includes(field.property)&&
   !(isCategory&&['range','combine','weight'].includes(field.property))).map(field=>rangeField(field,parameter));
 const chooseParameter=(property:string)=>{
   const next=catalog.parameters.find(row=>row.property===property);setProperty(property);setError('');
   setChoices(bindingChoices(next).slice(0,32));
   const range=catalog.binding_fields.find(field=>field.property==='range');
   if(range)setSettings(previous=>({...previous,range:graphFieldDraft(range,next?.binding_defaults&&'range' in next.binding_defaults?next.binding_defaults.range:range.default)}));
 };
 const chooseMotion=(id:string)=>{
   const next=catalog.motions.find(row=>row.id===id);setMotionId(id);setError('');
   setMotionSettings(next?fieldDrafts(next.motion_fields,next.motion):{});
   if(next&&!(typeof axis==='number'?axis>=0&&axis<next.axes.length:next.axes.includes(String(axis))))setAxis(0);
 };
 const submit=(event:FormEvent)=>{event.preventDefault();try{
   onOperation(createGraphBindingProposal(catalog,{index:binding?.index??null,property,motionId,axis,settings,choices,motionSettings}));setError('');
 }catch(value){setError(errorText(value));}};
 return <form className="fr-binding-form" aria-label={binding?'Edit native motion binding':'Add native motion binding'} onSubmit={submit}>
   <fieldset disabled={pending}><legend>{binding?'Edit motion binding':'New motion binding'}</legend>
     <label>Parameter<select value={property} onChange={event=>chooseParameter(event.target.value)} required>
       {!bindable.some(row=>row.property===property)&&<option value="">Choose a bindable parameter</option>}
       {bindable.map(row=><option key={row.property} value={row.property}>{row.label} · {row.property}</option>)}
     </select></label>
     <label>Find a motion<input type="search" maxLength={256} value={motionSearch} placeholder="Movement, source type or axis"
       onChange={event=>setMotionSearch(event.target.value)}/></label>
     <label>Motion source<select value={motionId} onChange={event=>chooseMotion(event.target.value)} required>
       <option value="">Choose a native motion</option>{shownMotions.map(row=><option key={row.id} value={row.id}>{row.label} · {row.kind}</option>)}
     </select></label>
     {matchingMotions.length>100&&<p>Showing the first 100 of {matchingMotions.length} matching motions. Refine the search to find another source.</p>}
     {matchingMotions.length===0&&<p>No motion matches this search. The current source is retained.</p>}
     {motion&&<>
       <label>Motion axis<select value={JSON.stringify(axis)} onChange={event=>setAxis(JSON.parse(event.target.value) as GraphControlScalar)}>
         {motion.axes.map((name,index)=><option key={name} value={JSON.stringify(typeof axis==='number'?index:name)}>{name}</option>)}
       </select></label>
       {motion.motion_fields.length>0?<details className="fr-motion-settings"><summary>Named motion timing</summary>
         {motion.motion_fields.map(field=><GeneratedParameterField key={field.property} field={field}
           prefix={`motion-${catalog.node.id}`} draft={motionSettings[field.property]??graphFieldDraft(field,field.value)}
           disabled={pending||!field.editable} onChange={value=>setMotionSettings(previous=>({...previous,[field.property]:value}))}/>)}
       </details>:<p className="fr-param-help">This saved native source keeps its admitted configuration, pins and clock. Selecting another source explicitly replaces it.</p>}
     </>}
     {isCategory&&<fieldset className="fr-binding-choices"><legend>Ordered parameter choices</legend>
       <p>Motion chooses among these values in this order. Categorical controls use set combination and full weight.</p>
       {choices.map((choice,index)=><div className="fr-binding-choice" key={index}>
         <label>Choice {index+1}<select value={JSON.stringify(choice)} onChange={event=>setChoices(previous=>previous.map((entry,i)=>i===index?JSON.parse(event.target.value) as GraphControlScalar:entry))}>
           {availableChoices.map(value=><option key={JSON.stringify(value)} value={JSON.stringify(value)} disabled={value!==choice&&choices.includes(value)}>{scalarLabel(value)}</option>)}
         </select></label>
         <button type="button" aria-label={`Move choice ${index+1} earlier`} disabled={index===0} onClick={()=>setChoices(previous=>{
           const next=[...previous];[next[index-1],next[index]]=[next[index],next[index-1]];return next;
         })}>↑</button>
         <button type="button" aria-label={`Move choice ${index+1} later`} disabled={index===choices.length-1} onClick={()=>setChoices(previous=>{
           const next=[...previous];[next[index+1],next[index]]=[next[index],next[index+1]];return next;
         })}>↓</button>
         <button type="button" aria-label={`Remove choice ${index+1}`} disabled={choices.length<=minChoices} onClick={()=>setChoices(previous=>previous.filter((_,i)=>i!==index))}>Remove</button>
       </div>)}
       <button type="button" disabled={choices.length>=Math.min(maxChoices,availableChoices.length)} onClick={()=>{
         const next=availableChoices.find(value=>!choices.includes(value));if(next!==undefined)setChoices(previous=>[...previous,next]);
       }}>Add choice</button>
     </fieldset>}
     <div className="fr-binding-fields">{fields.map(field=><GeneratedParameterField key={field.property} field={field}
       prefix={`binding-${catalog.node.id}`} draft={settings[field.property]??graphFieldDraft(field,field.default)} disabled={pending}
       onChange={value=>setSettings(previous=>({...previous,[field.property]:value}))}/>)}</div>
     {error&&<p role="alert" className="fr-param-error">{error}</p>}
     <div className="fr-param-actions"><button type="submit" disabled={!parameter?.bindable||!motion||isCategory&&choices.length<minChoices}>
       {binding?'Apply binding':'Add binding'}</button><button type="button" onClick={onCancel}>Cancel</button></div>
   </fieldset>
 </form>;
}

/** A pure metadata view is separately rendered in SSR contract tests. */
export function GraphParameterPanel({catalog,bindingCount,pending=false,onOperation}:{catalog:GraphControlCatalog;
 bindingCount:number;pending?:boolean;onOperation:OperationHandler}){
 const [search,setSearch]=useState(''),[showInactive,setShowInactive]=useState(false),[limit,setLimit]=useState(128);
 const [editing,setEditing]=useState<number|null>(null),[operationError,setOperationError]=useState('');
 const query=search.trim().toLocaleLowerCase();
 const matches=catalog.parameters.filter(row=>(showInactive||row.active)&&(!query||
   `${row.property} ${row.label} ${row.group}`.toLocaleLowerCase().includes(query)));
 const groups=new Map<string,GraphParameterDescriptor[]>();
 for(const row of matches.slice(0,limit)){const rows=groups.get(row.group)??[];rows.push(row);groups.set(row.group,rows);}
 const run=(make:()=>GraphEditOperation)=>{try{onOperation(make());setOperationError('');}catch(error){setOperationError(errorText(error));}};
 const selectedBinding=editing===null||editing===-1?undefined:catalog.bindings.find(row=>row.index===editing);
 return <>
   <section className="fr-native-parameters" aria-label="Generated native parameters">
     <div className="fr-param-heading"><h3>Parameters</h3><span>{catalog.parameters.length} native fields</span></div>
     <p className="fr-param-help">Apply validates the proposal and saves one undo step. Changing the draft or target resets unapplied values.</p>
     <label>Find a parameter<input type="search" maxLength={256} value={search} placeholder="Name, property or group"
       onChange={event=>{setSearch(event.target.value);setLimit(128);}}/></label>
     {catalog.parameters.some(row=>!row.active)&&<label className="fr-param-toggle"><input type="checkbox" checked={showInactive}
       onChange={event=>{setShowInactive(event.target.checked);setLimit(128);}}/>Show inactive parameters</label>}
     {matches.length===0&&<p>No parameters match this selection.</p>}
     {[...groups].map(([group,fields])=><details className="fr-param-group" key={group} open>
       <summary>{group}<span>{fields.length}</span></summary>{fields.map(field=><ParameterRow key={field.property} catalog={catalog}
         field={field} pending={pending} onOperation={onOperation}/>)}</details>)}
     {matches.length>limit&&<button type="button" onClick={()=>setLimit(previous=>Math.min(2048,previous+128))}>Show more parameters ({matches.length-limit} remaining)</button>}
   </section>
   <section className="fr-native-bindings" aria-label="Native motion bindings">
     <div className="fr-param-heading"><h3>Motion bindings</h3><span>{bindingCount}/{catalog.limits.bindings} in project</span></div>
     <p className="fr-param-help">Native motions use the project clock and renderer. Bindings run in project order.</p>
     {catalog.bindings.length===0&&<p>No motion binding on this target.</p>}
     <ol className="fr-binding-list">{catalog.bindings.map((binding,position)=>{
       const motion=motionMatch(catalog,binding),parameter=catalog.parameters.find(row=>row.property===binding.config.property);
       return <li key={binding.index}><strong>{parameter?.label??String(binding.config.property)}</strong>
         <span>{String(binding.config.id??`Binding ${binding.index+1}`)} · {motion?.label??'Saved native motion'}</span>
         <div className="fr-param-actions">
           <button type="button" disabled={pending} aria-label={`Edit binding ${binding.index+1}`} onClick={()=>setEditing(binding.index)}>Edit</button>
           <button type="button" disabled={pending||position===0} aria-label={`Move binding ${binding.index+1} earlier`}
             onClick={()=>run(()=>moveGraphBinding(catalog,binding.index,-1))}>↑</button>
           <button type="button" disabled={pending||position===catalog.bindings.length-1} aria-label={`Move binding ${binding.index+1} later`}
             onClick={()=>run(()=>moveGraphBinding(catalog,binding.index,1))}>↓</button>
           <button type="button" disabled={pending} aria-label={`Remove binding ${binding.index+1}`}
             onClick={()=>run(()=>removeGraphBinding(catalog,binding.index))}>Remove</button>
         </div></li>;
     })}</ol>
     <button type="button" disabled={pending||bindingCount>=catalog.limits.bindings||!catalog.parameters.some(row=>row.bindable)||!catalog.motions.length}
       onClick={()=>setEditing(-1)}>Add native motion binding</button>
     {bindingCount>=catalog.limits.bindings&&<p>The project has reached its native binding limit.</p>}
     {operationError&&<p role="alert" className="fr-param-error">{operationError}</p>}
     {editing!==null&&<GraphBindingForm key={editing} catalog={catalog} binding={selectedBinding} pending={pending}
       onOperation={onOperation} onCancel={()=>setEditing(null)}/>}
   </section>
 </>;
}

interface EditorContext {revision:object;project:Document;sidecars:Record<string,unknown>;target:string;}
interface CatalogState {context:EditorContext;phase:'loading'|'ready'|'error';catalog?:GraphControlCatalog;message:string;}
interface EditState {context:EditorContext;phase:'pending'|'error'|'cancelled';message:string;operation:GraphEditOperation;}
export interface GraphParameterEditorProps {
 project:Document;revision:object;node:string;sidecars?:Record<string,unknown>;
 onApply?:(project:Document,revision:object)=>boolean;
 renderPreview?:(catalog:GraphControlCatalog)=>ReactNode;
}
function responseError(value:unknown,status:number):string {
 const row=value&&typeof value==='object'?value as Record<string,unknown>:null;
 if(row&&typeof row.error==='string'&&row.error.length<=512)return row.error;
 return status===429?'The native editor is busy. Retry when the current request finishes.':
   status===400?'The native validator rejected this proposal. Check its values and existing connections.':'The native editor could not complete the request.';
}

export function GraphParameterEditor({project,revision,node,sidecars=EMPTY_SIDECARS,onApply,renderPreview}:GraphParameterEditorProps){
 const [selection,setSelection]=useState({owner:node,target:node}),[retry,setRetry]=useState(0);
 const enabled=!!onApply;
 const target=selection.owner===node?selection.target:node;
 const context=useMemo<EditorContext>(()=>({revision,project,sidecars,target}),[revision,project,sidecars,target]);
 const currentRef=useRef(context);currentRef.current=context;
 const applyRef=useRef(onApply);applyRef.current=onApply;
 const catalogFence=useRef(new GraphControlFence<EditorContext>()),editFence=useRef(new GraphControlFence<EditorContext>());
 const editRequest=useRef<AbortController|null>(null);
 const [loaded,setLoaded]=useState<CatalogState|null>(null),[edit,setEdit]=useState<EditState|null>(null);
 const state=loaded?.context===context?loaded:null,catalog=state?.phase==='ready'?state.catalog:undefined;
 const currentEdit=edit?.context===context?edit:null,pending=currentEdit?.phase==='pending';
 useLayoutEffect(()=>()=>{
   // App remains mounted when the user leaves the graph workspace. Retire requests
   // during that commit, before a late promise can run ahead of passive cleanup.
   catalogFence.current.cancel();editFence.current.cancel();
   editRequest.current?.abort();editRequest.current=null;
 },[]);
 useEffect(()=>{
   const fence=catalogFence.current,edits=editFence.current;
   edits.cancel();editRequest.current?.abort();editRequest.current=null;setEdit(null);
   if(!node||!enabled){fence.cancel();return;}
   const ticket=fence.begin(context,target),controller=new AbortController();
   const update=(next:Omit<CatalogState,'context'>)=>{
     if(currentRef.current===context&&fence.current(ticket))setLoaded({context,...next});
   };
   update({phase:'loading',message:'Loading native parameter definitions…'});
   const timer=setTimeout(()=>controller.abort(),15000);
   void (async()=>{try{
     const response=await fetch('/api/graph/catalog',{method:'POST',headers:{'Content-Type':'application/json'},
       body:JSON.stringify({project,sidecars,node:target}),signal:controller.signal});
     const value=await readGraphControlResponse(response);
     if(!response.ok)throw Error(responseError(value,response.status));
     const catalog=fence.acceptCatalog(value,ticket);
     if(!catalog){if(fence.current(ticket))throw Error('The native parameter definitions did not match this selection.');return;}
     update({phase:'ready',catalog,message:'Native parameter definitions loaded.'});
   }catch(error){update({phase:'error',message:controller.signal.aborted?'The native parameter request timed out. Retry to load the inspector.':errorText(error)});}
   finally{clearTimeout(timer);}})();
   return ()=>{clearTimeout(timer);fence.cancel();edits.cancel();controller.abort();editRequest.current?.abort();editRequest.current=null;};
   // Context binds project and sidecars; the current commit callback is read through applyRef.
 },[context,node,target,retry,enabled]);
 const propose=(operation:GraphEditOperation)=>{
   if(!catalog||editRequest.current||!applyRef.current)return;
   const fence=editFence.current,ticket=fence.begin(context,target,catalog.request_sha256),controller=new AbortController();
   editRequest.current=controller;
   const update=(phase:EditState['phase'],message:string)=>{
     if(currentRef.current===context&&fence.current(ticket))setEdit({context,phase,message,operation});
   };
   update('pending','Checking this proposal against the native graph and motion controls…');
   const timer=setTimeout(()=>controller.abort(),15000);
   void (async()=>{try{
     const response=await fetch('/api/graph/edit',{method:'POST',headers:{'Content-Type':'application/json'},
       body:JSON.stringify({project,sidecars,node:target,operation}),signal:controller.signal});
     const value=await readGraphControlResponse(response);
     if(currentRef.current!==context||!fence.current(ticket))return;
     if(!response.ok)throw Error(responseError(value,response.status));
     const proposed=fence.acceptEdit(value,ticket,project,operation);
     if(!proposed)throw Error('The native edit did not match this draft and selection.');
     if(currentRef.current!==context||!applyRef.current?.(proposed,revision))throw Error('The draft changed before this proposal could be applied. Retry from the current inspector.');
     // An accepted no-op keeps the same history/context, so explicitly release
     // its pending form instead of waiting for a new snapshot to retire it.
     if(currentRef.current===context&&fence.current(ticket))setEdit(null);
   }catch(error){update('error',controller.signal.aborted?'The native proposal timed out. Your draft is unchanged.':`${errorText(error)} Your draft is unchanged.`);}
   finally{clearTimeout(timer);if(editRequest.current===controller)editRequest.current=null;}})();
 };
 const cancelProposal=()=>{
   if(!editRequest.current)return;
   editFence.current.cancel();editRequest.current.abort();editRequest.current=null;
   setEdit(previous=>previous?.context===context?{...previous,phase:'cancelled',message:'Proposal cancelled. Your draft is unchanged.'}:null);
 };
 const targets=catalog?.targets.filter(row=>row.authored_id===node)??[];
 return <div className="fr-parameter-editor" aria-label="Native parameter and motion editor" aria-busy={!catalog&&enabled||pending}>
   {!enabled?<p>Native parameter editing is unavailable in this view.</p>:<>
     {!catalog&&<div className="fr-param-status" role={state?.phase==='error'?'alert':'status'}>
       {state?.message??'Loading native parameter definitions…'}
       {state?.phase==='error'&&<button type="button" onClick={()=>setRetry(value=>value+1)}>Retry parameters</button>}
       {state?.phase==='error'&&target!==node&&<button type="button" onClick={()=>setSelection({owner:node,target:node})}>Show node parameters</button>}
     </div>}
     {catalog&&<>
       {targets.length>1&&<label>Parameter target<select value={target} onChange={event=>setSelection({owner:node,target:event.target.value})}>
         {targets.map(row=><option key={row.id} value={row.id}>{row.label} · {row.type}</option>)}
       </select></label>}
       {renderPreview?.(catalog)}
       {currentEdit&&<div className={`fr-param-status ${currentEdit.phase}`} role={currentEdit.phase==='error'?'alert':'status'}>{currentEdit.message}
         {pending&&<button type="button" onClick={cancelProposal}>Cancel proposal</button>}
         {currentEdit.phase==='error'&&<button type="button" onClick={()=>propose(currentEdit.operation)}>Retry last proposal</button>}
       </div>}
       <GraphParameterPanel key={`${catalog.request_sha256}:${target}`} catalog={catalog}
         bindingCount={Array.isArray(project.effect_graph_controls)?project.effect_graph_controls.length:0} pending={pending} onOperation={propose}/>
     </>}
   </>}
 </div>;
}

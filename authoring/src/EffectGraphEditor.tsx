/** M10.2 ordered RGBA8 connection authoring. Gestures are never exported. */
import {useEffect,useMemo,useRef,useState,type ChangeEvent,type MouseEvent,type PointerEvent} from 'react';
import './effect-graph-editor.css';
import type {Document} from './model.ts';
import {ADDABLE_EFFECTS,availableEffectSources,connectionIssue,effectInputContract,inspectEffectGraph,layoutEffectGraph,
  type AddableEffect,type EffectNode} from './graphEditor.ts';
import {pendingConnectionText,selectGraphPort,type GraphEndpoint,type PendingConnection} from './graphInteraction.ts';

export interface GraphValidationDisplay{phase:'none'|'checking'|'valid'|'invalid'|'unavailable';message:string;}
interface Props {
 project:Document;revision?:object;validation:GraphValidationDisplay;onValidate:()=>void;
 onInit:()=>void;onSource:(id:string)=>void;
 onAdd:(type:AddableEffect,inputs?:string[],options?:{makeOutput?:boolean})=>void;
 onRewire:(target:string,index:number,source:string)=>void;onInputs:(target:string,inputs:string[])=>void;
 onOpacity:(id:string,value:number)=>void;onBlur:(id:string,value:number)=>void;onProgress:(id:string,value:number)=>void;
 onOutput:(id:string)=>void;onRemove:(id:string)=>void;
}
const errorText=(error:unknown)=>error instanceof Error?error.message:'Graph operation failed';
const names:Record<AddableEffect,string>={copy:'Copy / fork',opacity:'Opacity',filter:'Blur',composite:'Composite layers',
 matte:'Image with matte',transition:'Dissolve transition',matte_transition:'Matte transition'};
const template=(kind:AddableEffect):EffectNode=>({id:'new-node',type:kind==='matte_transition'?'transition':kind,
 ...(kind==='matte_transition'?{transition_style:'matte'}:{})});

export function EffectGraphEditor({project,revision=project,validation,onValidate,onInit,onSource,onAdd,onRewire,onInputs,
 onOpacity,onBlur,onProgress,onOutput,onRemove}:Props){
 const [selected,setSelected]=useState(''),[adding,setAdding]=useState<AddableEffect>('opacity');
 const [createInputs,setCreateInputs]=useState<string[]>([]),[makeOutput,setMakeOutput]=useState(true);
 const [newSource,setNewSource]=useState(''),[appendSource,setAppendSource]=useState('');
 const [pending,setPending]=useState<PendingConnection|null>(null),[operationError,setOperationError]=useState('');
 const [selectedInput,setSelectedInput]=useState<{node:string;index:number}|null>(null);
 const [notice,setNotice]=useState(''),[pointer,setPointer]=useState<{x:number;y:number}|null>(null);
 const stageRef=useRef<HTMLDivElement>(null);
 const drag=useRef<{snapshot:Document;revision:object;endpoint:GraphEndpoint;id:number;x:number;y:number;moved:boolean}|null>(null);
 const suppressClick=useRef(false);
 const inspected=useMemo(()=>{
   try{return {graph:inspectEffectGraph(project),canvas:layoutEffectGraph(project),error:''};}
   catch(error){return {graph:null,canvas:null,error:errorText(error)};}
 },[project]);
 const {graph,canvas}=inspected;
 const activeId=graph?.nodes.some(node=>node.id===selected)?selected:graph?.output??'';
 const active=graph?.nodes.find(node=>node.id===activeId);
 const currentPending=pending?.snapshot===project&&pending.revision===revision?pending:null;
 const contract=active?effectInputContract(active):null,addContract=effectInputContract(template(adding));
 const inputs=Array.from({length:addContract.min},(_,i)=>createInputs[i]??(i===0?activeId:''));
 const providers=availableEffectSources(project).filter(id=>Array.from(id).length<=128&&!graph?.nodes.some(node=>node.id===id));
 const canRemove=!!active&&activeId===graph?.output&&active.type!=='source'&&active.inputs?.length===1&&
   !canvas?.edges.some(edge=>edge.from===activeId&&edge.to!==activeId)&&
   !(Array.isArray(project.effect_graph_controls)&&project.effect_graph_controls.some((row:Document)=>row?.node===activeId));
 useEffect(()=>{setPending(null);setPointer(null);setOperationError('');setAppendSource('');
   if(drag.current)suppressClick.current=true;drag.current=null;},[project,revision]);
 const cancel=()=>{setPending(null);setPointer(null);if(drag.current)suppressClick.current=true;drag.current=null;setOperationError('');setNotice('Connection cancelled.');};
 const act=(callback:()=>void,success='')=>{
   try{callback();setOperationError('');if(success)setNotice(success);}
   catch(error){setOperationError(errorText(error));}
 };
 // Cycle rejection is independent of which existing input is replaced.
 const pendingIssues=useMemo(()=>{
   const result=new Map<string,string|null>();if(!currentPending||!graph)return result;
   const endpoint=currentPending.endpoint;
   for(const node of graph.nodes){
     if(endpoint.kind==='output'){
       if(node.inputs?.length)result.set(node.id,connectionIssue(project,node.id,0,endpoint.node));
     }else result.set(node.id,connectionIssue(project,endpoint.node,endpoint.index,node.id));
   }
   return result;
 },[currentPending,project,graph]);
 const activeIssues=useMemo(()=>{
   const result=new Map<string,string|null>();
   if(active?.inputs?.length)for(const node of graph?.nodes??[])result.set(node.id,connectionIssue(project,active.id,0,node.id));
   return result;
 },[project,graph,active]);
 const activatePort=(endpoint:GraphEndpoint,start:PendingConnection|null=currentPending)=>{
   if(!graph)return;
   const action=selectGraphPort(project,start,endpoint,revision);
   setPending(action.pending);setPointer(null);setOperationError(action.error??'');
   if(endpoint.kind==='input'){setSelected(endpoint.node);setSelectedInput({node:endpoint.node,index:endpoint.index});}
   if(action.connection){
     const {target,index,source}=action.connection;
     if(graph.nodes.find(node=>node.id===target)?.inputs?.[index]!==source)
       act(()=>onRewire(target,index,source),`Connected ${source} to ${target} input ${index+1}.`);
     else setNotice('That input already uses this output.');
   }
 };
 const clickPort=(event:MouseEvent<HTMLButtonElement>,endpoint:GraphEndpoint)=>{
   const ignore=suppressClick.current&&event.detail!==0;suppressClick.current=false;
   if(!ignore)activatePort(endpoint);
 };
 const beginDrag=(event:PointerEvent<HTMLButtonElement>,endpoint:GraphEndpoint)=>{
   if(event.button!==0)return;
   drag.current={snapshot:project,revision,endpoint,id:event.pointerId,x:event.clientX,y:event.clientY,moved:false};
   suppressClick.current=false;event.currentTarget.setPointerCapture?.(event.pointerId);
 };
 const moveDrag=(event:PointerEvent<HTMLButtonElement>)=>{
   const held=drag.current;if(!held||held.id!==event.pointerId||held.snapshot!==project||held.revision!==revision)return;
   if(Math.hypot(event.clientX-held.x,event.clientY-held.y)<6&&!held.moved)return;
   held.moved=true;setPending({snapshot:project,revision,endpoint:held.endpoint});
   const rect=stageRef.current?.getBoundingClientRect();if(rect)setPointer({x:event.clientX-rect.left,y:event.clientY-rect.top});
 };
 const finishDrag=(event:PointerEvent<HTMLButtonElement>)=>{
   const held=drag.current;drag.current=null;
   if(event.currentTarget.hasPointerCapture?.(event.pointerId))event.currentTarget.releasePointerCapture(event.pointerId);
   if(!held||held.id!==event.pointerId||!held.moved)return;
   suppressClick.current=true;setPointer(null);if(held.snapshot!==project||held.revision!==revision){setPending(null);return;}
   const element=document.elementFromPoint(event.clientX,event.clientY)?.closest<HTMLElement>('[data-fx-endpoint]');
   if(!element||element.getAttribute('aria-disabled')==='true'||element.hasAttribute('disabled')){
     setPending({snapshot:project,revision,endpoint:held.endpoint});return;
   }
   const endpoint:GraphEndpoint=element.dataset.fxEndpoint==='output'?{kind:'output',node:element.dataset.fxNode!}:
     {kind:'input',node:element.dataset.fxNode!,index:Number(element.dataset.fxIndex)};
   activatePort(endpoint,{snapshot:project,revision,endpoint:held.endpoint});
 };
 const gestures=(endpoint:GraphEndpoint)=>({
   onClick:(event:MouseEvent<HTMLButtonElement>)=>clickPort(event,endpoint),onPointerDown:(event:PointerEvent<HTMLButtonElement>)=>beginDrag(event,endpoint),
   onPointerMove:moveDrag,onPointerUp:finishDrag,onPointerCancel:()=>{drag.current=null;suppressClick.current=true;setPending(null);setPointer(null);},
 });
 const ghost=(()=>{
   if(!pointer||!currentPending||!canvas)return null;
   const endpoint=currentPending.endpoint,node=canvas.nodes.find(row=>row.id===endpoint.node);if(!node)return null;
   const index=endpoint.kind==='input'?node.inputs.findIndex(port=>port.inputIndex===endpoint.index):-1;
   const fixed={x:endpoint.kind==='output'?node.x+node.width:node.x,y:node.y+(endpoint.kind==='output'?37:53+index*23)};
   const from=endpoint.kind==='output'?fixed:pointer,to=endpoint.kind==='output'?pointer:fixed,bend=Math.max(32,Math.abs(to.x-from.x)/2);
   return `M${from.x} ${from.y} C${from.x+bend} ${from.y},${to.x-bend} ${to.y},${to.x} ${to.y}`;
 })();
 const reorder=(index:number,direction:number)=>{
   if(!active?.inputs)return;const next=[...active.inputs],other=index+direction;
   [next[index],next[other]]=[next[other],next[index]];act(()=>onInputs(active.id,next),'Input order changed.');
 };
 const setValue=(callback:(id:string,value:number)=>void,value:string)=>{if(active)act(()=>callback(active.id,Number(value)));};
 return <section className="fr-fx-editor" aria-label="Executable effect graph editor" onKeyDown={event=>{
   if(event.key==='Escape'&&(currentPending||drag.current)){event.preventDefault();cancel();}
 }}>
   <div className="fr-fx-toolbar"><div><strong>Executable effect graph</strong><p>Ordered RGBA8 connections · output highlighted</p></div>
     {project.effect_graph===undefined&&<button className="primary" onClick={()=>act(()=>{onInit();setSelected('frame');})}>Create graph from final frame</button>}
     {graph&&<details className="fr-fx-create"><summary>Add a node or source</summary><div className="fr-fx-create-body">
       <form onSubmit={event=>{event.preventDefault();act(()=>{onAdd(adding,inputs,{makeOutput});setCreateInputs([]);setSelected('');},`${names[adding]} node created.`);}}>
         <label>Node template<select value={adding} onChange={event=>{setAdding(event.target.value as AddableEffect);setCreateInputs([]);}}>
           {ADDABLE_EFFECTS.map(kind=><option key={kind} value={kind}>{names[kind]}</option>)}</select></label>
         {inputs.map((value,index)=><label key={index}>{addContract.labels[index]??`input ${index+1}`}<select required value={value}
           onChange={event=>setCreateInputs(inputs.map((entry,i)=>i===index?event.target.value:entry))}>
           <option value="">Choose a graph output</option>{graph.nodes.map(node=><option key={node.id} value={node.id}>{node.id}</option>)}</select></label>)}
         <label className="fr-fx-checkbox"><input type="checkbox" checked={makeOutput} onChange={event=>setMakeOutput(event.target.checked)}/>Make the new node the graph output</label>
         <button type="submit" disabled={inputs.some(value=>!graph.nodes.some(node=>node.id===value))||graph.nodes.length>=256}>Create connected node</button>
       </form>
       <form onSubmit={event=>{event.preventDefault();if(newSource)act(()=>{onSource(newSource);setSelected(newSource);setNewSource('');},'Isolated source added.');}}>
         <label>Isolated source<select value={newSource} onChange={event=>setNewSource(event.target.value)}>
           <option value="">Choose an available source</option>{providers.map(id=><option key={id} value={id}>{id}</option>)}</select></label>
         <button type="submit" disabled={!providers.includes(newSource)||graph.nodes.length>=256}>Add source</button>
       </form>
     </div></details>}
   </div>
   <div className={`fr-fx-validation ${validation.phase}`} role="status"><strong>{validation.phase==='valid'?'Native graph checks passed':
     validation.phase==='checking'?'Checking graph':validation.phase==='invalid'?'Graph needs attention':validation.phase==='unavailable'?'Graph check unavailable':'Graph not enabled'}</strong>
     <span>{validation.message}</span>{(validation.phase==='invalid'||validation.phase==='unavailable')&&<button onClick={onValidate}>Retry check</button>}</div>
   {inspected.error?<div className="fr-fx-blank fr-fx-error" role="alert">{inspected.error}<p>The authored graph is preserved. Undo the edit or correct its project JSON before reconnecting ports.</p></div>:
    !canvas?<div className="fr-fx-blank">Create an executable graph explicitly to connect the final frame and isolated sources.</div>:
    <>
     <div className="fr-fx-connection-status" role="status"><span>{operationError||currentPending&&pendingConnectionText(currentPending)||notice||pendingConnectionText(null)}</span>
       {currentPending&&<button type="button" onClick={cancel}>Cancel connection</button>}</div>
     <div className="fr-fx-workspace">
       <div className="fr-fx-scroll" tabIndex={0} aria-label="Effect graph canvas, scroll horizontally or vertically">
         <div className="fr-fx-stage" ref={stageRef} style={{width:canvas.width,height:canvas.height}}>
           <svg className="fr-fx-wires" width={canvas.width} height={canvas.height} aria-hidden="true">
             {canvas.edges.map(edge=><path key={JSON.stringify([edge.from,edge.to,edge.port])} d={edge.path}
               className={`${selectedInput?edge.to===selectedInput.node&&edge.port===selectedInput.index?'selected ':'':edge.to===activeId?'selected ':''}${edge.readOnly?'read-only':''}`}/>)}
             {ghost&&<path className="pending" d={ghost}/>}</svg>
           {canvas.nodes.map(node=>{
             const outputIssue=currentPending?.endpoint.kind==='input'?pendingIssues.get(node.id):null;
             return <div key={node.id} className={`fr-fx-card${node.id===activeId?' selected':''}${node.isOutput?' output':''}`}
               style={{left:node.x,top:node.y,width:node.width,height:node.height}}>
               <button type="button" className="fr-fx-card-select" aria-pressed={node.id===activeId}
                 aria-label={`Select ${node.id}, ${node.type}${node.isOutput?', graph output':''}`} onClick={()=>{setSelected(node.id);setSelectedInput(null);setPending(null);setPointer(null);drag.current=null;setNotice('');}}>
                 <span className="fr-fx-card-title">{node.id}</span><span className="fr-fx-card-type">{node.type}{node.isOutput?' · OUTPUT':''}</span></button>
               <button type="button" className={`fr-fx-out-port${currentPending?.endpoint.kind==='input'?(outputIssue?' incompatible':' compatible'):''}`}
                 data-fx-endpoint="output" data-fx-node={node.id} aria-label={`Connect output of ${node.id}`}
                 aria-pressed={currentPending?.endpoint.kind==='output'&&currentPending.endpoint.node===node.id}
                 title={outputIssue??`${node.id}: RGBA8 output`} {...gestures({kind:'output',node:node.id})}/>
               {node.inputs.map((port,index)=>{
                 const issue=currentPending?.endpoint.kind==='output'?pendingIssues.get(node.id):null;
                 const selectedPort=currentPending?.endpoint.kind==='input'&&currentPending.endpoint.node===node.id&&currentPending.endpoint.index===port.inputIndex;
                 return <button key={port.id} type="button" disabled={!port.editable}
                   className={`fr-fx-in-port${selectedPort?' connecting':''}${port.editable&&currentPending?.endpoint.kind==='output'?(issue?' incompatible':' compatible'):''}`}
                   style={{top:53+index*23}} data-fx-endpoint="input" data-fx-node={node.id} data-fx-index={port.inputIndex}
                   aria-label={`${port.editable?'Connect':'Read-only'} ${node.id} ${port.label} input`} aria-pressed={selectedPort}
                   title={!port.editable?'Nested transition-chain matte; edit the authored stage in project JSON':issue??`${port.label}: RGBA8 interpreted as ${port.role}`}
                   {...(port.editable?gestures({kind:'input',node:node.id,index:port.inputIndex!}):{})}>
                   <span className="fr-fx-port-dot" aria-hidden="true"/>{port.label}</button>;
               })}
             </div>;
           })}
         </div>
       </div>
       <aside className="fr-fx-inspector" aria-label="Selected graph node properties">
         <div><strong>{active?.id}</strong><span> · {active?.type}</span>{active?.id===graph?.output&&<span> · output</span>}</div>
         <div className="fr-fx-inspector-actions">
           <button disabled={!active||active.id===graph?.output} onClick={()=>active&&act(()=>onOutput(active.id))}>Set as output</button>
           <button disabled={!active||graph!.nodes.length>=256} onClick={()=>active&&act(()=>onAdd('copy',[active.id],{makeOutput:false}),`Created a copy branch from ${active.id}.`)}>Fork copy</button>
           <button disabled={!canRemove} onClick={()=>active&&act(()=>{onRemove(active.id);setSelected('');})}>Remove terminal node</button></div>
         {active&&<>
           <div className="fr-fx-inspector-fields">
             <label>Output opacity<input type="number" min="0" max="1" step="0.05" value={typeof active.opacity==='number'?active.opacity:1}
               onChange={(event:ChangeEvent<HTMLInputElement>)=>setValue(onOpacity,event.target.value)}/></label>
             {active.type==='filter'&&<label>Blur radius (px)<input type="number" min="0" max="4" step="0.25" value={typeof active.filter_radius==='number'?active.filter_radius:1.5}
               onChange={(event:ChangeEvent<HTMLInputElement>)=>setValue(onBlur,event.target.value)}/></label>}
             {active.type==='transition'&&<label>Progress<input type="number" min="0" max="1" step="0.05" value={typeof active.progress==='number'?active.progress:0}
               onChange={(event:ChangeEvent<HTMLInputElement>)=>setValue(onProgress,event.target.value)}/></label>}
           </div>
           <div className="fr-fx-port-details"><strong>Ordered input connections</strong>
             {!active.inputs?.length?<p>No inputs on this source.</p>:active.inputs.map((input,index)=><div className="fr-fx-input-row" key={index}>
               <label>{contract?.labels[index]??`input ${index+1}`}<select aria-label={`${active.id} ${contract?.labels[index]??`input ${index+1}`} source`} value={input}
                 onFocus={()=>setSelectedInput({node:active.id,index})}
                 onChange={event=>{if(event.target.value!==input)act(()=>onRewire(active.id,index,event.target.value),'Connection updated.');}}>
                 {graph!.nodes.map(node=><option key={node.id} value={node.id} disabled={!!activeIssues.get(node.id)}>{node.id}{activeIssues.get(node.id)?' (unavailable)':''}</option>)}
               </select></label>
               <div className="fr-fx-input-actions">
                 <button title="Move input earlier" aria-label={`Move ${active.id} input ${index+1} earlier`} disabled={index===0} onClick={()=>reorder(index,-1)}>↑</button>
                 <button title="Move input later" aria-label={`Move ${active.id} input ${index+1} later`} disabled={index===active.inputs!.length-1} onClick={()=>reorder(index,1)}>↓</button>
                 {contract?.variable&&<button aria-label={`Remove ${active.id} input ${index+1}`} disabled={active.inputs!.length<=contract.min}
                   onClick={()=>act(()=>onInputs(active.id,active.inputs!.filter((_,i)=>i!==index)),'Input removed.')}>Remove</button>}</div>
             </div>)}
             {contract?.variable&&<form className="fr-fx-add-input" onSubmit={event=>{event.preventDefault();if(appendSource)act(()=>{onInputs(active.id,[...active.inputs!,appendSource]);setAppendSource('');},'Input added.');}}>
               <label>Add {active.type==='matte'?'mask':'layer'} input<select value={appendSource} onChange={event=>setAppendSource(event.target.value)}>
                 <option value="">Choose a graph output</option>{graph!.nodes.map(node=><option key={node.id} value={node.id} disabled={!!activeIssues.get(node.id)}>{node.id}</option>)}</select></label>
               <button type="submit" disabled={!appendSource||!!activeIssues.get(appendSource)||active.inputs!.length>=contract.max}>Add input</button></form>}
             {active.type==='transition_chain'&&<p>Nested stage matte wires are shown read-only. Top-level image connections remain editable.</p>}
           </div>
         </>}
         <p>Inputs keep their order. Masks and displacement maps use RGBA8 image values; connection roles do not convert pixels.</p>
       </aside>
     </div>
    </>}
 </section>;
}

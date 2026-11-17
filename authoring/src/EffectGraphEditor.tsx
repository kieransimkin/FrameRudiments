/** M10.1: selectable typed node/port canvas; M10.2 owns rewiring semantics. */
import {useMemo,useState,type ChangeEvent} from 'react';
import './effect-graph-editor.css';
import type {Document} from './model.ts';
import {ADDABLE_EFFECTS,inspectEffectGraph,layoutEffectGraph,
  type AddableEffect} from './graphEditor.ts';

interface Props {
 project:Document;
 onInit:()=>void;
 onAdd:(type:AddableEffect)=>void;
 onOpacity:(id:string,value:number)=>void;
 onBlur:(id:string,value:number)=>void;
 onProgress:(id:string,value:number)=>void;
 onOutput:(id:string)=>void;
 onRemove:(id:string)=>void;
}
export function EffectGraphEditor({project,onInit,onAdd,onOpacity,onBlur,onProgress,onOutput,onRemove}:Props) {
 const [selected,setSelected]=useState('');
 const [adding,setAdding]=useState<AddableEffect>('opacity');
 // Validate before displaying any authored path. This is a structural check;
 // the backend still owns all shader, audio and semantic preflight.
 const canvas=useMemo(()=>layoutEffectGraph(project),[project.effect_graph]);
 const graph=useMemo(()=>inspectEffectGraph(project),[project.effect_graph]);
 const activeId=graph?.nodes.some(node=>node.id===selected)?selected:graph?.output??'';
 const active=graph?.nodes.find(node=>node.id===activeId);
 const canRemove=!!active&&activeId===graph?.output&&active.type!=='source'&&
   active.inputs?.length===1&&!graph?.nodes.some(n=>n.id!==activeId&&n.inputs?.includes(activeId))&&
   !project.effect_graph_controls?.some((row:Document)=>row.node===activeId);
 const update=(callback:(id:string,value:number)=>void,value:string)=>{
   if(!active)return;
   const num=Number(value);
   callback(active.id,num);
 };
 return <section className="fr-fx-editor" aria-label="Executable effect graph editor">
   <div className="fr-fx-toolbar"><div><strong>Executable effect graph</strong>
     <p>RGBA8 ports · graph output highlighted · native timing unchanged</p></div>
     {!graph?<button className="primary" onClick={()=>{onInit();setSelected('frame');}}>Create graph from final frame</button>:
      <div className="fr-fx-add"><label>Add node <select value={adding} onChange={(e:ChangeEvent<HTMLSelectElement>)=>setAdding(e.target.value as AddableEffect)}>
       {ADDABLE_EFFECTS.map(type=><option key={type} value={type}>{type}</option>)}
       </select></label><button onClick={()=>{onAdd(adding);setSelected('');}}>Append after output</button></div>}
   </div>
   {!canvas?<div className="fr-fx-blank">No executable <code>effect_graph</code> is configured. Creating one is opt-in and preserves the rest of your project.</div>:
    <>
     <div className="fr-fx-scroll" tabIndex={0} aria-label="Effect graph canvas, scroll horizontally or vertically">
      <div className="fr-fx-stage" style={{width:canvas.width,height:canvas.height}}>
       <svg className="fr-fx-wires" width={canvas.width} height={canvas.height} aria-hidden="true">
        {canvas.edges.map(edge=><path key={`${edge.from}-${edge.to}-${edge.port}`} d={edge.path} />)}
       </svg>
       {canvas.nodes.map(node=><button key={node.id} type="button"
         className={`fr-fx-card${node.id===activeId?' selected':''}${node.isOutput?' output':''}`}
         style={{left:node.x,top:node.y,width:node.width,height:node.height}}
         aria-pressed={node.id===activeId} aria-label={`${node.id}, ${node.type}, ${node.inputs.length} inputs${node.isOutput?', graph output':''}`}
         onClick={()=>setSelected(node.id)}>
         <span className="fr-fx-card-title">{node.id}</span>
         <span className="fr-fx-card-type">{node.type}{node.isOutput?' · OUTPUT':''}</span>
         <span className="fr-fx-out-port" aria-hidden="true" title="RGBA8 output"/>
         {node.inputs.map((port,i)=><span key={port.id} className="fr-fx-in-port" style={{top:53+i*23}}>
           <span className="fr-fx-port-dot" aria-hidden="true"/>{port.label}</span>)}
        </button>)}
      </div>
     </div>
     <div className="fr-fx-inspector" aria-label="Selected graph node properties">
      <div><strong>{active?.id}</strong><span> · {active?.type}</span>
       {active?.id===graph?.output&&<span> · final graph output</span>}</div>
      <div className="fr-fx-inspector-actions">
       <button disabled={!active||active.id===graph?.output} onClick={()=>active&&onOutput(active.id)}>Set as output</button>
       <button disabled={!canRemove} onClick={()=>{if(active){onRemove(active.id);setSelected('');}}}>Remove terminal node</button>
      </div>
      {active&&<div className="fr-fx-inspector-fields">
       <label>Output opacity <input type="number" min="0" max="1" step="0.05"
         value={typeof active.opacity==='number'?active.opacity:1}
         onChange={(e:ChangeEvent<HTMLInputElement>)=>update(onOpacity,e.target.value)}/></label>
       {active.type==='filter'&&<label>Blur radius (px) <input type="number" min="0" max="4" step="0.25"
         value={typeof active.filter_radius==='number'?active.filter_radius:1.5}
         onChange={(e:ChangeEvent<HTMLInputElement>)=>update(onBlur,e.target.value)}/></label>}
       {active.type==='transition'&&<label>Progress <input type="number" min="0" max="1" step="0.05"
         value={typeof active.progress==='number'?active.progress:0}
         onChange={(e:ChangeEvent<HTMLInputElement>)=>update(onProgress,e.target.value)}/></label>}
       <div className="fr-fx-port-details"><strong>Input ports</strong>
        {(active.inputs??[]).length===0?<p>No inputs (source node)</p>:
          (active.inputs??[]).map((input,i)=><div key={i}><code>{i}</code> ← <code>{input}</code> (RGBA8)</div>)}
       </div>
      </div>}
      <p>Port wires are read-only in M10.1. Connection editing and richer semantic checks arrive in M10.2. Exported project JSON uses the existing Python <code>effect_graph</code> format.</p>
     </div>
    </>}
 </section>;
}

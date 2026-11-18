/** Actual React SSR and form-adapter contracts; these do not exercise a browser. */
import {after,before,test} from 'node:test';
import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';
import {createElement,type ComponentType} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer,type ViteDevServer} from 'vite';
import {parseGraphControlCatalog,type GraphControlCatalog,type GraphParameterDescriptor} from '../src/graphControls.ts';

let server:ViteDevServer,Field:ComponentType<any>,Panel:ComponentType<any>,Binding:ComponentType<any>,Editor:ComponentType<any>;
let fieldDraft:(...args:any[])=>any,fieldValue:(...args:any[])=>any,proposal:(...args:any[])=>any;
before(async()=>{
 server=await createServer({configFile:false,root:fileURLToPath(new URL('..',import.meta.url)),logLevel:'error',
   optimizeDeps:{noDiscovery:true,include:[]},server:{middlewareMode:true,hmr:false,ws:false,watch:null}});
 const module=await server.ssrLoadModule('/src/GraphParameterEditor.tsx');
 Field=module.GeneratedParameterField;Panel=module.GraphParameterPanel;Binding=module.GraphBindingForm;Editor=module.GraphParameterEditor;
 fieldDraft=module.graphFieldDraft;fieldValue=module.graphFieldValue;proposal=module.createGraphBindingProposal;
});
after(async()=>{await server?.close();});
const render=(component:ComponentType<any>,props:Record<string,unknown>)=>renderToStaticMarkup(createElement(component,props));
const number=(property:string,overrides:Partial<GraphParameterDescriptor>={}):GraphParameterDescriptor=>({property,label:property,
 type:'number',value:.4,default:1,minimum:0,maximum:1,editable:true,bindable:true,group:'Appearance',active:true,
 binding_defaults:{range:[0,1]},...overrides});
function catalog():GraphControlCatalog {
 const target={id:'fx',type:'layer_style',label:'fx',authored_id:'fx'};
 const native={schema:'framerudiments.authoring-controls.v1',request_sha256:'a'.repeat(64),node:target,targets:[target],
   parameters:[number('opacity'),number('palette.0.r',{label:'Palette red',value:.1}),number('offset.x',{label:'Offset X',minimum:-65536,maximum:65536,
     binding_defaults:{range:[-65536,65536]},binding_range:[-65536,65536]}),number('passes',{label:'Passes',type:'integer',value:2,default:2,minimum:1,maximum:8,step:1}),
     {property:'enabled',label:'Enabled',type:'boolean',value:true,default:false,editable:true,bindable:true,group:'Options',active:true,
       choices:[false,true],binding_defaults:{choices:[false,true]}},
     {property:'mode',label:'Mode',type:'enum',value:'screen',default:'normal',editable:true,bindable:true,group:'Options',active:true,
       choices:['normal','screen'],binding_defaults:{choices:['normal','screen']}},
     {property:'resource',label:'External resource',type:'enum',value:'<unsafe>',default:null,editable:false,bindable:false,
       group:'Source',active:true,choices:['<unsafe>'],reason:'Pinned resources stay read-only.'},
     number('inactive',{label:'Inactive option',active:false,editable:false,reason:'Choose the applicable style first.'})],
   bindings:[],motions:[{id:'motion:native',label:'Released movement',kind:'named',axes:['x','y'],motion:{kind:'named',pattern:'released'},
     motion_fields:[number('rate',{label:'Rate',value:1,default:1,minimum:.00001,maximum:undefined,bindable:false,group:'motion',binding_defaults:undefined}),
       {property:'repeat_clock',label:'Repeat clock',type:'enum',value:'continuous',default:'continuous',choices:['continuous','source'],
         editable:true,bindable:false,active:true,group:'motion'},
       {property:'interpolate',label:'Interpolate',type:'boolean',value:false,default:false,choices:[false,true],editable:true,bindable:false,active:true,group:'motion'}]},
     {id:'motion:pinned',label:'Saved recording',kind:'recording',axes:['left_hand','right_hand'],
       motion:{kind:'recording',file:{path:'motion.json',sha256:'b'.repeat(64)},repeat_clock:'source'},motion_fields:[]}],
   binding_fields:[{property:'id',label:'Binding ID',type:'text',default:'',max_length:64},
     {property:'axis',label:'Axis',type:'enum',default:'x',choices:['x','y','z']},
     {property:'range',label:'Range',type:'number_pair',default:[-1,1],minimum:-10000,maximum:10000},
     {property:'curve',label:'Curve',type:'enum',default:'linear',choices:['linear','smoothstep']},
     {property:'steps',label:'Steps',type:'integer',default:0,minimum:0,maximum:128,excluded:[1]},
     {property:'invert',label:'Invert',type:'boolean',default:false},
     {property:'combine',label:'Combination',type:'enum',default:'set',choices:['set','add']},
     {property:'weight',label:'Weight',type:'number',default:1,minimum:0,maximum:1},
     {property:'choices',label:'Choices',type:'choices',default:[],min_items:2,max_items:32},
     {property:'smoothing_frames',label:'Smoothing',type:'integer',default:0,minimum:0,maximum:120,units:'frames'}],
   limits:{bindings:64},scope:{rendered:false,gpu_context_opened:false}};
 // Strip fixture-only undefined optional fields just as a native JSON wire does.
 const parsed=parseGraphControlCatalog(JSON.parse(JSON.stringify(native)));
 assert.ok(parsed,'fixture must pass the real native contract parser');return parsed;
}
const handlers={onOperation(){},onCancel(){}};
function noNestedForms(markup:string){
 let depth=0;
 for(const [tag] of markup.matchAll(/<\/?form\b[^>]*>/g)){
   if(tag.startsWith('</'))depth--;else{assert.equal(depth,0);depth++;}
   assert.ok(depth>=0);
 }
 assert.equal(depth,0);
}

test('generated fields preserve native numeric, integer, boolean and enum controls with accessible labels',()=>{
 const data=catalog();
 const markup=render(Panel,{catalog:data,bindingCount:0,...handlers});
 assert.match(markup,/data-property="palette\.0\.r"/);assert.match(markup,/Palette red/);
 assert.match(markup,/id="parameter-fx-passes" type="number"[^>]*min="1"[^>]*max="8"[^>]*step="1"/);
 assert.match(markup,/id="parameter-fx-enabled" type="checkbox"[^>]*checked=""/);
 assert.match(markup,/value="&quot;screen&quot;" selected=""/);
 assert.match(markup,/for="parameter-fx-opacity"/);assert.match(markup,/aria-describedby="parameter-fx-opacity-help"/);
 assert.match(markup,/&lt;unsafe&gt;/);assert.doesNotMatch(markup,/<unsafe>/);
 assert.match(markup,/Pinned resources stay read-only/);assert.doesNotMatch(markup,/data-property="inactive"/);
 assert.match(markup,/Show inactive parameters/);assert.match(markup,/Native motions use the project clock/);
 noNestedForms(markup);
});

test('metadata default semantics and native exclusions remain visible in generated forms',()=>{
 const data=catalog();data.parameters=[number('gain',{default:.4,value:.4,factory_default:1,default_semantics:'configured_base',
   constraints:['Gain is applied after the source.','band_start_must_precede_end']})];
 const markup=render(Panel,{catalog:data,bindingCount:0,...handlers});
 assert.match(markup,/Configured base 0\.4/);assert.match(markup,/Use factory default/);
 assert.match(markup,/Gain is applied after the source/);
 assert.match(markup,/band start must precede end/);assert.doesNotMatch(markup,/band_start_must_precede_end/);
 const steps=data.binding_fields.find(field=>field.property==='steps')!;
 const input=render(Field,{field:steps,draft:'1',onChange(){}});
 assert.match(input,/Unavailable: 1/);
 assert.throws(()=>fieldValue(steps,'1'),/does not admit 1/);
});

test('factory reset cannot stage a choice outside the current native topology',()=>{
 const data=catalog();data.parameters=[{property:'transition_style',label:'Transition style',type:'enum',value:'matte',
   default:'dissolve',factory_default:'dissolve',default_semantics:'factory_default',choices:['matte'],
   editable:true,bindable:false,group:'Transition',active:true}];
 let markup=render(Panel,{catalog:data,bindingCount:0,...handlers});
 assert.doesNotMatch(markup,/Use factory default|Use default/);
 data.parameters[0]={...data.parameters[0],default:'matte',default_semantics:'configured_base'};
 markup=render(Panel,{catalog:data,bindingCount:0,...handlers});
 assert.match(markup,/Use configured base/);assert.doesNotMatch(markup,/Use factory default/);
});

test('field label references stay single HTML tokens for native IDs containing spaces or Unicode',()=>{
 const field=number('color.r',{label:'Red'}),markup=render(Field,{field,draft:'.5',prefix:'node with spaces ⌁',onChange(){}});
 const id=/\bid="([^"]+)"/.exec(markup)![1];
 assert.ok(id&&!/\s/.test(id));assert.ok(markup.includes(`for="${id}"`));assert.ok(markup.includes(`aria-describedby="${id}-help"`));
});

test('new numeric binding uses target range and generated native driver and clock fields',()=>{
 const data=catalog(),before=JSON.stringify(data);
 const markup=render(Binding,{catalog:data,...handlers});
 assert.match(markup,/aria-label="Add native motion binding"/);
 assert.match(markup,/aria-label="Range lower"[^>]*value="0"/);assert.match(markup,/aria-label="Range upper"[^>]*value="1"/);
 assert.match(markup,/maxLength="64"/i);assert.match(markup,/Named motion timing/);
 assert.match(markup,/Repeat clock/);assert.match(markup,/Smoothing \(frames\)/);
 assert.match(markup,/Saved recording · recording/);assert.match(markup,/Motion axis/);
 assert.equal(JSON.stringify(data),before);noNestedForms(markup);
 const wide={...data,parameters:[data.parameters.find(row=>row.property==='offset.x')!]};
 const wideMarkup=render(Binding,{catalog:wide,...handlers});
 assert.match(wideMarkup,/aria-label="Range lower"[^>]*min="-65536"[^>]*max="65536"[^>]*value="-65536"/);
});

test('saved source uses its exact catalog choice and axis while exposing readonly native clock text',()=>{
 const data=catalog(),saved=data.motions.find(row=>row.kind==='recording')!;
 const binding={index:4,config:{node:'fx',property:'opacity',id:'retained',motion:structuredClone(saved.motion),axis:'right_hand',range:[.2,.7],curve:'linear'}};
 data.bindings=[binding];
 const markup=render(Binding,{catalog:data,binding,...handlers});
 assert.match(markup,/value="motion:pinned" selected=""/);
 assert.match(markup,/value="&quot;right_hand&quot;" selected=""/);
 assert.match(markup,/keeps its admitted configuration, pins and clock/);
 assert.doesNotMatch(markup,/Named motion timing/);
 assert.match(markup,/aria-label="Range lower"[^>]*value="0.2"/);
 const panel=render(Panel,{catalog:data,bindingCount:6,...handlers});
 assert.match(panel,/<button[^>]*disabled=""[^>]*aria-label="Move binding 5 earlier"/);
 assert.match(panel,/<button[^>]*disabled=""[^>]*aria-label="Move binding 5 later"/);
});

test('categorical bindings expose ordered typed choices and omit numeric range, combination and weight inputs',()=>{
 const data=catalog(),binding={index:1,config:{node:'fx',property:'enabled',motion:data.motions[0].motion,choices:[true,false],axis:0}};
 data.bindings=[binding];
 const markup=render(Binding,{catalog:data,binding,...handlers});
 assert.match(markup,/Ordered parameter choices/);assert.match(markup,/Choice 1/);
 assert.match(markup,/value="true" selected=""/);
 assert.match(markup,/aria-label="Remove choice 1" disabled=""/);
 assert.match(markup,/aria-label="Move choice 1 earlier" disabled=""/);
 assert.doesNotMatch(markup,/aria-label="Range lower"|id="binding-fx-combine"|id="binding-fx-weight"/);
 noNestedForms(markup);
});

test('pending proposals disable editable fields and full binding tables disable addition',()=>{
 const data=catalog();
 const markup=render(Panel,{catalog:data,bindingCount:64,pending:true,...handlers});
 assert.match(markup,/id="parameter-fx-opacity"[^>]*disabled=""/);
 assert.match(markup,/<button[^>]*disabled=""[^>]*>Add native motion binding/);
 assert.match(markup,/native binding limit/);
 const form=render(Binding,{catalog:data,pending:true,...handlers});
 assert.match(form,/<fieldset disabled=""/);
});

test('parameter lists remain bounded and missing native service has an explicit non-editable state',()=>{
 const data=catalog();data.parameters=Array.from({length:129},(_,index)=>number(`parameters.${index}`,{label:`Parameter ${index}`}));
 const markup=render(Panel,{catalog:data,bindingCount:0,...handlers});
 assert.equal([...markup.matchAll(/data-property=/g)].length,128);
 assert.match(markup,/Show more parameters \(1 remaining\)/);
 const project={effect_graph:{nodes:[],output:'fx'}},revision={project};
 const unavailable=render(Editor,{project,revision,node:'fx'});
 assert.match(unavailable,/Native parameter editing is unavailable/);assert.doesNotMatch(unavailable,/<input|<select/);
 const loading=render(Editor,{project,revision,node:'fx',onApply(){return true;}});
 assert.match(loading,/aria-busy="true"/);assert.match(loading,/Loading native parameter definitions/);
 assert.doesNotMatch(loading,/Output opacity|Blur radius/);
});

test('large native motion catalogs render a bounded search chooser while retaining the exact selected source',()=>{
 const data=catalog(),template=data.motions[0];
 data.motions=Array.from({length:1731},(_,index)=>({...template,id:`motion:${index}`,label:`Released movement ${index}`,
   motion:{...template.motion,pattern:`pattern-${index}`}}));
 const binding={index:0,config:{node:'fx',property:'opacity',motion:data.motions[1730].motion,axis:0,range:[0,1]}};
 data.bindings=[binding];
 const markup=render(Binding,{catalog:data,binding,...handlers});
 assert.match(markup,/Find a motion/);assert.match(markup,/first 100 of 1731 matching motions/);
 assert.match(markup,/value="motion:1730" selected=""/);
 assert.equal([...markup.matchAll(/<option value="motion:/g)].length,101);
});

test('form conversion keeps enum/boolean types and rejects empty, nonfinite, fractional integer or reversed range input',()=>{
 const data=catalog();
 const opacity=data.parameters[0],passes=data.parameters.find(row=>row.property==='passes')!;
 assert.equal(fieldValue(opacity,'0.35'),.35);assert.equal(fieldValue(data.parameters.find(row=>row.property==='enabled')!,false),false);
 const enumeration={...opacity,type:'enum',choices:[1,2,true],value:1,default:1};
 assert.equal(fieldValue(enumeration,fieldDraft(enumeration,2)),2);
 assert.equal(fieldValue(enumeration,fieldDraft(enumeration,true)),true);
 for(const invalid of ['', ' ', 'Infinity', 'NaN'])assert.throws(()=>fieldValue(opacity,invalid),/finite number/);
 assert.throws(()=>fieldValue(passes,'1.5'),/whole number/);
 assert.throws(()=>fieldValue(opacity,'2'),/native bounds/);
 const range=data.binding_fields.find(field=>field.property==='range')!;
 assert.throws(()=>fieldValue(range,['1','0']),/increase from lower to upper/);
 assert.deepEqual(fieldValue(range,['-2','3']),[-2,3]);
});

test('binding form proposal preserves native source identity and passes typed mapping/settings to the atomic operation',()=>{
 const data=catalog(),before=JSON.stringify(data);
 const input={index:null,property:'opacity',motionId:'motion:pinned',axis:'left_hand',settings:{id:'saved',range:['.2','.8'],
   smoothing_frames:'5',invert:true},choices:[],motionSettings:{}};
 const operation=proposal(data,input);
 assert.equal(operation.kind,'put_binding');assert.equal(operation.motion_id,'motion:pinned');assert.equal(operation.index,null);
 assert.deepEqual(operation.settings.range,[.2,.8]);assert.equal(operation.settings.smoothing_frames,5);
 assert.equal(operation.settings.invert,true);assert.equal(operation.settings.axis,'left_hand');
 assert.equal(operation.motion_settings,undefined);assert.equal(operation.settings.motion,undefined);
 assert.equal(JSON.stringify(data),before);
 const category=proposal(data,{...input,property:'enabled',choices:[true,false]});
 assert.deepEqual(category.settings.choices,[true,false]);assert.equal(category.settings.range,undefined);
 assert.equal(category.settings.combine,'set');assert.equal(category.settings.weight,1);
 assert.throws(()=>proposal(data,{...input,property:'enabled',choices:[true,true]}),/distinct/);
 const named=proposal(data,{...input,motionId:'motion:native',axis:1,motionSettings:{rate:'2',repeat_clock:'"source"',interpolate:true}});
 assert.deepEqual(named.motion_settings,{rate:2,repeat_clock:'source',interpolate:true});
 assert.throws(()=>proposal(data,{...input,settings:{...input.settings,steps:'1'}}),/does not admit/);
 const wide=proposal(data,{...input,property:'offset.x',settings:{range:['-65536','65536']}});
 assert.deepEqual(wide.settings.range,[-65536,65536]);
 data.parameters.push(number('time_seconds',{maximum:1e8,binding_range:[-10000,10000],binding_defaults:{range:[0,10000]}}));
 assert.throws(()=>proposal(data,{...input,property:'time_seconds',settings:{range:['0','100000000']}}),/native bounds/);
});

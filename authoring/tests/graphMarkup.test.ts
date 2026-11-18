/** Server-rendered markup contracts; these do not exercise a browser or layout. */
import {after,before,test} from 'node:test';
import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';
import {createElement,type ComponentType} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer,type ViteDevServer} from 'vite';
import type {Document} from '../src/model.ts';

let server:ViteDevServer,Editor:ComponentType<any>;
before(async()=>{
 server=await createServer({configFile:false,root:fileURLToPath(new URL('..',import.meta.url)),
   logLevel:'error',optimizeDeps:{noDiscovery:true,include:[]},server:{middlewareMode:true,hmr:false,ws:false,watch:null}});
 Editor=(await server.ssrLoadModule('/src/EffectGraphEditor.tsx')).EffectGraphEditor;
});
after(async()=>{await server?.close();});
const render=(project:Document)=>renderToStaticMarkup(createElement(Editor,{project,
 validation:{phase:'valid',message:'Native check complete'},
 onValidate(){},onInit(){},onSource(){},onAdd(){},onRewire(){},onInputs(){},
 onNativeEdit(){return true;},onOutput(){},onRemove(){}}));
const sources=[{id:'frame',type:'source'},{id:'background',type:'source'}];
function noNestedButtons(markup:string){
 let depth=0;
 for(const [token] of markup.matchAll(/<\/?button\b[^>]*>/g)){
   if(token.startsWith('</'))depth--;else{assert.equal(depth,0,'buttons cannot contain other buttons');depth++;}
   assert.ok(depth>=0);
 }
 assert.equal(depth,0);
}

test('graph markup exposes named ports, explicit joins and input selectors without nested buttons',()=>{
 const project={effect_graph:{nodes:[...sources,{id:'reveal',type:'transition',transition_style:'matte',
   inputs:['frame','background','background'],progress:.5}],output:'reveal'}};
 const markup=render(project);noNestedButtons(markup);
 for(const label of ['Connect output of frame','Connect reveal from input','Connect reveal to input',
   'Connect reveal matte input','reveal matte source','Fork copy','Composite layers','Matte transition'])
   assert.ok(markup.includes(label),label);
 assert.match(markup,/aria-label="Select reveal, transition, graph output"/);
 assert.match(markup,/RGBA8/);
 assert.match(markup,/Native parameter and motion editor/);
 assert.match(markup,/Loading native parameter definitions/);
 assert.doesNotMatch(markup,/>Output opacity<input|>Blur radius \(px\)<input/);
});

test('variable inputs expose native order and enforce the minimum in remove controls',()=>{
 const markup=render({effect_graph:{nodes:[...sources,{id:'mix',type:'composite',inputs:['background','frame']}],output:'mix'}});
 assert.match(markup,/aria-label="mix layer 1 source"[\s\S]*?<option value="background" selected=""/);
 assert.match(markup,/aria-label="Remove mix input 1" disabled=""/);
 assert.match(markup,/aria-label="Move mix input 1 earlier" disabled=""/);
 assert.match(markup,/Add layer input/);noNestedButtons(markup);
});

test('specialised chain masks remain visible read-only while invalid graphs retain a safe error surface',()=>{
 const project={effect_graph:{nodes:[...sources,{id:'chain',type:'transition_chain',inputs:['frame','background'],
   stages:[{id:'reveal',transition_style:'matte',matte_input:'background',progress:.4}],
   input_effects:[[{id:'fade',type:'opacity',opacity:.8}],[]]}],output:'chain'}};
 const before=JSON.stringify(project),markup=render(project);
 assert.match(markup,/<button[^>]*disabled=""[^>]*aria-label="Read-only chain stage reveal matte input"/);
 assert.match(markup,/Nested stage matte wires are shown read-only/);
 assert.equal(JSON.stringify(project),before);noNestedButtons(markup);
 const broken=render({effect_graph:{nodes:[{id:'bad',type:'unknown'}],output:'bad'}});
 assert.match(broken,/role="alert"/);assert.match(broken,/The authored graph is preserved/);
});

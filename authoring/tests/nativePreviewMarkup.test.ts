/** React view contracts. Actual native pixels and async decoding are qualified separately. */
import {after,before,test} from 'node:test';
import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';
import {createElement,type ComponentType} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer,type ViteDevServer} from 'vite';
import type {NativePreviewRequest,NativePreviewState,TransitionPreviewMetadata} from '../src/graphPreview.ts';

let server:ViteDevServer,View:ComponentType<any>,Controls:ComponentType<any>,Preview:ComponentType<any>;
let Selected:ComponentType<any>,Parameters:ComponentType<any>;
before(async()=>{
 server=await createServer({configFile:false,root:fileURLToPath(new URL('..',import.meta.url)),logLevel:'error',
   optimizeDeps:{noDiscovery:true,include:[]},server:{middlewareMode:true,hmr:false,ws:false,watch:null}});
 const module=await server.ssrLoadModule('/src/NativeFramePreview.tsx');
 View=module.NativeFramePreviewView;Controls=module.FramePreviewControls;Preview=module.NativeFramePreview;
 Selected=(await server.ssrLoadModule('/src/EffectGraphEditor.tsx')).SelectedGraphPreview;
 Parameters=(await server.ssrLoadModule('/src/GraphParameterEditor.tsx')).GraphParameterEditor;
});
after(async()=>{await server?.close();});
const render=(component:ComponentType<any>,props:Record<string,unknown>)=>renderToStaticMarkup(createElement(component,props));
const noop=()=>{};
function request():NativePreviewRequest {
 const project={width:160,height:90,fps:60},sidecars={};
 return {snapshot:{project,sidecars},project,sidecars,node:'chain::stage:reveal',frame:17,quality:'fast',request_sha256:'a'.repeat(64)};
}
function ready(current:NativePreviewRequest,excluded=false):NativePreviewState {
 const report={scope:{sequential_effects_excluded:excluded}} as TransitionPreviewMetadata;
 return {status:'ready',request:current,message:'',result:{request:current,report,
   images:(['from','result','to'] as const).map(role=>({role,node:role==='result'?current.node:'input:'+role,label:role,
     width:160,height:90,url:'blob:verified-'+role}))}};
}

test('transition comparison labels the shared frame and exposes From/Result/To without changing progress',()=>{
 const current=request(),state=ready(current);
 const markup=render(View,{request:current,state,label:'Chain / reveal',onView:noop});
 assert.match(markup,/aria-label="Selected transition preview"/);
 assert.match(markup,/Chain \/ reveal/);assert.match(markup,/Frame 17 · Fast · 1×/);
 assert.equal([...markup.matchAll(/<img\b/g)].length,3);
 for(const role of ['From','Result','To'])assert.match(markup,new RegExp(`alt="${role} at frame 17"`));
 assert.match(markup,/aria-label="Transition comparison view"/);
 assert.match(markup,/aria-pressed="true">Compare/);
 assert.match(markup,/timeline controls the frame/);assert.match(markup,/Transparent pixels are shown over black/);
 assert.doesNotMatch(markup,/type="range"|Progress override|<input/);
});

test('comparison view switches only displayed images while retaining native image size and frame labels',()=>{
 const current=request(),state=ready(current);
 for(const view of ['from','result','to']){
   const markup=render(View,{request:current,state,view,onView:noop});
   assert.equal([...markup.matchAll(/<img\b/g)].length,1);
   assert.ok(markup.includes(`src="blob:verified-${view}"`));
   assert.match(markup,/width="160" height="90"/);
   assert.ok(markup.includes(`aria-pressed="true">${view[0].toUpperCase()+view.slice(1)}`));
 }
});

test('no previous pixels survive a frame, quality, draft, sidecar, selection or digest change',()=>{
 const current=request(),state=ready(current);
 const changes:Partial<NativePreviewRequest>[]=[{frame:18},{quality:'full'},{snapshot:{}},
   {project:{...current.project}},{sidecars:{words:[]}},{node:'other-transition'},{request_sha256:'b'.repeat(64)}];
 for(const change of changes){
   const markup=render(View,{request:{...current,...change},state,onCancel:noop});
   assert.doesNotMatch(markup,/<img\b|blob:verified/);
   assert.match(markup,/aria-busy="true"/);assert.match(markup,/Rendering frame/);
 }
});

test('a current state envelope cannot display an older result and a blocked graph hides every image',()=>{
 const old=request(),current={...old,frame:18},state=ready(old);state.request=current;
 let markup=render(View,{request:current,state});assert.doesNotMatch(markup,/<img\b|blob:verified/);
 markup=render(View,{request:old,state:ready(old),enabled:false,blockedMessage:'Checking the edited graph.'});
 assert.match(markup,/Checking the edited graph/);assert.match(markup,/aria-busy="false"/);
 assert.doesNotMatch(markup,/<img\b|blob:verified|Cancel preview|Retry preview/);
});

test('cancelled and failed requests expose explicit retry actions without retaining previous images',()=>{
 const current=request();
 const cancelled=render(View,{request:current,state:ready(current),cancelled:true,onRetry:noop,onCancel:noop});
 assert.match(cancelled,/Preview cancelled/);assert.match(cancelled,/Retry preview/);
 assert.doesNotMatch(cancelled,/<img\b|blob:verified|Cancel preview/);
 const failed=render(View,{request:current,state:{status:'error',request:current,result:null,message:'The native preview could not complete.'},onRetry:noop});
 assert.match(failed,/role="alert"/);assert.match(failed,/native preview could not complete/);assert.match(failed,/Retry preview/);
 const loading=render(View,{request:current,state:{status:'loading',request:current,result:null,message:''},onCancel:noop});
 assert.match(loading,/aria-busy="true"/);assert.match(loading,/Cancel preview/);assert.doesNotMatch(loading,/<img\b/);
});

test('the graph comparison discloses excluded sequential stages and full uses authored quality wording',()=>{
 const current={...request(),quality:'full' as const};
 const markup=render(View,{request:current,state:ready(current,true)});
 assert.match(markup,/Frame 17 · Authored quality/);
 assert.match(markup,/project’s authored quality settings/);
 assert.match(markup,/Sequential visualiser stages are excluded from this graph comparison/);
 assert.doesNotMatch(markup,/Production approved|Production AA/);
});

test('the normal composition preview uses one decoded image without transition-only controls or claims',()=>{
 const current={...request(),node:null,request_sha256:undefined},state=ready(current);
 state.result!.report=null;state.result!.images=[{role:'composition',node:null,label:'Composition',width:160,height:90,url:'blob:composition'}];
 const markup=render(View,{request:current,state,view:'result'});
 assert.match(markup,/aria-label="Native frame preview"/);assert.match(markup,/Composited frame/);
 assert.equal([...markup.matchAll(/<img\b/g)].length,1);assert.match(markup,/alt="Composition at frame 17"/);
 assert.doesNotMatch(markup,/Transition comparison view|Sequential visualiser stages|Transparent pixels/);
});

test('exact frame controls use the last valid frame and make quality ownership explicit',()=>{
 const props={frame:0,lastFrame:119,fps:60,quality:'fast',showQuality:true,onFrame:noop,onQuality:noop};
 let markup=render(Controls,props);
 assert.match(markup,/aria-label="Seek draft preview"[^>]*max="1.9833333333333334"/);
 assert.match(markup,/aria-label="Exact preview frame"[^>]*max="119"[^>]*step="1"[^>]*value="0"/);
 assert.match(markup,/aria-label="Previous preview frame" disabled=""/);
 assert.match(markup,/aria-label="Frame preview quality"/);assert.match(markup,/Fast \(1×\)/);assert.match(markup,/Authored quality/);
 markup=render(Controls,{...props,frame:119,showQuality:false});
 assert.match(markup,/aria-label="Next preview frame" disabled=""/);
 assert.match(markup,/Quality follows the adaptive preview controls/);
 assert.doesNotMatch(markup,/aria-label="Frame preview quality"|<select/);
});

test('a selected native chain stage uses its catalogue label while the chain alias guides stage selection',()=>{
 const current=request(),node={id:current.node,type:'transition',label:'Chain reveal <stage>',authored_id:'chain'};
 const catalog={request_sha256:current.request_sha256,node,targets:[node,{id:'chain',type:'copy',label:'Chain',authored_id:'chain'}]};
 const props={catalog,project:current.project,revision:current.snapshot,sidecars:current.sidecars,owner:'chain',
   preview:{frame:17,quality:'fast',enabled:true}};
 let markup=render(Selected,props);
 assert.match(markup,/Selected transition preview/);assert.match(markup,/Chain reveal &lt;stage&gt;/);assert.match(markup,/Rendering frame 17/);
 assert.doesNotMatch(markup,/<img\b/);
 markup=render(Selected,{...props,catalog:{...catalog,node:catalog.targets[1]}});
 assert.match(markup,/Choose a transition stage in Parameter target/);assert.doesNotMatch(markup,/Selected transition preview/);
 assert.equal(render(Selected,{...props,owner:'unrelated',catalog:{...catalog,node:catalog.targets[1]}}),'');
 for(const type of ['opacity','unknown'])assert.equal(render(Selected,{...props,catalog:{...catalog,node:{...node,type},targets:[]}}),'');
});

test('preview slot cannot use an unaccepted parameter catalogue during initial loading',()=>{
 let called=0;const current=request();
 const markup=render(Parameters,{project:current.project,revision:current.snapshot,sidecars:current.sidecars,node:'chain',
   onApply(){return true;},renderPreview(){called++;return createElement('img',{src:'blob:should-not-render'});}});
 assert.equal(called,0);assert.match(markup,/Loading native parameter definitions/);assert.doesNotMatch(markup,/blob:should-not-render/);
 const loading=render(Preview,{project:current.project,revision:current.snapshot,sidecars:current.sidecars,frame:17,quality:'fast'});
 assert.match(loading,/Rendering frame 17/);assert.doesNotMatch(loading,/<img\b/);
});

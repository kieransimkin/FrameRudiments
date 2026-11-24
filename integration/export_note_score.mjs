// Export an explicitly authored two-bar score. No MIDI-to-notation inference.
// Optional tools: VexFlow 5.11.0 and Playwright 1.62.1; no renderer dependency.
import { createRequire } from 'node:module';
import { mkdir, writeFile, readFile } from 'node:fs/promises';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.FRAMERUDIMENTS_PLAYWRIGHT || 'playwright');
const bundle=process.env.FRAMERUDIMENTS_VEXFLOW || require.resolve('vexflow');
const output=resolve(process.argv[2] || resolve(dirname(fileURLToPath(import.meta.url)),'fixtures/note-suite-score'));
const authored=[['c/5','d/5','e/5','g/5'],['f/5','e/5','d/5','c/5']];
const keys=[[72,74,76,79],[77,76,74,72]];
await mkdir(output,{recursive:true});
let browser;
const pages=[];
try {
 browser=await chromium.launch({headless:true,...(process.env.FRAMERUDIMENTS_BROWSER_CHANNEL ? {channel:process.env.FRAMERUDIMENTS_BROWSER_CHANNEL}:{})});
 const context=await browser.newContext({viewport:{width:960,height:240},deviceScaleFactor:1});
 const page=await context.newPage();
 await page.route('**/*',route=>route.abort());
 for(let index=0;index<authored.length;index++){
  await page.setContent('<!doctype html><meta charset="utf-8"><style>html,body{margin:0;background:white}#score{width:960px;height:240px}</style><div id="score"></div>');
  await page.addScriptTag({path:bundle});
  const glyphs=await page.evaluate(async ({spellings})=>{
   const VF=globalThis.VexFlow;
   await document.fonts.ready;
   const renderer=new VF.Renderer(document.getElementById('score'),VF.Renderer.Backends.SVG);
   renderer.resize(960,240);
   const ctx=renderer.getContext();
   const stave=new VF.Stave(30,65,900).addClef('treble').addTimeSignature('4/4');
   stave.setContext(ctx).draw();
   const notes=spellings.map(key=>new VF.StaveNote({clef:'treble',keys:[key],duration:'q'}));
   const voice=new VF.Voice({num_beats:4,beat_value:4}).addTickables(notes);
   new VF.Formatter().joinVoices([voice]).format([voice],740);
   voice.draw(ctx,stave);
   await document.fonts.ready;
   return notes.map(note=>{const b=note.getBoundingBox();return [b.getX()-4,b.getY()-4,b.getX()+b.getW()+4,b.getY()+b.getH()+4]});
  },{spellings:authored[index]});
  const name=`page-${index+1}.png`;
  await page.locator('#score').screenshot({path:resolve(output,name)});
  const bytes=await readFile(resolve(output,name));
  pages.push({image:name,sha256:createHash('sha256').update(bytes).digest('hex'),start_seconds:index*2,end_seconds:(index+1)*2,
   glyphs:glyphs.map((bounds,i)=>({bounds,authored:{track:3,key:keys[index][i],start_seconds:index*2+i*.5,spelling:authored[index][i],duration:'q'}}))});
 }
 await context.close();
} finally {await browser?.close()}
await writeFile(resolve(output,'layout.json'),JSON.stringify({schema:'framerudiments.authored-score-fixture.v1',engraver:'VexFlow 5.11.0',engraver_source:'https://github.com/vexflow/vexflow',licence:'MIT; bundled Bravura font SIL Open Font License 1.1',font:'Bundled Bravura',authorship:'Original C-major two-bar demonstration; spellings and quarter durations explicitly authored; silent soundtrack',pages},null,2)+'\n');
console.log(JSON.stringify({status:'exported',pages:pages.length,output,all_owned_browser_contexts_closed:true}));

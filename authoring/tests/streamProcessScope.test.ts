import assert from 'node:assert/strict'
import {spawn} from 'node:child_process'
import {mkdtemp, mkdir, readFile, readdir, rm, writeFile} from 'node:fs/promises'
import path from 'node:path'
import {fileURLToPath} from 'node:url'
import test from 'node:test'

// Executes the actual packaged worker with explicit npm API doubles. It proves
// launch/cleanup contracts, not Chromium or Butterchurn pixel equivalence.
const worker = fileURLToPath(new URL('../../src/framerudiments/visualiser_assets/butterchurn-worker.mjs', import.meta.url))
const parent = fileURLToPath(new URL('../', import.meta.url))
const puppeteerMock = `
const fs = require('node:fs');
const log = item => fs.appendFileSync(process.env.SCOPE_TEST_LOG, JSON.stringify(item)+'\\n');
let closed=false;
const page={setRequestInterception:async()=>{},on:()=>{},setContent:async()=>{},addScriptTag:async()=>{},
 evaluate:async()=>({renderer:'explicit-test-double'}),isClosed:()=>closed};
function browser(){ return {connected:true, newPage:async()=>page,version:async()=>'double-browser',
 close:async function(){log({event:'browser.close'});closed=true;this.connected=false;
 if(process.env.SCOPE_TEST_FAILURE==='close')throw Error('injected close failure');}}; }
exports.defaultArgs = options => { log({event:'defaultArgs', options}); return ['--user-data-dir='+options.userDataDir,...options.args]; };
exports.connect = async options => { log({event:'connect', options});
 if(process.env.SCOPE_TEST_FAILURE==='connect')throw Error('injected connect failure');return browser(); };
exports.launch = async options => { log({event:'launch', options}); return browser(); };
`
const launcherMock = `
const fs = require('node:fs');
const log = item => fs.appendFileSync(process.env.SCOPE_TEST_LOG, JSON.stringify(item)+'\\n');
exports.CDP_WEBSOCKET_ENDPOINT_REGEX=/DevTools listening on (.*)/;
exports.launch=options=>{const {env,...safeOptions}=options;
 log({event:'scoped.launch', options:safeOptions, inherited:env?.SCOPE_TEST_LOG===process.env.SCOPE_TEST_LOG});return {
 waitForLineOutput:async(_regex,timeout)=>{log({event:'endpoint',timeout});
 if(process.env.SCOPE_TEST_FAILURE==='endpoint')throw Error('injected endpoint failure');return 'ws://owned-test-double';},
 close:async()=>{log({event:'process.close'});}};};
`

async function exercise(scoped: boolean, failure = '') {
  const folder = await mkdtemp(path.join(parent, '.test-tmp-browser-scope-'))
  try {
    const runtime = path.join(folder, 'runtime')
    const work = path.join(folder, 'work')
    const logfile = path.join(folder, 'calls.jsonl')
    await mkdir(work)
    for (const [name, version, source] of [
      ['puppeteer-core', '25.14.0', puppeteerMock],
      ['@puppeteer/browsers', '3.0.0-test-double', launcherMock],
      ['butterchurn', '2.6.7', '/* explicit test double */'],
    ]) {
      const directory = path.join(runtime, 'node_modules', name)
      await mkdir(directory, {recursive:true})
      await writeFile(path.join(directory, 'package.json'), JSON.stringify({name, version, main:'index.cjs'}))
      await writeFile(path.join(directory, 'index.cjs'), source)
    }
    await writeFile(path.join(runtime, 'package.json'), '{}')
    await writeFile(logfile, '')
    const env = {...process.env, SCOPE_TEST_LOG:logfile, SCOPE_TEST_FAILURE:failure}
    delete env.FRAMERUDIMENTS_TEXTURES_DIR
    if (scoped) env.FRAMERUDIMENTS_PREVIEW_PROCESS_SCOPE = '1'
    else delete env.FRAMERUDIMENTS_PREVIEW_PROCESS_SCOPE
    const child = spawn(process.execPath, [worker], {env, stdio:['pipe','pipe','pipe']})
    const output: string[] = [], errors: string[] = []
    child.stdout.on('data', bytes=>output.push(bytes.toString()))
    child.stderr.on('data', bytes=>errors.push(bytes.toString()))
    const done = new Promise<number|null>((resolve,reject)=>{
      child.once('error',reject)
      child.once('close',resolve)
    })
    child.stdin.end(JSON.stringify({command:'init', runtime, browser:'explicit-test-double',
      width:16,height:16,fps:30,preset:{},mix:1,seed:1,work,profile_root:work})+'\n'+
      JSON.stringify({command:'close'})+'\n')
    const timeout = setTimeout(()=>child.kill('SIGKILL'),10000)
    let code: number|null
    try { code=await done } finally { clearTimeout(timeout) }
    const calls = (await readFile(logfile,'utf8')).trim().split('\n').filter(Boolean).map(line=>JSON.parse(line))
    const messages = output.join('').trim().split('\n').filter(Boolean).map(line=>JSON.parse(line))
    assert.deepEqual(await readdir(work), [], 'every owned profile must be removed')
    return {code,calls,messages,stderr:errors.join('')}
  } finally {
    await rm(folder,{recursive:true,force:true})
  }
}

test('S6 browser stays in owned scope and closes both browser and process', async()=>{
  const {code,calls,messages} = await exercise(true)
  assert.equal(code,0)
  const launch=calls.find(call=>call.event==='scoped.launch')
  assert.equal(launch.options.detached,false)
  assert.equal(launch.inherited,true)
  assert.equal(launch.options.handleSIGTERM,false)
  assert.ok(launch.options.args.includes('--remote-debugging-port=0'))
  assert.equal(calls.some(call=>call.event==='launch'),false)
  assert.deepEqual(calls.filter(call=>call.event.endsWith('.close')).map(call=>call.event),
    ['browser.close','process.close'])
  assert.equal(messages.at(-1).cleanup.owned_process_closed,true)
  assert.ok(Object.values(messages.at(-1).cleanup).every(Boolean))
})

for (const failure of ['endpoint','connect']) {
  test(`S6 ${failure} failure closes owned process before removing its profile`, async()=>{
    const {code,calls,messages}=await exercise(true,failure)
    assert.equal(code,1)
    assert.ok(messages.some(message=>message.error?.includes(`injected ${failure} failure`)))
    assert.equal(calls.filter(call=>call.event==='process.close').length,1)
    assert.equal(messages.at(-1).cleanup.owned_process_closed,true)
  })
}

test('S6 failed browser close still closes owned process and reports cleanup failure',async()=>{
  const {code,calls,messages}=await exercise(true,'close')
  assert.equal(code,1)
  assert.equal(calls.filter(call=>call.event==='process.close').length,1)
  assert.match(messages.at(-1).error,/Owned visualiser cleanup failed/)
})

test('ordinary visualiser rendering retains its existing Puppeteer launch route',async()=>{
  const {code,calls,messages}=await exercise(false)
  assert.equal(code,0)
  assert.equal(calls.some(call=>call.event==='scoped.launch'),false)
  assert.equal(calls.some(call=>call.event==='connect'),false)
  const launch=calls.find(call=>call.event==='launch')
  assert.equal(launch.options.headless,true)
  assert.equal(launch.options.protocolTimeout,60000)
  assert.equal(messages.at(-1).cleanup.owned_process_closed,undefined)
})

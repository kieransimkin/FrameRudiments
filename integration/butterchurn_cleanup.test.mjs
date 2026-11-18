/** Resource lifecycle coverage; native pixel/reset evidence belongs to the GPU suite. */
import assert from 'node:assert/strict';
import {mkdtemp, mkdir, readFile, rm, stat, writeFile} from 'node:fs/promises';
import path from 'node:path';
import test from 'node:test';
import {setImmediate} from 'node:timers/promises';
import {closeButterchurnResources} from '../src/framerudiments/visualiser_assets/butterchurn-cleanup.mjs';

async function ownedProfile(t) {
  const parent = path.resolve('.test-tmp');
  await mkdir(parent, {recursive: true});
  const root = await mkdtemp(path.join(parent, 'butterchurn-cleanup-'));
  t.after(() => rm(root, {recursive: true, force: true}));
  const profile = path.join(root, 'owned-profile');
  const sibling = path.join(root, 'unrelated');
  await mkdir(profile);
  await writeFile(path.join(profile, 'state'), 'owned browser state');
  await writeFile(sibling, 'must remain');
  return {profile, sibling};
}

test('connected browser closes the page before detaching CDP and retains all cleanup evidence', async t => {
  const {profile, sibling} = await ownedProfile(t);
  const events = [];
  let pageClosed = false;
  const browser = {connected: true, async close() {
    events.push('browser-disconnect');
    this.connected = false; // connect().close() does not wait for a target event.
  }};
  const page = {isClosed: () => pageClosed, async close() {
    events.push('page-close-request');
    await setImmediate(); // The target event arrives asynchronously over CDP.
    assert.equal(browser.connected, true, 'page acknowledgement needs the live CDP connection');
    pageClosed = true;
    events.push('page-closed');
  }};
  const scopedProcess = {async close() {events.push('process-closed');}};
  const receipt = await closeButterchurnResources({page, browser, scopedProcess, profile});
  assert.deepEqual(events, ['page-close-request', 'page-closed', 'browser-disconnect', 'process-closed']);
  assert.deepEqual(receipt, {browser_closed: true, page_closed: true,
    owned_profile_removed: true, owned_process_closed: true});
  await assert.rejects(stat(profile), {code: 'ENOENT'});
  assert.equal(await readFile(sibling, 'utf8'), 'must remain');
});

for (const failedStep of ['page', 'browser', 'process']) {
  test(`failed ${failedStep} closure still closes other owned resources and does not grant a receipt`, async t => {
    const {profile, sibling} = await ownedProfile(t);
    const events = [];
    const failure = new Error(`${failedStep} close failed`);
    const step = async name => {events.push(name); if (name === failedStep) throw failure;};
    const page = {isClosed: () => false, close: () => step('page')};
    const browser = {connected: true, close: () => step('browser')};
    const scopedProcess = {close: () => step('process')};
    await assert.rejects(closeButterchurnResources({page, browser, scopedProcess, profile}), error => error === failure);
    assert.deepEqual(events, ['page', 'browser', 'process']);
    await assert.rejects(stat(profile), {code: 'ENOENT'});
    assert.equal(await readFile(sibling, 'utf8'), 'must remain');
  });
}

test('already-closed pages are not reopened and unverified states remain false', async t => {
  const {profile} = await ownedProfile(t);
  const page = {isClosed: () => true, close: () => assert.fail('page is already closed')};
  const browser = {connected: true, async close() {}};
  assert.deepEqual(await closeButterchurnResources({page, browser, profile}),
    {browser_closed: false, page_closed: true, owned_profile_removed: true});
});

test('partial launch failure still closes the owned process and profile', async t => {
  const {profile} = await ownedProfile(t);
  let processClosed = false;
  const scopedProcess = {async close() {processClosed = true;}};
  assert.deepEqual(await closeButterchurnResources({scopedProcess, profile}),
    {browser_closed: true, page_closed: true, owned_profile_removed: true, owned_process_closed: true});
  assert.equal(processClosed, true);
  await assert.rejects(stat(profile), {code: 'ENOENT'});
});

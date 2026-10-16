import {createRequire} from 'node:module';
import {readFile, readdir, mkdtemp, rm} from 'node:fs/promises';
import path from 'node:path';
import {createHash} from 'node:crypto';
import readline from 'node:readline';
import {fileURLToPath} from 'node:url';

const lines = readline.createInterface({input: process.stdin, crlfDelay: Infinity});
let browser, profile, page;
const send = object => process.stdout.write(JSON.stringify(object) + '\n');
try {
  for await (const line of lines) {
    const message = JSON.parse(line);
    if (message.command === 'init') {
      const require = createRequire(path.join(message.runtime, 'package.json'));
      if (require('butterchurn/package.json').version !== '2.6.7' || require('puppeteer-core/package.json').version !== '25.14.0')
        throw new Error('Butterchurn runtime versions differ from the package lock');
      const puppeteer = require('puppeteer-core');
      profile = await mkdtemp(path.join(message.profile_root || message.work, 'owned-headless-'));
      browser = await puppeteer.launch({executablePath: message.browser, headless: true, userDataDir: profile,
        protocolTimeout: 60000, timeout: 60000,
        args: ['--enable-webgl', '--enable-unsafe-swiftshader', '--disable-background-timer-throttling',
          '--disable-backgrounding-occluded-windows', '--disable-renderer-backgrounding']});
      page = await browser.newPage();
      await page.setRequestInterception(true);
      page.on('request', request => request.url().startsWith('about:') || request.url().startsWith('data:')
        ? request.continue() : request.abort());
      page.on('console', event => { if (event.type() === 'error') process.stderr.write(event.text() + '\n'); });
      await page.setContent('<!doctype html><html><body></body></html>');
      await page.addScriptTag({content: await readFile(require.resolve('butterchurn'), 'utf8')});
      const host = await readFile(path.join(path.dirname(fileURLToPath(import.meta.url)), 'butterchurn-page.js'), 'utf8');
      await page.addScriptTag({content: host});
      const textureFiles = [], extraImages = {};
      if (process.env.FRAMERUDIMENTS_TEXTURES_DIR) {
        const folder = process.env.FRAMERUDIMENTS_TEXTURES_DIR;
        for (const name of (await readdir(folder)).sort()) {
          const extension = path.extname(name).toLowerCase();
          const mime = {'.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg','.bmp':'image/bmp'}[extension];
          if (!mime) continue;
          const bytes = await readFile(path.join(folder, name));
          extraImages[path.basename(name, extension)] = `data:${mime};base64,${bytes.toString('base64')}`;
          textureFiles.push({name, sha256:createHash('sha256').update(bytes).digest('hex')});
        }
      }
      message.extraImages = extraImages;
      send({ok: true, evidence: {...await page.evaluate(config => initialise(config), message),
        texture_files: textureFiles,
        node: process.version, browser: await browser.version(),
        butterchurn_bundle_sha256: createHash('sha256').update(await readFile(require.resolve('butterchurn'))).digest('hex')}});
    } else if (message.command === 'frame') {
      if (!page) throw new Error('Engine not initialised');
      const result = await Promise.race([page.evaluate(data => window.renderFrame(data), message),
        new Promise((_, reject) => { const timeout = setTimeout(() => reject(new Error('Frame processing timeout')), 60000); timeout.unref(); })]);
      send({ok: true, ...result});
    } else if (message.command === 'close') {
      break;
    } else throw new Error('Unknown rendering command');
  }
} catch (error) {
  send({ok: false, error: String(error.stack || error)});
  process.exitCode = 1;
} finally {
  lines.close();
  try {
    if (browser) await browser.close();
    // Only this freshly allocated profile is removed. Windows may briefly retain
    // file locks after browser.close() resolves; bounded fs retries handle that.
    if (profile) await rm(profile, {recursive: true, force: true, maxRetries: 15, retryDelay: 200});
    send({ok: true, cleanup: {browser_closed: !browser || !browser.connected,
      page_closed: !page || page.isClosed(), owned_profile_removed: true}});
  } catch (error) {
    send({ok: false, error: `Owned visualiser cleanup failed: ${error}`});
    process.exitCode = 1;
  }
}

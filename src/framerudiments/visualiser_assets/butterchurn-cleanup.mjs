import {rm} from 'node:fs/promises';

/** Close the owned page while its CDP connection can still acknowledge it. */
export async function closeButterchurnResources({page, browser, scopedProcess, profile}) {
  let cleanupError;
  // puppeteer.connect().close() disconnects after Browser.close without waiting
  // for Target.targetDestroyed. Closing the page first retains its acknowledgement
  // and the existing page_closed evidence instead of racing that disconnect.
  try { if (page && !page.isClosed()) await page.close(); } catch (error) { cleanupError = error; }
  try { if (browser) await browser.close(); } catch (error) { cleanupError ??= error; }
  // Also owns launch/connect failures for which no Browser object exists.
  try { if (scopedProcess) await scopedProcess.close(); } catch (error) { cleanupError ??= error; }
  // Only this freshly allocated profile is removed. Windows may briefly retain
  // file locks after browser.close() resolves; bounded fs retries handle that.
  if (profile) await rm(profile, {recursive: true, force: true, maxRetries: 15, retryDelay: 200});
  if (cleanupError) throw cleanupError;
  return {browser_closed: !browser || !browser.connected,
    page_closed: !page || page.isClosed(), owned_profile_removed: true,
    ...(scopedProcess ? {owned_process_closed: true} : {})};
}

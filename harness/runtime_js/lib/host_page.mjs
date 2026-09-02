/**
 * Node-side driver around the page host: serve the workspace, launch the
 * browser, open the host page, boot the scene, collect console / page errors.
 * All three scene CLIs (render / probe / check_shaders) go through here.
 */

import fs from 'node:fs';
import path from 'node:path';
import { importMapHtml, launchBrowser, runtimeMount, serveWorkspace } from './host_env.mjs';

const SHADER_NOISE_RE = /shader|program not valid|glsl|WebGL|compile|THREE\.WebGLProgram/i;
const MAX_CONSOLE = 60;
const CONTROL_RE = new RegExp('[\\u0000-\\u001f]+', 'g');

function hostHtml() {
  return [
    '<!doctype html><html><head><meta charset="utf-8"><title>3dcv scene host</title>',
    importMapHtml(),
    '<style>html,body{margin:0;background:#000;overflow:hidden}</style></head><body>',
    `<script type="module" src="${runtimeMount()}lib/scene_host.mjs"></script></body></html>`,
  ].join('\n');
}

function clean(s) {
  return String(s).replace(CONTROL_RE, ' ').trim();
}

/**
 * createScene() boot timeout for a driver: an explicit --create-timeout-ms
 * wins, else 60% of the driver budget capped at 20 s — always below the
 * watchdog so a hung boot is an agent-attributed scene failure (exit 1 with
 * boot.stage = 'createScene'), never a watchdog kill (exit 3).
 */
export function createTimeoutMs(flagValue, driverTimeoutMs) {
  const flag = parseInt(flagValue || '', 10);
  if (Number.isFinite(flag) && flag > 0) return flag;
  return Math.min(20000, Math.max(1000, Math.round(0.6 * driverTimeoutMs)));
}

/**
 * Error line for a puppeteer 'requestfailed' event, or null when it must be
 * ignored.  Chrome intermittently fires requestfailed with net::ERR_ABORTED
 * for a same-origin fetch that already received a 200 and was fully consumed
 * (three's FileLoader stream wrapper) — a phantom failure that used to fail
 * the render_console / scene_probe gates ~10% of rounds.  Real load failures
 * are still reported by the 404 'response' handler and LoadingManager.onError.
 */
export function requestFailureLine(url, base, hasResponse, errorText) {
  if (!url.startsWith(base)) return null;
  if (hasResponse || errorText === 'net::ERR_ABORTED') return null;
  return `request failed: ${url.slice(base.length)} (${errorText || '?'})`;
}

/**
 * Open the host for workspace `wsRoot`.
 * @returns {Promise<{page, browser, base, gpu, renderer, errors, boot, close}>}
 */
export async function openHost(wsRoot, { width = 1024, height = 576, gpu = 'auto', logDepth = false, sceneRel = 'src/scene.js', createSceneTimeoutMs = 0, settle = true, cameraRepair = false, autoExposure = false, post = true, postOptions = null } = {}) {
  wsRoot = path.resolve(wsRoot);
  if (!fs.existsSync(path.join(wsRoot, sceneRel))) {
    throw new Error(`missing ${sceneRel} in workspace ${wsRoot}`);
  }
  const srv = await serveWorkspace(wsRoot, { hostHtml: hostHtml() });
  const errors = { console: [], page: [], shader_console: [], warnings: [] };
  let launched = null;
  let page = null;
  try {
    launched = await launchBrowser({ gpu });
    page = await launched.browser.newPage();
    await page.setViewport({ width, height, deviceScaleFactor: 1 });
    page.on('console', (m) => {
      const type = m.type();
      if (type !== 'error' && type !== 'warning') return;
      const text = clean(m.text());
      if (!text) return;
      const isShader = SHADER_NOISE_RE.test(text);
      // Chrome's bare resource-load line duplicates the 404 the response handler
      // already reported with the URL and a hint; keep it out of the error bucket.
      const isResourceNoise = /Failed to load resource/.test(text);
      const bucket = type === 'warning' || isResourceNoise ? errors.warnings : (isShader ? errors.shader_console : errors.console);
      if (bucket.length < MAX_CONSOLE && !bucket.includes(text)) bucket.push(text.slice(0, 600));
    });
    page.on('pageerror', (e) => {
      const text = clean((e && (e.stack || e.message)) || e);
      if (errors.page.length < MAX_CONSOLE) errors.page.push(text.slice(0, 800));
    });
    page.on('requestfailed', (req) => {
      const line = requestFailureLine(req.url(), srv.base, !!req.response(), req.failure()?.errorText || '?');
      if (line) errors.console.push(line);
    });
    page.on('response', (res) => {
      if (res.status() === 404 && res.url().startsWith(srv.base)) {
        const rel = res.url().slice(srv.base.length);
        // a missing GLB/texture is a WARNING (the scene still boots and renders
        // without it — the judge sees the absence); a missing MODULE is fatal.
        const bucket = rel.startsWith('/assets/') || rel.startsWith('/public/') ? errors.warnings : errors.console;
        bucket.push(`404 not found: ${rel}` + (bucket === errors.console ? " (imports must be 'three', 'three/addons/*' or relative; assets under public/assets → '/assets/...')" : ' (build/copy the asset into public/assets/)'));
      }
    });
    await page.goto(`${srv.base}/__host.html`, { waitUntil: 'load', timeout: 60000 });
    await page.waitForFunction('window.__c3v_ready === true', { timeout: 60000 });
    const boot = await page.evaluate(
      (o) => window.__c3v.boot(o),
      { sceneUrl: `/${sceneRel}`, width, height, logDepth, createSceneTimeoutMs, settle, cameraRepair, autoExposure, post, postOptions },
    );
    const close = async () => {
      // page first, then release: a SHARED browser (connect-first reuse) must get
      // its pages closed by us and be disconnected, never closed.  Each step is
      // time-bounded: the result is already in hand when close() runs.
      await bounded(page.close(), 3000);
      await bounded(releaseBrowser(launched), 3000);
      await bounded(srv.close(), 2000);
    };
    return { page, browser: launched.browser, base: srv.base, gpu: launched.gpu, renderer: launched.renderer, errors, boot, close };
  } catch (e) {
    if (page) await page.close().catch(() => {});
    if (launched && launched.browser) await releaseBrowser(launched).catch(() => {});
    await srv.close();
    throw e;
  }
}

/** Await `work`, but at most `ms` — cleanup must never dominate a driver's wall time
 * (a wedged shared browser once cost 100 s per render in page.close alone; the daemon's
 * page reaper picks up anything a bounded close leaves behind). */
export function bounded(work, ms) {
  let timer = null;
  return Promise.race([
    Promise.resolve(work).catch(() => {}),
    new Promise((res) => { timer = setTimeout(res, ms); if (timer.unref) timer.unref(); }),
  ]).finally(() => { if (timer) clearTimeout(timer); });
}

/** Release a launchBrowser handle: release() when present (shared-aware), else close(). */
export async function releaseBrowser(launched) {
  if (!launched || !launched.browser) return;
  if (typeof launched.release === 'function') await launched.release();
  else await launched.browser.close().catch(() => {});
}

/** Flatten the collected errors into the summary shape used by all drivers. */
export function errorSummary(errors, boot) {
  const consoleErrors = [...errors.console, ...errors.page.map((p) => `pageerror: ${p}`)];
  if (boot && boot.error) consoleErrors.unshift(`boot[${boot.stage}]: ${boot.error}`);
  const warnings = [...errors.warnings];
  for (const le of (boot && boot.load_errors) || []) warnings.push(`load: ${le}`);
  return {
    console_errors: consoleErrors,
    console_warnings: warnings,
    shader_console: errors.shader_console,
  };
}

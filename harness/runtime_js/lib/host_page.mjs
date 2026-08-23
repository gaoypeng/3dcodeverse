/**
 * Node-side driver around the page host: serve the workspace, launch the
 * browser, open the host page, boot the scene, collect console / page errors.
 * All three scene CLIs (render / probe / check_shaders) go through here.
 */

import fs from 'node:fs';
import path from 'node:path';
import { importMapHtml, launchBrowser, runtimeMount, serveWorkspace } from './_compat.mjs';

const SHADER_NOISE_RE = /shader|program not valid|glsl|WebGL|compile|THREE\.WebGLProgram/i;
const MAX_CONSOLE = 60;
const CONTROL_RE = new RegExp('[\\u0000-\\u001f]+', 'g');

function hostHtml() {
  return [
    '<!doctype html><html><head><meta charset="utf-8"><title>c3v scene host</title>',
    importMapHtml(),
    '<style>html,body{margin:0;background:#000;overflow:hidden}</style></head><body>',
    `<script type="module" src="${runtimeMount()}lib/scene_host.mjs"></script></body></html>`,
  ].join('\n');
}

function clean(s) {
  return String(s).replace(CONTROL_RE, ' ').trim();
}

/**
 * Open the host for workspace `wsRoot`.
 * @returns {Promise<{page, browser, base, gpu, renderer, errors, boot, close}>}
 */
export async function openHost(wsRoot, { width = 1024, height = 576, gpu = 'auto', logDepth = false, sceneRel = 'src/scene.js' } = {}) {
  wsRoot = path.resolve(wsRoot);
  if (!fs.existsSync(path.join(wsRoot, sceneRel))) {
    throw new Error(`missing ${sceneRel} in workspace ${wsRoot}`);
  }
  const srv = await serveWorkspace(wsRoot, { hostHtml: hostHtml() });
  const errors = { console: [], page: [], shader_console: [], warnings: [] };
  let launched = null;
  try {
    launched = await launchBrowser({ gpu });
    const page = await launched.browser.newPage();
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
      const url = req.url();
      if (url.startsWith(srv.base)) errors.console.push(`request failed: ${url.slice(srv.base.length)} (${req.failure()?.errorText || '?'})`);
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
      { sceneUrl: `/${sceneRel}`, width, height, logDepth },
    );
    const close = async () => {
      try { await launched.browser.close(); } catch (e) { /* ignore */ }
      await srv.close();
    };
    return { page, browser: launched.browser, base: srv.base, gpu: launched.gpu, renderer: launched.renderer, errors, boot, close };
  } catch (e) {
    if (launched && launched.browser) await launched.browser.close().catch(() => {});
    await srv.close();
    throw e;
  }
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

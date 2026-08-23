#!/usr/bin/env node
// Render a GLB from fixed views with headless Chrome (GPU when available).
//
//   node runtime_js/render_glb.mjs --glb <path> --out <dir> --views '<json [{name,azimuth,elevation}]>'
//        [--mode shaded|wire|normals|silhouette|clay] [--width 768] [--height 768]
//        [--isolate Name1,Name2] [--explode 0.3] [--background studio|white|transparent]
//        [--anim-time t] [--gpu auto|on|off] [--shadow 1|0] [--fill 0.85] [--timeout-s 240]
//
// Writes <out>/view_<name>.png for every view + <out>/views.json, and prints one JSON
// line last on stdout: {ok, renderer, gpu, views:[{name,path,camera_position,look_at,fov,...}],
// warnings, timing_ms}.  Exit 1 (with {ok:false,error}) on any failure.
//
// Thin driver: arg parsing / JSON-last-line protocol come from lib/cli.mjs, the
// browser + static server from lib/host_env.mjs, the browser release dance from
// lib/host_page.mjs, and everything visual from lib/browser/render_rig.js.

import fs from 'node:fs';
import path from 'node:path';

import { ensureDir, finish, parseCli, writeJson } from './lib/cli.mjs';
import { launchBrowser, importMapHtml, runtimeMount, serveDirs } from './lib/host_env.mjs';
import { releaseBrowser } from './lib/host_page.mjs';

const MODES = ['shaded', 'wire', 'normals', 'silhouette', 'clay'];

function config() {
  const values = parseCli({
    glb: {}, out: {}, views: {},
    mode: { default: 'shaded' }, width: { default: '768' }, height: { default: '768' },
    isolate: { default: '' }, explode: { default: '0' }, background: { default: 'studio' },
    'anim-time': {}, gpu: { default: 'auto' }, shadow: { default: '1' }, fill: { default: '0.85' },
    'timeout-s': { default: '240' },
  });
  if (!values.glb || !values.out || !values.views) throw new Error('--glb, --out and --views are required');
  if (!MODES.includes(values.mode)) throw new Error(`--mode must be one of ${MODES.join('|')}`);
  const views = JSON.parse(values.views);
  if (!Array.isArray(views) || views.length === 0) throw new Error('--views must be a non-empty JSON list');
  for (const v of views) {
    if (typeof v.name !== 'string' || !/^[A-Za-z0-9_\-]+$/.test(v.name)) throw new Error(`bad view name: ${JSON.stringify(v.name)}`);
    if (typeof v.azimuth !== 'number' || typeof v.elevation !== 'number') throw new Error(`view ${v.name}: azimuth/elevation must be numbers`);
  }
  return {
    glb: path.resolve(values.glb),
    out: path.resolve(values.out),
    views,
    mode: values.mode,
    width: Number(values.width),
    height: Number(values.height),
    isolate: values.isolate ? values.isolate.split(',').map((s) => s.trim()).filter(Boolean) : [],
    explode: Number(values.explode) || 0,
    background: values.background,
    animTime: values['anim-time'] != null ? Number(values['anim-time']) : null,
    gpu: values.gpu,
    shadow: values.shadow !== '0',
    fill: Number(values.fill) || 0.85,
    timeoutMs: Number(values['timeout-s']) * 1000,
  };
}

function pageHtml(cfg) {
  const config_ = { ...cfg, glbUrl: '/' + path.basename(cfg.glb) };
  delete config_.glb;
  delete config_.out;
  return `<!doctype html><html><head><meta charset="utf-8">${importMapHtml()}
<style>html,body{margin:0;background:#000}canvas{display:block}</style></head>
<body><canvas id="c" width="${cfg.width}" height="${cfg.height}"></canvas>
<script type="module">
import { renderGlbViews } from '${runtimeMount()}lib/browser/render_rig.js';
window.__c3v_result = null;
renderGlbViews(${JSON.stringify(config_)}).then(
  (r) => { window.__c3v_result = r; },
  (e) => { window.__c3v_result = { ok: false, error: String(e && e.message || e), stack: String(e && e.stack || '') }; },
);
</script></body></html>`;
}

async function main() {
  const t0 = Date.now();
  const cfg = config();
  if (!fs.existsSync(cfg.glb)) throw new Error(`GLB not found: ${cfg.glb}`);
  ensureDir(cfg.out);

  const srv = await serveDirs({ root: path.dirname(cfg.glb), routes: { '/__render.html': { body: pageHtml(cfg) } } });
  const consoleErrors = [];
  let launched = null;
  let page = null;
  let record = null;
  try {
    launched = await launchBrowser({ gpu: cfg.gpu });
    const timing = { launch_ms: Date.now() - t0 };

    page = await launched.browser.newPage();
    await page.setViewport({ width: cfg.width, height: cfg.height, deviceScaleFactor: 1 });
    page.on('console', (msg) => {
      if (msg.type() === 'error' || msg.type() === 'warning') consoleErrors.push(`${msg.type()}: ${msg.text()}`);
    });
    page.on('pageerror', (e) => consoleErrors.push(`pageerror: ${e.message}`));
    await page.goto(srv.url('/__render.html'), { waitUntil: 'load', timeout: cfg.timeoutMs });
    await page.waitForFunction('window.__c3v_result !== null', { timeout: cfg.timeoutMs, polling: 100 });
    const result = await page.evaluate(() => window.__c3v_result);
    if (!result || !result.ok) {
      const err = new Error(result ? result.error : 'render produced no result');
      err.details = { stack: result && result.stack, console: consoleErrors };
      throw err;
    }
    const views = [];
    for (const v of result.views) {
      const file = path.join(cfg.out, `view_${v.name}.png`);
      fs.writeFileSync(file, Buffer.from(v.b64, 'base64'));
      const { b64: _b64, ...meta } = v;
      views.push({ ...meta, path: file, mode: cfg.mode });
    }
    record = {
      ok: true,
      renderer: result.renderer || launched.renderer,
      gpu: launched.gpu,
      mode: cfg.mode,
      bbox: result.bbox,
      views,
      warnings: result.warnings || [],
      console_errors: consoleErrors,
      timing_ms: { ...timing, ...result.timing, total_ms: Date.now() - t0 },
    };
    writeJson(path.join(cfg.out, 'views.json'), record);
  } finally {
    // page first, then release: a shared browser is disconnected, never closed
    if (page) await page.close().catch(() => {});
    await releaseBrowser(launched).catch(() => {});
    await srv.close();
  }
  return finish(record, 0);
}

main().catch((err) => {
  finish({ ok: false, error: err.message, details: err.details || null, stack: String(err.stack || '').split('\n').slice(0, 8) }, 1);
});

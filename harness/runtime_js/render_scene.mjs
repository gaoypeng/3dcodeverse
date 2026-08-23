#!/usr/bin/env node
/**
 * Render a scene_threejs workspace: authored cameras + an overview orbit rig,
 * at one or more animation times, with deterministic instruments.
 *
 *   node render_scene.mjs --ws <ws> --out <dir> [--cameras authored|<json>]
 *        [--orbit-views '<json list of {name,azimuth,elevation}>'|none]
 *        [--times 0,1.5] [--width 1024] [--height 576] [--gpu auto|on|off]
 *        [--fps-seconds 2] [--timeout-ms 240000]
 *
 * Writes PNG per (camera, time), views.json, metrics.json
 * {console_errors, shader_errors, fps, census, camera_checks, ...}.
 * Last stdout line = JSON summary.  Exit 0 ok, 1 scene failed, 2 driver error.
 */

import path from 'node:path';
import { armWatchdog, dataUrlToPng, ensureDir, fail, finish, parseCli, readJsonArg, writeJson } from './lib/cli.mjs';
import { errorSummary, openHost } from './lib/host_page.mjs';
import { fitOrbitCameras } from './lib/orbit.mjs';

const args = parseCli({
  ws: {}, out: {}, cameras: { default: 'authored' }, 'orbit-views': { default: 'none' },
  times: { default: '0,1.5' }, width: { default: '1024' }, height: { default: '576' },
  gpu: { default: process.env.C3V_RENDER_GPU || 'auto' }, 'fps-seconds': { default: '2' },
  'timeout-ms': { default: '240000' }, 'log-depth': { type: 'boolean', default: false },
  'orbit-fog': { type: 'boolean', default: false }, counterfactual: { type: 'boolean', default: false },
});

function tag(t) {
  return 't' + t.toFixed(2).replace(/0+$/, '').replace(/\.$/, '').replace('.', 'p');
}

async function main() {
  if (!args.ws || !args.out) throw new Error('--ws and --out are required');
  const width = parseInt(args.width, 10), height = parseInt(args.height, 10);
  const times = String(args.times).split(',').map((s) => parseFloat(s)).filter((x) => Number.isFinite(x) && x >= 0).sort((a, b) => a - b);
  if (!times.length) throw new Error('--times must list non-negative numbers');
  const outDir = ensureDir(path.resolve(args.out));
  const watchdog = armWatchdog(parseInt(args['timeout-ms'], 10));
  const t0 = Date.now();

  let host;
  try {
    host = await openHost(args.ws, { width, height, gpu: args.gpu, logDepth: args['log-depth'] });
  } catch (e) {
    return fail(`host failed: ${e.message}`);
  }
  const { page, boot } = host;
  const metrics = { ok: false, renderer: boot.renderer || host.renderer, gpu: host.gpu, boot, views: [], camera_checks: [], fps: null, census: null, shader_errors: [] };
  try {
    if (!boot.ok) {
      Object.assign(metrics, errorSummary(host.errors, boot));
      writeJson(path.join(outDir, 'metrics.json'), metrics);
      writeJson(path.join(outDir, 'views.json'), []);
      return finish({ ok: false, error: boot.error || 'scene did not boot', stage: boot.stage, out: outDir, ...counts(metrics) }, 1);
    }
    metrics.census = await page.evaluate(() => window.__c3v.census());

    // camera list
    let cams = [];
    if (args.cameras === 'authored') cams = boot.cameras.map((c) => ({ ...c, kind: 'authored' }));
    else if (args.cameras !== 'none') cams = (readJsonArg(args.cameras, 'cameras') || []).map((c) => ({ ...c, kind: c.kind || 'authored' }));
    if (args['orbit-views'] !== 'none') {
      const views = readJsonArg(args['orbit-views'], 'orbit-views') || [];
      const bbox = metrics.census.content_bbox || metrics.census.bbox;
      cams.push(...fitOrbitCameras(bbox, views, { aspect: width / height, groundY: metrics.census.ground_y, noFog: !args['orbit-fog'] }));
    }
    const seen = new Set();
    cams = cams.filter((c) => { const k = c.name; if (seen.has(k)) return false; seen.add(k); return true; });
    if (!cams.length) throw new Error('no cameras to render (authored list empty and no orbit views)');

    // instruments per camera (at t=0, before any stepping)
    for (const c of cams) {
      const chk = await page.evaluate((spec) => window.__c3v.cameraChecks(spec), c);
      metrics.camera_checks.push({ kind: c.kind, ...chk });
    }
    // renders: times ascending (sim time cannot rewind)
    for (const t of times) {
      for (const c of cams) {
        const r = await page.evaluate((spec, tt) => window.__c3v.renderAt(spec, tt), c, t);
        const file = `${c.name}_${tag(t)}.png`;
        dataUrlToPng(r.dataUrl, path.join(outDir, file));
        metrics.views.push({ name: c.name, kind: c.kind, path: file, time_s: t, position: c.position, lookAt: c.lookAt, fov: c.fov, render_ms: r.ms });
        if (args.counterfactual) {
          const cf = await page.evaluate((spec, tt) => window.__c3v.renderAt(spec, tt, { stripCustom: true }), c, t);
          const cfFile = `${c.name}_${tag(t)}_nocustom.png`;
          dataUrlToPng(cf.dataUrl, path.join(outDir, cfFile));
          metrics.views.push({ name: `${c.name}_nocustom`, kind: 'counterfactual', path: cfFile, time_s: t, position: c.position, lookAt: c.lookAt, fov: c.fov, render_ms: cf.ms, counterfactual_of: file });
        }
      }
    }
    const fpsSec = parseFloat(args['fps-seconds']);
    if (fpsSec > 0) metrics.fps = await page.evaluate((s) => window.__c3v.fps(s), fpsSec);
    metrics.shader_errors = (await page.evaluate(() => window.__c3v.shaderErrors())).map(({ _key, ...e }) => e);
    Object.assign(metrics, errorSummary(host.errors, boot));
    metrics.ok = metrics.console_errors.length === 0 && metrics.shader_errors.length === 0;
    metrics.duration_ms = Date.now() - t0;
    writeJson(path.join(outDir, 'metrics.json'), metrics);
    writeJson(path.join(outDir, 'views.json'), metrics.views);
    clearTimeout(watchdog);
    await host.close();
    return finish({ ok: metrics.ok, out: outDir, n_views: metrics.views.length, renderer: metrics.renderer, gpu: host.gpu, fps: metrics.fps ? +metrics.fps.fps.toFixed(1) : null, duration_ms: metrics.duration_ms, ...counts(metrics) }, metrics.ok ? 0 : 1);
  } catch (e) {
    Object.assign(metrics, errorSummary(host.errors, boot));
    metrics.console_errors.push(`driver: ${e.message}`);
    writeJson(path.join(outDir, 'metrics.json'), metrics);
    await host.close();
    return fail(`render failed: ${e.message}`, { out: outDir });
  }
}

function counts(m) {
  return { console_errors: (m.console_errors || []).length, shader_errors: (m.shader_errors || []).length };
}

main().catch((e) => fail(e.stack || String(e)));

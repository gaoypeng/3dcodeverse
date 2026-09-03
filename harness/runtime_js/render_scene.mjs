#!/usr/bin/env node
/**
 * Render a scene_threejs workspace: authored cameras + an overview orbit rig,
 * at one or more animation times, with deterministic instruments.
 *
 *   node render_scene.mjs --ws <ws> --out <dir> [--cameras authored|<json>]
 *        [--orbit-views '<json list of {name,azimuth,elevation}>'|none]
 *        [--bounds '{"min":[x,y,z],"max":[x,y,z]}'|none]   (plan bounds: orbit framing guard)
 *        [--times 0,1.5] [--width 1024] [--height 576] [--gpu auto|on|off]
 *        [--fps-seconds 2] [--timeout-ms 240000]
 *        [--no-post] [--post-options '{"ao":0.45,"bloomStrength":0.22}']
 *
 * Writes PNG per (camera, time), views.json, metrics.json
 * {console_errors, shader_errors, fps, census, camera_checks, ...}.
 * Last stdout line = JSON summary.  Exit 0 ok, 1 scene failed, 2 driver error.
 */

import path from 'node:path';
import { armWatchdog, dataUrlToPng, ensureDir, envFlag, fail, finish, parseCli, readJsonArg, safeName, writeJson } from './lib/cli.mjs';
import { createTimeoutMs, errorSummary, openHost } from './lib/host_page.mjs';
import { fitOrbitCameras, framingBox } from './lib/orbit.mjs';

const args = parseCli({
  'no-settle': { type: 'boolean', default: false },
  'camera-repair': { type: 'boolean', default: false },
  'auto-exposure': { type: 'boolean', default: false },
  // post chain (GTAO + soft bloom + grade): ON for scene pictures, `--no-post` /
  // CV3D_POST=0 to render raw.  Object renders never come through here.
  'no-post': { type: 'boolean', default: false },
  'post-options': { default: '' },
  ws: {}, out: {}, cameras: { default: 'authored' }, 'orbit-views': { default: 'none' }, bounds: { default: 'none' },
  times: { default: '0,1.5' }, width: { default: '1024' }, height: { default: '576' },
  gpu: { default: process.env.CV3D_RENDER_GPU || 'auto' }, 'fps-seconds': { default: '2' },
  'timeout-ms': { default: '240000' }, 'create-timeout-ms': { default: '' }, 'log-depth': { type: 'boolean', default: false },
  'orbit-fog': { type: 'boolean', default: false },
});

function tag(t) {
  return 't' + t.toFixed(2).replace(/0+$/, '').replace(/\.$/, '').replace('.', 'p');
}

/** outDir-contained path for a view file; refuses any name that escapes outDir. */
function outFile(outDir, file) {
  const p = path.resolve(outDir, file);
  if (!p.startsWith(outDir + path.sep)) throw new Error(`unsafe output path: ${file}`);
  return p;
}

async function main() {
  if (!args.ws || !args.out) throw new Error('--ws and --out are required');
  const width = parseInt(args.width, 10), height = parseInt(args.height, 10);
  const times = String(args.times).split(',').map((s) => parseFloat(s)).filter((x) => Number.isFinite(x) && x >= 0).sort((a, b) => a - b);
  if (!times.length) throw new Error('--times must list non-negative numbers');
  const outDir = ensureDir(path.resolve(args.out));
  const timeoutMs = parseInt(args['timeout-ms'], 10);
  const watchdog = armWatchdog(timeoutMs);
  const t0 = Date.now();

  let host;
  try {
    host = await openHost(args.ws, {
      width, height, gpu: args.gpu, logDepth: args['log-depth'],
      createSceneTimeoutMs: createTimeoutMs(args['create-timeout-ms'], timeoutMs),
      settle: !args['no-settle'],
      cameraRepair: !!args['camera-repair'],
      autoExposure: !!args['auto-exposure'],
      post: args['no-post'] ? false : envFlag('CV3D_POST', true),
      postOptions: readJsonArg(args['post-options'], 'post-options'),
    });
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
      const bounds = args.bounds && args.bounds !== 'none' ? readJsonArg(args.bounds, 'bounds') : null;
      const bbox = framingBox(metrics.census, bounds);   // content only: never the ground plane / sky dome
      metrics.framing_bbox = bbox;
      cams.push(...fitOrbitCameras(bbox, views, { aspect: width / height, groundY: metrics.census.ground_y, noFog: !args['orbit-fog'] }));
    }
    // camera names become filenames: reject path tricks before composing any output path
    for (const c of cams) c.name = safeName(c.name, 'camera name');
    const seen = new Set();
    cams = cams.filter((c) => { const k = c.name; if (seen.has(k)) return false; seen.add(k); return true; });
    if (!cams.length) throw new Error('no cameras to render (authored list empty and no orbit views)');

    // agent exceptions inside a single view (update() is already guarded page-side;
    // this catches e.g. an onBeforeRender that throws) must not abort the other
    // views: record them as scene errors → console_errors → gate finding, exit 1.
    const sceneErrors = [];
    const sceneErr = (what, e) => sceneErrors.push(`${what}: ${String(e.message || e).slice(0, 600)}`);
    // instruments per camera (at t=0, before any stepping)
    for (const c of cams) {
      try {
        const chk = await page.evaluate((spec) => window.__c3v.cameraChecks(spec), c);
        metrics.camera_checks.push({ kind: c.kind, ...chk });
      } catch (e) { sceneErr(`camera checks failed for '${c.name}'`, e); }
    }
    // renders: times ascending (sim time cannot rewind)
    for (const t of times) {
      for (const c of cams) {
        try {
          const r = await page.evaluate((spec, tt) => window.__c3v.renderAt(spec, tt), c, t);
          const file = `${c.name}_${tag(t)}.png`;
          dataUrlToPng(r.dataUrl, outFile(outDir, file));
          const view = { name: c.name, kind: c.kind, path: file, time_s: t, position: c.position, lookAt: c.lookAt, fov: c.fov, render_ms: r.ms };
          // position stays the AUTHORED camera; when repair moved the lens, record where the pixels really came from
          if (r.position && c.position && r.position.some((v, i) => Math.abs(v - c.position[i]) > 1e-6)) view.repaired_position = r.position;
          metrics.views.push(view);
        } catch (e) { sceneErr(`render failed for '${c.name}' at t=${t}`, e); }
      }
    }
    const fpsSec = parseFloat(args['fps-seconds']);
    if (fpsSec > 0) {
      try { metrics.fps = await page.evaluate((s) => window.__c3v.fps(s), fpsSec); }
      catch (e) { sceneErr('fps measurement failed', e); }
    }
    // repair fires lazily on each camera's first build, i.e. AFTER the census above
    // was captured: re-read it here so census.camera_repair is observable (review-3 S5)
    try {
      const reps = await page.evaluate(() => window.__c3v.cameraRepairs());
      if (reps.length && metrics.census) metrics.census.camera_repair = reps;
    } catch (e) { sceneErr('camera repair readback failed', e); }
    // same story for the post chain: it counts its bloom sources while RENDERING,
    // so the census captured above always reported zero of them
    try {
      const post = await page.evaluate(() => window.__c3v.post());
      if (post && metrics.census) metrics.census.post = post;
    } catch (e) { sceneErr('post readback failed', e); }
    metrics.shader_errors = (await page.evaluate(() => window.__c3v.shaderErrors())).map(({ _key, ...e }) => e);
    metrics.update_errors = await page.evaluate(() => window.__c3v.updateErrors());
    Object.assign(metrics, errorSummary(host.errors, boot));
    for (const e of sceneErrors) if (!metrics.console_errors.includes(e)) metrics.console_errors.push(e);
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

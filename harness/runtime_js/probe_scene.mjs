#!/usr/bin/env node
/**
 * Fast import-only probe of a scene_threejs workspace (the scene 'build' gate):
 * does src/scene.js load, does createScene() return {scene, cameras, update},
 * are cameras valid, does update(t, dt) run — plus the census and all
 * console/page/shader errors.  No screenshots.
 *
 *   node probe_scene.mjs --ws <ws> [--scene src/scene.js] [--gpu auto] [--timeout-ms 60000] [--out probe.json]
 *        [--compile] [--shaders-out report.json] [--sun-azimuth 60]
 *
 * `--compile` additionally runs the full shader preflight (static GLSL audits +
 * force-compile of every material, lib/shader_report.mjs) on the SAME booted
 * page — one browser boot instead of two for a scene build.  The report
 * (identical schema to check_shaders.mjs) lands in the summary as
 * `shader_report` and, when `--shaders-out` is given, in that file.
 * `--sun-azimuth <deg>` returns harness-fitted camera specs (lib/orbit.mjs —
 * the single owner of camera-fit math) from the measured census: an overview
 * of the content bounds plus one eye-level camera per top-level group, all
 * standing on the sun side — `fitted_cameras: {azimuth, overview, zones}`.
 * The scene assembler (assemble.py) converts these into its CameraPlans.
 * Last stdout line = JSON {ok, boot, census, console_errors, shader_errors, ...}.
 */

import path from 'node:path';
import { armWatchdog, fail, finish, parseCli, writeJson } from './lib/cli.mjs';
import { createTimeoutMs, errorSummary, openHost } from './lib/host_page.mjs';
import { fitOverviewCamera, fitZoneCamera, framingBox } from './lib/orbit.mjs';
import { compileIntoReport, staticShaderReport } from './lib/shader_report.mjs';

const args = parseCli({
  'no-settle': { type: 'boolean', default: false },
  'camera-repair': { type: 'boolean', default: false },
  'auto-exposure': { type: 'boolean', default: false },
  // The probe is a 320x180 GEOMETRY instrument, never a judged picture: it runs raw
  // so its shader report names the scene's own programs and nothing of ours.  The
  // flags parse (drivers share a switch list) but only `--post` turns the chain on.
  'no-post': { type: 'boolean', default: false },
  post: { type: 'boolean', default: false },
  ws: {}, out: {}, gpu: { default: process.env.CV3D_RENDER_GPU || 'auto' }, 'timeout-ms': { default: '60000' },
  'create-timeout-ms': { default: '' }, 'update-steps': { default: '10' }, scene: { default: 'src/scene.js' },
  compile: { type: 'boolean', default: false }, 'shaders-out': { default: '' }, 'sun-azimuth': { default: '' },
});

/** Harness-fitted camera specs from the census: overview + one per group. */
function fitCameras(census, azimuth) {
  const groundY = census.ground_y;
  const box = framingBox(census, null);
  const overview = box ? fitOverviewCamera(box, { azimuth, elevation: 30, groundY }) : null;
  const zones = [];
  for (const g of census.groups || []) {
    if (!g.bbox || g.kind === 'sky' || g.kind === 'empty' || g.kind === 'light') continue;
    zones.push({ group: g.name, ...fitZoneCamera(g.bbox, { azimuth, floor: groundY }) });
  }
  return { azimuth, overview, zones };
}

async function main() {
  if (!args.ws) throw new Error('--ws is required');
  const timeoutMs = parseInt(args['timeout-ms'], 10);
  const watchdog = armWatchdog(timeoutMs);
  const t0 = Date.now();
  let host;
  try {
    host = await openHost(args.ws, {
      width: 320, height: 180, gpu: args.gpu, sceneRel: args.scene.replace(/^\.?\//, ''),
      createSceneTimeoutMs: createTimeoutMs(args['create-timeout-ms'], timeoutMs),
      settle: !args['no-settle'],
      cameraRepair: !!args['camera-repair'],
      autoExposure: !!args['auto-exposure'],
      post: !!args.post && !args['no-post'],
    });
  } catch (e) {
    return fail(`host failed: ${e.message}`);
  }
  const { page, boot } = host;
  const result = { ok: false, renderer: boot.renderer || host.renderer, gpu: host.gpu, boot, census: null, update_ok: null, update_error: '', shader_errors: [] };
  try {
    if (boot.ok) {
      result.census = await page.evaluate(() => window.__c3v.census());
      // placement table (floating / sunken / unsupported / interpenetration per asset);
      // a failure here must never fail the probe — the python gate reports it as a WARN
      try {
        result.census.placement = await page.evaluate(() => window.__c3v.placement());
      } catch (e) {
        result.census.placement = { error: String((e && e.message) || e).slice(0, 400) };
      }
      // exercise update() for a few fixed steps and one tiny render (lazy programs compile)
      const steps = parseInt(args['update-steps'], 10);
      const upd = await page.evaluate((n) => {
        try {
          const cam = window.__c3v.cameras()[0];
          const r = window.__c3v.renderAt(cam, n / 30);
          return { ok: true, sim_time: r.sim_time, ms: r.ms };
        } catch (e) {
          return { ok: false, error: String((e && e.stack) || e).slice(0, 800) };
        }
      }, steps);
      result.update_ok = upd.ok;
      result.update_error = upd.error || '';
      result.first_render_ms = upd.ms;
      // update() exceptions are caught page-side (rendering continues); still a probe failure
      const updateErrors = await page.evaluate(() => window.__c3v.updateErrors());
      if (upd.ok && updateErrors.length) {
        result.update_ok = false;
        result.update_error = updateErrors[0].slice(0, 800);
      }
      const frame = await page.evaluate(() => { const c = window.__c3v.cameras()[0]; return window.__c3v.cameraChecks(c); });
      result.first_camera = frame;
      // the render/check exercises above may have repaired a camera AFTER the census
      // was captured: stitch it back so census.camera_repair is observable (review-3 S5)
      const reps = await page.evaluate(() => window.__c3v.cameraRepairs());
      if (reps.length) result.census.camera_repair = reps;
      const sunAz = parseFloat(args['sun-azimuth']);
      if (Number.isFinite(sunAz)) result.fitted_cameras = fitCameras(result.census, sunAz);
    }
    // Probe truth is snapshotted BEFORE any forced compile: the probe gate sees
    // exactly what a standalone probe would; the preflight below sees everything.
    result.shader_errors = (await page.evaluate(() => window.__c3v.shaderErrors())).map(({ _key, ...e }) => e);
    Object.assign(result, errorSummary(host.errors, boot));
    result.ok = boot.ok && result.update_ok === true && result.console_errors.length === 0 && result.shader_errors.length === 0;

    if (args.compile) {
      const ts = Date.now();
      if (boot.ok) {
        const { report, files } = staticShaderReport(path.resolve(args.ws), null);
        await compileIntoReport(report, files, host);
        report.ok = report.errors.length === 0;
        report.duration_ms = Date.now() - ts;
        result.shader_report = report;
      } else {
        result.shader_report = { ok: false, skipped: 'scene did not boot', errors: [], warnings: [], compile: null, duration_ms: Date.now() - ts };
      }
      if (args['shaders-out']) writeJson(path.resolve(args['shaders-out']), result.shader_report);
    }

    result.duration_ms = Date.now() - t0;
    clearTimeout(watchdog);
    await host.close();
    if (args.out) writeJson(path.resolve(args.out), result);
    return finish(result, result.ok ? 0 : 1);
  } catch (e) {
    await host.close();
    return fail(`probe failed: ${e.message}`);
  }
}

main().catch((e) => fail(e.stack || String(e)));

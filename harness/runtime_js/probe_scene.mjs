#!/usr/bin/env node
/**
 * Fast import-only probe of a scene_threejs workspace (the scene 'build' gate):
 * does src/scene.js load, does createScene() return {scene, cameras, update},
 * are cameras valid, does update(t, dt) run — plus the census and all
 * console/page/shader errors.  No screenshots.
 *
 *   node probe_scene.mjs --ws <ws> [--scene src/scene.js] [--gpu auto] [--timeout-ms 60000] [--out probe.json]
 * Last stdout line = JSON {ok, boot, census, console_errors, shader_errors, ...}.
 */

import path from 'node:path';
import { armWatchdog, fail, finish, parseCli, writeJson } from './lib/cli.mjs';
import { createTimeoutMs, errorSummary, openHost } from './lib/host_page.mjs';

const args = parseCli({
  ws: {}, out: {}, gpu: { default: process.env.CV3D_RENDER_GPU || 'auto' }, 'timeout-ms': { default: '60000' },
  'create-timeout-ms': { default: '' }, 'update-steps': { default: '10' }, scene: { default: 'src/scene.js' },
});

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
    });
  } catch (e) {
    return fail(`host failed: ${e.message}`);
  }
  const { page, boot } = host;
  const result = { ok: false, renderer: boot.renderer || host.renderer, gpu: host.gpu, boot, census: null, update_ok: null, update_error: '', shader_errors: [] };
  try {
    if (boot.ok) {
      result.census = await page.evaluate(() => window.__c3v.census());
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
    }
    result.shader_errors = (await page.evaluate(() => window.__c3v.shaderErrors())).map(({ _key, ...e }) => e);
    Object.assign(result, errorSummary(host.errors, boot));
    result.ok = boot.ok && result.update_ok === true && result.console_errors.length === 0 && result.shader_errors.length === 0;
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

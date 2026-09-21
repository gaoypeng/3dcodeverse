#!/usr/bin/env node
/**
 * Ablation probe for a scene_threejs workspace: does the scene's custom GLSL
 * actually reach the frame?
 *
 *   node ablate_scene.mjs --ws <ws> [--out <dir>] [--cameras authored|<json>]
 *        [--t 1.5] [--width 512] [--height 288] [--frames] [--max-materials 8]
 *        [--gpu auto] [--timeout-ms 120000]
 *
 * Boots the host once and calls `window.__c3v.ablation()` (lib/host_ablation.mjs):
 * every camera is rendered as authored and again with every ShaderMaterial /
 * onBeforeCompile patch replaced by a neutral material of the same base colour,
 * and the changed-pixel fraction is reported per camera, plus per material
 * (leave-one-out) on the camera where the shaders show most.
 *
 * `--frames` also writes `<camera>_authored.png` / `<camera>_ablated.png` into
 * --out: the pair a human (or a VLM) looks at to see WHAT the shader paints.
 * Writes `ablation.json` into --out when given; last stdout line = the same JSON.
 * Exit 0 measured, 1 the scene did not boot, 2 driver failure.
 */

import path from 'node:path';
import { armWatchdog, dataUrlToPng, ensureDir, fail, finish, parseCli, readJsonArg, safeName, writeJson } from './lib/cli.mjs';
import { createTimeoutMs, errorSummary, openHost } from './lib/host_page.mjs';

const args = parseCli({
  ws: {}, out: { default: '' }, cameras: { default: 'authored' }, t: { default: '1.5' },
  width: { default: '512' }, height: { default: '288' }, 'max-materials': { default: '8' },
  frames: { type: 'boolean', default: false }, gpu: { default: process.env.CV3D_RENDER_GPU || 'auto' },
  'timeout-ms': { default: '120000' }, 'create-timeout-ms': { default: '' },
});

async function main() {
  if (!args.ws) throw new Error('--ws is required');
  const width = parseInt(args.width, 10), height = parseInt(args.height, 10);
  const t = parseFloat(args.t);
  if (!Number.isFinite(t) || t < 0) throw new Error('--t must be a non-negative number');
  const timeoutMs = parseInt(args['timeout-ms'], 10);
  const watchdog = armWatchdog(timeoutMs);
  const t0 = Date.now();
  const outDir = args.out ? ensureDir(path.resolve(args.out)) : null;

  let host;
  try {
    host = await openHost(args.ws, {
      width, height, gpu: args.gpu,
      createSceneTimeoutMs: createTimeoutMs(args['create-timeout-ms'], timeoutMs),
    });
  } catch (e) {
    return fail(`host failed: ${e.message}`);
  }
  const { page, boot } = host;
  const summary = { ok: false, renderer: boot.renderer || host.renderer, gpu: host.gpu, time_s: t, ...emptyReport() };
  try {
    if (!boot.ok) {
      Object.assign(summary, errorSummary(host.errors, boot), { error: boot.error || 'scene did not boot' });
      await host.close();
      if (outDir) writeJson(path.join(outDir, 'ablation.json'), summary);
      return finish(summary, 1);
    }
    let cams = null;
    if (args.cameras !== 'authored' && args.cameras !== 'none') {
      cams = (readJsonArg(args.cameras, 'cameras') || []).map((c) => ({ ...c, name: safeName(c.name, 'camera name') }));
    }
    const report = await page.evaluate(
      (o) => window.__c3v.ablation(o),
      { t, cameras: cams, maxMaterials: parseInt(args['max-materials'], 10), frames: !!(args.frames && outDir) },
    );
    // PNG pairs live on disk, never in the summary (a data URL per camera is megabytes)
    const frames = report.frames || [];
    delete report.frames;
    Object.assign(summary, report);
    if (outDir && frames.length) {
      summary.frame_files = frames.map((f) => {
        const name = safeName(f.camera, 'camera name');
        const a = `${name}_authored.png`, b = `${name}_ablated.png`;
        dataUrlToPng(f.authored, path.join(outDir, a));
        dataUrlToPng(f.ablated, path.join(outDir, b));
        return { camera: name, authored: a, ablated: b };
      });
    }
    Object.assign(summary, errorSummary(host.errors, boot));
    summary.ok = !report.error;
    summary.duration_ms = Date.now() - t0;
    clearTimeout(watchdog);
    await host.close();
    if (outDir) writeJson(path.join(outDir, 'ablation.json'), summary);
    return finish(summary, summary.ok ? 0 : 1);
  } catch (e) {
    await host.close();
    return fail(`ablation failed: ${e.message}`);
  }
}

/** The report shape a caller can rely on even when the scene never booted. */
function emptyReport() {
  return { custom_materials: 0, cameras: [], materials: [], max_changed_frac: 0,
           content_changed_frac: 0, per_material_measured: false, per_material_cameras: [] };
}

main().catch((e) => fail(e.stack || String(e)));

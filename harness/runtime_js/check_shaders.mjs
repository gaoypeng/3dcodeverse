#!/usr/bin/env node
/**
 * Shader compile preflight for a scene_threejs workspace (standalone tool;
 * the scene build runs the same stages through `probe_scene.mjs --compile`).
 *
 *   node check_shaders.mjs --ws <ws> [--module src/shaders/x.js] [--gpu auto] [--out report.json]
 *
 * 1. Static audits over src/**\/*.js GLSL strings (no browser): #include not
 *    alone on a line, #version directives, gl_FragColor + custom `out`,
 *    GLSL3 + gl_FragColor, uTime read but undeclared/unbound, patches that
 *    drop the chunk they replace, fog-less ShaderMaterials in fogged scenes.
 * 2. Compile preflight: boot the scene, force-compile every material
 *    (renderer.compile + one frame), collect GPU info logs and map the
 *    offending source line back to file:line.
 * Both stages live in lib/shader_report.mjs.
 * Output (last stdout line): {ok, errors:[{file,line,message,...}], warnings:[...]}.
 * Exit 0 clean, 1 errors, 2 could not run.
 */

import path from 'node:path';
import { armWatchdog, fail, finish, parseCli, writeJson } from './lib/cli.mjs';
import { openHost } from './lib/host_page.mjs';
import { compileIntoReport, staticShaderReport } from './lib/shader_report.mjs';

const args = parseCli({
  ws: {}, module: {}, out: {}, gpu: { default: process.env.CV3D_RENDER_GPU || 'auto' },
  'timeout-ms': { default: '90000' },
});

async function main() {
  if (!args.ws) throw new Error('--ws is required');
  const ws = path.resolve(args.ws);
  const watchdog = armWatchdog(parseInt(args['timeout-ms'], 10));
  const t0 = Date.now();
  const { report, files } = staticShaderReport(ws, args.module || null);
  const { errors, warnings } = report;

  let host = null;
  try {
    host = await openHost(ws, { width: 256, height: 144, gpu: args.gpu });
    await compileIntoReport(report, files, host);
    await host.close();
  } catch (e) {
    if (host) await host.close().catch(() => {});
    return fail(`compile preflight failed: ${e.message}`, { errors, warnings });
  }
  report.ok = errors.length === 0;
  report.duration_ms = Date.now() - t0;
  clearTimeout(watchdog);
  if (args.out) writeJson(path.resolve(args.out), report);
  // human summary first, JSON last
  for (const e of errors) process.stdout.write(`ERROR ${e.file}${e.line ? ':' + e.line : ''}: ${e.message}\n`);
  for (const w of warnings) process.stdout.write(`WARN  ${w.file}${w.line ? ':' + w.line : ''}: ${w.message}\n`);
  process.stdout.write(report.ok ? 'OK every program compiled\n' : `${errors.length} shader error(s)\n`);
  return finish(report, report.ok ? 0 : 1);
}

main().catch((e) => fail(e.stack || String(e)));

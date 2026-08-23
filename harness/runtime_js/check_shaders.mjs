#!/usr/bin/env node
/**
 * Shader compile preflight for a scene_threejs workspace.
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
 * Output (last stdout line): {ok, errors:[{file,line,message,...}], warnings:[...]}.
 * Exit 0 clean, 1 errors, 2 could not run.
 */

import fs from 'node:fs';
import path from 'node:path';
import { armWatchdog, fail, finish, parseCli, writeJson } from './lib/cli.mjs';
import { auditFile, fixHintFor, hasGlsl, locateSourceLine } from './lib/glsl_audit.mjs';
import { errorSummary, openHost } from './lib/host_page.mjs';

const args = parseCli({
  ws: {}, module: {}, out: {}, gpu: { default: process.env.C3V_RENDER_GPU || 'auto' },
  'timeout-ms': { default: '90000' }, 'no-compile': { type: 'boolean', default: false },
});

function listSources(root, sub = 'src') {
  const out = [];
  const walk = (dir) => {
    if (!fs.existsSync(dir)) return;
    for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
      const p = path.join(dir, ent.name);
      if (ent.isDirectory()) { if (ent.name !== 'node_modules') walk(p); continue; }
      if (/\.(m?js)$/.test(ent.name)) out.push({ file: path.relative(root, p).split(path.sep).join('/'), text: fs.readFileSync(p, 'utf8') });
    }
  };
  walk(path.join(root, sub));
  return out;
}

async function main() {
  if (!args.ws) throw new Error('--ws is required');
  const ws = path.resolve(args.ws);
  const watchdog = armWatchdog(parseInt(args['timeout-ms'], 10));
  const t0 = Date.now();
  let files = listSources(ws);
  const sceneUsesFog = files.some((f) => /new\s+THREE\.(Fog|FogExp2)\b|\.fog\s*=/.test(f.text));
  const auditTargets = args.module ? files.filter((f) => f.file === args.module.replace(/^\.?\//, '')) : files;
  if (args.module && !auditTargets.length) throw new Error(`--module ${args.module} not found under ${ws}`);
  const errors = [], warnings = [];
  for (const f of auditTargets) {
    for (const fd of auditFile(f.file, f.text, { sceneUsesFog })) {
      (fd.severity === 'error' ? errors : warnings).push({ file: fd.file, line: fd.line, kind: fd.kind, message: fd.message, fix_hint: fd.severity === 'error' ? fd.message : '' });
    }
  }
  const report = { ok: false, ws, module: args.module || null, static: { files: auditTargets.length, glsl_files: auditTargets.filter((f) => hasGlsl(f.text)).length }, compile: null, errors, warnings };

  if (!args['no-compile']) {
    let host = null;
    try {
      host = await openHost(ws, { width: 256, height: 144, gpu: args.gpu });
      const { page, boot } = host;
      if (!boot.ok) {
        errors.push({ file: 'src/scene.js', line: null, kind: 'boot', message: `scene did not boot at stage '${boot.stage}': ${boot.error}`.slice(0, 1200), fix_hint: 'fix the import/runtime error first; then re-run check_shaders' });
      } else {
        const comp = await page.evaluate((spec) => window.__c3v.compileAll(spec || undefined), null);
        const shaderErrors = comp.shader_errors.map(({ _key, ...e }) => e);
        for (const e of shaderErrors) {
          const loc = locateSourceLine(files, e.source_line);
          errors.push({
            file: loc.file || (e.material ? `(material ${e.material})` : '(assembled shader)'),
            line: loc.line, kind: 'compile', stage: e.stage, material: e.material || '', material_type: e.material_type || '',
            message: `${e.stage} shader: ${e.message}` + (e.source_line ? ` — at: ${e.source_line}` : '') + (loc.candidates > 1 ? ` (${loc.candidates} identical lines; first shown)` : ''),
            assembled_line: e.assembled_line, source_line: e.source_line, context: e.context, fix_hint: fixHintFor(e.message),
          });
        }
        for (const a of comp.material_audit) {
          (a.severity === 'error' ? errors : warnings).push({ file: '(runtime material)', line: null, kind: a.kind, material: a.material, message: a.message, fix_hint: a.severity === 'error' ? a.message : '' });
        }
        report.compile = { ms: comp.compile_ms, programs: comp.programs, custom_materials: comp.custom_materials, renderer: boot.renderer, gpu: host.gpu };
      }
      const es = errorSummary(host.errors, boot);
      for (const c of es.shader_console) {
        if (!errors.some((e) => e.kind === 'compile')) errors.push({ file: '(console)', line: null, kind: 'shader_console', message: c, fix_hint: 'search your GLSL for the quoted ERROR line' });
      }
      for (const c of es.console_errors) if (!boot.error || !c.startsWith('boot[')) warnings.push({ file: '(console)', line: null, kind: 'console', message: c });
      await host.close();
    } catch (e) {
      if (host) await host.close().catch(() => {});
      return fail(`compile preflight failed: ${e.message}`, { errors, warnings });
    }
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

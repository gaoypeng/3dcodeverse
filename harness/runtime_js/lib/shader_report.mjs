/**
 * Shader preflight report builder, shared by the standalone tool
 * (`check_shaders.mjs`) and the combined build probe (`probe_scene.mjs
 * --compile`).  Two node-side stages over one report shape:
 *
 *   staticShaderReport(ws, module)  — walk src/**, run the static GLSL audits
 *   compileIntoReport(report, ...)  — on an already-booted host page, force-
 *     compile every material and fold GPU errors (file:line mapped), the
 *     runtime material audit and console noise into the same report.
 *
 * Report shape (identical to the historical check_shaders.mjs output):
 *   {ok, ws, module, static:{files, glsl_files}, compile, errors:[...], warnings:[...]}
 */

import fs from 'node:fs';
import path from 'node:path';
import { auditFile, fixHintFor, hasGlsl, locateSourceLine } from './glsl_audit.mjs';
import { errorSummary } from './host_page.mjs';
import { listJsFiles } from './syntax_check.mjs';

/**
 * All .js/.mjs sources under <root>/<sub> as [{file, text}] (posix rel paths,
 * sorted).  The directory walk itself is `syntax_check.listJsFiles` — the one
 * source-file walker in runtime_js.
 */
export function listSources(root, sub = 'src') {
  return listJsFiles(path.join(root, sub)).map((p) => ({
    file: path.relative(root, p).split(path.sep).join('/'),
    text: fs.readFileSync(p, 'utf8'),
  }));
}

/** `src/lib/*.js` — the harness's own effect library, shipped into every
 * scene workspace (D51).  The STATIC audits skip it: its GLSL is written for
 * `shader.js patchStandard`, which declares and binds `uTime` itself (see
 * `withTime`), and no string-level audit can see that — it fired the
 * undeclared/unbound uTime PAIR on six shipped modules, and once a workspace
 * carried the library that pair outranked the agent's own real shader error
 * in the build report.  These files are verified on the GPU by
 * `tests/scene_runtime/lib/`, not by the workspace gate.  They stay in
 * `files`, so a COMPILE error inside one still maps back to its file:line. */
const LIB_RE = /^src\/lib\//;

/**
 * Static-audit stage: build the report skeleton with the node-side GLSL
 * findings.  `module` (optional) restricts the audit to one source file.
 * @returns {{report: object, files: Array<{file, text}>}}
 */
export function staticShaderReport(ws, module = null) {
  const files = listSources(ws);
  const sceneUsesFog = files.some((f) => /new\s+THREE\.(Fog|FogExp2)\b|\.fog\s*=/.test(f.text));
  const auditTargets = module
    ? files.filter((f) => f.file === module.replace(/^\.?\//, ''))
    : files.filter((f) => !LIB_RE.test(f.file));
  if (module && !auditTargets.length) throw new Error(`--module ${module} not found under ${ws}`);
  const errors = [], warnings = [];
  for (const f of auditTargets) {
    for (const fd of auditFile(f.file, f.text, { sceneUsesFog })) {
      (fd.severity === 'error' ? errors : warnings).push({ file: fd.file, line: fd.line, kind: fd.kind, message: fd.message, fix_hint: fd.severity === 'error' ? fd.message : '' });
    }
  }
  const report = {
    ok: false, ws, module: module || null,
    static: { files: auditTargets.length, glsl_files: auditTargets.filter((f) => hasGlsl(f.text)).length },
    compile: null, errors, warnings,
  };
  return { report, files };
}

/**
 * Compile stage on an already-booted host page: force-compile every material
 * (`window.__c3v.compileAll`), map compiler errors back to file:line, audit
 * runtime materials, fold console noise.  Mutates and returns `report`.
 * @param {object} report      from staticShaderReport
 * @param {Array} files        from staticShaderReport
 * @param {{page, boot, gpu, errors}} host   openHost result (or equivalent)
 */
export async function compileIntoReport(report, files, host) {
  const { page, boot } = host;
  const { errors, warnings } = report;
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
  return report;
}

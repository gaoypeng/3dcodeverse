/**
 * Static GLSL audits over agent source files (node-side, pure functions).
 *
 * Finds GLSL string literals in JS sources, then flags the traps that compile
 * silently-wrong or not at all in three.js ShaderMaterial / onBeforeCompile:
 * #include not alone on a line, a #version directive, gl_FragColor next to a
 * custom `out vec4`, GLSL3 + gl_FragColor, uTime read but never declared or
 * bound, patches that drop the chunk they replace, precision directives.
 * Also maps compiler "source line" text back to file:line.
 */

const GLSL_MARK_RE = /\b(void\s+main|gl_FragColor|gl_Position|#include\s*<|uniform\s+\w+|varying\s+\w+|vec[234]\s+\w+|csm_|gl_FragCoord)\b/;
const INCLUDE_OK_RE = /^\s*#include\s*<[A-Za-z0-9_]+>\s*;?\s*$/;
const OUT_VEC4_RE = /^\s*(layout\s*\([^)]*\)\s*)?out\s+(lowp\s+|mediump\s+|highp\s+)?vec4\s+\w+\s*;/m;
const REPLACE_RE = /\.replace\(\s*(['"`])#include\s*<([A-Za-z0-9_]+)>\1\s*,\s*([`'"])([\s\S]*?)\3\s*\)/g;

/** Extract template/quoted string literals that look like GLSL: [{text, line}]. */
export function extractGlslStrings(source) {
  const out = [];
  const re = /`([^`\\]*(?:\\.[^`\\]*)*)`/g;
  let m;
  while ((m = re.exec(source))) {
    const text = m[1];
    if (!GLSL_MARK_RE.test(text)) continue;
    const line = source.slice(0, m.index).split('\n').length; // line of the opening backtick
    out.push({ text, line, start: m.index });
  }
  // Single/double quoted multi-statement GLSL (rare; one-liners with \n)
  const re2 = /(['"])((?:\\.|(?!\1)[^\\\n])*)\1/g;
  while ((m = re2.exec(source))) {
    const text = m[2].replace(/\\n/g, '\n');
    if (text.length < 40 || !GLSL_MARK_RE.test(text)) continue;
    const line = source.slice(0, m.index).split('\n').length;
    out.push({ text, line, start: m.index });
  }
  return out;
}

function finding(file, line, kind, message, severity = 'error') {
  return { file, line, kind, message, severity };
}

export function isShaderSource(file) {
  return /\.(glsl|vert|frag)$/.test(file);
}

/** Audit one file; returns findings. */
export function auditFile(file, source, { sceneUsesFog = false } = {}) {
  const findings = [];
  if (isShaderSource(file)) {
    // Raw files may be joined into another shader or used by RawShaderMaterial.
    // Their JS uniform bindings, material type and assembled declarations are
    // unknown here; compile/runtime audits own those cross-file questions.
    source.split('\n').forEach((line, i) => {
      if (/^\s*#include\b/.test(line) && !INCLUDE_OK_RE.test(line)) {
        findings.push(finding(file, i + 1, 'include_not_alone',
          `'#include <chunk>' must be ALONE on its line (found: ${line.trim().slice(0, 80)})`));
      }
    });
    return findings;
  }
  const strings = extractGlslStrings(source);
  if (!strings.length) return findings;
  const isRaw = /RawShaderMaterial/.test(source);
  const glsl3 = /glslVersion\s*:\s*(THREE\.)?GLSL3/.test(source);
  let usesUTime = false;
  let declaresUTime = false;
  let firstUTimeLine = 0;
  for (const s of strings) {
    const lines = s.text.split('\n');
    lines.forEach((ln, i) => {
      const fileLine = s.line + i;
      if (/#include/.test(ln) && !INCLUDE_OK_RE.test(ln) && !/\$\{/.test(ln)) {
        findings.push(finding(file, fileLine, 'include_not_alone', `'#include <chunk>' must be ALONE on its line (found: ${ln.trim().slice(0, 80)})`));
      }
      if (/^\s*#version/.test(ln)) {
        findings.push(finding(file, fileLine, 'version_directive', isRaw
          ? '#version inside RawShaderMaterial must be the very first line of the string'
          : 'three.js prepends its own #version line to ShaderMaterial shaders; a second one is a compile error — delete it', isRaw ? 'warn' : 'error'));
      }
      if (/^\s*precision\s+(lowp|mediump|highp)\s+float\s*;/.test(ln) && !isRaw) {
        findings.push(finding(file, fileLine, 'precision_directive', 'three.js prepends precision for ShaderMaterial; remove your own precision line', 'warn'));
      }
      if (/\buTime\b/.test(ln) && !firstUTimeLine) firstUTimeLine = fileLine;
    });
    const isFragment = /gl_FragColor|gl_FragCoord|csm_FragColor|pc_fragColor|fragColor|output_fragment|discard/.test(s.text) && !/gl_Position/.test(s.text);
    if (OUT_VEC4_RE.test(s.text) && /gl_FragColor/.test(s.text)) {
      findings.push(finding(file, s.line, 'fragment_out_and_gl_fragcolor', 'fragment declares its own `out vec4` AND writes gl_FragColor — use one: keep gl_FragColor (no out) or set glslVersion: THREE.GLSL3 and write your out variable'));
    }
    if (glsl3 && /gl_FragColor/.test(s.text) && isFragment) {
      findings.push(finding(file, s.line, 'glsl3_gl_fragcolor', 'glslVersion: THREE.GLSL3 removes gl_FragColor — declare `out vec4 fragColor;` and write fragColor (or drop glslVersion)'));
    }
    if (/\buTime\b/.test(s.text)) usesUTime = true;
    if (/uniform\s+float\s+uTime\s*;/.test(s.text)) declaresUTime = true;
  }
  if (usesUTime && !declaresUTime) {
    findings.push(finding(file, firstUTimeLine, 'undeclared_uniform', 'GLSL reads uTime but no string declares `uniform float uTime;` — a JS uniforms entry is not a GLSL declaration'));
  }
  if (usesUTime && !/\buTime\s*[:=]/.test(source.replace(/`[^`]*`/g, ''))) {
    findings.push(finding(file, firstUTimeLine, 'unbound_uniform', 'GLSL declares/reads uTime but JS never binds it (uniforms: { uTime: { value: 0 } } and update .value in update(t))'));
  }
  // onBeforeCompile patches dropping the chunk they replace
  let rm;
  REPLACE_RE.lastIndex = 0;
  while ((rm = REPLACE_RE.exec(source))) {
    const chunk = rm[2];
    const replacement = rm[4];
    const line = source.slice(0, rm.index).split('\n').length;
    if (!new RegExp(`#include\\s*<${chunk}>`).test(replacement)) {
      findings.push(finding(file, line, 'chunk_dropped', `patch replaces '#include <${chunk}>' without keeping it — prepend the original include so fog/logdepth/shadow code still runs: '#include <${chunk}>\\n' + yourCode`, 'warn'));
    }
  }
  // Full ShaderMaterial fragment without fog chunks while the scene uses fog
  const fogOptOut = /fog\s*:\s*false|3dcode:\s*no-fog|\bsky\b/i.test(source);
  if (sceneUsesFog && /ShaderMaterial/.test(source) && !isRaw && !fogOptOut) {
    for (const s of strings) {
      if (/void\s+main/.test(s.text) && /gl_FragColor|fragColor/.test(s.text) && !/fog_fragment|USE_FOG|fogColor/.test(s.text)) {
        findings.push(finding(file, s.line, 'no_fog', 'custom fragment shader ignores scene.fog: add `#include <fog_pars_fragment>` / `#include <fog_fragment>` (+ fog: true and UniformsLib.fog) so it recedes like the rest of the scene', 'warn'));
      }
    }
  }
  return findings;
}

/** True when the file contains at least one GLSL-looking string. */
export function hasGlsl(source) {
  return extractGlslStrings(source).length > 0;
}

/** Locate compiler text in raw shader files or GLSL strings inside JS modules. */
export function locateSourceLine(files, sourceLine) {
  const needle = String(sourceLine || '').trim();
  if (needle.length < 3) return { file: '', line: null, candidates: 0 };
  const hits = [];
  for (const { file, text } of files) {
    const blocks = isShaderSource(file) ? [{ text, line: 1 }] : extractGlslStrings(text);
    for (const s of blocks) {
      s.text.split('\n').forEach((ln, i) => { if (ln.trim() === needle) hits.push({ file, line: s.line + i }); });
    }
  }
  if (!hits.length) return { file: '', line: null, candidates: 0 };
  return { ...hits[0], candidates: hits.length };
}

/** Fix hint for a compile error message (common WebGL GLSL diagnostics). */
export function fixHintFor(message) {
  const m = String(message);
  if (/undeclared identifier/i.test(m)) return 'declare it (uniform/varying/local) or check spelling/case; uniforms must be declared in the GLSL string too';
  if (/syntax error/i.test(m)) return 'check the quoted source line for a missing semicolon/brace or a ${} template that expanded badly';
  if (/no matching overloaded function/i.test(m)) return 'argument types differ (e.g. mix(vec3, vec3, vec3) vs float): cast with float()/vec3()';
  if (/cannot convert|wrong operand types|dimension mismatch/i.test(m)) return 'GLSL has no implicit int↔float or vec size conversion: write 1.0 not 1, vec3(x) not x';
  if (/redefinition|already defined|redeclaration/i.test(m)) return 'you declared something three.js already injects (position, uv, normal, projectionMatrix, modelViewMatrix, cameraPosition, or a chunk varying); delete your declaration';
  if (/l-value|constant expression/i.test(m)) return 'assigning to a read-only/attribute; copy it to a local first';
  if (/gl_FragColor/i.test(m)) return 'with glslVersion GLSL3 write to your own `out vec4`; otherwise drop glslVersion';
  return 'fix the quoted GLSL line; run shader_probe again';
}

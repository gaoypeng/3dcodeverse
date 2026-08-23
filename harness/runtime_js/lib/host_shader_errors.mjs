/**
 * WebGL shader compile/link error capture through `renderer.debug.onShaderError`.
 * Records every `ERROR: n:m: msg` with the assembled source line text so the
 * node side can map it back to the author's file:line (three expands
 * #includes and prepends hundreds of lines, so compiler line numbers alone
 * point nowhere useful).
 */

const ERR_RE = /ERROR:\s*(\d+):(\d+):\s*([^\n]*)/g;

function stageErrors(gl, shader, stage, materialInfo) {
  let log = '';
  try { log = (gl.getShaderInfoLog(shader) || '').trim(); } catch (e) { log = ''; }
  if (!log) return [];
  let lines = [];
  try { lines = (gl.getShaderSource(shader) || '').split('\n'); } catch (e) { lines = []; }
  const out = [];
  let m;
  ERR_RE.lastIndex = 0;
  while ((m = ERR_RE.exec(log))) {
    const ln = parseInt(m[2], 10);
    out.push({
      stage,
      message: m[3].trim(),
      assembled_line: ln,
      source_line: (lines[ln - 1] || '').trim(),
      context: lines.slice(Math.max(0, ln - 3), ln + 2).map((s) => s.trim()),
      ...materialInfo,
    });
  }
  if (!out.length) out.push({ stage, message: log.slice(0, 400), assembled_line: null, source_line: '', context: [], ...materialInfo });
  return out;
}

/** Install the hook; errors are appended to `sink` (array) and deduped. */
export function installShaderErrorHook(renderer, sink) {
  renderer.debug.checkShaderErrors = true;
  renderer.debug.onShaderError = (gl, program, glVS, glFS) => {
    let matInfo = { material: '', material_type: '' };
    try {
      // three keeps the material name/type in the program's cacheKey; parse defensively
      const pl = gl.getProgramInfoLog(program) || '';
      matInfo.program_log = pl.trim().slice(0, 300);
    } catch (e) { /* ignore */ }
    const errs = [...stageErrors(gl, glVS, 'vertex', matInfo), ...stageErrors(gl, glFS, 'fragment', matInfo)];
    if (!errs.length) errs.push({ stage: 'program', message: matInfo.program_log || 'program not valid', assembled_line: null, source_line: '', context: [] });
    for (const e of errs) {
      const key = `${e.stage}|${e.assembled_line}|${e.message}|${e.source_line}`;
      if (!sink.some((s) => s._key === key)) sink.push({ ...e, _key: key });
    }
  };
}

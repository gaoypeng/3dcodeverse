// Turn a JS Error into a structured, workspace-relative error record so the
// python side can route "file:line" to the repair agent.
//
//   import { errorRecord } from './stack.mjs';
//   errorRecord(err, wsDir) -> { type, message, file, line, column, frames: [{file,line,column,fn}], stack }

import path from 'node:path';
import { fileURLToPath } from 'node:url';

const FRAME_RE = /^\s*at\s+(?:(.*?)\s+\()?((?:file:\/\/|\/|[A-Za-z]:\\)[^:)]+):(\d+):(\d+)\)?\s*$/;

function relativeTo(file, wsDir) {
  let p = file;
  if (p.startsWith('file://')) {
    try {
      p = fileURLToPath(p);
    } catch (_e) {
      return file;
    }
  }
  if (wsDir) {
    const rel = path.relative(wsDir, p);
    if (rel && !rel.startsWith('..') && !path.isAbsolute(rel)) return rel;
  }
  return p;
}

/** Parse V8 stack frames into {file,line,column,fn}, workspace-relative where possible. */
export function parseFrames(stack, wsDir, maxFrames = 12) {
  const frames = [];
  for (const line of String(stack || '').split('\n')) {
    const m = FRAME_RE.exec(line);
    if (!m) continue;
    frames.push({ fn: m[1] || '', file: relativeTo(m[2], wsDir), line: Number(m[3]), column: Number(m[4]) });
    if (frames.length >= maxFrames) break;
  }
  return frames;
}

/** Pick the first frame that lives inside the workspace's src/ (the agent's code). */
function firstSrcFrame(frames) {
  return frames.find((f) => !path.isAbsolute(f.file) && !f.file.startsWith('node:')) || null;
}

/** Structured record for an exception (SyntaxError from node has no src frame: parse its message). */
export function errorRecord(err, wsDir) {
  const e = err instanceof Error ? err : new Error(String(err));
  const frames = parseFrames(e.stack, wsDir);
  let src = firstSrcFrame(frames);
  // Node reports ESM syntax errors as "file:///abs/path.js:LINE\n<code>\n  ^^^\nSyntaxError: ..."
  if (!src && e.stack) {
    const m = /^(file:\/\/\S+?|\/\S+?):(\d+)\s*$/m.exec(e.stack);
    if (m) src = { fn: '', file: relativeTo(m[1], wsDir), line: Number(m[2]), column: 0 };
  }
  return {
    type: e.name || e.constructor.name || 'Error',
    message: e.message,
    file: src ? src.file : '',
    line: src ? src.line : null,
    column: src ? src.column : null,
    frames,
    stack: String(e.stack || '').split('\n').slice(0, 20).join('\n'),
  };
}

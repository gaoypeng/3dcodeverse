// Locate ESM syntax errors file-by-file.  V8 SyntaxErrors thrown by a dynamic
// import() carry no file/line, so we re-check each candidate source file with
// `node --input-type=module --check` (stdin) and parse "[stdin]:LINE".
//
//   import { checkSyntax, findSyntaxError } from './syntax_check.mjs';

import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';

/** Check one file; returns null when it parses, else {file, line, message}. */
export function checkSyntax(file) {
  const src = fs.readFileSync(file);
  const r = spawnSync(process.execPath, ['--input-type=module', '--check'], { input: src, encoding: 'utf8', timeout: 20000 });
  if (r.status === 0) return null;
  const err = r.stderr || '';
  const m = /\[stdin\]:(\d+)/.exec(err);
  const msg = (/^(SyntaxError:.*)$/m.exec(err) || [])[1] || err.trim().split('\n').pop() || 'syntax error';
  return { file, line: m ? Number(m[1]) : null, message: msg };
}

/** Recursively list *.js / *.mjs under dir (skipping node_modules). */
export function listJsFiles(dir) {
  const out = [];
  if (!fs.existsSync(dir)) return out;
  const walk = (d) => {
    for (const ent of fs.readdirSync(d, { withFileTypes: true })) {
      if (ent.name === 'node_modules' || ent.name.startsWith('.')) continue;
      const p = path.join(d, ent.name);
      if (ent.isDirectory()) walk(p);
      else if (/\.(m?js)$/.test(ent.name)) out.push(p);
    }
  };
  walk(dir);
  return out.sort();
}

/** First file under srcDir that fails to parse, or null. */
export function findSyntaxError(srcDir) {
  for (const f of listJsFiles(srcDir)) {
    const r = checkSyntax(f);
    if (r) return r;
  }
  return null;
}

// THE JS syntax check: every file parsed as an ES module in ONE node process
// (vm.SourceTextModule: compiled, never linked or run).  That parse error has no line, so
// only a file that fails pays a second process, `node --input-type=module --check`.
//   node --experimental-vm-modules lib/syntax_check.mjs FILE...  → {"bad": [{file, line, message, stderr}]}
//   import { findSyntaxError, listJsFiles } from './syntax_check.mjs';

import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const SELF = fileURLToPath(import.meta.url);

/** `node --check`'s report on one file: null when it parses, else {file, line, message, stderr}. */
function checkOne(file) {
  const r = spawnSync(process.execPath, ['--input-type=module', '--check'], { input: fs.readFileSync(file), encoding: 'utf8', timeout: 20000 });
  if (r.status === 0) return null;
  const err = (r.stderr || '').trim();
  const m = /\[stdin\]:(\d+)/.exec(err);
  const msg = (/^(SyntaxError:.*)$/m.exec(err) || [])[1] || err.split('\n').pop() || 'syntax error';
  return { file, line: m ? Number(m[1]) : null, message: msg, stderr: err.slice(-600) };
}

/** The files of `files` that do not parse as ES modules, in order. */
export function checkFiles(files) {
  const bad = [];
  for (const file of files) {
    try {
      new vm.SourceTextModule(fs.readFileSync(file, 'utf8'), { identifier: file });
    } catch (_e) {
      // node --check decides: it gives a SyntaxError its line, and if vm.SourceTextModule itself
      // is missing (an experimental API) every file still gets a correct answer, just slower
      const hit = checkOne(file);
      if (hit) bad.push(hit);
    }
  }
  return bad;
}

/** Recursively list source suffixes, skipping node_modules and dot entries. */
export function listSourceFiles(dir, suffixes = new Set(['.js', '.mjs'])) {
  const out = [];
  if (!fs.existsSync(dir)) return out;
  const walk = (d) => {
    for (const ent of fs.readdirSync(d, { withFileTypes: true })) {
      if (ent.name === 'node_modules' || ent.name.startsWith('.')) continue;
      const p = path.join(d, ent.name);
      if (ent.isDirectory()) walk(p);
      else if (suffixes.has(path.extname(ent.name))) out.push(p);
    }
  };
  walk(dir);
  return out.sort();
}

/** Only JavaScript is passed to the ES module syntax parser. */
export function listJsFiles(dir) {
  return listSourceFiles(dir);
}

/** First file under srcDir that fails to parse, or null (one child process for all of them:
 *  vm modules need the flag this process was not started with). */
export function findSyntaxError(srcDir) {
  const r = spawnSync(process.execPath, ['--experimental-vm-modules', SELF, ...listJsFiles(srcDir)], { encoding: 'utf8', timeout: 60000 });
  try {
    return JSON.parse((r.stdout || '').trim().split('\n').pop()).bad[0] || null;
  } catch (_e) {
    return null;
  }
}

if (process.argv[1] && fs.realpathSync(process.argv[1]) === SELF) {
  process.stdout.write(JSON.stringify({ bad: checkFiles(process.argv.slice(2)) }) + '\n');
}

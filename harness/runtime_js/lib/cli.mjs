/** Small CLI helpers shared by the scene drivers: args, JSON summary, exits. */

import fs from 'node:fs';
import path from 'node:path';
import { parseArgs } from 'node:util';

/** Parse argv with `node:util.parseArgs`; every option is a string unless boolean. */
export function parseCli(spec, argv = process.argv.slice(2)) {
  const options = {};
  for (const [k, v] of Object.entries(spec)) options[k] = { type: v.type || 'string', default: v.default };
  const { values } = parseArgs({ options, args: argv, allowPositionals: false });
  return values;
}

/**
 * Print the final JSON summary as the LAST stdout line and exit.
 *
 * `process.exit()` does NOT flush a pending stdout write when stdout is a PIPE — node's
 * stdout is asynchronous there — so a summary larger than the pipe buffer is cut mid-JSON
 * and the caller sees no parsable last line.  Measured 2026-09-06 on the starter scene:
 * to a file the summary is 10 462 bytes and parses; through a pipe it is **exactly 8192**
 * and does not.  That is the real mechanism behind every "driver output lost" /
 * "[?] scene did not boot" in `bench/out/scene_baseline` and `scene_textures` — it depends
 * on the census size, not on how busy the box is, which is why it looked like weather.
 *
 * Write with a completion callback and exit from it; `exitCode` is set first so that a
 * process with nothing else pending still ends with the right code.
 */
export function finish(summary, code = 0) {
  process.exitCode = code;
  process.stdout.write(JSON.stringify(summary) + '\n', () => process.exit(code));
}

/** Fail loudly with a JSON summary line (code 2 = could not run). */
export function fail(message, extra = {}) {
  process.stderr.write(`error: ${message}\n`);
  finish({ ok: false, error: message, ...extra }, 2);
}

export function readJsonArg(value, label) {
  if (value === undefined || value === null || value === '') return null;
  try {
    return JSON.parse(value);
  } catch (e) {
    if (fs.existsSync(value)) return JSON.parse(fs.readFileSync(value, 'utf8'));
    throw new Error(`--${label} is neither JSON nor a file: ${String(e.message)}`);
  }
}

export function ensureDir(p) {
  fs.mkdirSync(p, { recursive: true });
  return p;
}

export function writeJson(p, data) {
  fs.mkdirSync(path.dirname(p), { recursive: true });
  fs.writeFileSync(p, JSON.stringify(data, null, 2));
}

export function dataUrlToPng(dataUrl, outPath) {
  const b64 = String(dataUrl).replace(/^data:image\/png;base64,/, '');
  fs.mkdirSync(path.dirname(outPath), { recursive: true });
  fs.writeFileSync(outPath, Buffer.from(b64, 'base64'));
}

/** A name used as a filename component (cameras, views): [A-Za-z0-9_-]{1,64} or throw. */
export function safeName(name, label = 'name') {
  const s = String(name);
  if (!/^[A-Za-z0-9_-]{1,64}$/.test(s)) {
    throw new Error(`unsafe ${label}: ${JSON.stringify(name)} (must match [A-Za-z0-9_-]{1,64})`);
  }
  return s;
}

/** Wall-clock guard: kills the process if the driver exceeds its budget. */
export function armWatchdog(ms, onFire) {
  const t = setTimeout(() => {
    try { onFire && onFire(); } catch (e) { /* ignore */ }
    process.stderr.write(`error: driver exceeded ${ms} ms budget\n`);
    finish({ ok: false, error: `timeout after ${ms} ms` }, 3);   // flushes; see finish()
  }, ms);
  t.unref();
  return t;
}

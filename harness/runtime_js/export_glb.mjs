#!/usr/bin/env node
// Export an agent-authored three.js object module to a canonical GLB + census.
//
//   node --import runtime_js/lib/resolve_three.mjs runtime_js/export_glb.mjs \
//        --ws <workspace> [--entry src/object.js] [--out artifacts/object.glb] [--census artifacts/census.json]
//
// Runs in plain node (no browser).  The agent module must `export function build(THREE)`
// returning a THREE.Group (or a Promise of one).  Last stdout line is a JSON record:
//   ok:true  -> {ok, glb, census, duration_ms}
//   ok:false -> {ok:false, error:{type,message,file,line,frames,stack}, warnings}
// and the same error record is written next to the census as export_error.json.

import fs from 'node:fs';
import path from 'node:path';
import { parseArgs } from 'node:util';
import { pathToFileURL } from 'node:url';

import { installExporterPolyfills } from './lib/node_polyfills.mjs';
import { objectCensus, hasNonFinitePositions } from './lib/census.mjs';
import { errorRecord } from './lib/stack.mjs';
import { findSyntaxError } from './lib/syntax_check.mjs';

const TEXTURE_SLOTS = ['map', 'normalMap', 'roughnessMap', 'metalnessMap', 'emissiveMap', 'aoMap',
  'bumpMap', 'displacementMap', 'alphaMap', 'envMap', 'lightMap', 'specularMap', 'clearcoatMap',
  'clearcoatNormalMap', 'clearcoatRoughnessMap', 'sheenColorMap', 'transmissionMap', 'thicknessMap'];
const NORMALISE_THRESHOLD_M = 0.01;

function cli() {
  const { values } = parseArgs({
    options: {
      ws: { type: 'string' },
      entry: { type: 'string', default: 'src/object.js' },
      out: { type: 'string', default: 'artifacts/object.glb' },
      census: { type: 'string', default: 'artifacts/census.json' },
      normalise: { type: 'string', default: '1' },
    },
  });
  if (!values.ws) throw new Error('--ws <workspace dir> is required');
  const ws = path.resolve(values.ws);
  const abs = (p) => (path.isAbsolute(p) ? p : path.join(ws, p));
  return {
    ws,
    entry: abs(values.entry),
    out: abs(values.out),
    census: abs(values.census),
    normalise: values.normalise !== '0',
  };
}

function emit(obj) {
  process.stdout.write(JSON.stringify(obj) + '\n');
}

/** Capture console.warn/error from three (e.g. unsupported materials) as warnings. */
function captureWarnings(sink) {
  const origWarn = console.warn;
  const origError = console.error;
  console.warn = (...a) => {
    sink.push(a.map(String).join(' '));
    origWarn.apply(console, a);
  };
  console.error = (...a) => {
    sink.push(a.map(String).join(' '));
    origError.apply(console, a);
  };
}

async function loadBuild(entry) {
  if (!fs.existsSync(entry)) {
    const e = new Error(`entry module not found: ${entry}`);
    e.name = 'MissingEntry';
    throw e;
  }
  const mod = await import(pathToFileURL(entry).href);
  if (typeof mod.build !== 'function') {
    const e = new Error(`${path.basename(entry)} must 'export function build(THREE)' (got ${typeof mod.build})`);
    e.name = 'ContractError';
    throw e;
  }
  return mod.build;
}

function validateGroup(THREE, group, entry) {
  const fail = (msg) => {
    const e = new Error(msg);
    e.name = 'ContractError';
    throw e;
  };
  if (!group || !group.isObject3D) fail(`build(THREE) must return a THREE.Group (got ${group && group.constructor ? group.constructor.name : typeof group})`);
  let meshes = 0;
  group.traverse((o) => {
    if (o.isMesh || o.isInstancedMesh || o.isSkinnedMesh) meshes += 1;
  });
  if (meshes === 0) fail('build(THREE) returned a group with no meshes');
  if (hasNonFinitePositions(group)) fail('geometry contains NaN/Infinity vertex positions (check divisions by zero, radius 0, empty shapes)');
  const box = new THREE.Box3().setFromObject(group, true);
  if (box.isEmpty() || !Number.isFinite(box.min.x) || !Number.isFinite(box.max.x)) fail('object has an empty or non-finite bounding box');
  if (!group.name) group.name = path.basename(path.dirname(entry)) === 'src' ? 'Object' : path.basename(entry, '.js');
}

/** Shift the group so it stands on y=0 centred on the Y axis; returns the applied offset or null. */
function normalise(THREE, group, enabled) {
  const box = new THREE.Box3().setFromObject(group, true);
  const c = box.getCenter(new THREE.Vector3());
  const offset = new THREE.Vector3(-c.x, -box.min.y, -c.z);
  if (!enabled || offset.length() <= NORMALISE_THRESHOLD_M) return null;
  group.position.add(offset);
  group.updateWorldMatrix(true, true);
  return offset.toArray().map((v) => +v.toFixed(5));
}

function stripTextures(group, warnings) {
  let stripped = 0;
  group.traverse((o) => {
    const mats = Array.isArray(o.material) ? o.material : o.material ? [o.material] : [];
    for (const m of mats) {
      for (const slot of TEXTURE_SLOTS) {
        if (m[slot]) {
          m[slot] = null;
          stripped += 1;
        }
      }
    }
  });
  if (stripped) warnings.push(`stripped ${stripped} texture slot(s): textures are not exportable from node (use vertex colours / materials)`);
}

let ARGS = null;

async function main() {
  const t0 = Date.now();
  const args = cli();
  ARGS = args;
  const warnings = [];
  captureWarnings(warnings);
  installExporterPolyfills();

  const THREE = await import('three');
  const { GLTFExporter } = await import('three/addons/exporters/GLTFExporter.js');

  const build = await loadBuild(args.entry);
  let group = build(THREE);
  if (group && typeof group.then === 'function') group = await group;
  validateGroup(THREE, group, args.entry);

  const tickPresent = typeof (group.userData && group.userData.tick) === 'function';
  if (tickPresent) delete group.userData.tick; // functions cannot be serialised into glTF extras

  const normalisedOffset = normalise(THREE, group, args.normalise);
  if (normalisedOffset) warnings.push(`object was off ground/centre by ${JSON.stringify(normalisedOffset)} m; translated so min.y=0 and xz-centre=0 (fix the source: the object must stand on y=0 centred on the Y axis)`);
  stripTextures(group, warnings);

  const census = objectCensus(THREE, group);
  census.normalised_offset = normalisedOffset;
  census.tick_present = tickPresent;
  let unnamed = 0;
  group.traverse((o) => {
    if ((o.isMesh || o.isInstancedMesh) && !o.name) unnamed += 1;
  });
  census.unnamed_meshes = unnamed;
  if (unnamed) warnings.push(`${unnamed} mesh(es) have no .name; name every mesh (e.g. 'SeatCushion') so the census and judge can refer to them`);

  const exporter = new GLTFExporter();
  const glb = await exporter.parseAsync(group, { binary: true, onlyVisible: true, trs: false });
  if (!(glb instanceof ArrayBuffer) || glb.byteLength < 20) {
    const e = new Error('GLTFExporter produced no binary output');
    e.name = 'ExportError';
    throw e;
  }
  fs.mkdirSync(path.dirname(args.out), { recursive: true });
  fs.writeFileSync(args.out, Buffer.from(glb));

  census.warnings = warnings;
  census.glb_bytes = glb.byteLength;
  census.three_revision = THREE.REVISION;
  fs.mkdirSync(path.dirname(args.census), { recursive: true });
  fs.writeFileSync(args.census, JSON.stringify(census, null, 2));

  emit({ ok: true, glb: args.out, census: args.census, duration_ms: Date.now() - t0,
    tri_count: census.tri_count, parts: census.parts.length, warnings });
}

main().catch((err) => {
  const ws = ARGS ? ARGS.ws : '';
  const record = { ok: false, error: errorRecord(err, ws) };
  // ESM SyntaxErrors from import() carry no location: find the offending file ourselves.
  if (record.error.type === 'SyntaxError' && !record.error.file && ARGS) {
    const hit = findSyntaxError(path.dirname(ARGS.entry));
    if (hit) {
      record.error.file = path.relative(ws, hit.file);
      record.error.line = hit.line;
      record.error.message = `${hit.message} (in ${record.error.file}:${hit.line})`;
    }
  }
  try {
    if (ARGS) {
      fs.mkdirSync(path.dirname(ARGS.census), { recursive: true });
      fs.writeFileSync(path.join(path.dirname(ARGS.census), 'export_error.json'), JSON.stringify(record, null, 2));
    }
  } catch (_e) {
    /* reporting must never mask the original failure */
  }
  emit(record);
  process.exit(1);
});

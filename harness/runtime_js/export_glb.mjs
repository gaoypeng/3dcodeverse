#!/usr/bin/env node
// Export an agent-authored three.js object module to a canonical GLB + census.
//
//   node --import runtime_js/lib/resolve_three.mjs runtime_js/export_glb.mjs \
//        --ws <workspace> [--entry src/object.js] [--out artifacts/object.glb] [--census artifacts/census.json]
//
// Runs in plain node (no browser).  The agent module must `export function build(THREE)`
// returning a THREE.Group (or a Promise of one); an optional `export function
// selfcheck(THREE, root)` is called on the built group and fails the build when it
// throws.  InstancedMesh objects are baked into named plain meshes (lib/instances.mjs)
// so trimesh-based gates see every copy.  The object is exported exactly where the
// source put it: an off-ground / off-centre build only WARNS (census.placement_offset),
// like the Blender/CadQuery wrappers, so plan-frame gates stay valid.
// Last stdout line is a JSON record:
//   ok:true  -> {ok, glb, census, duration_ms}
//   ok:false -> {ok:false, error:{type,message,file,line,frames,stack,part?}, warnings}
// (`part` = the plan part holding the offending mesh, for file routing on the python
// side) and the same error record is written next to the census as export_error.json.

import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

import { finish, parseCli } from './lib/cli.mjs';
import { installExporterPolyfills } from './lib/node_polyfills.mjs';
import { findNonFinitePositions, worldBox } from './lib/census.mjs';
import { bakeInstancedMeshes, expandInstancedMesh } from './lib/instances.mjs';
import { errorRecord } from './lib/stack.mjs';
import { findSyntaxError } from './lib/syntax_check.mjs';

const TEXTURE_SLOTS = ['map', 'normalMap', 'roughnessMap', 'metalnessMap', 'emissiveMap', 'aoMap',
  'bumpMap', 'displacementMap', 'alphaMap', 'envMap', 'lightMap', 'specularMap', 'clearcoatMap',
  'clearcoatNormalMap', 'clearcoatRoughnessMap', 'sheenColorMap', 'transmissionMap', 'thicknessMap'];
//: placement offsets below this are not worth a warning (1 cm)
const PLACEMENT_THRESHOLD_M = 0.01;

function cli() {
  const values = parseCli({
    ws: {}, entry: { default: 'src/object.js' }, out: { default: 'artifacts/object.glb' },
    census: { default: 'artifacts/census.json' },
  });
  if (!values.ws) throw new Error('--ws <workspace dir> is required');
  const ws = path.resolve(values.ws);
  const abs = (p) => (path.isAbsolute(p) ? p : path.join(ws, p));
  return {
    ws,
    entry: abs(values.entry),
    out: abs(values.out),
    census: abs(values.census),
  };
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
    e.name = 'MissingEntryFile';  // same spelling as every python runtime's build.json
    throw e;
  }
  const mod = await import(pathToFileURL(entry).href);
  if (typeof mod.build !== 'function') {
    const e = new Error(`${path.basename(entry)} must 'export function build(THREE)' (got ${typeof mod.build})`);
    e.name = 'ContractError';
    throw e;
  }
  return mod;
}

/** Throw a ContractError; `part` (plan part name) lets the python side route it to src/parts/<snake>.js. */
function fail(msg, part = '') {
  const e = new Error(msg);
  e.name = 'ContractError';
  if (part) e.part = part;
  throw e;
}

function describeMesh(hit) {
  const where = hit.ancestor && hit.ancestor !== hit.name ? ` (in part '${hit.ancestor}')` : '';
  return `${hit.type} '${hit.name || '<unnamed>'}'${where}`;
}

/** First direct child of `group` with an empty / non-finite world box, or null. */
function firstBadBoxPart(THREE, group) {
  for (const child of group.children) {
    const box = worldBox(THREE, child);
    if (!box || !box.flat().every(Number.isFinite)) return child;
  }
  return null;
}

function validateGroup(THREE, group, entry) {
  if (!group || !group.isObject3D) fail(`build(THREE) must return a THREE.Group (got ${group && group.constructor ? group.constructor.name : typeof group})`);
  let meshes = 0;
  group.traverse((o) => {
    if (o.isInstancedMesh) meshes += o.count > 0 ? 1 : 0; // (isMesh is true for InstancedMesh too)
    else if (o.isMesh || o.isSkinnedMesh) meshes += 1;
  });
  if (meshes === 0) fail('build(THREE) returned a group with no meshes');
  const nan = findNonFinitePositions(group);
  if (nan) fail(`${describeMesh(nan)} has NaN/Infinity vertex positions (check divisions by zero, radius 0, empty shapes)`, nan.part);
  const box = new THREE.Box3().setFromObject(group, true);
  if (box.isEmpty() || !Number.isFinite(box.min.x) || !Number.isFinite(box.max.x)) {
    const bad = firstBadBoxPart(THREE, group);
    const who = bad ? `part '${bad.name || '<unnamed>'}' (${bad.type}) has` : 'object has';
    fail(`${who} an empty or non-finite bounding box (NaN position/scale/matrix or no geometry)`, bad ? bad.name : '');
  }
  if (!group.name) group.name = path.basename(path.dirname(entry)) === 'src' ? 'Object' : path.basename(entry, '.js');
}

/** Run the module's optional `selfcheck(THREE, root)`; a throw fails the build with the agent's message. */
async function runSelfcheck(mod, THREE, group) {
  if (typeof mod.selfcheck !== 'function') return false;
  try {
    await mod.selfcheck(THREE, group);
  } catch (err) {
    const e = err instanceof Error ? err : new Error(String(err));
    e.message = `selfcheck(THREE, root) threw: ${e.message}`;
    if (!e.name || e.name === 'Error') e.name = 'SelfCheckError';
    throw e;
  }
  return true;
}

/**
 * Offset that WOULD stand the object on y=0 centred on the Y axis (null when within
 * PLACEMENT_THRESHOLD_M).  Never applied: the GLB keeps the source placement so
 * check_contract compares in the plan's frame (law 7).
 */
function placement(THREE, group) {
  const box = new THREE.Box3().setFromObject(group, true);
  const c = box.getCenter(new THREE.Vector3());
  const offset = new THREE.Vector3(-c.x, -box.min.y, -c.z);
  return offset.length() <= PLACEMENT_THRESHOLD_M ? null : offset.toArray().map((v) => +v.toFixed(5));
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

  const mod = await loadBuild(args.entry);
  let group = mod.build(THREE);
  if (group && typeof group.then === 'function') group = await group;
  validateGroup(THREE, group, args.entry);
  const selfchecked = await runSelfcheck(mod, THREE, group);
  let bakedInstances = 0;
  if (group.isInstancedMesh) {
    group = expandInstancedMesh(THREE, group); // a bare InstancedMesh root has no parent to swap it in
    bakedInstances += 1;
  }
  bakedInstances += bakeInstancedMeshes(THREE, group);

  const tickPresent = typeof (group.userData && group.userData.tick) === 'function';
  if (tickPresent) delete group.userData.tick; // functions cannot be serialised into glTF extras

  const offset = placement(THREE, group);
  if (offset) warnings.push(`object is off ground/centre: it needs a translation of ${JSON.stringify(offset)} m to stand on y=0 centred on the Y axis; exported as authored — fix the source (the contract gate reports this too)`);
  stripTextures(group, warnings);

  // what only the export knows; the object's measurements are read off the GLB (spatial/measure.py)
  const census = { placement_offset: offset, instanced_meshes_baked: bakedInstances,
    selfcheck_ran: selfchecked, tick_present: tickPresent };
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

  finish({ ok: true, glb: args.out, census: args.census, duration_ms: Date.now() - t0, warnings });
}

main().catch((err) => {
  const ws = ARGS ? ARGS.ws : '';
  const record = { ok: false, error: errorRecord(err, ws) };
  if (err && typeof err.part === 'string' && err.part) record.error.part = err.part;
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
  finish(record, 1);
});

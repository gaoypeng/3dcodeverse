/**
 * Page-side host for `scene_threejs` workspaces (runs in headless Chrome).
 *
 * Boots a WebGLRenderer with the harness's fixed settings (antialias, ACES,
 * sRGB, shadow maps), imports the agent's `src/scene.js` through the import
 * map, calls `createScene({THREE, renderer, loaders})`, validates the returned
 * shape and exposes `window.__c3v` with deterministic instruments:
 *   boot(opts) · renderAt(cameraSpec, t) · census() · fps(seconds)
 *   cameraChecks(cameraSpec) (near geometry + luminance + content coverage)
 *   compileAll(cameraSpec) · shaderErrors()
 * Node drivers (render_scene / probe_scene / check_shaders) call these via
 * page.evaluate.  Agent code never imports this file.
 */

import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { sceneCensus } from './host_census.mjs';
import { placementTable } from './host_placement.mjs';
import { frameStats, nearGeometry } from './host_metrics.mjs';
import { frameCoverage } from './host_coverage.mjs';
import { installShaderErrorHook } from './host_shader_errors.mjs';
import { attributeErrors, captured, captureMaterialSources, materialAudit, stripCustomShaders } from './host_compile.mjs';
import { makeRenderer, rendererString } from './browser/renderer.js';

const FIXED_DT = 1 / 30;
const LOAD_IDLE_TIMEOUT_MS = 30000;
const CREATE_SCENE_TIMEOUT_MS = 20000;

const state = {
  booted: false,
  renderer: null,
  canvas: null,
  scene: null,
  cameras: [],
  update: null,
  updateBroken: false,
  updateErrors: [],
  simTime: 0,
  width: 1024,
  height: 576,
  rafCalls: 0,
  shaderErrors: [],
  loadErrors: [],
  pendingLoads: new Set(),
  hostWarnings: [],
  bootInfo: null,
  contentBox: null,
  fullBox: null,
};

/**
 * Call the agent's update(t, dt), catching exceptions: the first throw is
 * recorded (state.updateErrors + console.error so the node driver collects it
 * as a console error → gate finding) and update() is disabled so the remaining
 * views still render — an agent bug must never abort the whole render run.
 */
function runUpdate(t, dt) {
  if (!state.update || state.updateBroken) return;
  try {
    state.update(t, dt);
  } catch (e) {
    state.updateBroken = true;
    const msg = `update(t=${t.toFixed(2)}) threw: ${formatError(e)} — update() disabled, rendering continues without animation`;
    state.updateErrors.push(msg);
    console.error(msg);
  }
}

/** Host canvas + the shared harness renderer (lib/browser/renderer.js). */
function makeHostRenderer(width, height, opts) {
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  canvas.id = '3dcv-canvas';
  document.body.appendChild(canvas);
  return { renderer: makeRenderer(canvas, width, height, { logDepth: !!opts.logDepth }), canvas };
}

function validateCameras(raw) {
  const out = [];
  const problems = [];
  if (!Array.isArray(raw)) {
    problems.push('createScene().cameras is not an array');
    return { cameras: out, problems };
  }
  raw.forEach((c, i) => {
    const p = c && c.position;
    const l = c && c.lookAt;
    const okVec = (v) => Array.isArray(v) && v.length === 3 && v.every((x) => Number.isFinite(x));
    if (!c || typeof c !== 'object') { problems.push(`cameras[${i}] is not an object`); return; }
    if (!okVec(p)) { problems.push(`cameras[${i}] position must be [x,y,z] finite numbers`); return; }
    if (!okVec(l)) { problems.push(`cameras[${i}] lookAt must be [x,y,z] finite numbers`); return; }
    const fov = Number.isFinite(c.fov) ? c.fov : 50;
    if (fov < 5 || fov > 150) problems.push(`cameras[${i}] fov ${fov} outside [5,150]`);
    if (p.every((x, k) => Math.abs(x - l[k]) < 1e-6)) problems.push(`cameras[${i}] position equals lookAt`);
    out.push({ name: String(c.name || `cam_${i}`), position: p.map(Number), lookAt: l.map(Number), fov });
  });
  if (out.length === 0) problems.push('no valid cameras (author 1-6 {name, position, lookAt, fov})');
  if (out.length > 6) problems.push(`too many cameras (${out.length} > 6)`);
  return { cameras: out, problems };
}

// Every GLB the scene asked for, with the geometry it brought: {url, geometry_uuids}.
// Reset per boot; read by census() to answer "was this asset actually USED?".
let loadedGlbs = [];

function makeLoaders(manager) {
  const gltf = new GLTFLoader(manager);
  // Record what each GLB load delivered.  `Loader.loadAsync` is a promise wrapper around
  // `this.load`, so wrapping load alone covers both call styles.
  const origLoad = gltf.load.bind(gltf);
  gltf.load = (url, onLoad, onProgress, onError) => origLoad(url, (res) => {
    const uuids = [];
    try { res?.scene?.traverse((o) => { if (o.geometry && o.geometry.uuid) uuids.push(o.geometry.uuid); }); } catch { /* census is best-effort */ }
    loadedGlbs.push({ url: String(url), geometry_uuids: uuids });
    if (onLoad) onLoad(res);
  }, onProgress, onError);
  const texture = new THREE.TextureLoader(manager);
  const cube = new THREE.CubeTextureLoader(manager);
  return { gltf, texture, cube, manager };
}

/** Per loaded GLB: did any of its geometry reach the rendered scene?
 *
 * `Object3D.clone()` SHARES BufferGeometry (Mesh.copy assigns the same reference), so an
 * asset used via clone still matches — which is how the scene track normally places a
 * hero.  A procedural rebuild allocates its own geometry and matches nothing, which is
 * exactly the case this exists to catch.  A deep `geometry.clone()` would read as unused;
 * that is why the finding is a WARN and says "no geometry from it", not "not used".
 */
function glbUsage(scene) {
  const used = new Set();
  try { scene.traverse((o) => { if (o.geometry && o.geometry.uuid) used.add(o.geometry.uuid); }); } catch { return []; }
  return loadedGlbs.map((g) => {
    const n = g.geometry_uuids.filter((u) => used.has(u)).length;
    return { url: g.url, meshes: g.geometry_uuids.length, meshes_in_scene: n, in_scene: n > 0 };
  });
}

function waitForLoads(manager, timeoutMs) {
  return new Promise((resolve) => {
    let done = false;
    const finish = (why) => { if (!done) { done = true; resolve(why); } };
    // If nothing is loading the manager never fires onLoad; check idle flag.
    const t0 = performance.now();
    const tick = () => {
      if (done) return;
      if (!manager.__c3vPending) return finish('idle');
      if (performance.now() - t0 > timeoutMs) return finish('timeout');
      setTimeout(tick, 50);
    };
    tick();
  });
}

/**
 * Boot the host: renderer → import module → createScene → wait loads.
 * @param {{sceneUrl:string,width?:number,height?:number,logDepth?:boolean}} opts
 */
async function boot(opts) {
  const t0 = performance.now();
  const info = { ok: false, stage: 'init', error: '', cameras: [], camera_problems: [], shape: {}, timings_ms: {}, renderer: '' };
  state.bootInfo = info;
  try {
    loadedGlbs = [];
    state.width = opts.width || 1024;
    state.height = opts.height || 576;
    window.requestAnimationFrame = () => { state.rafCalls += 1; return 0; };
    window.cancelAnimationFrame = () => {};
    const { renderer, canvas } = makeHostRenderer(state.width, state.height, opts);
    state.renderer = renderer;
    state.canvas = canvas;
    installShaderErrorHook(renderer, state.shaderErrors);
    try {
      info.renderer = rendererString(renderer);
    } catch (e) { info.renderer = 'unknown'; }

    info.stage = 'import';
    const tImp = performance.now();
    const mod = await import(opts.sceneUrl);
    info.timings_ms.import = Math.round(performance.now() - tImp);
    if (typeof mod.createScene !== 'function') {
      throw new Error(`src/scene.js must export function createScene({THREE, renderer, loaders}); exports found: [${Object.keys(mod).join(', ')}]`);
    }

    info.stage = 'createScene';
    const manager = new THREE.LoadingManager();
    manager.__c3vPending = false;
    manager.onStart = () => { manager.__c3vPending = true; };
    manager.onLoad = () => { manager.__c3vPending = false; };
    manager.onError = (url) => { const m = `failed to load ${url}`; if (!state.loadErrors.includes(m)) state.loadErrors.push(m); };
    trackPendingLoads(manager);
    const loaders = makeLoaders(manager);
    const tCs = performance.now();
    const result = await raceCreateScene(mod, { THREE, renderer, loaders }, opts.createSceneTimeoutMs || CREATE_SCENE_TIMEOUT_MS);
    info.timings_ms.create_scene = Math.round(performance.now() - tCs);
    if (!result || typeof result !== 'object') throw new Error('createScene() must return {scene, cameras, update}');
    info.shape = {
      has_scene: !!(result.scene && result.scene.isScene),
      has_cameras: Array.isArray(result.cameras),
      has_update: typeof result.update === 'function',
    };
    if (!info.shape.has_scene) throw new Error('createScene().scene is not a THREE.Scene');
    const { cameras, problems } = validateCameras(result.cameras);
    info.cameras = cameras;
    info.camera_problems = problems;
    state.scene = result.scene;
    state.cameras = cameras;
    state.update = typeof result.update === 'function' ? result.update : null;
    if (!state.update) info.camera_problems.push('createScene().update(t, dt) is not a function (scene will be static)');

    info.stage = 'loads';
    const tLd = performance.now();
    info.load_wait = await waitForLoads(manager, LOAD_IDLE_TIMEOUT_MS);
    info.timings_ms.loads = Math.round(performance.now() - tLd);
    if (info.load_wait === 'timeout') state.hostWarnings.push('asset loads still pending after timeout');

    info.stage = 'first_update';
    runUpdate(0, 0);
    state.simTime = 0;
    state.scene.updateMatrixWorld(true);
    state.booted = true;
    info.ok = cameras.length > 0;
    info.stage = 'ready';
  } catch (e) {
    info.error = formatError(e);
  }
  info.timings_ms.total = Math.round(performance.now() - t0);
  info.raf_calls = state.rafCalls;
  info.load_errors = state.loadErrors.slice();
  info.host_warnings = state.hostWarnings.slice();
  return info;
}

function formatError(e) {
  if (!e) return 'unknown error';
  const msg = String(e.message || e);
  const stack = String(e.stack || '').split('\n').slice(0, 6).join('\n');
  return stack && stack.includes(msg) ? stack : `${msg}\n${stack}`;
}

/** Track which loader URLs are in flight so a boot timeout can name them. */
function trackPendingLoads(manager) {
  const start = manager.itemStart.bind(manager);
  const end = manager.itemEnd.bind(manager);
  manager.itemStart = (url) => { state.pendingLoads.add(url); start(url); };
  manager.itemEnd = (url) => { state.pendingLoads.delete(url); end(url); };
}

/**
 * Await the agent's createScene() with a hard timeout: a classic
 * `await new Promise(r => loaders.texture.load(url, r))` on a missing asset
 * never settles (three's loaders skip onLoad on 404) and would otherwise hang
 * the whole driver into its watchdog (exit 3, blamed on the harness).  On
 * expiry the boot fails at stage 'createScene' with an error naming the
 * pending loader URLs — an agent-fixable scene failure, not a driver one.
 */
function raceCreateScene(mod, ctx, timeoutMs) {
  const createScenePromise = Promise.resolve().then(() => mod.createScene(ctx));
  createScenePromise.catch(() => {});   // no unhandled rejection if the timeout wins
  let timer = null;
  const timeout = new Promise((resolve, reject) => {
    timer = setTimeout(() => {
      const pending = [...state.pendingLoads].slice(0, 5);
      let msg = `createScene() did not resolve within ${Math.round(timeoutMs / 1000)} s — an awaited promise never settled `
        + '(loader.load(...) without an onError handler on a missing/broken asset? prefer await loaders.gltf.loadAsync(url) in try/catch)';
      if (pending.length) msg += `; pending loads: ${pending.join(', ')}`;
      if (state.loadErrors.length) msg += `; load errors: ${state.loadErrors.slice(0, 5).join('; ')}`;
      reject(new Error(msg));
    }, timeoutMs);
  });
  return Promise.race([createScenePromise, timeout]).finally(() => clearTimeout(timer));
}

function buildCamera(spec) {
  const aspect = state.width / state.height;
  if (!state.fullBox) { const c = sceneCensus(state.scene, THREE); state.fullBox = c.bbox; state.contentBox = c.content_bbox; }
  const fb = state.fullBox;
  let farCorner = 1000;
  if (fb) {
    farCorner = 0;
    for (const x of [fb.min[0], fb.max[0]]) for (const y of [fb.min[1], fb.max[1]]) for (const z of [fb.min[2], fb.max[2]]) {
      farCorner = Math.max(farCorner, Math.hypot(x - spec.position[0], y - spec.position[1], z - spec.position[2]));
    }
  }
  const far = Number.isFinite(spec.far) ? spec.far : Math.max(1000, farCorner * 1.25 + 10);
  const near = Number.isFinite(spec.near) ? spec.near : Math.max(0.05, Math.min(0.5, far / 20000));
  const cam = new THREE.PerspectiveCamera(spec.fov || 50, aspect, near, far);
  cam.position.set(...spec.position);
  cam.lookAt(...spec.lookAt);
  cam.updateMatrixWorld(true);
  cam.updateProjectionMatrix();
  return cam;
}

/** Advance the simulation deterministically to time t (fixed steps).
 * Agent update() exceptions are recorded (runUpdate) and never propagate. */
function advanceTo(t) {
  if (!state.update) return;
  if (t < state.simTime - 1e-9) throw new Error(`cannot rewind time: at ${state.simTime}, asked ${t}`);
  let steps = 0;
  while (state.simTime + FIXED_DT <= t + 1e-9) {
    state.simTime += FIXED_DT;
    runUpdate(state.simTime, FIXED_DT);
    steps += 1;
    if (steps > 100000) throw new Error('too many update steps');
  }
  const rem = t - state.simTime;
  if (rem > 1e-6) {
    state.simTime = t;
    runUpdate(t, rem);
  }
}

function renderOnce(cam) {
  state.renderer.render(state.scene, cam);
  try { const gl = state.renderer.getContext(); if (gl && gl.finish) gl.finish(); } catch (e) { /* ignore */ }
}

/** Render camera spec at time t; returns {dataUrl, ms}. */
function renderAt(spec, t, opts = {}) {
  if (!state.booted) throw new Error('host not booted');
  advanceTo(t);
  state.scene.updateMatrixWorld(true);
  const cam = buildCamera(spec);
  const t0 = performance.now();
  const savedFog = state.scene.fog;
  if (spec.noFog) state.scene.fog = null;   // overview rig: structure over atmosphere
  const undo = opts.stripCustom ? stripCustomShaders(state.scene, THREE) : null;
  try {
    renderOnce(cam);
  } finally {
    state.scene.fog = savedFog;
    if (undo) undo();
  }
  const ms = Math.round(performance.now() - t0);
  return { dataUrl: state.canvas.toDataURL('image/png'), ms, sim_time: state.simTime };
}

/**
 * Camera instruments (renders): near geometry, frame luminance stats and
 * coverage (content / ground / sky fractions of the frame, via mask passes).
 */
function cameraChecks(spec) {
  const cam = buildCamera(spec);
  state.scene.updateMatrixWorld(true);
  const near = nearGeometry(state.scene, cam, THREE);
  renderOnce(cam);
  const stats = frameStats(state.canvas);
  let coverage = {};
  try {
    coverage = frameCoverage(state.renderer, state.scene, cam, state.canvas, THREE, state.contentBox);
  } catch (e) {
    state.hostWarnings.push(`coverage failed for ${spec.name}: ${e.message}`);
  }
  return { name: spec.name, ...near, ...stats, ...coverage };
}

/** Force-compile every material as seen from spec (or the first camera). */
function compileAll(spec) {
  const cam = buildCamera(spec || state.cameras[0] || { position: [10, 10, 10], lookAt: [0, 0, 0], fov: 50 });
  captureMaterialSources(state.scene, THREE);
  const t0 = performance.now();
  state.renderer.compile(state.scene, cam);
  renderOnce(cam);
  // shadow + depth programs compile lazily; a second frame from another spot
  if (state.cameras.length > 1) renderOnce(buildCamera(state.cameras[state.cameras.length - 1]));
  const ms = Math.round(performance.now() - t0);
  attributeErrors(state.shaderErrors);
  const programs = state.renderer.info.programs ? state.renderer.info.programs.length : null;
  return { compile_ms: ms, programs, shader_errors: state.shaderErrors.slice(), material_audit: materialAudit(state.scene, THREE), custom_materials: captured.length };
}

/** Measure update+render throughput over ~seconds (advances sim time). */
function fps(seconds, spec) {
  const cam = buildCamera(spec || state.cameras[0]);
  const deadline = performance.now() + seconds * 1000;
  let frames = 0;
  const t0 = performance.now();
  while (performance.now() < deadline) {
    state.simTime += FIXED_DT;
    runUpdate(state.simTime, FIXED_DT);
    state.renderer.render(state.scene, cam);
    frames += 1;
  }
  try { state.renderer.getContext().finish(); } catch (e) { /* ignore */ }
  const elapsed = (performance.now() - t0) / 1000;
  const r = state.renderer.info.render;
  return { fps: frames / Math.max(elapsed, 1e-3), frames, seconds: elapsed, draw_calls: r.calls, triangles: r.triangles };
}

/** Per-asset placement table (host_placement.mjs) — never throws: a failure is `{error}`. */
function placement() {
  try {
    const c = sceneCensus(state.scene, THREE);
    return placementTable(state.scene, THREE, { groundY: c.ground_y, contentBox: c.content_bbox });
  } catch (e) {
    return { error: String((e && e.message) || e).slice(0, 400) };
  }
}

function census() {
  const c = sceneCensus(state.scene, THREE);
  c.glb_assets = glbUsage(state.scene);
  state.contentBox = c.content_bbox;
  state.fullBox = c.bbox;
  c.cameras = state.cameras.length;
  c.raf_calls = state.rafCalls;
  return c;
}

window.__c3v = {
  boot,
  renderAt,
  cameraChecks,
  compileAll,
  census,
  placement,
  fps,
  setViewport(w, h) { state.width = w; state.height = h; state.renderer.setSize(w, h, false); },
  shaderErrors: () => state.shaderErrors.slice(),
  loadErrors: () => state.loadErrors.slice(),
  updateErrors: () => state.updateErrors.slice(),
  cameras: () => state.cameras.slice(),
  simTime: () => state.simTime,
  THREE,
  get scene() { return state.scene; },
};
window.__c3v_ready = true;

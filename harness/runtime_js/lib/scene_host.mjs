/**
 * Page-side host for `scene_threejs` workspaces (runs in headless Chrome).
 *
 * Boots a WebGLRenderer with the harness's fixed settings (antialias, ACES,
 * sRGB, shadow maps), imports the agent's `src/scene.js` through the import
 * map, calls `createScene({THREE, renderer, loaders})`, validates the returned
 * shape and exposes `window.__c3v` with deterministic instruments:
 * Scene renders go through the post chain (browser/post.js: GTAO + soft bloom +
 * grade, `post: false` to disable); the coverage mask passes stay raw.
 *   boot(opts) · renderAt(cameraSpec, t) · census() · fps(seconds)
 *   cameraChecks(cameraSpec) (near geometry + luminance + content coverage)
 *   compileAll(cameraSpec) · shaderErrors()
 * Node drivers (render_scene / probe_scene / check_shaders) call these via
 * page.evaluate.  Agent code never imports this file.
 */

import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { sceneCensus } from './host_census.mjs';
import { settleScene } from './host_placement.mjs';
import { frameStats, nearGeometry, repairCameraSpec } from './host_metrics.mjs';
import { frameCoverage, glbCoverage } from './host_coverage.mjs';
import { classifyBackdrop, nonSolid, drawableBox } from './backdrop.mjs';
import { installShaderErrorHook } from './host_shader_errors.mjs';
import { attributeErrors, captured, captureMaterialSources, materialAudit } from './host_compile.mjs';
import { makeRenderer, rendererString } from './browser/renderer.js';
import { observeUpdateHooks } from './update_hooks.mjs';

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
  settleInfo: null,
  cameraRepair: false,
  cameraRepairs: [],
  repairedSpecs: new Map(),
  autoExposure: false,
  autoExposureInfo: null,
  post: null,
  postInfo: null,
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
  canvas.id = '3dcode-canvas';
  document.body.appendChild(canvas);
  return { renderer: makeRenderer(canvas, width, height, { logDepth: !!opts.logDepth }), canvas };
}

/** Auto-exposure's target mean-luminance band.  Its floor is the scene_frames gate's dark line
 *  (`frame_metrics.DARK_MEAN_LUM`, pinned by a test): at 0.10 a frame lifted to 0.10-0.119
 *  still drew "frame too dark". */
const EXPOSURE_BAND = [0.12, 0.45];

/** The authored cameras, and each problem as {severity, text}: the severity is decided HERE,
 *  where the problem is known (the python gate used to re-derive it from the wording).
 *  The plan's own cameras pass the same fov / position / name rules (contracts/plan.CameraPlan). */
function validateCameras(raw) {
  const out = [];
  const problems = [];
  const error = (text) => problems.push({ severity: 'error', text });
  const warn = (text) => problems.push({ severity: 'warn', text });
  if (!Array.isArray(raw)) {
    error('createScene().cameras is not an array');
    return { cameras: out, problems };
  }
  raw.forEach((c, i) => {
    const p = c && c.position;
    const l = c && c.lookAt;
    const okVec = (v) => Array.isArray(v) && v.length === 3 && v.every((x) => Number.isFinite(x));
    if (!c || typeof c !== 'object') { warn(`cameras[${i}] is not an object`); return; }
    if (!okVec(p)) { error(`cameras[${i}] position must be [x,y,z] finite numbers`); return; }
    if (!okVec(l)) { error(`cameras[${i}] lookAt must be [x,y,z] finite numbers`); return; }
    const fov = Number.isFinite(c.fov) ? c.fov : 50;
    if (fov < 5 || fov > 150) warn(`cameras[${i}] fov ${fov} outside [5,150]`);
    if (p.every((x, k) => Math.abs(x - l[k]) < 1e-6)) warn(`cameras[${i}] position equals lookAt`);
    const name = String(c.name || `cam_${i}`);
    if (!/^[A-Za-z0-9_-]{1,64}$/.test(name)) {
      warn(`cameras[${i}] name ${JSON.stringify(name)} must match [A-Za-z0-9_-]{1,64} — it becomes a render filename`);
      return;
    }
    out.push({ name, position: p.map(Number), lookAt: l.map(Number), fov });
  });
  if (out.length === 0) error('no valid cameras (author 1-6 {name, position, lookAt, fov})');
  if (out.length > 6) warn(`too many cameras (${out.length} > 6)`);
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
/** geometry uuid -> world box of every mesh drawing it (the GLB bookkeeping's one traversal). */
function geometryBoxes(scene) {
  const used = new Map();
  scene.traverse((o) => {
    if (!o.geometry || !o.geometry.uuid || !(o.isMesh || o.isInstancedMesh)) return;
    if (!used.has(o.geometry.uuid)) used.set(o.geometry.uuid, new THREE.Box3());
    const box = drawableBox(o, THREE);
    if (box) used.get(o.geometry.uuid).union(box);
  });
  return used;
}

/** Words of a GLB's file name that can name a camera for it: `lantern_room.glb` → ['lantern']. */
const GENERIC_HERO_WORDS = new Set(['room', 'detail', 'hero', 'main', 'view', 'shot', 'camera', 'asset', 'model', 'close', 'closeup', 'wide', 'zone', 'prop']);
function heroWords(url) {
  const base = String(url).split('/').pop().replace(/\.glb$/i, '');
  return base.replace(/([a-z0-9])([A-Z])/g, '$1 $2').split(/[^A-Za-z0-9]+/)
    .map((w) => w.toLowerCase()).filter((w) => w.length >= 4 && !GENERIC_HERO_WORDS.has(w));
}

/** THE "camera named for a hero" rule: a name word of the GLB's file is in the camera's name.
 *  Camera repair re-aims by it, and the census carries it (`camera_checks[].hero_for`) for the
 *  frames gate (frame_metrics._hero_findings), which has no copy of its own. */
function namesHero(cameraName, url) {
  const lower = String(cameraName || '').toLowerCase();
  return heroWords(url).some((w) => lower.includes(w));
}

/** The loaded GLB a camera is named for, with its world box as placed — or null. */
function heroForCamera(name) {
  if (!loadedGlbs.length) return null;
  let boxes = null;
  for (const g of loadedGlbs) {
    if (!namesHero(name, g.url)) continue;
    if (!boxes) { try { boxes = geometryBoxes(state.scene); } catch (e) { return null; } }
    const all = new THREE.Box3();
    for (const u of g.geometry_uuids) { const b = boxes.get(u); if (b && !b.isEmpty()) all.union(b); }
    if (!all.isEmpty()) return { url: g.url, box: all };
  }
  return null;
}

function glbUsage(scene) {
  let used;
  try { used = geometryBoxes(scene); } catch { return []; }
  return loadedGlbs.map((g) => {
    const all = new THREE.Box3();
    let n = 0;
    for (const u of g.geometry_uuids) { const b = used.get(u); if (b) { n += 1; if (!b.isEmpty()) all.union(b); } }
    // the largest extent of the GLB as placed: "fills 0.3 % of the frame" reads as "microscopic"
    // without it (loop 23's lighthouse: a 4.4 m lantern room on a 30 m tower, far from every
    // camera, judged "scaled far too small" for three rounds)
    const size = all.isEmpty() ? null : +Math.max(...all.getSize(new THREE.Vector3()).toArray()).toFixed(2);
    return { url: g.url, meshes: g.geometry_uuids.length, meshes_in_scene: n, in_scene: n > 0, size_m: size };
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
    state.cameraRepair = !!opts.cameraRepair;
    state.cameraRepairs = [];
    state.repairedSpecs = new Map();
    state.autoExposure = !!opts.autoExposure;
    state.autoExposureInfo = null;
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
    if (!state.update) info.camera_problems.push({ severity: 'warn', text: 'createScene().update(t, dt) is not a function (scene will be static)' });

    info.stage = 'loads';
    const tLd = performance.now();
    info.load_wait = await waitForLoads(manager, LOAD_IDLE_TIMEOUT_MS);
    info.timings_ms.loads = Math.round(performance.now() - tLd);
    if (info.load_wait === 'timeout') state.hostWarnings.push('asset loads still pending after timeout');

    info.stage = 'first_update';
    runUpdate(0, 0);
    state.simTime = 0;
    state.scene.updateMatrixWorld(true);

    // post chain (browser/post.js): ON for scene renders unless `post: false`.
    // Built here — after the first update, so a scene that sets its grade hint
    // while building has already set it — and before auto-exposure, which must
    // measure the frame that will actually be judged.  A chain that cannot be
    // built is a host WARNING, never a boot failure: the raw path still renders.
    if (opts.post !== false) {
      info.stage = 'post';
      try {
        const { makePostChain } = await import('./browser/post.js');
        state.post = makePostChain(state.renderer, state.scene, {
          width: state.width, height: state.height, options: opts.postOptions || {},
        });
        state.postInfo = state.post.info;
        for (const w of state.post.info.warnings) state.hostWarnings.push(`post: ${w}`);
      } catch (e) {
        state.post = null;
        state.postInfo = { enabled: false, error: String((e && e.message) || e).slice(0, 300) };
        state.hostWarnings.push(`post chain unavailable, plain render: ${state.postInfo.error}`);
      }
    } else {
      state.postInfo = { enabled: false };
    }

    // settle (2026-08-30): deterministically seat floating / sunken assets before any
    // census or render — the placement gate measured the errors for two batteries and
    // the refine agent left 38 sunken + 22 floating standing in final rounds.  Runs on
    // every boot (probe and render see the same seated scene); --no-settle disables.
    if (opts.settle !== false) {
      info.stage = 'settle';
      try {
        const c0 = sceneCensus(state.scene, THREE);
        const words = await (await fetch(new URL('./placement_words.json', import.meta.url))).json();
        state.settleInfo = settleScene(state.scene, THREE, { groundY: c0.ground_y, contentBox: c0.content_bbox, words });
      } catch (e) {
        state.settleInfo = { count: 0, moves: [], error: String((e && e.message) || e).slice(0, 300) };
      }
      info.settled = state.settleInfo.count;
    }

    // auto-exposure (opt-in, --auto-exposure): one scene-wide bounded exposure, like a
    // photographer picking ISO once.  fv_izakaya_night sat at mean_lum 0.07 for three
    // rounds with the 'frame too dark' ERROR in every refine prompt and nobody fixed it;
    // the frame gate's healthy band is 0.12..0.35.  Factor clamped to [0.5, 3.0] so a
    // deliberately moody scene is brightened, never rewritten; recorded in the census.
    if (opts.autoExposure && state.cameras.length) {
      info.stage = 'auto_exposure';
      try {
        const probeCam = () => buildCameraRaw(state.cameras[0]);
        const lum = () => { renderOnce(probeCam()); return frameStats(state.canvas).mean_lum; };
        const before = lum();
        let factor = 1;
        let after = before;
        const [lo, hi] = EXPOSURE_BAND;
        for (let i = 0; i < 4 && (after < lo || after > hi); i++) {
          const step = after < lo ? 1.6 : 0.7;
          const next = Math.min(3.0, Math.max(0.5, factor * step));
          if (next === factor) break;
          factor = next;
          state.renderer.toneMappingExposure = factor;
          after = lum();
        }
        if (factor !== 1) state.autoExposureInfo = { factor: +factor.toFixed(2), lum_before: before, lum_after: after };
      } catch (e) {
        state.hostWarnings.push(`auto-exposure failed: ${String((e && e.message) || e).slice(0, 200)}`);
      }
    }
    state.booted = true;
    info.ok = cameras.length > 0;
    // A camera-less scene used to fail with info.error EMPTY, so the caller
    // reported `scene did not boot at stage 'ready': ` and named no reason —
    // ~20 minutes of blind bisecting down to a two-line scene, twice.
    // validateCameras already said WHY; carry it into the error.
    if (!info.ok) {
      info.error = 'createScene() returned no usable cameras'
        + (problems.length ? ': ' + problems.map((q) => q.text).join('; ') : '');
    }
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
  // camera repair (opt-in, --camera-repair): cameras belong to the plan and no refine
  // agent can fix one, so a lens inside geometry is repaired here — the smallest
  // backward/upward retreat that clears it, cached per camera name so every render of
  // that camera uses the same repaired spec, recorded in census.camera_repair.
  if (state.cameraRepair && spec && spec.name && Array.isArray(spec.position) && Array.isArray(spec.lookAt)) {
    const key = spec.name + '|' + spec.position.join(',');
    if (!state.repairedSpecs.has(key)) {
      // A camera NAMED for a hero whose hero is out of its frame is re-aimed at the hero first:
      // loop 25's lighthouse (2026-09-09) shot `LanternDetail` at the tower wall for three rounds
      // while `hero_unseen` said 0.0 % each time and no session moved the plan's camera.  Only
      // when the hero's centre is outside the frustum — a small or far hero is the author's shot.
      let base = spec;
      let aimed = '';
      try {
        const hero = heroForCamera(spec.name);
        if (hero) {
          const c = hero.box.getCenter(new THREE.Vector3());
          const cam0 = buildCameraRaw(spec);
          const inFront = c.clone().applyMatrix4(cam0.matrixWorldInverse).z < 0;
          const ndc = c.clone().project(cam0);
          if (!(inFront && Math.abs(ndc.x) <= 1 && Math.abs(ndc.y) <= 1)) { base = { ...spec, lookAt: [c.x, c.y, c.z] }; aimed = hero.url; }
        }
      } catch (e) { base = spec; aimed = ''; }
      let fix = null;
      try { fix = repairCameraSpec(state.scene, base, THREE, buildCameraRaw); } catch (e) { fix = null; }
      state.repairedSpecs.set(key, fix ? fix.spec : (aimed ? base : null));
      if (fix || aimed) state.cameraRepairs.push({ name: spec.name, moved_back_m: fix ? fix.moved_back_m : 0, moved_up_m: fix ? fix.moved_up_m : 0,
                                                   moved_side_m: fix ? fix.moved_side_m || 0 : 0, nearest_before: fix ? fix.nearest_before : null,
                                                   inside_before: fix ? fix.inside_before : [], under_before: fix ? fix.under_before || '' : '',
                                                   blocked_before: !!(fix && fix.blocked_before), cut_before: fix ? fix.cut_before || '' : '',
                                                   aimed_at: aimed, nearest_after: fix ? fix.nearest_after : null });
    }
    const fixed = state.repairedSpecs.get(key);
    if (fixed) return buildCameraRaw(fixed);
  }
  return buildCameraRaw(spec);
}

function buildCameraRaw(spec) {
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

/** One frame to the canvas — through the post chain when it is armed. */
function renderOnce(cam) {
  if (state.post) {
    state.post.refreshGrade();   // a scene may set userData.grade from update()
    state.post.render(cam);
  } else {
    state.renderer.render(state.scene, cam);
  }
  try { const gl = state.renderer.getContext(); if (gl && gl.finish) gl.finish(); } catch (e) { /* ignore */ }
}

/**
 * What a rig view must not draw: visible see-through meshes classified 'sky' whose whole box
 * lies below `eye` (a cloud deck seen from above), and the harness-injected room shell's
 * ceiling when the eye is above it — an interior seen by the overview rig was a closed box,
 * and the pairwise judge called the bakery "a tiny fragment in the overviews" against a
 * one-shot whose room had no roof (cmp7, 2026-09-09).  Lifting the shell's lid shows the
 * layout the rig exists to show; an author's own roof stays, and so does every authored camera.
 */
function skyLayersBelow(eye) {
  const out = [];
  state.scene.traverse((o) => {
    if (!(o.isMesh || o.isInstancedMesh) || !o.visible || !o.geometry) return;
    if (o.parent && o.parent.name === 'RoomShell' && /^Ceiling/.test(o.name || '')) {
      const box = drawableBox(o, THREE);
      if (box && box.max.y < eye.y) out.push(o);
      return;
    }
    if (!nonSolid(o)) return;
    const box = drawableBox(o, THREE);
    if (!box) return;
    if (o.frustumCulled === false && o.geometry.isInstancedBufferGeometry) {
      // an instanced billboard deck keeps its quad at the origin and offsets it in the shader:
      // its instance offsets are the only honest extent
      const off = o.geometry.getAttribute('iOff');
      if (off) { box.makeEmpty(); for (let i = 0; i < off.count; i++) box.expandByPoint(new THREE.Vector3(off.getX(i), off.getY(i), off.getZ(i))); }
    }
    if (box.isEmpty() || box.max.y >= eye.y) return;
    if (classifyBackdrop(o, box) === 'sky' || /cloud|cumulus|cirrus/i.test(o.name || '')) out.push(o);
  });
  return out;
}

/** Render camera spec at time t; returns {dataUrl, ms}. */
function renderAt(spec, t) {
  if (!state.booted) throw new Error('host not booted');
  advanceTo(t);
  state.scene.updateMatrixWorld(true);
  const cam = buildCamera(spec);
  const t0 = performance.now();
  const savedFog = state.scene.fog;
  if (spec.noFog) state.scene.fog = null;   // overview rig: structure over atmosphere
  // …and no see-through sky layer under the lens: a billboard cloud deck seen from above
  // is a grid of white discs over the sea, and the judge read it as "a polka-dot sea
  // shader" on two lighthouse runs (2026-09-09).  The authored cameras keep their clouds.
  const hiddenLayers = spec.noFog ? skyLayersBelow(cam.position) : [];
  for (const o of hiddenLayers) o.visible = false;
  try {
    renderOnce(cam);
  } finally {
    state.scene.fog = savedFog;
    for (const o of hiddenLayers) o.visible = true;
  }
  const ms = Math.round(performance.now() - t0);
  // the camera actually used: differs from spec.position when camera repair fired
  return { dataUrl: state.canvas.toDataURL('image/png'), ms, sim_time: state.simTime,
           position: [cam.position.x, cam.position.y, cam.position.z] };
}

/** Repairs performed so far ({name, moved_back_m, moved_up_m, ...} per camera).
 * Repair fires lazily on the first build of each camera, so drivers read this
 * AFTER their render evaluations to stitch census.camera_repair (review-3 S5). */
function cameraRepairs() {
  return state.cameraRepairs.slice();
}

/**
 * Camera instruments (renders): near geometry, frame luminance stats and
 * coverage (content / ground / sky fractions of the frame, via mask passes).
 */
function cameraChecks(spec) {
  const cam = buildCamera(spec);
  state.scene.updateMatrixWorld(true);
  const near = nearGeometry(state.scene, cam, THREE, undefined, Array.isArray(spec.lookAt) ? spec.lookAt : null);
  // the instruments see what the rig view draws (a lifted lid, no cloud deck under the eye)
  const hiddenLayers = spec.noFog ? skyLayersBelow(cam.position) : [];
  for (const o of hiddenLayers) o.visible = false;
  let stats = {};
  let coverage = {};
  let glbFrac = {};
  try {
    renderOnce(cam);
    stats = frameStats(state.canvas);
    try {
      coverage = frameCoverage(state.renderer, state.scene, cam, state.canvas, THREE, state.contentBox);
    } catch (e) {
      state.hostWarnings.push(`coverage failed for ${spec.name}: ${e.message}`);
    }
    if (loadedGlbs.length) {
      try {
        glbFrac = glbCoverage(state.renderer, state.scene, cam, state.canvas, THREE, loadedGlbs);
      } catch (e) {
        state.hostWarnings.push(`glb coverage failed for ${spec.name}: ${e.message}`);
      }
    }
  } finally {
    for (const o of hiddenLayers) o.visible = true;
  }
  const heroFor = loadedGlbs.filter((g) => namesHero(spec.name, g.url)).map((g) => g.url);
  return { name: spec.name, ...near, ...stats, ...coverage, glb_frac: glbFrac, hero_for: heroFor };
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
    if (state.post) state.post.render(cam); else state.renderer.render(state.scene, cam);
    frames += 1;
  }
  try { state.renderer.getContext().finish(); } catch (e) { /* ignore */ }
  const elapsed = (performance.now() - t0) / 1000;
  // draw calls / triangles must stay the SCENE's numbers: renderer.info resets on
  // every render() call, so after a composer frame it holds the last full-screen
  // quad.  One plain render refills it with what the agent's budget is about.
  if (state.post) state.renderer.render(state.scene, cam);
  const r = state.renderer.info.render;
  return { fps: frames / Math.max(elapsed, 1e-3), frames, seconds: elapsed, draw_calls: r.calls, triangles: r.triangles };
}

/** Per-asset placement table (host_placement.mjs) — never throws: a failure is `{error}`. */
function placement() {
  try {
    return sceneCensus(state.scene, THREE, { placement: true }).placement;
  } catch (e) {
    return { error: String((e && e.message) || e).slice(0, 400) };
  }
}

function census() {
  const c = sceneCensus(state.scene, THREE);
  c.glb_assets = glbUsage(state.scene);
  if (state.settleInfo) c.settle = state.settleInfo;
  if (state.cameraRepairs.length) c.camera_repair = state.cameraRepairs.slice();
  if (state.autoExposureInfo) c.auto_exposure = state.autoExposureInfo;
  if (state.postInfo) c.post = state.postInfo;
  state.contentBox = c.content_bbox;
  state.fullBox = c.bbox;
  c.cameras = state.cameras.length;
  c.raf_calls = state.rafCalls;
  return c;
}

/** Probe-only observation around the normal deterministic update steps. */
function probeUpdateHooks(t) {
  const start = state.simTime;
  try {
    const report = observeUpdateHooks(state.scene, () => advanceTo(t));
    return { ...report, from_time: start, to_time: state.simTime };
  } catch (error) {
    // Optional instrumentation must not turn an unusual userData descriptor
    // into an agent build failure; the ordinary update error checks still run.
    advanceTo(t);
    return { error: String(error?.message || error).slice(0, 400), from_time: start, to_time: state.simTime };
  }
}

window.__c3v = {
  boot,
  renderAt,
  cameraChecks,
  cameraRepairs,
  compileAll,
  census,
  placement,
  probeUpdateHooks,
  fps,
  post: () => (state.postInfo ? { ...state.postInfo } : null),
  hostWarnings: () => state.hostWarnings.slice(),
  shaderErrors: () => state.shaderErrors.slice(),
  updateErrors: () => state.updateErrors.slice(),
  cameras: () => state.cameras.slice(),
};
window.__c3v_ready = true;

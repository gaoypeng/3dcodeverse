/**
 * Ablation instrument (page-side): how much of the delivered frame the scene's
 * CUSTOM shaders actually paint.
 *
 * A shader can compile, pass every gate and still be absent from the picture —
 * behind the camera, occluded, alpha-zero, scaled to nothing, or quietly
 * retreated to a flat material after the first compiler error.  Nothing the
 * harness measures today can tell "the effect is there" from "the effect
 * compiled": `census.custom_materials` counts materials, `check_shaders`
 * compiles them, and both are happy about a shader nobody can see.
 *
 * The answer is a counterfactual, and it is deterministic (law 3): render the
 * frame twice — as authored, and with every custom-shader CONTRIBUTION removed
 * — and diff the pixels.  Two removals, because a shader is absent in two
 * different ways:
 *
 *   ShaderMaterial / RawShaderMaterial  owns everything its mesh draws, so it
 *     is replaced by a neutral MeshStandardMaterial of the same base colour
 *     (sniffed from `uniforms`), keeping only the compositing state — side,
 *     transparent, opacity, blending, depth — because "neutral" means neutral
 *     SHADING, not a different silhouette or a different blend.
 *   onBeforeCompile-patched builtin  draws either way, so the question is what
 *     the patch changed: it is replaced by an unpatched clone of ITSELF, which
 *     is literally the same MeshStandardMaterial with the same base colour and
 *     maps, minus the patch.  Anything coarser (a grey stand-in) would measure
 *     the material's textures rather than the agent's GLSL.
 *
 * Everything here is pure over a THREE.Scene plus a `renderFrame(spec)`
 * callback the host supplies (see `scene_host.mjs: ablation()`), so the module
 * knows nothing about renderers, canvases or cameras and is testable on its own.
 * `isCustomShader` is imported from the census — "is this a custom shader" is
 * stated once, there (law 2 in spirit: import, never restate).
 */

import { classifyBackdrop } from './backdrop.mjs';
import { isCustomShader } from './host_census.mjs';

/** Per-channel difference (0..255) that counts a pixel as changed. */
export const DIFF_THRESHOLD = 8;
/** Diff readback cap: frames are compared at native size up to this grid. */
export const DIFF_MAX_W = 512;
export const DIFF_MAX_H = 288;
/** Leave-one-out contribution is measured only when at most this many custom materials exist. */
export const MAX_MATERIALS = 8;
/** Render budget for the whole leave-one-out stage (materials x cameras). */
export const MAX_LOO_RENDERS = 24;
/** Neutral stand-in colour when a ShaderMaterial declares no colour-ish uniform. */
export const NEUTRAL_HEX = 0x808080;

const COLOR_UNIFORM_RE = /(^|_)(u_?)?(colou?r|tint|albedo|diffuse|base)\b|colou?r$/i;

/**
 * Base colour of a material as a THREE.Color: `.color` when it has one (every
 * builtin), else the first colour-ish uniform in NAME ORDER (sorted, so the
 * pick is deterministic when a shader carries several), else neutral grey.
 * HDR values are clamped into [0,1]: an emissive uniform at 4.0 would make the
 * stand-in blow out the frame and inflate the diff for the wrong reason.
 */
export function baseColor(mat, THREE) {
  if (mat.color && mat.color.isColor) return mat.color.clone();
  const u = mat.uniforms || {};
  for (const key of Object.keys(u).sort()) {
    if (!COLOR_UNIFORM_RE.test(key)) continue;
    const v = u[key] && u[key].value;
    if (!v) continue;
    const c = v.isColor ? v.clone()
      : (typeof v.x === 'number' && typeof v.y === 'number' && typeof v.z === 'number')
        ? new THREE.Color(v.x, v.y, v.z) : null;
    if (!c) continue;
    c.r = Math.min(1, Math.max(0, c.r));
    c.g = Math.min(1, Math.max(0, c.g));
    c.b = Math.min(1, Math.max(0, c.b));
    return c;
  }
  return new THREE.Color(NEUTRAL_HEX);
}

/**
 * Neutral stand-in for a from-scratch ShaderMaterial: lit MeshStandardMaterial
 * of the same base colour, same compositing state, no vertex colours (the
 * geometry may not carry the attribute the custom program declared).
 */
export function neutralMaterial(mat, THREE) {
  const m = new THREE.MeshStandardMaterial({
    color: baseColor(mat, THREE),
    roughness: 0.85,
    metalness: 0.0,
    side: mat.side,
    transparent: !!mat.transparent,
    opacity: Number.isFinite(mat.opacity) ? mat.opacity : 1,
    depthTest: mat.depthTest !== false,
    depthWrite: mat.depthWrite !== false,
    blending: mat.blending,
    alphaTest: Number.isFinite(mat.alphaTest) ? mat.alphaTest : 0,
    fog: true,
  });
  m.name = `${mat.name || mat.type}__ablated`;
  return m;
}

/**
 * The same builtin material without its `onBeforeCompile` patch.  `userData` is
 * emptied across the clone because `Material.copy` JSON-clones it and a patched
 * material routinely parks live uniforms (or a texture) there — verified on
 * three 0.182: `clone()` copies neither `onBeforeCompile` nor
 * `customProgramCacheKey`, but both are reset explicitly so a future three
 * cannot silently carry the patch across.
 */
export function unpatchedClone(mat, THREE) {
  const keep = mat.userData;
  let c;
  try {
    mat.userData = {};
    c = mat.clone();
  } finally {
    mat.userData = keep;
  }
  c.userData = {};
  c.onBeforeCompile = THREE.Material.prototype.onBeforeCompile;
  c.customProgramCacheKey = THREE.Material.prototype.customProgramCacheKey;
  c.name = `${mat.name || mat.type}__unpatched`;
  c.needsUpdate = true;
  return c;
}

/** World-space AABB of a drawable, or null when it has none. */
function worldBox(o, THREE) {
  if (!o.geometry) return null;
  if (!o.geometry.boundingBox) o.geometry.computeBoundingBox();
  const gb = o.geometry.boundingBox;
  if (!gb || gb.isEmpty()) return null;
  const box = new THREE.Box3().copy(gb).applyMatrix4(o.matrixWorld);
  return box.isEmpty() ? null : box;
}

/**
 * Every custom-shader material in the scene, once each, in traversal order.
 * @returns {Array<{uuid, name, type, kind, on, meshes, backdrop, visible}>}
 *   `kind` is 'ShaderMaterial' | 'onBeforeCompile' (same words the census uses),
 *   `backdrop` the shared sky/ground/content class of the meshes carrying it.
 */
export function ablationTargets(scene, THREE) {
  scene.updateMatrixWorld(true);
  const byUuid = new Map();
  scene.traverse((o) => {
    if (!o.material) return;
    for (const m of Array.isArray(o.material) ? o.material : [o.material]) {
      if (!m || !isCustomShader(m, THREE)) continue;
      let row = byUuid.get(m.uuid);
      if (!row) {
        row = {
          uuid: m.uuid,
          name: m.name || `${m.type} on ${o.name || o.parent?.name || o.type}`,
          type: m.type,
          kind: m.isShaderMaterial || m.isRawShaderMaterial ? 'ShaderMaterial' : 'onBeforeCompile',
          on: o.name || o.parent?.name || o.type,
          meshes: 0,
          backdrop: 'content',
          visible: false,
        };
        byUuid.set(m.uuid, row);
      }
      row.meshes += 1;
      if (o.visible) row.visible = true;
      const box = worldBox(o, THREE);
      if (box && row.backdrop === 'content') row.backdrop = classifyBackdrop(o, box);
    }
  });
  return [...byUuid.values()];
}

/**
 * Replace custom-shader materials with their neutral stand-ins; returns the
 * undo function (call it before the next render, always in a `finally`).
 * `only` (a material uuid) ablates that ONE material — the leave-one-out pass
 * that gives per-material contribution.
 */
export function ablate(scene, THREE, { only = null } = {}) {
  const undo = [];
  const cache = new Map();
  const standIn = (m) => {
    if (!cache.has(m.uuid)) {
      cache.set(m.uuid, (m.isShaderMaterial || m.isRawShaderMaterial)
        ? neutralMaterial(m, THREE) : unpatchedClone(m, THREE));
    }
    return cache.get(m.uuid);
  };
  scene.traverse((o) => {
    if (!o.material) return;
    const mats = Array.isArray(o.material) ? o.material : [o.material];
    const swapped = mats.map((m) => (
      m && isCustomShader(m, THREE) && (!only || m.uuid === only) ? standIn(m) : m));
    if (!swapped.some((m, i) => m !== mats[i])) return;
    const orig = o.material;
    o.material = Array.isArray(orig) ? swapped : swapped[0];
    undo.push(() => { o.material = orig; });
  });
  return () => {
    for (const u of undo) u();
    for (const m of cache.values()) m.dispose();
  };
}

/**
 * Pixel difference between two RGBA readbacks of the same size.
 * `changed_frac` — pixels whose largest channel difference exceeds `threshold`;
 * `mean_abs` — mean of that per-pixel maximum, 0..1 (a whole-frame tint that
 * moves every pixel by 3/255 shows up here and not in `changed_frac`).
 */
export function frameDiff(a, b, threshold = DIFF_THRESHOLD) {
  if (!a || !b || a.length !== b.length) throw new Error('frameDiff: frames differ in size');
  const n = a.length / 4;
  let changed = 0;
  let sum = 0;
  for (let i = 0; i < n; i++) {
    const j = i * 4;
    const d = Math.max(Math.abs(a[j] - b[j]), Math.abs(a[j + 1] - b[j + 1]), Math.abs(a[j + 2] - b[j + 2]));
    if (d > threshold) changed += 1;
    sum += d;
  }
  return {
    changed_frac: +(changed / Math.max(n, 1)).toFixed(4),
    mean_abs: +(sum / Math.max(n, 1) / 255).toFixed(4),
    pixels: n,
  };
}

/**
 * A reusable RGBA sampler for one render canvas.  Frames are read at NATIVE
 * size up to DIFF_MAX_W × DIFF_MAX_H: unlike `host_metrics.sampleFrame` (a
 * fixed 96×54 statistics grid shared with the coverage masks so the numbers
 * stay comparable), this instrument measures WHERE pixels changed, and a 2 px
 * light shaft averaged into a 96×54 cell falls under the 8/255 threshold and
 * reads as "no effect".  One scratch canvas is kept for the whole report.
 */
export function frameSampler(canvas) {
  const w = Math.min(canvas.width, DIFF_MAX_W);
  const h = Math.min(canvas.height, DIFF_MAX_H);
  const small = document.createElement('canvas');
  small.width = w;
  small.height = h;
  const ctx = small.getContext('2d', { willReadFrequently: true });
  const sample = () => {
    ctx.clearRect(0, 0, w, h);
    ctx.drawImage(canvas, 0, 0, w, h);
    return ctx.getImageData(0, 0, w, h).data;
  };
  sample.grid = [w, h];
  return sample;
}

/**
 * The whole measurement.  Renders per camera: authored, then everything
 * ablated; then one leave-one-out pass per custom material per camera, whose
 * MAXIMUM over cameras is that material's contribution.
 *
 * Per camera and not "on the busiest camera": measured on the starter scene,
 * the busiest camera was the windmill (53.8 %, all of it the sky dome) where
 * the pond water is not in shot at all, so a single-camera pass reported the
 * water at 0.0 % while it paints 17.7 % of the pond camera.  A material's
 * contribution is the most it paints in ANY delivered frame.  The stage is
 * skipped above MAX_MATERIALS (past eight the answer is already "yes"), never
 * runs on a camera the shaders do not touch at all, and stops at
 * MAX_LOO_RENDERS renders — cameras are visited busiest-first, so the budget
 * always buys the frames that carry the effects.
 *
 * @param {THREE.Scene} scene
 * @param {object} THREE
 * @param {{renderFrame: (spec:object)=>Uint8ClampedArray, cameras: object[],
 *          maxMaterials?: number, threshold?: number, maxRenders?: number,
 *          grid?: number[], snapshot?: ()=>string}} opts
 *   `renderFrame(spec)` renders that camera at the already-fixed sim time and
 *   returns the RGBA readback.  `snapshot()`, when given, returns the canvas
 *   as it stands — called right after each of the two renders the report
 *   already makes, so the PNG pair the agent looks at costs no extra frames.
 * @returns {object} plain JSON (see `codeverse3d.spatial.ablation.AblationReport`)
 */
export function ablationReport(scene, THREE, opts) {
  const { renderFrame, cameras: rawCameras, maxMaterials = MAX_MATERIALS, threshold = DIFF_THRESHOLD,
    maxRenders = MAX_LOO_RENDERS, grid = null, snapshot = null } = opts;
  // camera names key every row and every PNG filename: a duplicate would pair one
  // camera's authored frame with another's ablated one
  const seen = new Set();
  const cameras = (rawCameras || []).filter((c) => !seen.has(c.name) && seen.add(c.name));
  const targets = ablationTargets(scene, THREE);
  const out = {
    custom_materials: targets.length,
    threshold,
    grid: grid || [0, 0],
    cameras: [],
    materials: targets.map((t) => ({
      material: t.name, type: t.type, kind: t.kind, on: t.on, meshes: t.meshes,
      backdrop: t.backdrop, changed_frac: null, camera: '',
    })),
    per_material_cameras: [],
    per_material_measured: false,
    max_changed_frac: 0,
    content_changed_frac: 0,
    frames: [],
  };
  if (!cameras.length) {
    out.error = 'no cameras to ablate';
    return out;
  }
  const authored = new Map();
  for (const spec of cameras) {
    const a = renderFrame(spec);
    authored.set(spec.name, a);
    const authoredUrl = snapshot ? snapshot() : '';
    let row;
    let ablatedUrl = authoredUrl;
    if (!targets.length) {
      // Nothing to ablate: the counterfactual IS the authored frame.  Report the
      // exact zero rather than rendering it again — "no custom shader at all" is
      // the retreat this instrument exists to name, not a measurement failure.
      row = { camera: spec.name, changed_frac: 0, mean_abs: 0, pixels: a.length / 4 };
    } else {
      const undo = ablate(scene, THREE, {});
      let b;
      try {
        b = renderFrame(spec);
        if (snapshot) ablatedUrl = snapshot();
      } finally {
        undo();
      }
      row = { camera: spec.name, ...frameDiff(a, b, threshold) };
    }
    out.cameras.push(row);
    out.max_changed_frac = Math.max(out.max_changed_frac, row.changed_frac);
    if (snapshot) out.frames.push({ camera: spec.name, authored: authoredUrl, ablated: ablatedUrl });
  }
  if (!targets.length) return out;

  // leave-one-out: busiest camera first, so a render budget always buys the
  // frames the effects are actually in (index keeps the order deterministic on ties)
  if (targets.length <= maxMaterials) {
    const order = out.cameras
      .map((c, i) => ({ c, i }))
      .sort((x, y) => (y.c.changed_frac - x.c.changed_frac) || (x.i - y.i))
      .map((x) => x.c);
    let spent = 0;
    for (const cam of order) {
      // the busiest camera is always measured (even at 0: "which of these shaders
      // is missing" is exactly the question a 0 raises); after that, skip frames no
      // shader touches and stop when the budget is spent
      if (spent && cam.changed_frac <= 0) continue;
      if (spent && spent + targets.length > maxRenders) break;
      const spec = cameras.find((c) => c.name === cam.camera);
      const a = authored.get(cam.camera);
      targets.forEach((t, i) => {
        const undo = ablate(scene, THREE, { only: t.uuid });
        let b;
        try {
          b = renderFrame(spec);
        } finally {
          undo();
        }
        const frac = frameDiff(a, b, threshold).changed_frac;
        const row = out.materials[i];
        if (row.changed_frac === null || frac > row.changed_frac) {
          row.changed_frac = frac;
          row.camera = cam.camera;
        }
      });
      spent += targets.length;
      out.per_material_cameras.push(cam.camera);
    }
    out.per_material_measured = out.per_material_cameras.length > 0;
    out.content_changed_frac = out.materials
      .filter((m) => m.backdrop === 'content')
      .reduce((mx, m) => Math.max(mx, m.changed_frac || 0), 0);
  }
  return out;
}

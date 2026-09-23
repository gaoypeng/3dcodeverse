/**
 * Page-side frame coverage instrument: what fraction of a camera's frame is
 * CONTENT (meshes that are neither the sky dome nor the ground plane), and
 * what fraction is ground.  Implemented as two mask renders — backdrop hidden,
 * fog/background off, a flat white override material on everything left —
 * followed by a downscaled readback.  `sky_frac` is the remainder.
 *
 * The backdrop classifier is shared with the census (`lib/backdrop.mjs`) so
 * "content" here means the same thing as `census.content_bbox`; passing the
 * content span additionally treats a mesh whose footprint is far larger than
 * the content bbox (scatter across the whole ground) as backdrop.
 */

import { classifyBackdrop, nonSolid, drawableBox } from './backdrop.mjs';
import { sampleFrame } from './host_metrics.mjs';


function collectDrawables(scene, THREE, contentSpan) {
  const out = [];
  scene.traverse((o) => {
    if (!o.visible) return;
    if (!(o.isMesh || o.isPoints || o.isLine || o.isSprite)) return;
    const box = drawableBox(o, THREE);
    if (box) out.push({ obj: o, kind: classifyBackdrop(o, box, contentSpan) });
  });
  return out;
}

function brightFraction(canvas) {
  const { data, n } = sampleFrame(canvas);
  let bright = 0;
  for (let i = 0; i < n; i++) {
    if (data[i * 4] + data[i * 4 + 1] + data[i * 4 + 2] > 3 * 64) bright += 1;
  }
  return bright / n;
}

/** Run `fn(mask)` with the scene set up for a mask render (no background, fog or tone
 *  mapping, black clear, `mask` = flat white override) and restore everything after. */
function maskScope(renderer, scene, THREE, fn, override = true) {
  const saved = {
    background: scene.background, fog: scene.fog, override: scene.overrideMaterial,
    clear: renderer.getClearColor(new THREE.Color()), alpha: renderer.getClearAlpha(),
    toneMapping: renderer.toneMapping,
  };
  const mask = new THREE.MeshBasicMaterial({ color: 0xffffff, fog: false, side: THREE.DoubleSide });
  try {
    scene.background = null;
    scene.fog = null;
    if (override) scene.overrideMaterial = mask;
    renderer.setClearColor(0x000000, 1);
    renderer.toneMapping = THREE.NoToneMapping;
    return fn(mask);
  } finally {
    scene.background = saved.background;
    scene.fog = saved.fog;
    scene.overrideMaterial = saved.override;
    renderer.setClearColor(saved.clear, saved.alpha);
    renderer.toneMapping = saved.toneMapping;
    mask.dispose();
  }
}

/**
 * Coverage of `camera`'s frame: {content_frac, ground_frac, sky_frac}.
 * Restores every scene/renderer setting it touches.
 */
export function frameCoverage(renderer, scene, camera, canvas, THREE, contentBox) {
  const contentSpan = contentBox ? Math.max(contentBox.size[0], contentBox.size[2]) : 0;
  const drawables = collectDrawables(scene, THREE, contentSpan);
  const pass = (keep) => {
    for (const d of drawables) d.obj.visible = keep.includes(d.kind);
    renderer.render(scene, camera);
    return brightFraction(canvas);
  };
  let content = 0, covered = 0;
  try {
    maskScope(renderer, scene, THREE, () => {
      content = pass(['content']);
      covered = pass(['content', 'ground']);   // ground pass includes content so hidden ground is not counted
    });
  } finally {
    for (const d of drawables) d.obj.visible = true;
  }
  return {
    content_frac: +content.toFixed(4),
    ground_frac: +Math.max(0, covered - content).toFixed(4),
    sky_frac: +Math.max(0, 1 - covered).toFixed(4),
  };
}

/**
 * How much of `camera`'s frame each loaded GLB fills, occlusion included: one render per
 * GLB with its meshes flat white and every other solid mesh flat black (see-through
 * volumetrics and points/lines/sprites hidden), then the bright fraction.  `glbs` is the
 * host's load log [{url, geometry_uuids}]; a GLB none of whose geometry is in the scene
 * is skipped.  cmp6's crypt (2026-09-09): the hero stood behind a pillar in every authored
 * frame and the judge blamed the hero — "in the scene" was known, "in the frame" was not.
 * @returns {{[url: string]: number}}  fraction of the frame per GLB url
 */
export function glbCoverage(renderer, scene, camera, canvas, THREE, glbs) {
  const solids = [];
  const hidden = [];
  scene.traverse((o) => {
    if (!o.visible) return;
    if ((o.isMesh || o.isInstancedMesh) && o.geometry) {
      if (nonSolid(o)) hidden.push(o); else solids.push(o);
    } else if (o.isPoints || o.isLine || o.isSprite) hidden.push(o);
  });
  const out = {};
  const black = new THREE.MeshBasicMaterial({ color: 0x000000, fog: false, side: THREE.DoubleSide });
  const saved = solids.map((o) => o.material);
  try {
    maskScope(renderer, scene, THREE, (white) => {
      for (const o of hidden) o.visible = false;
      for (const g of glbs || []) {
        const ids = new Set(g.geometry_uuids || []);
        let n = 0;
        for (const o of solids) { const hit = ids.has(o.geometry.uuid); if (hit) n += 1; o.material = hit ? white : black; }
        if (!n) continue;
        renderer.render(scene, camera);
        out[g.url] = +brightFraction(canvas).toFixed(4);
      }
    }, false);
  } finally {
    solids.forEach((o, i) => { o.material = saved[i]; });
    for (const o of hidden) o.visible = true;
    black.dispose();
  }
  return out;
}

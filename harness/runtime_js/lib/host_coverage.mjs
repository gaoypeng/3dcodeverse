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

import { classifyBackdrop } from './backdrop.mjs';
import { sampleFrame } from './host_metrics.mjs';

export { classifyBackdrop };   // re-export: this module used to own the classifier


function collectDrawables(scene, THREE, contentSpan) {
  const out = [];
  const box = new THREE.Box3();
  scene.traverse((o) => {
    if (!o.visible) return;
    if (!(o.isMesh || o.isPoints || o.isLine || o.isSprite)) return;
    if (!o.geometry) return;
    if (!o.geometry.boundingBox) o.geometry.computeBoundingBox();
    const gb = o.geometry.boundingBox;
    if (!gb || gb.isEmpty()) return;
    box.copy(gb).applyMatrix4(o.matrixWorld);
    out.push({ obj: o, kind: classifyBackdrop(o, box, contentSpan) });
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

/**
 * Coverage of `camera`'s frame: {content_frac, ground_frac, sky_frac}.
 * Restores every scene/renderer setting it touches.
 */
export function frameCoverage(renderer, scene, camera, canvas, THREE, contentBox) {
  const contentSpan = contentBox ? Math.max(contentBox.size[0], contentBox.size[2]) : 0;
  const drawables = collectDrawables(scene, THREE, contentSpan);
  const saved = {
    background: scene.background, fog: scene.fog, override: scene.overrideMaterial,
    clear: renderer.getClearColor(new THREE.Color()), alpha: renderer.getClearAlpha(),
    toneMapping: renderer.toneMapping,
  };
  const mask = new THREE.MeshBasicMaterial({ color: 0xffffff, fog: false, side: THREE.DoubleSide });
  const pass = (keep) => {
    for (const d of drawables) d.obj.visible = keep.includes(d.kind);
    renderer.render(scene, camera);
    return brightFraction(canvas);
  };
  let content = 0, covered = 0;
  try {
    scene.background = null;
    scene.fog = null;
    scene.overrideMaterial = mask;
    renderer.setClearColor(0x000000, 1);
    renderer.toneMapping = THREE.NoToneMapping;
    content = pass(['content']);
    covered = pass(['content', 'ground']);   // ground pass includes content so hidden ground is not counted
  } finally {
    for (const d of drawables) d.obj.visible = true;
    scene.background = saved.background;
    scene.fog = saved.fog;
    scene.overrideMaterial = saved.override;
    renderer.setClearColor(saved.clear, saved.alpha);
    renderer.toneMapping = saved.toneMapping;
    mask.dispose();
  }
  return {
    content_frac: +content.toFixed(4),
    ground_frac: +Math.max(0, covered - content).toFixed(4),
    sky_frac: +Math.max(0, 1 - covered).toFixed(4),
  };
}

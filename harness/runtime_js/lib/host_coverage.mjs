/**
 * Page-side frame coverage instrument: what fraction of a camera's frame is
 * CONTENT (meshes that are neither the sky dome nor the ground plane), and
 * what fraction is ground.  Implemented as two mask renders — backdrop hidden,
 * fog/background off, a flat white override material on everything left —
 * followed by a downscaled readback.  `sky_frac` is the remainder.
 *
 * The backdrop classifier mirrors the census rules (name + world-bbox size) so
 * "content" here means the same thing as `census.content_bbox`; a mesh is also
 * backdrop when its footprint is far larger than the content bbox (scatter
 * across the whole ground).
 */

const SAMPLE_W = 96;
const SAMPLE_H = 54;
const SKY_NAME_RE = /\b(sky|skydome|skybox|stars|clouds?|sun|moon|atmosphere)\b/i;
const GROUND_NAME_RE = /\b(ground|terrain|floor|water|ocean|sea|lake|river|plane|sand|grass|land)\b/i;

/** 'sky' | 'ground' | 'content' for one drawable with world box `box`. */
export function classifyBackdrop(obj, box, contentSpan) {
  const name = obj.name || (obj.parent && obj.parent.name) || '';
  const sx = box.max.x - box.min.x, sy = box.max.y - box.min.y, sz = box.max.z - box.min.z;
  const span = Math.max(sx, sz);
  if (SKY_NAME_RE.test(name) && span > 50) return 'sky';
  if (span > 2000 || (sy > 300 && span > 300)) return 'sky';
  if (obj.isInstancedMesh) return 'content';
  if (span > 20 && sy < 0.06 * span && GROUND_NAME_RE.test(name)) return 'ground';
  if (span > 40 && sy < 0.02 * span) return 'ground';
  if (contentSpan > 0 && span > 2.5 * contentSpan && sy < 0.1 * span) return 'ground';
  return 'content';
}

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
  const small = document.createElement('canvas');
  small.width = SAMPLE_W;
  small.height = SAMPLE_H;
  const ctx = small.getContext('2d', { willReadFrequently: true });
  ctx.drawImage(canvas, 0, 0, SAMPLE_W, SAMPLE_H);
  const { data } = ctx.getImageData(0, 0, SAMPLE_W, SAMPLE_H);
  const n = SAMPLE_W * SAMPLE_H;
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

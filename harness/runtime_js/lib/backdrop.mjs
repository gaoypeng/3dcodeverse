/**
 * Backdrop classification — THE single owner of "is this drawable sky, ground
 * or content?" for a scene.  Used by the census (`host_census.mjs`, which
 * decides what `content_bbox` covers) and by the frame-coverage instrument
 * (`host_coverage.mjs`, which masks the same three classes per frame), so the
 * word "content" means exactly one thing everywhere.
 *
 * Rules (name + world-box shape, in order):
 *   sky      a sky-ish name spanning > 50 m or not writing depth, or anything enormous (span > 2 km,
 *            or tall AND wide: sy > 300 with span > 300)
 *   content  an InstancedMesh (scatter spans the map but is not ground)
 *   ground   a ground-ish name on a wide, flat box (span > 20, sy < 6 % of span)
 *            or any very wide, very flat box (span > 40, sy < 2 % of span)
 *            or — only when `contentSpan` is known (coverage) — a flat box far
 *            larger than the content itself (span > 2.5 × contentSpan)
 *   content  everything else
 */

/**
 * A mesh that does not WRITE DEPTH is a volumetric pass, not matter: haze shells,
 * god rays, light shafts, glow cards.  It cannot support anything, nothing can sink
 * into it, and overlapping it is what it is FOR.
 *
 * Measured on bench/out/scene_baseline (2026-09-05): every scene the generator wrote
 * uses `depthWrite: false` 34-42 times, and two of the six cells failed
 * `scene_placement` on nothing else — "BlackPine_5 is sunken 3.46 m into
 * AtmosphereHaze" (MeshBasicMaterial, opacity 0.035) and "WindowSnowView/Mesh_49 and
 * Environment/MoonlightShaft overlap 100%" (opacity 0.04, AdditiveBlending).  Both
 * scenes were correct; the gate was measuring fog.
 *
 * Opacity is deliberately NOT part of the rule: glass sits at 0.3-0.6 and keeps
 * writing depth, and a greenhouse pane really is a surface.  A solid wall the model
 * mistakenly wrote `depthWrite: false` on stops being a support — a missed defect,
 * which is the cheaper error, and one that matches how the frame actually renders.
 */
export function nonSolid(mesh) {
  const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
  const real = mats.filter(Boolean);
  return real.length > 0 && real.every((m) => m.depthWrite === false);
}

/** World-space AABB of a drawable (a fresh THREE.Box3), or null when it has none. */
export function drawableBox(o, THREE) {
  if (!o.geometry) return null;
  if (!o.geometry.boundingBox) o.geometry.computeBoundingBox();
  const gb = o.geometry.boundingBox;
  if (!gb || gb.isEmpty()) return null;
  const box = new THREE.Box3().copy(gb).applyMatrix4(o.matrixWorld);
  return box.isEmpty() ? null : box;
}

export const SKY_NAME_RE = /\b(sky|skydome|skybox|stars|clouds?|sun|moon|atmosphere)\b/i;
export const GROUND_NAME_RE = /\b(ground|terrain|floor|water|ocean|sea|lake|river|plane|sand|grass|land)\b/i;

/**
 * 'sky' | 'ground' | 'content' for one drawable with world box `box`.
 * @param {object} obj           mesh/points/line/sprite (only `name`, `parent`, `isInstancedMesh` are read)
 * @param {{min:{x,y,z}, max:{x,y,z}}} box   world-space AABB
 * @param {number} contentSpan   horizontal span of the known content bbox (0 = unknown)
 */
export function classifyBackdrop(obj, box, contentSpan = 0) {
  // Generated scene names normally use PascalCase / snake_case. Word boundaries
  // alone missed PlanetSkyBackdrop and SkyAtmosphereBand0, framing the sky as a
  // building. Keep the same vocabulary and shape rules for every naming style.
  const name = (obj.name || (obj.parent && obj.parent.name) || '')
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2').replace(/[_-]+/g, ' ');
  const sx = box.max.x - box.min.x, sy = box.max.y - box.min.y, sz = box.max.z - box.min.z;
  const span = Math.max(sx, sz);
  // sunRig's MoonDisc can be only five metres wide in a compact worldShell, but
  // its non-depth-writing light image still must not move the scene's centre.
  if (SKY_NAME_RE.test(name) && (span > 50 || nonSolid(obj))) return 'sky';
  if (span > 2000 || (sy > 300 && span > 300)) return 'sky';
  // a small thing kilometres from the origin is a backdrop whatever it is called: the sun/moon
  // disc `sunRig` parks at 0.9 × the sky radius ('SunDisc' — no word boundary for the regex)
  // blew the content bbox to 2.5 km and put the overview rig 5 km up (measured 2026-09-07)
  const far = Math.max(Math.abs(box.min.x), Math.abs(box.max.x), Math.abs(box.min.y), Math.abs(box.max.y),
                       Math.abs(box.min.z), Math.abs(box.max.z));
  if (far > 1500 && span < 0.1 * far) return 'sky';
  if (obj.isInstancedMesh) return 'content';   // scattered instances span the map but are not ground
  if (span > 20 && sy < 0.06 * span && GROUND_NAME_RE.test(name)) return 'ground';
  if (span > 40 && sy < 0.02 * span) return 'ground';
  if (contentSpan > 0 && span > 2.5 * contentSpan && sy < 0.1 * span) return 'ground';
  return 'content';
}

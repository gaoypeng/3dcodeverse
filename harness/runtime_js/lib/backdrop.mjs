/**
 * Backdrop classification — THE single owner of "is this drawable sky, ground
 * or content?" for a scene.  Used by the census (`host_census.mjs`, which
 * decides what `content_bbox` covers) and by the frame-coverage instrument
 * (`host_coverage.mjs`, which masks the same three classes per frame), so the
 * word "content" means exactly one thing everywhere.
 *
 * Rules (name + world-box shape, in order):
 *   sky      a sky-ish name spanning > 50 m, or anything enormous (span > 2 km,
 *            or tall AND wide: sy > 300 with span > 300)
 *   content  an InstancedMesh (scatter spans the map but is not ground)
 *   ground   a ground-ish name on a wide, flat box (span > 20, sy < 6 % of span)
 *            or any very wide, very flat box (span > 40, sy < 2 % of span)
 *            or — only when `contentSpan` is known (coverage) — a flat box far
 *            larger than the content itself (span > 2.5 × contentSpan)
 *   content  everything else
 */

export const SKY_NAME_RE = /\b(sky|skydome|skybox|stars|clouds?|sun|moon|atmosphere)\b/i;
export const GROUND_NAME_RE = /\b(ground|terrain|floor|water|ocean|sea|lake|river|plane|sand|grass|land)\b/i;

/**
 * 'sky' | 'ground' | 'content' for one drawable with world box `box`.
 * @param {object} obj           mesh/points/line/sprite (only `name`, `parent`, `isInstancedMesh` are read)
 * @param {{min:{x,y,z}, max:{x,y,z}}} box   world-space AABB
 * @param {number} contentSpan   horizontal span of the known content bbox (0 = unknown)
 */
export function classifyBackdrop(obj, box, contentSpan = 0) {
  const name = obj.name || (obj.parent && obj.parent.name) || '';
  const sx = box.max.x - box.min.x, sy = box.max.y - box.min.y, sz = box.max.z - box.min.z;
  const span = Math.max(sx, sz);
  if (SKY_NAME_RE.test(name) && span > 50) return 'sky';
  if (span > 2000 || (sy > 300 && span > 300)) return 'sky';
  if (obj.isInstancedMesh) return 'content';   // scattered instances span the map but are not ground
  if (span > 20 && sy < 0.06 * span && GROUND_NAME_RE.test(name)) return 'ground';
  if (span > 40 && sy < 0.02 * span) return 'ground';
  if (contentSpan > 0 && span > 2.5 * contentSpan && sy < 0.1 * span) return 'ground';
  return 'content';
}

// Part: BackrestSlat  (horizontal wooden slats forming the comfortable backrest)
// Array of 5 parallel teak wood slats (each 1.54 m long, 65 mm wide, 24 mm thick) with rounded edges, tilted back 15° from vertical and arranged along the upper back rail from y=0.48, z=-0.14 up to the top crest at y=0.81, z=-0.26 with 12 mm gaps between slats.
// Plan bbox (world, meters): centre (0, 0.65, -0.2)  extents (1.54, 0.35, 0.18)
// Material: varnished golden teak wood with visible grain and satin gloss
// Contract: export function buildBackrestSlat(THREE) -> THREE.Group named 'BackrestSlat', at WORLD pose.

import * as THREE from 'three';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';

export const BACKREST_SLAT_CONFIGS = [
  { y: 0.505, z: -0.128, rx: -1.20 },
  { y: 0.575, z: -0.158, rx: -1.25 },
  { y: 0.650, z: -0.198, rx: -1.30 },
  { y: 0.722, z: -0.238, rx: -1.35 },
  { y: 0.795, z: -0.275, rx: -1.38 },
];

export const BACKREST_SLAT_LENGTH = 1.54;
export const BACKREST_SLAT_WIDTH = 0.065;
export const BACKREST_SLAT_THICKNESS = 0.024;
export const BACKREST_SLAT_RADIUS = 0.006;

export function buildBackrestSlat(THREE_) {
  const group = new THREE.Group();
  group.name = 'BackrestSlat';

  const baseGeo = new RoundedBoxGeometry(
    BACKREST_SLAT_LENGTH,
    BACKREST_SLAT_THICKNESS,
    BACKREST_SLAT_WIDTH,
    3,
    BACKREST_SLAT_RADIUS
  );

  const slatGeos = [];
  for (const cfg of BACKREST_SLAT_CONFIGS) {
    const g = baseGeo.clone();
    g.rotateX(cfg.rx);
    g.translate(0, cfg.y, cfg.z);
    slatGeos.push(g);
  }

  const mergedGeo = mergeGeometries(slatGeos);
  mergedGeo.computeVertexNormals();

  const material = new THREE.MeshStandardMaterial({
    color: 0xa86024,
    roughness: 0.38,
    metalness: 0.02,
  });

  const mesh = new THREE.Mesh(mergedGeo, material);
  mesh.name = 'BackrestSlat';
  group.add(mesh);

  return group;
}

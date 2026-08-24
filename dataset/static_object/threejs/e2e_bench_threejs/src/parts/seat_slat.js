// Part: SeatSlat  (horizontal wooden slats forming the ergonomic seat pan)
// Array of 6 parallel teak wood slats (each 1.54 m long, 60 mm wide, 24 mm thick) with fully rounded 6 mm bullnose edges, distributed along the gentle concave curve of the seat from front lip (z=0.28, y=0.42) to back joint (z=-0.12, y=0.38) with 10 mm gaps between slats.
// Plan bbox (world, meters): centre (0, 0.41, 0.08)  extents (1.54, 0.07, 0.44)
// Material: varnished golden teak wood with visible grain and satin gloss
// Contract: export function buildSeatSlat(THREE) -> THREE.Group named 'SeatSlat', at WORLD pose.

import * as THREE from 'three';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';

export const SEAT_SLAT_CONFIGS = [
  { y: 0.405, z: 0.265, rx: -0.28 },
  { y: 0.428, z: 0.190, rx: -0.12 },
  { y: 0.422, z: 0.115, rx:  0.00 },
  { y: 0.408, z: 0.040, rx:  0.08 },
  { y: 0.395, z: -0.035, rx: 0.14 },
  { y: 0.390, z: -0.108, rx: 0.18 },
];

export const SLAT_LENGTH = 1.54;
export const SEAT_SLAT_WIDTH = 0.060;
export const SLAT_THICKNESS = 0.024;
export const SLAT_RADIUS = 0.006;

export function buildSeatSlat(THREE_) {
  const group = new THREE.Group();
  group.name = 'SeatSlat';

  const baseGeo = new RoundedBoxGeometry(SLAT_LENGTH, SLAT_THICKNESS, SEAT_SLAT_WIDTH, 3, SLAT_RADIUS);
  const slatGeos = [];

  for (const cfg of SEAT_SLAT_CONFIGS) {
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
  mesh.name = 'SeatSlat';
  group.add(mesh);

  return group;
}

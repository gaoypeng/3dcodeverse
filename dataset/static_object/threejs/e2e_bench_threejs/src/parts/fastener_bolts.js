// Part: FastenerBolts  (carriage bolts fastening wooden slats to the iron frame brackets)
// Array of smooth domed carriage bolt heads (diameter 12 mm, dome height 4 mm) embedded slightly into the top surface of each slat directly over the two side frames and the center rib.
// Plan bbox (world, meters): centre (0, 0.53, -0.06)  extents (1.46, 0.58, 0.60)
// Material: patinated blackened bronze
// Contract: export function buildFastenerBolts(THREE) -> THREE.Group named 'FastenerBolts', at WORLD pose.

import * as THREE from 'three';
import { mergeGeometries, mergeVertices } from 'three/addons/utils/BufferGeometryUtils.js';
import { SEAT_SLAT_CONFIGS, SLAT_THICKNESS } from './seat_slat.js';
import { BACKREST_SLAT_CONFIGS, BACKREST_SLAT_THICKNESS } from './backrest_slat.js';

function createBoltHeadGeometry(radius, height) {
  // Beveled domed carriage bolt head: lower base and upper dome chamfer
  const geo = new THREE.CylinderGeometry(radius * 0.65, radius, height, 12);
  geo.translate(0, height / 2, 0);
  return geo;
}

export function buildFastenerBolts(THREE_) {
  const group = new THREE.Group();
  group.name = 'FastenerBolts';

  const BOLT_R = 0.0055;
  const BOLT_H = 0.0030;
  const X_POSITIONS = [-0.720, 0.000, 0.720];

  const boltHeadGeo = createBoltHeadGeometry(BOLT_R, BOLT_H, 12, 6);
  const boltGeos = [];

  // 1. Seat slats carriage bolts (on upper surface of seat slats)
  for (const cfg of SEAT_SLAT_CONFIGS) {
    const halfT = SLAT_THICKNESS / 2 - 0.0010;
    const topY = cfg.y + halfT * Math.cos(cfg.rx);
    const topZ = cfg.z + halfT * Math.sin(cfg.rx);
    for (const x of X_POSITIONS) {
      const g = boltHeadGeo.clone();
      g.rotateX(cfg.rx);
      g.translate(x, topY, topZ);
      boltGeos.push(g);
    }
  }

  // 2. Backrest slats carriage bolts (on front surface of backrest slats)
  for (const cfg of BACKREST_SLAT_CONFIGS) {
    const halfT = BACKREST_SLAT_THICKNESS / 2 - 0.0010;
    const frontY = cfg.y - halfT * Math.cos(cfg.rx);
    const frontZ = cfg.z - halfT * Math.sin(cfg.rx);
    for (const x of X_POSITIONS) {
      const g = boltHeadGeo.clone();
      g.rotateX(cfg.rx + Math.PI); // dome points forward
      g.translate(x, frontY, frontZ);
      boltGeos.push(g);
    }
  }

  // 3. Frame assembly joint carriage bolts (at leg and scroll junctions matching plan bbox)
  const frameJointBolts = [
    { y: 0.242, z: -0.355 }, // rear leg lower joint
    { y: 0.242, z:  0.235 }, // front leg lower joint
    { y: 0.816, z: -0.298 }, // top crest scroll
  ];

  for (const fb of frameJointBolts) {
    for (const x of [-0.728, 0.728]) {
      const g = boltHeadGeo.clone();
      g.rotateZ(x > 0 ? -Math.PI / 2 : Math.PI / 2);
      g.translate(x, fb.y, fb.z);
      boltGeos.push(g);
    }
  }

  const mergedGeo = mergeGeometries(boltGeos);
  mergedGeo.computeVertexNormals();

  const material = new THREE.MeshStandardMaterial({
    color: 0x2a2825,
    roughness: 0.40,
    metalness: 0.85,
  });

  const mesh = new THREE.Mesh(mergedGeo, material);
  mesh.name = 'FastenerBolts';
  group.add(mesh);

  return group;
}

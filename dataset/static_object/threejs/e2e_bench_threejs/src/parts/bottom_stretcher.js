// Part: BottomStretcher  (horizontal tie rod connecting lower frame legs for lateral stability)
// Cylindrical cast-iron rod of diameter 22 mm running horizontally along the X-axis between the lower rear frame legs, ending with threaded decorative acorn nuts on the outer sides.
// Plan bbox (world, meters): centre (0, 0.16, -0.18)  extents (1.52, 0.035, 0.035)
// Material: dark hunter green cast iron, satin metal finish
// Contract: export function buildBottomStretcher(THREE) -> THREE.Group named 'BottomStretcher', at WORLD pose.

import * as THREE from 'three';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';

export function buildBottomStretcher(THREE_) {
  const group = new THREE.Group();
  group.name = 'BottomStretcher';

  const Y = 0.160;
  const Z = -0.180;
  const ROD_R = 0.011;

  const geos = [];

  // Main tie rod spanning full length into the acorn tips
  const rodLen = 1.516;
  const rodGeo = new THREE.CylinderGeometry(ROD_R, ROD_R, rodLen, 24);
  rodGeo.rotateZ(Math.PI / 2);
  rodGeo.translate(0, Y, Z);
  geos.push(rodGeo);

  // Decorative collars and acorn nuts overlapping the rod
  for (const sign of [-1, 1]) {
    // Mounting sleeve at x = sign * 0.720 (overlaps the side frame)
    const collarGeo = new THREE.CylinderGeometry(0.0145, 0.0145, 0.030, 20);
    collarGeo.rotateZ(Math.PI / 2);
    collarGeo.translate(sign * 0.720, Y, Z);
    geos.push(collarGeo);

    // Decorative hex nut ring at x = sign * 0.745
    const nutGeo = new THREE.CylinderGeometry(0.016, 0.016, 0.014, 16);
    nutGeo.rotateZ(Math.PI / 2);
    nutGeo.translate(sign * 0.745, Y, Z);
    geos.push(nutGeo);

    // Acorn nut cone at x = sign * 0.752
    const acornGeo = new THREE.ConeGeometry(0.015, 0.016, 16);
    acornGeo.rotateZ(-sign * Math.PI / 2);
    acornGeo.translate(sign * 0.752, Y, Z);
    geos.push(acornGeo);
  }

  const mergedGeo = mergeGeometries(geos);
  mergedGeo.computeVertexNormals();

  const material = new THREE.MeshStandardMaterial({
    color: 0x183424,
    roughness: 0.45,
    metalness: 0.35,
  });

  const mesh = new THREE.Mesh(mergedGeo, material);
  mesh.name = 'BottomStretcher';
  group.add(mesh);

  return group;
}

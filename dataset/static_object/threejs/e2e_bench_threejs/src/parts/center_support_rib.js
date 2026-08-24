// Part: CenterSupportRib  (central under-slat cast iron contour bracket preventing slat sagging)
// Slender cast-iron curved spine matching the contour of the seat curve and backrest curve, positioned at the bench centerline under the wooden slats.
// Plan bbox (world, meters): centre (0, 0.46, -0.02)  extents (0.03, 0.58, 0.58)
// Material: dark hunter green cast iron, satin metal finish
// Contract: export function buildCenterSupportRib(THREE) -> THREE.Group named 'CenterSupportRib', at WORLD pose.

import * as THREE from 'three';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';

export function buildCenterSupportRib(THREE_) {
  const group = new THREE.Group();
  group.name = 'CenterSupportRib';

  const geos = [];
  const BAR_R = 0.010;

  // 1. Continuous spine flush against underside of seat slats and back of backrest slats
  const spineCurve = new THREE.CatmullRomCurve3([
    new THREE.Vector3(0, 0.380,  0.280),
    new THREE.Vector3(0, 0.383,  0.259),
    new THREE.Vector3(0, 0.405,  0.188),
    new THREE.Vector3(0, 0.399,  0.115),
    new THREE.Vector3(0, 0.385,  0.041),
    new THREE.Vector3(0, 0.372, -0.033),
    new THREE.Vector3(0, 0.367, -0.106),
    new THREE.Vector3(0, 0.350, -0.177),
    new THREE.Vector3(0, 0.510, -0.166),
    new THREE.Vector3(0, 0.580, -0.196),
    new THREE.Vector3(0, 0.655, -0.236),
    new THREE.Vector3(0, 0.726, -0.276),
    new THREE.Vector3(0, 0.750, -0.313),
  ], false, 'centripetal');
  const spineGeo = new THREE.TubeGeometry(spineCurve, 48, BAR_R, 12, false);
  spineGeo.scale(1.2, 1.0, 1.0);
  geos.push(spineGeo);

  // 2. Lower structural arch / truss reaching down to y = 0.170
  const lowerTrussCurve = new THREE.CatmullRomCurve3([
    new THREE.Vector3(0, 0.385,  0.180),
    new THREE.Vector3(0, 0.270,  0.100),
    new THREE.Vector3(0, 0.170, -0.020),
    new THREE.Vector3(0, 0.250, -0.100),
    new THREE.Vector3(0, 0.350, -0.165),
  ], false, 'centripetal');
  const lowerTrussGeo = new THREE.TubeGeometry(lowerTrussCurve, 28, BAR_R, 12, false);
  geos.push(lowerTrussGeo);

  // 3. Rear bracing spur reaching backward to z = -0.308, y = 0.220
  const rearSpurCurve = new THREE.CatmullRomCurve3([
    new THREE.Vector3(0, 0.170, -0.020),
    new THREE.Vector3(0, 0.190, -0.160),
    new THREE.Vector3(0, 0.220, -0.308),
  ], false, 'centripetal');
  const rearSpurGeo = new THREE.TubeGeometry(rearSpurCurve, 20, BAR_R * 0.9, 10, false);
  geos.push(rearSpurGeo);

  // 4. Decorative internal reinforcing web / scroll (embedded at both ends)
  const scrollCurve = new THREE.CatmullRomCurve3([
    new THREE.Vector3(0, 0.370, -0.030),
    new THREE.Vector3(0, 0.270, -0.015),
    new THREE.Vector3(0, 0.220, -0.055),
    new THREE.Vector3(0, 0.280, -0.090),
  ], false, 'centripetal');
  const scrollGeo = new THREE.TubeGeometry(scrollCurve, 16, BAR_R * 0.8, 8, false);
  geos.push(scrollGeo);

  const mergedGeo = mergeGeometries(geos);
  mergedGeo.computeVertexNormals();

  const material = new THREE.MeshStandardMaterial({
    color: 0x183424,
    roughness: 0.45,
    metalness: 0.35,
  });

  const mesh = new THREE.Mesh(mergedGeo, material);
  mesh.name = 'CenterSupportRib';
  group.add(mesh);

  return group;
}

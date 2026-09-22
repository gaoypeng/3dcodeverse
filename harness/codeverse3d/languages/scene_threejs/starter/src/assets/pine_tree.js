// src/assets/pine_tree.js — reusable asset factory.
//   export function buildPineTree(THREE) → THREE.Group  (origin at the base, +Y up, meters)
//   export function pineTreeParts(THREE) → [{ geometry, material, castShadow }]  for InstancedMesh use
// Geometry/material are created once per call and shared by the instancing zone.
import * as THREE from 'three';

export function pineTreeParts(T = THREE) {
  const trunkGeo = new T.CylinderGeometry(0.12, 0.22, 1.6, 7);
  trunkGeo.translate(0, 0.8, 0);
  const trunkMat = new T.MeshStandardMaterial({ color: 0x5a3a22, roughness: 0.9 });
  const canopy = new T.ConeGeometry(1.25, 3.0, 9);
  canopy.translate(0, 2.6, 0);
  const canopy2 = new T.ConeGeometry(0.9, 2.2, 9);
  canopy2.translate(0, 3.9, 0);
  const leafMat = new T.MeshStandardMaterial({ color: 0x2f6b2a, roughness: 0.85 });
  return [
    { geometry: trunkGeo, material: trunkMat, castShadow: true },
    { geometry: canopy, material: leafMat, castShadow: true },
    { geometry: canopy2, material: leafMat, castShadow: true },
  ];
}

export function buildPineTree(T = THREE) {
  const g = new T.Group();
  g.name = 'PineTree';
  for (const p of pineTreeParts(T)) {
    const m = new T.Mesh(p.geometry, p.material);
    m.castShadow = true;
    m.receiveShadow = true;
    g.add(m);
  }
  return g;
}

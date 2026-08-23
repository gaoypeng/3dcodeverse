// src/zones/meadow.js — one zone = one named THREE.Group.
//   export function build(ctx) → THREE.Group   (ctx: { THREE, scene, env, heightAt, renderer, loaders })
// Shows: instanced trees from an asset factory (pineTreeParts), seating on heightAt, a windmill
// with its own update, and fan-out of animation through group.userData.update(t, dt).
import * as THREE from 'three';
import { pineTreeParts } from '../assets/pine_tree.js';
import { buildWindmill } from '../assets/windmill.js';

// deterministic PRNG: same layout every run (the harness diffs renders between rounds)
function rng(seed) { let s = seed >>> 0; return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296); }

export function build(ctx) {
  const { heightAt } = ctx;
  const zone = new THREE.Group();
  zone.name = 'Meadow';

  // --- instanced pine trees (one InstancedMesh per part; shared matrices)
  const rand = rng(7);
  const N = 140;
  const mats = [];
  for (let i = 0; i < N; i++) {
    const a = rand() * Math.PI * 2, r = 14 + rand() * 30;
    const x = Math.cos(a) * r, z = Math.sin(a) * r;
    if (x < -2 && z > 0 && x > -18 && z < 18) continue;        // keep the pond clear
    const s = 0.7 + rand() * 0.9;
    const m = new THREE.Matrix4().compose(
      new THREE.Vector3(x, heightAt(x, z) - 0.05, z),
      new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), rand() * Math.PI * 2),
      new THREE.Vector3(s, s, s),
    );
    mats.push(m);
  }
  for (const part of pineTreeParts(THREE)) {
    const im = new THREE.InstancedMesh(part.geometry, part.material, mats.length);
    mats.forEach((m, i) => im.setMatrixAt(i, m));
    im.instanceMatrix.needsUpdate = true;
    im.castShadow = part.castShadow;
    im.receiveShadow = true;
    im.name = 'PineTrees';
    zone.add(im);
  }

  // --- a few rocks
  const rock = new THREE.MeshStandardMaterial({ color: 0x7b7b78, roughness: 0.95 });
  for (let i = 0; i < 12; i++) {
    const x = -20 + rand() * 40, z = -20 + rand() * 40;
    if (x < -2 && z > 0 && x > -18 && z < 18) continue;
    const s = 0.3 + rand() * 0.7;
    const mesh = new THREE.Mesh(new THREE.DodecahedronGeometry(s, 1), rock);
    mesh.position.set(x, heightAt(x, z) + s * 0.4, z);
    mesh.scale.set(1, 0.6 + rand() * 0.3, 1);
    mesh.rotation.y = rand() * 3;
    mesh.castShadow = mesh.receiveShadow = true;
    mesh.name = 'Rock';
    zone.add(mesh);
  }

  // --- windmill (animated asset)
  const mill = buildWindmill(THREE);
  mill.position.set(12, heightAt(12, -2), -2);
  mill.rotation.y = Math.PI * 0.15;
  zone.add(mill);

  zone.userData.update = (t, dt) => { mill.userData.update(t, dt); };
  return zone;
}

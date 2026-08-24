// src/zones/canyon_wash_floor.js — zone "CanyonWashFloor": Central sandy gorge floor with a sinuous dry cobble riverbed flanked by scattered rock piles, dead junipers, and desert scrub.
// Bbox: centre (0.000, 2.000, 0.000) m, extents (26.000, 6.000, 120.000) m.
// Bbox range: X in [-12.5, 12.5], Z in [-58, 58], Y in [-1, 5]

import * as THREE from 'three';
import { buildHeroBoulder } from '../assets/hero_boulder.js';
import { buildTalusRock } from '../assets/talus_rock.js';
import { buildDeadScrub } from '../assets/dead_scrub.js';

// Deterministic PRNG
function createRNG(seed = 45678) {
  let s = Math.floor(seed) || 45678;
  return function() {
    let t = (s += 0x6D2B79F5);
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function buildCanyonWashFloor(ctx) {
  const { heightAt, rand: parentRand } = ctx;
  const rand = parentRand || createRNG(78912);

  const zone = new THREE.Group();
  zone.name = 'CanyonWashFloor';

  // Materials for riverbed pebbles / cobble bed
  const matCobble1 = new THREE.MeshStandardMaterial({
    color: 0x8a5538,
    roughness: 0.88,
    metalness: 0.03,
    flatShading: true
  });
  const matCobble2 = new THREE.MeshStandardMaterial({
    color: 0xa86847,
    roughness: 0.92,
    metalness: 0.02,
    flatShading: true
  });
  const matSilt = new THREE.MeshStandardMaterial({
    color: 0xba764d,
    roughness: 0.95,
    metalness: 0.01,
    flatShading: true
  });

  // 1. Sinuous dry cobble riverbed ribbon made with InstancedMesh
  const cobbleGeo = new THREE.DodecahedronGeometry(0.35, 0);
  const cobblePos = cobbleGeo.attributes.position;
  for (let i = 0; i < cobblePos.count; i++) {
    cobblePos.setY(i, cobblePos.getY(i) * 0.45);
  }
  cobbleGeo.computeVertexNormals();

  const N_COBBLES = 120;
  const cobbleMesh1 = new THREE.InstancedMesh(cobbleGeo, matCobble1, N_COBBLES);
  const cobbleMesh2 = new THREE.InstancedMesh(cobbleGeo, matCobble2, N_COBBLES);
  cobbleMesh1.castShadow = cobbleMesh1.receiveShadow = true;
  cobbleMesh2.castShadow = cobbleMesh2.receiveShadow = true;

  const mat4 = new THREE.Matrix4();
  const q = new THREE.Quaternion();
  const scale = new THREE.Vector3();
  const pos = new THREE.Vector3();
  const euler = new THREE.Euler();

  for (let i = 0; i < N_COBBLES; i++) {
    const z = -56 + (112 * i) / N_COBBLES + (rand() - 0.5) * 1.5;
    const washCenter = 2.5 * Math.sin(z * 0.04) + 1.2 * Math.sin(z * 0.09);
    const x1 = THREE.MathUtils.clamp(washCenter + (rand() - 0.5) * 4.5, -11.0, 11.0);
    const y1 = heightAt(x1, z) + 0.05;

    const s1 = 0.5 + rand() * 0.8;
    scale.set(s1 * (0.8 + rand() * 0.4), s1 * 0.6, s1 * (0.8 + rand() * 0.4));
    euler.set(rand() * 0.3, rand() * Math.PI * 2, rand() * 0.3);
    q.setFromEuler(euler);
    pos.set(x1, y1, z);
    mat4.compose(pos, q, scale);
    cobbleMesh1.setMatrixAt(i, mat4);

    const x2 = THREE.MathUtils.clamp(washCenter + (rand() - 0.5) * 5.0, -11.0, 11.0);
    const y2 = heightAt(x2, z) + 0.04;
    const s2 = 0.4 + rand() * 0.7;
    scale.set(s2 * (0.8 + rand() * 0.4), s2 * 0.5, s2 * (0.8 + rand() * 0.4));
    euler.set(rand() * 0.3, rand() * Math.PI * 2, rand() * 0.3);
    q.setFromEuler(euler);
    pos.set(x2, y2, z);
    mat4.compose(pos, q, scale);
    cobbleMesh2.setMatrixAt(i, mat4);
  }
  cobbleMesh1.instanceMatrix.needsUpdate = true;
  cobbleMesh2.instanceMatrix.needsUpdate = true;
  cobbleMesh1.computeBoundingSphere();
  cobbleMesh2.computeBoundingSphere();
  zone.add(cobbleMesh1, cobbleMesh2);

  // 2. Low silt drift mounds along wash edges
  const moundGeo = new THREE.CylinderGeometry(1.8, 2.8, 0.4, 8);
  const moundMesh = new THREE.InstancedMesh(moundGeo, matSilt, 24);
  moundMesh.receiveShadow = true;
  for (let i = 0; i < 24; i++) {
    const z = -52 + (104 * i) / 24 + (rand() - 0.5) * 2.5;
    const washCenter = 2.5 * Math.sin(z * 0.04) + 1.2 * Math.sin(z * 0.09);
    const side = (i % 2 === 0 ? 1 : -1);
    const x = THREE.MathUtils.clamp(washCenter + side * (3.0 + rand() * 2.5), -11.0, 11.0);
    const y = heightAt(x, z) + 0.1;
    const s = 0.8 + rand() * 0.6;
    scale.set(s, s * 0.5, s * 1.4);
    euler.set(0, rand() * Math.PI, 0);
    q.setFromEuler(euler);
    pos.set(x, y, z);
    mat4.compose(pos, q, scale);
    moundMesh.setMatrixAt(i, mat4);
  }
  moundMesh.instanceMatrix.needsUpdate = true;
  moundMesh.computeBoundingSphere();
  zone.add(moundMesh);

  // 3. Hero Boulders: Placed prominently along the canyon wash
  // Master hero boulder templates to clone and share materials
  const heroTemplates = [
    buildHeroBoulder(THREE, { seed: 2001, variant: 0 }),
    buildHeroBoulder(THREE, { seed: 2051, variant: 1 }),
    buildHeroBoulder(THREE, { seed: 2101, variant: 2 }),
  ];

  const heroPositions = [
    { x: 1.5, z: -10.2, rotY: 0.35, scale: 1.0, template: 0 },
    { x: -6.5, z: 2.0, rotY: 1.1, scale: 0.95, template: 1 },
    { x: 5.2, z: 22.0, rotY: 2.4, scale: 1.1, template: 2 },
    { x: -5.0, z: -32.0, rotY: -0.8, scale: 1.05, template: 0 },
    { x: 4.0, z: 42.0, rotY: 0.7, scale: 0.9, template: 1 },
    { x: -3.8, z: -48.0, rotY: 2.1, scale: 1.0, template: 2 }
  ];

  for (let i = 0; i < heroPositions.length; i++) {
    const hp = heroPositions[i];
    const hb = heroTemplates[hp.template].clone(true);
    hb.scale.setScalar(hp.scale);
    hb.rotation.y = hp.rotY;
    hb.position.set(hp.x, heightAt(hp.x, hp.z), hp.z);
    zone.add(hb);
  }

  // 4. Talus Rock Clusters flanking the canyon floor and hero boulders
  const talusTemplates = [
    buildTalusRock(THREE, { seed: 5001, variant: 0 }),
    buildTalusRock(THREE, { seed: 5034, variant: 1 }),
    buildTalusRock(THREE, { seed: 5067, variant: 2 }),
    buildTalusRock(THREE, { seed: 5100, variant: 3 })
  ];

  const talusPositions = [
    { x: 3.2, z: -8.8, rotY: 0.5, s: 1.1, template: 0 },
    { x: -0.6, z: -11.5, rotY: 1.8, s: 0.95, template: 1 },
    { x: 2.8, z: -12.4, rotY: -1.2, s: 0.85, template: 2 },
    { x: -1.2, z: 12.0, rotY: 0.4, s: 1.0, template: 3 },
    { x: -4.8, z: 8.5, rotY: 2.2, s: 1.15, template: 0 },
    { x: 2.0, z: 6.0, rotY: -0.6, s: 0.9, template: 1 },
    { x: -7.5, z: 18.0, rotY: 1.5, s: 1.2, template: 2 },
    { x: 6.8, z: 15.0, rotY: -2.1, s: 1.05, template: 3 },
    { x: -6.2, z: 35.0, rotY: 0.8, s: 1.1, template: 0 },
    { x: 6.0, z: 32.0, rotY: -1.4, s: 0.95, template: 1 },
    { x: -2.5, z: 48.0, rotY: 2.7, s: 1.0, template: 2 },
    { x: 4.5, z: 52.0, rotY: -0.3, s: 1.2, template: 3 },
    { x: -7.0, z: -20.0, rotY: 1.2, s: 1.05, template: 0 },
    { x: 6.5, z: -24.0, rotY: -1.9, s: 1.15, template: 1 },
    { x: -2.0, z: -38.0, rotY: 0.6, s: 0.9, template: 2 },
    { x: 3.5, z: -42.0, rotY: 2.3, s: 1.0, template: 3 },
    { x: -5.5, z: -54.0, rotY: -0.8, s: 1.1, template: 0 },
    { x: 5.0, z: -52.0, rotY: 1.4, s: 1.05, template: 1 }
  ];

  for (let i = 0; i < talusPositions.length; i++) {
    const tp = talusPositions[i];
    const tr = talusTemplates[tp.template].clone(true);
    tr.scale.setScalar(tp.s);
    tr.rotation.y = tp.rotY;
    tr.position.set(tp.x, heightAt(tp.x, tp.z), tp.z);
    zone.add(tr);
  }

  // 5. Dead Scrub & Junipers scattered across wash banks and rock crevices
  const scrubTemplates = [
    buildDeadScrub(THREE, { seed: 8001, variant: 0 }),
    buildDeadScrub(THREE, { seed: 8048, variant: 1 }),
    buildDeadScrub(THREE, { seed: 8095, variant: 2 })
  ];

  const scrubPositions = [
    { x: 4.0, z: -9.5, rotY: 0.2, s: 0.95, template: 0 },
    { x: -0.8, z: -8.2, rotY: 1.4, s: 1.05, template: 1 },
    { x: -1.5, z: 14.5, rotY: 2.1, s: 1.1, template: 2 },
    { x: 1.2, z: 10.0, rotY: -0.7, s: 0.9, template: 0 },
    { x: -5.2, z: 4.0, rotY: 0.9, s: 1.0, template: 1 },
    { x: 7.2, z: 8.0, rotY: 1.8, s: 1.0, template: 2 },
    { x: -7.8, z: 24.0, rotY: -1.1, s: 1.15, template: 0 },
    { x: 3.8, z: 28.0, rotY: 0.5, s: 0.85, template: 1 },
    { x: -4.5, z: 40.0, rotY: 2.4, s: 1.0, template: 2 },
    { x: 5.5, z: 46.0, rotY: -2.0, s: 0.9, template: 0 },
    { x: 0.5, z: 54.0, rotY: 1.2, s: 1.05, template: 1 },
    { x: 6.2, z: -16.0, rotY: -0.5, s: 0.95, template: 2 },
    { x: -6.5, z: -26.0, rotY: 2.7, s: 1.1, template: 0 },
    { x: 2.5, z: -30.0, rotY: 0.3, s: 0.9, template: 1 },
    { x: -4.0, z: -44.0, rotY: -1.6, s: 1.0, template: 2 },
    { x: 4.2, z: -48.0, rotY: 1.9, s: 1.05, template: 0 }
  ];

  const animatedScrubs = [];
  for (let i = 0; i < scrubPositions.length; i++) {
    const sp = scrubPositions[i];
    const scrub = scrubTemplates[sp.template].clone(true);
    scrub.scale.setScalar(sp.s);
    scrub.rotation.y = sp.rotY;
    scrub.position.set(sp.x, heightAt(sp.x, sp.z), sp.z);
    zone.add(scrub);
    animatedScrubs.push({ obj: scrub, seed: i * 1.3 });
  }

  // Animation update hook
  zone.userData.update = (t, dt) => {
    for (let i = 0; i < animatedScrubs.length; i++) {
      const item = animatedScrubs[i];
      item.obj.rotation.z = Math.sin(t * 1.8 + item.seed) * 0.015;
    }
  };

  return zone;
}

// Support both build(ctx) and buildCanyonWashFloor(ctx)
export function build(ctx) {
  return buildCanyonWashFloor(ctx);
}

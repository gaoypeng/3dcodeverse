// src/zones/zen_garden.js — zone "ZenGarden"
// Flat dry-landscape area (karesansui) of raked light granite gravel with concentric wave patterns
// around isolated mossy rock groupings and stepping stones leading to the bridge.
// Bbox: centre (-6.5, 0.4, 3.5), extents (10 x 2 x 12) m.
// Contents: SteppingStone, PondRock, StoneLantern

import * as THREE from 'three';
import { buildSteppingStone } from '../assets/stepping_stone.js';
import { buildPondRock } from '../assets/pond_rock.js';
import { buildStoneLantern } from '../assets/stone_lantern.js';

function createRng(seed = 1337) {
  let s = (seed >>> 0) || 1;
  return function() {
    s = (s + 0x6D2B79F5) | 0;
    let t = Math.imul(s ^ (s >>> 15), 1 | s);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function build(ctx = {}) {
  const heightAt = ctx.heightAt || ((x, z) => 0);
  const zone = new THREE.Group();
  zone.name = 'ZenGarden';

  const rng = createRng(2025);
  const animTickers = [];

  // 1. Materials
  const gravelMat = new THREE.MeshStandardMaterial({
    color: 0xdcd6c8,
    roughness: 0.92,
    metalness: 0.02,
  });

  const ridgeMat = new THREE.MeshStandardMaterial({
    color: 0xc4bdae,
    roughness: 0.94,
    metalness: 0.02,
  });

  const mossMat = new THREE.MeshStandardMaterial({
    color: 0x4a602c,
    roughness: 0.96,
    metalness: 0.01,
    flatShading: true,
  });

  const woodMat = new THREE.MeshStandardMaterial({
    color: 0x3e2b20,
    roughness: 0.82,
    metalness: 0.04,
  });

  const tileMat = new THREE.MeshStandardMaterial({
    color: 0x525658,
    roughness: 0.76,
    metalness: 0.08,
  });

  const leafMat = new THREE.MeshStandardMaterial({
    color: 0xb8281a,
    roughness: 0.6,
    side: THREE.DoubleSide,
  });

  // 2. Main Raked Gravel Bed (centered at -6.5, 3.5; size 7.6 x 9.6 m)
  const bedCX = -6.5;
  const bedCZ = 3.5;
  const bedW = 7.6;
  const bedL = 9.6;
  const halfW = bedW * 0.5;
  const halfL = bedL * 0.5;

  const gravelGeo = new THREE.PlaneGeometry(bedW, bedL, 38, 46);
  gravelGeo.rotateX(-Math.PI / 2);
  const gPos = gravelGeo.attributes.position;
  for (let i = 0; i < gPos.count; i++) {
    const gx = gPos.getX(i) + bedCX;
    const gz = gPos.getZ(i) + bedCZ;
    gPos.setXYZ(i, gx, heightAt(gx, gz) + 0.014, gz);
  }
  gravelGeo.computeVertexNormals();

  const gravelMesh = new THREE.Mesh(gravelGeo, gravelMat);
  gravelMesh.receiveShadow = true;
  zone.add(gravelMesh);

  // 3. Perimeter Timber & Tile Border
  const makeBeam = (w, l, x, z) => {
    const m = new THREE.Mesh(new THREE.BoxGeometry(w, 0.08, l), woodMat);
    m.position.set(x, heightAt(x, z) + 0.03, z);
    m.castShadow = m.receiveShadow = true;
    zone.add(m);
  };
  makeBeam(bedW + 0.16, 0.1, bedCX, bedCZ - halfL); // North
  makeBeam(bedW + 0.16, 0.1, bedCX, bedCZ + halfL); // South
  makeBeam(0.1, bedL + 0.16, bedCX - halfW, bedCZ); // West
  makeBeam(0.1, 4.0, bedCX + halfW, 5.8);           // East

  // Kawara roof tiles along west border
  const tileCount = 24;
  const tileGeo = new THREE.CylinderGeometry(0.04, 0.04, 0.34, 7, 1, false, 0, Math.PI);
  tileGeo.rotateX(Math.PI / 2);
  const tileInst = new THREE.InstancedMesh(tileGeo, tileMat, tileCount);
  tileInst.castShadow = tileInst.receiveShadow = true;
  const tMat = new THREE.Matrix4();
  const tRot = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), Math.PI / 2);
  for (let i = 0; i < tileCount; i++) {
    const tz = (bedCZ - halfL + 0.4) + (i / (tileCount - 1)) * (bedL - 0.8);
    const tx = bedCX - halfW - 0.06;
    tMat.compose(new THREE.Vector3(tx, heightAt(tx, tz) + 0.06, tz), tRot, new THREE.Vector3(1, 1, 1));
    tileInst.setMatrixAt(i, tMat);
  }
  zone.add(tileInst);

  // 4. Concentric Ripple Ridges around Rock Islands
  const islands = [
    { x: -6.6, z: 3.6, rMax: 1.9, count: 6 }, // Central Sanzon triad
    { x: -8.6, z: 1.4, rMax: 1.3, count: 4 }, // Northwest rock
    { x: -4.8, z: 6.2, rMax: 1.2, count: 4 }, // Southeast rock
  ];

  for (const isl of islands) {
    for (let r = 1; r <= isl.count; r++) {
      const rad = (r / isl.count) * isl.rMax;
      const rGeo = new THREE.TorusGeometry(rad, 0.016, 4, 30);
      rGeo.rotateX(Math.PI / 2);
      const pos = rGeo.attributes.position;
      for (let k = 0; k < pos.count; k++) {
        const px = pos.getX(k) + isl.x;
        const pz = pos.getZ(k) + isl.z;
        pos.setXYZ(k, px, heightAt(px, pz) + 0.018, pz);
      }
      rGeo.computeVertexNormals();
      const rMesh = new THREE.Mesh(rGeo, ridgeMat);
      rMesh.receiveShadow = true;
      zone.add(rMesh);
    }
  }

  // Linear raked furrows across open gravel
  const numFurrows = 20;
  const fGeo = new THREE.CylinderGeometry(0.012, 0.012, 3.4, 4);
  fGeo.rotateZ(Math.PI / 2);
  const furrowInst = new THREE.InstancedMesh(fGeo, ridgeMat, numFurrows);
  furrowInst.receiveShadow = true;
  const fMat4 = new THREE.Matrix4();
  const fQ = new THREE.Quaternion();
  for (let i = 0; i < numFurrows; i++) {
    const fz = (bedCZ - halfL + 0.8) + (i / numFurrows) * (bedL - 1.6);
    const fx = bedCX - 1.3;
    const distToCenter = Math.hypot(fx - (-6.6), fz - 3.6);
    if (distToCenter < 1.8) {
      fMat4.compose(new THREE.Vector3(bedCX, 0, bedCZ), fQ, new THREE.Vector3(0, 0, 0));
    } else {
      const fy = heightAt(fx, fz) + 0.018;
      fMat4.compose(new THREE.Vector3(fx, fy, fz), fQ, new THREE.Vector3(1, 1, 1));
    }
    furrowInst.setMatrixAt(i, fMat4);
  }
  zone.add(furrowInst);

  // 5. Moss Cushions & Rock Formations (Sanzon Ishigumi)
  const makeMossMound = (mx, mz, radX, radZ, h) => {
    const geo = new THREE.CylinderGeometry(0.1, radX, h, 14);
    const pos = geo.attributes.position;
    for (let i = 0; i < pos.count; i++) {
      let x = pos.getX(i);
      let z = pos.getZ(i);
      const ang = Math.atan2(z, x);
      const r = Math.hypot(x, z);
      if (r > 0.01) {
        const mod = 1.0 + 0.15 * Math.sin(ang * 4.0);
        x *= mod;
        z *= mod * (radZ / radX);
      }
      pos.setXYZ(i, x, pos.getY(i), z);
    }
    geo.computeVertexNormals();
    const m = new THREE.Mesh(geo, mossMat);
    m.position.set(mx, heightAt(mx, mz) + h * 0.45, mz);
    m.castShadow = m.receiveShadow = true;
    zone.add(m);
  };

  // Group 1: Central Sanzon Triad
  makeMossMound(-6.6, 3.6, 1.25, 1.05, 0.16);
  const rock1 = buildPondRock(THREE, { seed: 101 });
  rock1.position.set(-6.6, heightAt(-6.6, 3.6) + 0.05, 3.6);
  rock1.rotation.y = 0.45;
  zone.add(rock1);

  const rock2 = buildPondRock(THREE, { seed: 202 });
  rock2.scale.set(0.7, 0.8, 0.7);
  rock2.position.set(-7.3, heightAt(-7.3, 3.8) + 0.03, 3.85);
  rock2.rotation.y = -0.8;
  zone.add(rock2);

  const rock3 = buildPondRock(THREE, { seed: 303 });
  rock3.scale.set(0.6, 0.55, 0.65);
  rock3.position.set(-5.9, heightAt(-5.9, 3.3) + 0.03, 3.35);
  rock3.rotation.y = 1.6;
  zone.add(rock3);

  // Group 2: Northwest Island
  makeMossMound(-8.6, 1.4, 0.85, 0.75, 0.14);
  const rock4 = buildPondRock(THREE, { seed: 404 });
  rock4.scale.set(0.8, 0.7, 0.8);
  rock4.position.set(-8.6, heightAt(-8.6, 1.4) + 0.04, 1.4);
  rock4.rotation.y = -1.2;
  zone.add(rock4);

  // Group 3: Southeast Island
  makeMossMound(-4.8, 6.2, 0.8, 0.7, 0.13);
  const rock5 = buildPondRock(THREE, { seed: 505 });
  rock5.scale.set(0.75, 0.65, 0.75);
  rock5.position.set(-4.8, heightAt(-4.8, 6.2) + 0.03, 6.2);
  rock5.rotation.y = 2.1;
  zone.add(rock5);

  // 6. Stepping Stone Path (Tobi-ishi) Leading to Bridge
  const pathWaypoints = [
    { x: -9.0, z: 7.6, rot: 0.1 },
    { x: -8.4, z: 6.7, rot: 0.35 },
    { x: -7.9, z: 5.7, rot: -0.2 },
    { x: -7.6, z: 4.7, rot: 0.15 },
    { x: -7.7, z: 3.6, rot: -0.4 },
    { x: -7.3, z: 2.5, rot: 0.25 },
    { x: -6.6, z: 1.6, rot: -0.1 },
    { x: -5.7, z: 0.9, rot: 0.3 },
    { x: -4.7, z: 0.4, rot: -0.25 },
    { x: -3.7, z: 0.0, rot: 0.2 },
    { x: -2.7, z: -0.4, rot: -0.15 },
    { x: -1.9, z: -0.8, rot: 0.1 },
  ];

  pathWaypoints.forEach((wp, idx) => {
    const stone = buildSteppingStone(THREE, { seed: 500 + idx * 29, variant: idx % 3 });
    stone.position.set(wp.x, heightAt(wp.x, wp.z), wp.z);
    stone.rotation.y = wp.rot;
    const sc = 0.9 + rng() * 0.18;
    stone.scale.set(sc, 1.0, sc);
    zone.add(stone);
  });

  // Secondary garden branch path
  const branchWaypoints = [
    { x: -7.0, z: 6.2, rot: 0.5 },
    { x: -6.1, z: 6.5, rot: 0.3 },
    { x: -5.2, z: 7.0, rot: 0.1 },
    { x: -4.3, z: 7.6, rot: 0.4 },
  ];
  branchWaypoints.forEach((wp, idx) => {
    const stone = buildSteppingStone(THREE, { seed: 800 + idx * 37, variant: (idx + 1) % 3 });
    stone.position.set(wp.x, heightAt(wp.x, wp.z), wp.z);
    stone.rotation.y = wp.rot;
    zone.add(stone);
  });

  // 7. Stone Lanterns (Kasuga & Yukimi Style)
  const lantern1 = buildStoneLantern(THREE, { seed: 101 });
  const l1x = -4.5;
  const l1z = 3.6;
  lantern1.position.set(l1x, heightAt(l1x, l1z), l1z);
  lantern1.rotation.y = -0.7;
  zone.add(lantern1);
  if (lantern1.userData.tick) animTickers.push(lantern1.userData.tick);
  makeMossMound(l1x, l1z, 0.55, 0.55, 0.07);

  const lantern2 = buildStoneLantern(THREE, { seed: 202 });
  const l2x = -9.4;
  const l2z = 7.2;
  lantern2.position.set(l2x, heightAt(l2x, l2z), l2z);
  lantern2.rotation.y = 0.9;
  lantern2.scale.set(0.85, 0.85, 0.85);
  zone.add(lantern2);
  if (lantern2.userData.tick) animTickers.push(lantern2.userData.tick);

  // 8. Scattered Autumn Maple Leaves (Momiji)
  const leafCount = 40;
  const leafGeo = new THREE.PlaneGeometry(0.07, 0.07);
  leafGeo.rotateX(-Math.PI / 2);
  const leafInst = new THREE.InstancedMesh(leafGeo, leafMat, leafCount);
  leafInst.receiveShadow = true;
  const lMat4 = new THREE.Matrix4();
  const lE = new THREE.Euler();
  const lQ = new THREE.Quaternion();
  for (let i = 0; i < leafCount; i++) {
    const lx = -9.2 + rng() * 6.2;
    const lz = -0.2 + rng() * 8.2;
    const ly = heightAt(lx, lz) + 0.02;
    lE.set((rng() - 0.5) * 0.2, rng() * Math.PI * 2, (rng() - 0.5) * 0.2);
    lQ.setFromEuler(lE);
    const sc = 0.7 + rng() * 0.5;
    lMat4.compose(new THREE.Vector3(lx, ly, lz), lQ, new THREE.Vector3(sc, sc, sc));
    leafInst.setMatrixAt(i, lMat4);
  }
  zone.add(leafInst);

  // 9. Zone Animation Update
  zone.userData.update = (t, dt) => {
    for (let i = 0; i < animTickers.length; i++) animTickers[i](t, dt);
  };

  return zone;
}

export const buildZenGarden = build;
export default build;

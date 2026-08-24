// src/zones/maple_grove_bank.js — zone "MapleGroveBank": Lush moss-covered elevated bank framing the northeast edge of the pond, dominated by a spreading Japanese maple with scarlet leaves, flanked by a traditional stone lantern and decorative river boulders.
// PLAN bbox: centre (4.500, 1.800, -5.000) m, extents (11.000, 5.000, 9.000) m
// Contents: JapaneseMaple, StoneLantern, PondRock, SteppingStone
import * as THREE from 'three';
import { buildJapaneseMaple } from '../assets/japanese_maple.js';
import { buildStoneLantern } from '../assets/stone_lantern.js';
import { buildPondRock } from '../assets/pond_rock.js';
import { buildSteppingStone } from '../assets/stepping_stone.js';

function mulberry32(a) {
  return function() {
    let t = (a += 0x6d2b79f5);
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function buildMapleGroveBank(ctx) {
  const T = ctx?.THREE || THREE;
  const heightAt = ctx?.heightAt || ((x, z) => 0);
  const rand = mulberry32(777);

  const zone = new THREE.Group();
  zone.name = 'MapleGroveBank';
  const animatedTickers = [];

  function placeAsset(obj, x, z, ry = 0, scale = 1.0) {
    if (scale !== 1.0) obj.scale.set(scale, scale, scale);
    obj.position.set(x, heightAt(x, z), z);
    obj.rotation.y = ry;
    zone.add(obj);
    if (obj.userData?.tick) animatedTickers.push(obj.userData.tick);
    return obj;
  }

  // 1. Dominant Japanese Maple & secondary grove maple
  const mainMaple = buildJapaneseMaple(T, { seed: 101 });
  placeAsset(mainMaple, 4.3, -4.8, 0.45, 1.05);

  const backMaple = buildJapaneseMaple(T, { seed: 202 });
  placeAsset(backMaple, 7.6, -6.6, 1.85, 0.78);

  // 2. Traditional Stone Lanterns (Yukimi-doro)
  const primaryLantern = buildStoneLantern(T, { seed: 55 });
  placeAsset(primaryLantern, 2.6, -3.2, 0.35, 1.0);

  const groveLantern = buildStoneLantern(T, { seed: 99 });
  placeAsset(groveLantern, 6.8, -4.2, 2.2, 0.72);

  // 3. Decorative River Boulders & Moss Rock Formations
  const rockPlacements = [
    { x: 1.8, z: -3.8, ry: 0.3, s: 0.95 },
    { x: 3.4, z: -2.4, ry: 1.8, s: 0.75 },
    { x: 5.2, z: -3.5, ry: 2.4, s: 1.05 },
    { x: 6.3, z: -5.6, ry: 0.9, s: 0.88 },
    { x: 7.8, z: -4.4, ry: 3.1, s: 0.92 },
    { x: 3.2, z: -5.8, ry: 1.4, s: 0.82 },
    { x: 5.6, z: -7.0, ry: 2.8, s: 0.70 },
  ];
  for (let i = 0; i < rockPlacements.length; i++) {
    const rp = rockPlacements[i];
    const rock = buildPondRock(T, { seed: 300 + i * 37, variant: i });
    placeAsset(rock, rp.x, rp.z, rp.ry, rp.s);
  }

  // 4. Stepping Stones along mossy bank
  const steppingStoneCoords = [
    { x: 1.2, z: -2.3, ry: 0.2 },
    { x: 1.8, z: -2.7, ry: -0.4 },
    { x: 2.3, z: -3.6, ry: 0.5 },
    { x: 3.1, z: -4.2, ry: 0.1 },
    { x: 3.9, z: -4.9, ry: -0.3 },
    { x: 4.8, z: -5.5, ry: 0.4 },
    { x: 5.7, z: -6.1, ry: -0.2 },
    { x: 6.6, z: -6.5, ry: 0.3 },
  ];
  for (let i = 0; i < steppingStoneCoords.length; i++) {
    const sc = steppingStoneCoords[i];
    const sStone = buildSteppingStone(T, { seed: 500 + i * 23 });
    placeAsset(sStone, sc.x, sc.z, sc.ry, 0.95);
  }

  // 5. Lush Moss Mounds
  const mossMat1 = new T.MeshStandardMaterial({ color: 0x486427, roughness: 0.94, metalness: 0.02 });
  const mossMat2 = new T.MeshStandardMaterial({ color: 0x587830, roughness: 0.90, metalness: 0.02 });
  const moundSpecs = [
    { x: 3.8, z: -4.6, rx: 2.2, rz: 1.8, h: 0.38, mat: mossMat1 },
    { x: 5.4, z: -5.2, rx: 2.6, rz: 2.1, h: 0.44, mat: mossMat2 },
    { x: 6.8, z: -6.2, rx: 2.4, rz: 1.9, h: 0.36, mat: mossMat1 },
    { x: 2.5, z: -3.6, rx: 1.4, rz: 1.3, h: 0.22, mat: mossMat2 },
    { x: 7.2, z: -4.6, rx: 1.7, rz: 1.5, h: 0.28, mat: mossMat1 },
    { x: 4.6, z: -6.4, rx: 2.0, rz: 1.6, h: 0.32, mat: mossMat2 },
  ];
  for (const m of moundSpecs) {
    const moundGeo = new T.SphereGeometry(1.0, 10, 6);
    moundGeo.scale(m.rx, m.h, m.rz);
    const moundMesh = new T.Mesh(moundGeo, m.mat);
    moundMesh.position.set(m.x, heightAt(m.x, m.z) + m.h * 0.2, m.z);
    moundMesh.rotation.y = rand() * Math.PI * 2;
    moundMesh.castShadow = moundMesh.receiveShadow = true;
    zone.add(moundMesh);
  }

  // 6. Fallen Autumn Leaves
  const leafMatRed = new T.MeshStandardMaterial({ color: 0xb01c14, roughness: 0.6, metalness: 0.0, side: T.DoubleSide });
  const NUM_GROUND_LEAVES = 40;
  const leafDiscGeo = new T.CircleGeometry(0.045, 5);
  leafDiscGeo.rotateX(-Math.PI / 2);
  const groundLeavesMesh = new T.InstancedMesh(leafDiscGeo, leafMatRed, NUM_GROUND_LEAVES);
  groundLeavesMesh.receiveShadow = true;

  const mat4 = new THREE.Matrix4();
  const dummyPos = new THREE.Vector3();
  const dummyQuat = new THREE.Quaternion();
  const dummyScale = new THREE.Vector3();
  const dummyEuler = new THREE.Euler();

  for (let i = 0; i < NUM_GROUND_LEAVES; i++) {
    const rad = 0.6 + 2.8 * Math.sqrt(rand());
    const theta = rand() * Math.PI * 2;
    const lx = 4.3 + Math.cos(theta) * rad;
    const lz = -4.8 + Math.sin(theta) * rad;
    const ly = heightAt(lx, lz) + 0.015 + rand() * 0.02;

    dummyPos.set(lx, ly, lz);
    dummyEuler.set((rand() - 0.5) * 0.2, rand() * Math.PI * 2, (rand() - 0.5) * 0.2);
    dummyQuat.setFromEuler(dummyEuler);
    const sc = 0.8 + rand() * 0.6;
    dummyScale.set(sc, sc, sc);
    mat4.compose(dummyPos, dummyQuat, dummyScale);
    groundLeavesMesh.setMatrixAt(i, mat4);
  }
  groundLeavesMesh.instanceMatrix.needsUpdate = true;
  zone.add(groundLeavesMesh);

  // 7. Falling Leaf Particle System
  const leafMatOrange = new T.MeshStandardMaterial({ color: 0xd65218, roughness: 0.6, metalness: 0.0, side: T.DoubleSide });
  const NUM_FALLING = 32;
  const fallingLeafGeo = new T.PlaneGeometry(0.065, 0.05);
  fallingLeafGeo.rotateX(-Math.PI * 0.35);
  const fallingMesh = new T.InstancedMesh(fallingLeafGeo, leafMatOrange, NUM_FALLING);
  fallingMesh.castShadow = true;

  const leafData = [];
  for (let i = 0; i < NUM_FALLING; i++) {
    const startX = 2.5 + rand() * 4.0;
    const startZ = -6.5 + rand() * 3.5;
    const startY = 2.0 + rand() * 2.2;
    leafData.push({
      x: startX,
      y: startY,
      z: startZ,
      initialY: startY,
      fallSpeed: 0.22 + rand() * 0.08,
      swayFreq: 0.5 + rand() * 0.3,
      swayPhase: rand() * Math.PI * 2,
      swayAmp: 0.10 + rand() * 0.06,
      rotSpeed: 1.0 + rand() * 2.0,
      seed: rand() * 10,
    });
  }
  zone.add(fallingMesh);

  // 8. Animation Update Loop
  zone.userData.update = (t, dt) => {
    for (let k = 0; k < animatedTickers.length; k++) animatedTickers[k](t, dt);

    const matrix = new THREE.Matrix4();
    const p = new THREE.Vector3();
    const q = new THREE.Quaternion();
    const s = new THREE.Vector3(1, 1, 1);
    const eul = new THREE.Euler();

    for (let i = 0; i < NUM_FALLING; i++) {
      const ld = leafData[i];
      const totalFall = (t * ld.fallSpeed) % 4.0;
      let currentY = ld.initialY - totalFall;
      const groundY = heightAt(ld.x, ld.z) + 0.05;
      if (currentY < groundY) currentY = groundY + (4.0 - (groundY - currentY));

      const lateralX = ld.x + Math.sin(t * ld.swayFreq + ld.swayPhase) * ld.swayAmp;
      const lateralZ = ld.z + Math.cos(t * ld.swayFreq * 0.8 + ld.swayPhase) * (ld.swayAmp * 0.7);

      p.set(lateralX, currentY, lateralZ);
      eul.set(Math.sin(t * ld.rotSpeed + ld.seed) * 0.6, t * ld.rotSpeed * 0.5 + ld.seed, Math.cos(t * ld.rotSpeed * 0.7 + ld.seed) * 0.4);
      q.setFromEuler(eul);
      matrix.compose(p, q, s);
      fallingMesh.setMatrixAt(i, matrix);
    }
    fallingMesh.instanceMatrix.needsUpdate = true;
  };

  return zone;
}

export const build = buildMapleGroveBank;

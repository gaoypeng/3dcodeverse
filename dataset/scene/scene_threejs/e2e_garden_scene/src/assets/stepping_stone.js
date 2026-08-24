// src/assets/stepping_stone.js — asset "SteppingStone": Flat natural slate stepping stone (tobi-ishi) set slightly proud of the moss or pond edge.
// Size: 0.65 x 0.12 x 0.55 m (w x h x d)
import * as THREE from 'three';

function mulberry32(a) {
  return function() {
    let t = (a += 0x6D2B79F5);
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function buildSteppingStone(T = THREE, opts = {}) {
  const seed = (opts.seed !== undefined ? opts.seed : 108) + (opts.variant !== undefined ? opts.variant * 1543 : 0);
  const rand = mulberry32(seed);

  const group = new T.Group();
  group.name = 'SteppingStone';

  const targetW = 0.65;
  const targetH = 0.12;
  const targetD = 0.55;

  const stoneAssembly = new T.Group();

  // 1. Main Slate Body (Tobi-Ishi)
  const radialSegments = 32;
  const heightSegments = 8;
  const rawRadius = 0.30;
  const rawHeight = 0.11;
  const slateGeo = new T.CylinderGeometry(rawRadius, rawRadius * 0.96, rawHeight, radialSegments, heightSegments);
  
  const pos = slateGeo.attributes.position;
  const v = new T.Vector3();
  const halfH = rawHeight / 2;

  const phase1 = rand() * Math.PI * 2;
  const phase2 = rand() * Math.PI * 2;
  const phase3 = rand() * Math.PI * 2;

  for (let i = 0; i < pos.count; i++) {
    v.fromBufferAttribute(pos, i);
    const theta = Math.atan2(v.z, v.x);
    const rCurrent = Math.sqrt(v.x * v.x + v.z * v.z);
    const normY = (v.y + halfH) / rawHeight; // 0 at bottom, 1 at top

    // Asymmetric natural boulder / slate slab outline
    const shapeMod = 1.0 
      + 0.14 * Math.cos(2 * theta + phase1)
      + 0.08 * Math.sin(3 * theta + phase2)
      + 0.04 * Math.cos(5 * theta + phase3);

    // Natural slate side stratification (subtle cleft horizontal bands)
    const cleftMod = 1.0 + 0.025 * Math.sin(v.y * 120.0 + theta * 2.0);

    // Top surface natural gentle undulation & slight convex crown for drainage
    let topRelief = 0;
    if (normY > 0.85) {
      const topFactor = (normY - 0.85) / 0.15;
      const centerDist = rCurrent / (rawRadius * shapeMod + 0.001);
      // Gentle dome + subtle micro-relief
      topRelief = topFactor * (
        0.008 * (1.0 - centerDist * centerDist)
        + 0.003 * Math.sin(v.x * 18.0 + v.z * 14.0)
        + 0.002 * Math.cos(v.x * 28.0 - v.z * 22.0)
      );
      // Weathered edge softening
      if (centerDist > 0.85) {
        topRelief -= 0.004 * (centerDist - 0.85) / 0.15;
      }
    }

    if (rCurrent > 0.001) {
      const newR = rCurrent * shapeMod * cleftMod;
      v.x = Math.cos(theta) * newR;
      v.z = Math.sin(theta) * newR;
    }
    v.y += topRelief;

    pos.setXYZ(i, v.x, v.y, v.z);
  }
  slateGeo.computeVertexNormals();

  // Dark charcoal Kurama slate material with soft wet sheen
  const slateMat = new T.MeshStandardMaterial({
    color: 0x3d4144,
    roughness: 0.82,
    metalness: 0.04,
  });

  const slateMesh = new T.Mesh(slateGeo, slateMat);
  slateMesh.name = 'SlateSlab';
  slateMesh.castShadow = true;
  slateMesh.receiveShadow = true;
  slateMesh.position.y = halfH;
  stoneAssembly.add(slateMesh);

  // 2. Secondary Chipped Slate Ledge / Layer (sedimentary split characteristic)
  const ledgeGeo = new T.CylinderGeometry(rawRadius * 0.92, rawRadius * 0.95, 0.035, 24, 2);
  const lPos = ledgeGeo.attributes.position;
  for (let i = 0; i < lPos.count; i++) {
    v.fromBufferAttribute(lPos, i);
    const theta = Math.atan2(v.z, v.x);
    const rCurrent = Math.sqrt(v.x * v.x + v.z * v.z);
    if (rCurrent > 0.001) {
      const shapeMod = 1.0 
        + 0.16 * Math.cos(2 * theta + phase1 + 0.4)
        + 0.09 * Math.sin(3 * theta + phase2)
        + 0.05 * Math.sin(7 * theta);
      const newR = rCurrent * shapeMod;
      v.x = Math.cos(theta) * newR;
      v.z = Math.sin(theta) * newR;
    }
    lPos.setXYZ(i, v.x, v.y, v.z);
  }
  ledgeGeo.computeVertexNormals();

  const ledgeMat = new T.MeshStandardMaterial({
    color: 0x323538,
    roughness: 0.88,
    metalness: 0.02,
  });
  const ledgeMesh = new T.Mesh(ledgeGeo, ledgeMat);
  ledgeMesh.name = 'SubLedge';
  ledgeMesh.position.y = 0.025;
  ledgeMesh.castShadow = true;
  ledgeMesh.receiveShadow = true;
  stoneAssembly.add(ledgeMesh);

  // 3. Velvety Moss Cushion Accent (nestled in perimeter crevices)
  const mossCount = 3;
  const mossMat = new T.MeshStandardMaterial({
    color: 0x4a652a,
    roughness: 0.95,
    metalness: 0.0,
  });

  const mossGroup = new T.Group();
  mossGroup.name = 'MossPatches';

  for (let m = 0; m < mossCount; m++) {
    const angle = phase1 + (m / mossCount) * Math.PI * 1.6 + 0.3 * rand();
    const patchRadius = 0.05 + 0.03 * rand();
    const patchGeo = new T.SphereGeometry(patchRadius, 8, 6);
    // Flatten sphere into a low cushion clinging to the edge
    const mPos = patchGeo.attributes.position;
    for (let k = 0; k < mPos.count; k++) {
      v.fromBufferAttribute(mPos, k);
      v.y *= 0.45;
      mPos.setXYZ(k, v.x, v.y, v.z);
    }
    patchGeo.computeVertexNormals();

    const patchMesh = new T.Mesh(patchGeo, mossMat);
    const rDist = rawRadius * (0.85 + 0.12 * Math.cos(2 * angle + phase1));
    patchMesh.position.set(
      Math.cos(angle) * rDist,
      0.035 + 0.015 * rand(),
      Math.sin(angle) * rDist
    );
    patchMesh.rotation.set(0.1 * rand(), rand() * Math.PI, 0.1 * rand());
    patchMesh.scale.set(1.4 + 0.3 * rand(), 0.9, 1.0 + 0.2 * rand());
    patchMesh.castShadow = true;
    patchMesh.receiveShadow = true;
    mossGroup.add(patchMesh);
  }
  stoneAssembly.add(mossGroup);

  // 4. Setting Bedding Pebbles (subtle base contact stones)
  const pebbleGroup = new T.Group();
  pebbleGroup.name = 'BeddingPebbles';
  const pebbleCount = 4;
  const pebbleMat = new T.MeshStandardMaterial({
    color: 0x2b2d30,
    roughness: 0.90,
  });

  for (let p = 0; p < pebbleCount; p++) {
    const pAngle = phase2 + (p / pebbleCount) * Math.PI * 2 + 0.4 * rand();
    const pSize = 0.02 + 0.015 * rand();
    const pGeo = new T.DodecahedronGeometry(pSize, 1);
    const pMesh = new T.Mesh(pGeo, p % 2 === 0 ? pebbleMat : slateMat);
    const pDist = rawRadius * 0.92;
    pMesh.position.set(
      Math.cos(pAngle) * pDist,
      pSize * 0.6,
      Math.sin(pAngle) * pDist
    );
    pMesh.rotation.set(rand() * 3, rand() * 3, rand() * 3);
    pMesh.scale.set(1.2, 0.7, 1.0);
    pMesh.castShadow = true;
    pMesh.receiveShadow = true;
    pebbleGroup.add(pMesh);
  }
  stoneAssembly.add(pebbleGroup);

  // Normalize and scale assembly to exact target dimensions: 0.65 x 0.12 x 0.55
  const box = new T.Box3().setFromObject(stoneAssembly);
  const currentSize = new T.Vector3();
  box.getSize(currentSize);

  const scaleX = targetW / currentSize.x;
  const scaleY = targetH / currentSize.y;
  const scaleZ = targetD / currentSize.z;
  stoneAssembly.scale.set(scaleX, scaleY, scaleZ);

  // Align footprint to origin center and bottom at y = 0
  const finalBox = new T.Box3().setFromObject(stoneAssembly);
  const finalCenter = new T.Vector3();
  finalBox.getCenter(finalCenter);

  stoneAssembly.position.x = -finalCenter.x;
  stoneAssembly.position.y = -finalBox.min.y;
  stoneAssembly.position.z = -finalCenter.z;

  group.add(stoneAssembly);

  group.userData.size = [targetW, targetH, targetD];

  return group;
}

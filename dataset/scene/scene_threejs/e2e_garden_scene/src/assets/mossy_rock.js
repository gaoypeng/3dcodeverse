// src/assets/mossy_rock.js — asset "MossyRock": Subdivided weathered granite boulder with green velvety moss patches on upward faces.
// Size: 1.20 x 0.80 x 1.00 m (w x h x d)
import * as THREE from 'three';

function mulberry32(a) {
  return function() {
    let t = (a += 0x6D2B79F5);
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function buildMossyRock(T = THREE, opts = {}) {
  const seed = (opts.seed !== undefined ? opts.seed : 42) + (opts.variant !== undefined ? opts.variant * 1013 : 0);
  const rand = mulberry32(seed);

  const group = new T.Group();
  group.name = 'MossyRock';

  const targetW = 1.20;
  const targetH = 0.80;
  const targetD = 1.00;

  // Base Rock Geometry created using a subdivided icosahedron
  const rockGeo = new T.IcosahedronGeometry(0.6, 4);
  const pos = rockGeo.attributes.position;
  const v = new T.Vector3();

  // Multi-frequency organic deformation for weathered granite look
  for (let i = 0; i < pos.count; i++) {
    v.fromBufferAttribute(pos, i);
    
    const len = v.length();
    const nx = v.x / len;
    const ny = v.y / len;
    const nz = v.z / len;

    // Organic boulder noise
    const f1 = Math.sin(nx * 3.5 + rand() * 0.3) * Math.cos(ny * 3.2) * Math.sin(nz * 3.7);
    const f2 = Math.sin(nx * 7.5 + nz * 6.5) * 0.25;
    const f3 = Math.cos(ny * 8.0 + nx * 5.0) * 0.15;
    
    let disp = 1.0 + 0.22 * f1 + 0.08 * f2 + 0.04 * f3;
    if (ny < -0.2) {
      disp *= 0.82 + 0.18 * (ny + 1.0); // flatter sitting base
    }
    
    v.x *= (1.05 + 0.12 * Math.sin(nz * 2.2)) * disp;
    v.y *= (0.72 + 0.08 * Math.cos(nx * 2.5)) * disp;
    v.z *= (0.92 + 0.10 * Math.sin(ny * 2.4)) * disp;

    pos.setXYZ(i, v.x, v.y, v.z);
  }
  rockGeo.computeVertexNormals();

  // Granite rock material with subtle warm-grey tone
  const rockMat = new T.MeshStandardMaterial({
    color: 0x6e6b66,
    roughness: 0.94,
    metalness: 0.02,
  });

  const rockMesh = new T.Mesh(rockGeo, rockMat);
  rockMesh.name = 'BoulderCore';
  rockMesh.castShadow = true;
  rockMesh.receiveShadow = true;

  // Moss layer geometry: cloned and displaced along top surfaces
  const mossGeo = new T.IcosahedronGeometry(0.603, 4);
  const mossPos = mossGeo.attributes.position;

  for (let i = 0; i < mossPos.count; i++) {
    v.fromBufferAttribute(mossPos, i);
    const len = v.length();
    const nx = v.x / len;
    const ny = v.y / len;
    const nz = v.z / len;

    const f1 = Math.sin(nx * 3.5 + rand() * 0.3) * Math.cos(ny * 3.2) * Math.sin(nz * 3.7);
    const f2 = Math.sin(nx * 7.5 + nz * 6.5) * 0.25;
    const f3 = Math.cos(ny * 8.0 + nx * 5.0) * 0.15;
    
    let disp = 1.0 + 0.22 * f1 + 0.08 * f2 + 0.04 * f3;
    if (ny < -0.2) {
      disp *= 0.82 + 0.18 * (ny + 1.0);
    }
    
    v.x *= (1.05 + 0.12 * Math.sin(nz * 2.2)) * disp;
    v.y *= (0.72 + 0.08 * Math.cos(nx * 2.5)) * disp;
    v.z *= (0.92 + 0.10 * Math.sin(ny * 2.4)) * disp;

    // Upward moss cushion
    const patchNoise = Math.sin(nx * 6.0 + nz * 7.0) * 0.5 + Math.cos(nx * 11.0 + ny * 8.0) * 0.5;
    if (ny > 0.15 && patchNoise > -0.3) {
      const mossThick = Math.max(0, ny - 0.15) * 0.04 + (patchNoise + 0.3) * 0.015;
      v.x += nx * mossThick;
      v.y += ny * mossThick;
      v.z += nz * mossThick;
    } else {
      // Tuck away underneath/steep surfaces
      v.multiplyScalar(0.96);
    }

    mossPos.setXYZ(i, v.x, v.y, v.z);
  }
  mossGeo.computeVertexNormals();

  // Velvety green moss material with rich foliage tone
  const mossMat = new T.MeshStandardMaterial({
    color: 0x476326,
    roughness: 0.96,
    metalness: 0.0,
  });

  const mossMesh = new T.Mesh(mossGeo, mossMat);
  mossMesh.name = 'MossCover';
  mossMesh.castShadow = true;
  mossMesh.receiveShadow = true;

  // Clustered small footing stones
  const subStonesGroup = new T.Group();
  subStonesGroup.name = 'SubStones';
  const numSubStones = 4;
  for (let s = 0; s < numSubStones; s++) {
    const sGeo = new T.DodecahedronGeometry(0.12 + 0.05 * rand(), 1);
    const sMesh = new T.Mesh(sGeo, s % 2 === 0 ? rockMat : mossMat);
    const angle = (s / numSubStones) * Math.PI * 2 + rand() * 0.5;
    const rDist = 0.44 + 0.06 * rand();
    sMesh.position.set(Math.cos(angle) * rDist, 0.06, Math.sin(angle) * rDist);
    sMesh.scale.set(1.1 + 0.2 * rand(), 0.7 + 0.2 * rand(), 0.9 + 0.2 * rand());
    sMesh.rotation.set(rand() * 2, rand() * 2, rand() * 2);
    sMesh.castShadow = true;
    sMesh.receiveShadow = true;
    subStonesGroup.add(sMesh);
  }

  const rockAssembly = new T.Group();
  rockAssembly.add(rockMesh);
  rockAssembly.add(mossMesh);
  rockAssembly.add(subStonesGroup);

  // Compute exact bounding box of assembly to scale and ground it precisely
  const box = new T.Box3().setFromObject(rockAssembly);
  const currentSize = new T.Vector3();
  box.getSize(currentSize);

  // Scale assembly to fit target dimensions exactly: 1.20 x 0.80 x 1.00
  const scaleX = targetW / currentSize.x;
  const scaleY = targetH / currentSize.y;
  const scaleZ = targetD / currentSize.z;
  rockAssembly.scale.set(scaleX, scaleY, scaleZ);

  // Re-evaluate bounds after scaling to align footprint to origin and base to y = 0
  const scaledBox = new T.Box3().setFromObject(rockAssembly);
  const center = new T.Vector3();
  scaledBox.getCenter(center);
  
  // Offset so min.y = 0 and footprint is centered on (0, 0)
  rockAssembly.position.x = -center.x;
  rockAssembly.position.y = -scaledBox.min.y;
  rockAssembly.position.z = -center.z;

  group.add(rockAssembly);

  group.userData.size = [targetW, targetH, targetD];

  return group;
}

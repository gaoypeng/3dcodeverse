// src/assets/talus_rock.js — asset "TalusRock": Angular tumbled rock scree and medium boulders
// Approx size: 1.40 x 0.90 x 1.20 m (w x h x d)
// CONTRACT: export function buildTalusRock(THREE, opts = {}) → THREE.Group
import * as THREE from 'three';

function mulberry32(a) {
  return function() {
    let t = a += 0x6D2B79F5;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/**
 * Creates a faceted angular boulder geometry with deterministic vertex displacement
 */
function createAngularRockGeo(T, radius, detail, seed, scaleVec = [1, 1, 1]) {
  const rng = mulberry32(seed);
  const geo = new T.DodecahedronGeometry(radius, detail);
  const pos = geo.attributes.position;
  const v = new T.Vector3();

  for (let i = 0; i < pos.count; i++) {
    v.fromBufferAttribute(pos, i);
    // Angular faceted deformation with preferential horizontal layering
    const noise = (rng() - 0.5) * 0.35 * radius;
    const layer = Math.sin(v.y * 5.0) * 0.08 * radius;
    v.x += (rng() - 0.5) * 0.15 * radius;
    v.y += layer + (rng() - 0.5) * 0.1 * radius;
    v.z += (rng() - 0.5) * 0.15 * radius;
    v.addScaledVector(v.clone().normalize(), noise);
    
    // Scale along axes
    v.x *= scaleVec[0];
    v.y *= scaleVec[1];
    v.z *= scaleVec[2];
    
    pos.setXYZ(i, v.x, v.y, v.z);
  }
  geo.computeVertexNormals();
  return geo;
}

export function buildTalusRock(T = THREE, opts = {}) {
  const seed = (opts.seed || 12345) + (opts.variant || 0) * 777;
  const rng = mulberry32(seed);

  const group = new T.Group();
  group.name = 'TalusRock';

  // Palette: Desert sandstone terracotta, red gravel, weathered desert varnish
  const matPrimary = new T.MeshStandardMaterial({
    color: 0xb55a36,
    roughness: 0.88,
    metalness: 0.05,
    flatShading: true
  });
  const matSecondary = new T.MeshStandardMaterial({
    color: 0x9e482b,
    roughness: 0.92,
    metalness: 0.04,
    flatShading: true
  });
  const matVarnish = new T.MeshStandardMaterial({
    color: 0x6e3c28,
    roughness: 0.82,
    metalness: 0.08,
    flatShading: true
  });
  const matGravel = new T.MeshStandardMaterial({
    color: 0xc46f47,
    roughness: 0.95,
    metalness: 0.02,
    flatShading: true
  });

  // 1. Primary main angular boulder (central-left, highest peak)
  const mainGeo = createAngularRockGeo(T, 0.46, 1, seed + 1, [1.15, 0.95, 1.05]);
  const mainRock = new T.Mesh(mainGeo, matPrimary);
  mainRock.position.set(-0.12, 0.44, -0.04);
  mainRock.rotation.set(0.2, rng() * 0.5, -0.15);
  mainRock.castShadow = true;
  mainRock.receiveShadow = true;
  group.add(mainRock);

  // 2. Secondary tumbled boulder (wedged on right side)
  const secGeo = createAngularRockGeo(T, 0.38, 1, seed + 2, [1.05, 0.85, 1.1]);
  const secRock = new T.Mesh(secGeo, matSecondary);
  secRock.position.set(0.36, 0.34, 0.12);
  secRock.rotation.set(-0.3, rng() * 0.8 + 1.0, 0.25);
  secRock.castShadow = true;
  secRock.receiveShadow = true;
  group.add(secRock);

  // 3. Medium fractured slab / spall (front left)
  const slabGeo = createAngularRockGeo(T, 0.28, 0, seed + 3, [1.25, 0.65, 0.9]);
  const slabRock = new T.Mesh(slabGeo, matVarnish);
  slabRock.position.set(-0.35, 0.20, 0.32);
  slabRock.rotation.set(0.4, 0.3, -0.4);
  slabRock.castShadow = true;
  slabRock.receiveShadow = true;
  group.add(slabRock);

  // 4. Rear backing wedge block
  const backGeo = createAngularRockGeo(T, 0.32, 0, seed + 4, [1.1, 0.75, 1.15]);
  const backRock = new T.Mesh(backGeo, matSecondary);
  backRock.position.set(0.14, 0.26, -0.34);
  backRock.rotation.set(-0.25, 1.8, 0.1);
  backRock.castShadow = true;
  backRock.receiveShadow = true;
  group.add(backRock);

  // 5. Scree / gravel bed: cluster of smaller angular stones locking base together
  const screeSpecs = [
    { pos: [-0.52, 0.08, -0.18], r: 0.14, s: [1.1, 0.6, 0.9], rot: [0.1, 0.4, 0.3], mat: matGravel },
    { pos: [0.55, 0.10, -0.12], r: 0.16, s: [0.9, 0.65, 1.2], rot: [0.3, 0.8, -0.2], mat: matGravel },
    { pos: [-0.18, 0.07, 0.48], r: 0.13, s: [1.2, 0.55, 0.8], rot: [-0.2, 0.1, 0.5], mat: matGravel },
    { pos: [0.32, 0.09, 0.44], r: 0.15, s: [1.0, 0.6, 1.1], rot: [0.4, -0.5, 0.1], mat: matVarnish },
    { pos: [-0.42, 0.06, -0.42], r: 0.12, s: [1.1, 0.5, 1.0], rot: [0.2, 1.1, -0.3], mat: matPrimary },
    { pos: [0.46, 0.07, -0.38], r: 0.13, s: [0.95, 0.55, 0.95], rot: [-0.3, 0.9, 0.2], mat: matGravel },
    { pos: [0.02, 0.06, -0.48], r: 0.11, s: [1.0, 0.5, 1.2], rot: [0.1, -0.7, 0.4], mat: matGravel },
    { pos: [-0.58, 0.06, 0.14], r: 0.12, s: [1.1, 0.5, 0.9], rot: [0.5, 0.2, -0.4], mat: matGravel }
  ];

  for (let i = 0; i < screeSpecs.length; i++) {
    const sp = screeSpecs[i];
    const sGeo = createAngularRockGeo(T, sp.r, 0, seed + 10 + i, sp.s);
    const sMesh = new T.Mesh(sGeo, sp.mat);
    sMesh.position.set(sp.pos[0], sp.pos[1], sp.pos[2]);
    sMesh.rotation.set(sp.rot[0], sp.rot[1], sp.rot[2]);
    sMesh.castShadow = true;
    sMesh.receiveShadow = true;
    group.add(sMesh);
  }

  // Measure and normalize to guarantee target bounds: 1.40 x 0.90 x 1.20
  // Target: width 1.40, height 0.90, depth 1.20
  const TARGET_W = 1.40;
  const TARGET_H = 0.90;
  const TARGET_D = 1.20;

  // Compute exact bounding box of the composite
  const bbox = new T.Box3().setFromObject(group);
  const size = new T.Vector3();
  bbox.getSize(size);
  const center = new T.Vector3();
  bbox.getCenter(center);

  // Offset children so lowest point sits exactly on y=0 and footprint is centered at (0, 0)
  const offsetY = -bbox.min.y;
  const offsetX = -center.x;
  const offsetZ = -center.z;

  for (let i = 0; i < group.children.length; i++) {
    const child = group.children[i];
    child.position.x += offsetX;
    child.position.y += offsetY;
    child.position.z += offsetZ;
  }

  // Scale uniformly or per-axis slightly to match target dimensions within ±2%
  const finalBBox = new T.Box3().setFromObject(group);
  finalBBox.getSize(size);
  const scaleX = TARGET_W / size.x;
  const scaleY = TARGET_H / size.y;
  const scaleZ = TARGET_D / size.z;

  for (let i = 0; i < group.children.length; i++) {
    const child = group.children[i];
    child.position.x *= scaleX;
    child.position.y *= scaleY;
    child.position.z *= scaleZ;
    child.scale.set(
      child.scale.x * scaleX,
      child.scale.y * scaleY,
      child.scale.z * scaleZ
    );
  }

  // Re-verify grounded base at y=0 and centered footprint
  const verifiedBox = new T.Box3().setFromObject(group);
  const groundFix = -verifiedBox.min.y;
  for (let i = 0; i < group.children.length; i++) {
    group.children[i].position.y += groundFix;
  }

  const measuredBox = new T.Box3().setFromObject(group);
  const measuredSize = new T.Vector3();
  measuredBox.getSize(measuredSize);

  group.userData.size = [measuredSize.x, measuredSize.y, measuredSize.z];

  return group;
}

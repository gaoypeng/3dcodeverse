// src/assets/hero_boulder.js — asset "HeroBoulder": Large weathered sandstone monolith with sharp fractures and eroded planar faces in warm terracottas #b8623d and desert varnish #4d382e.
// Size: 4.20 x 3.10 x 3.80 m (w x h x d)
// CONTRACT: export function buildHeroBoulder(THREE, opts = {}) → THREE.Group, origin at y = 0, +Y up, +Z front, meters.

import * as THREE from 'three';

// Tiny deterministic mulberry32 PRNG
function mulberry32(a) {
  return function() {
    let t = a += 0x6D2B79F5;
    t = Math.imul(t ^ t >>> 15, t | 1);
    t ^= t + Math.imul(t ^ t >>> 7, t | 61);
    return ((t ^ t >>> 14) >>> 0) / 4294967296;
  };
}

export function buildHeroBoulder(T = THREE, opts = {}) {
  const seed = (opts.seed !== undefined ? opts.seed : 12345) + (opts.variant || 0) * 1000;
  const rand = mulberry32(seed);

  const group = new T.Group();
  group.name = 'HeroBoulder';

  // Target bounding box dimensions
  const targetW = 4.20;
  const targetH = 3.10;
  const targetD = 3.80;

  // Materials representing weathered sandstone and desert varnish
  // Base terracotta sandstone
  const matTerracotta = new T.MeshStandardMaterial({
    color: 0xb8623d,
    roughness: 0.88,
    metalness: 0.05,
    flatShading: true,
  });

  // Dark desert varnish (manganese/iron oxide patina on exposed top/outer faces)
  const matVarnish = new T.MeshStandardMaterial({
    color: 0x4d382e,
    roughness: 0.75,
    metalness: 0.12,
    flatShading: true,
  });

  // Intermediate warm ochre / eroded sandstone stratum
  const matOchreStrata = new T.MeshStandardMaterial({
    color: 0xc97a4b,
    roughness: 0.92,
    metalness: 0.02,
    flatShading: true,
  });

  // Deep shadow crevice / dark brown sandstone
  const matCrevice = new T.MeshStandardMaterial({
    color: 0x3d2b22,
    roughness: 0.95,
    metalness: 0.05,
    flatShading: true,
  });

  // Helper to create a fractured rock slab / block with faceted geometry
  function createRockGeo(w, h, d, detailLevel = 1, jitter = 0.18) {
    // Start from BoxGeometry or Dodecahedron / Icosahedron to get angular planar faces
    const geo = new T.DodecahedronGeometry(1.0, detailLevel);
    
    // Deform vertices along box dimensions + directional stratification + planar chipping
    const pos = geo.attributes.position;
    const v = new T.Vector3();

    for (let i = 0; i < pos.count; i++) {
      v.fromBufferAttribute(pos, i);

      // Normalise to -0.5 .. 0.5 roughly
      let nx = v.x;
      let ny = v.y;
      let nz = v.z;

      // Add stratified horizontal steps (sandstone layering)
      const strataNoise = Math.sin(ny * 7.0 + nx * 2.0) * 0.08;
      // Sharp fracture planes (clipping / flattening along random angles)
      const fracture = Math.sin(nx * 3.5 + nz * 4.0) * jitter;

      nx = (nx + fracture) * (w * 0.5);
      ny = (ny + strataNoise) * (h * 0.5);
      nz = (nz + fracture * 0.8) * (d * 0.5);

      pos.setXYZ(i, nx, ny, nz);
    }

    geo.computeVertexNormals();
    return geo;
  }

  // --- Sub-shape 1: Main central monolithic core (Terracotta sandstone with stratified steps) ---
  const coreGeo = createRockGeo(3.4, 2.7, 3.1, 1, 0.15);
  const coreMesh = new T.Mesh(coreGeo, matTerracotta);
  coreMesh.name = 'BoulderCore';
  coreMesh.position.set(-0.1, 1.45, -0.05);
  coreMesh.rotation.set(0.05, 0.2, -0.04);
  coreMesh.castShadow = coreMesh.receiveShadow = true;
  group.add(coreMesh);

  // --- Sub-shape 2: High weathered cap with dark desert varnish (#4d382e) ---
  const capGeo = createRockGeo(2.9, 1.3, 2.6, 1, 0.22);
  const capMesh = new T.Mesh(capGeo, matVarnish);
  capMesh.name = 'BoulderVarnishCap';
  capMesh.position.set(0.15, 2.35, 0.1);
  capMesh.rotation.set(-0.08, -0.35, 0.06);
  capMesh.castShadow = capMesh.receiveShadow = true;
  group.add(capMesh);

  // --- Sub-shape 3: Prominent fractured west/left facet & buttress (Warm ochre #c97a4b) ---
  const westButtressGeo = createRockGeo(1.9, 2.1, 2.2, 1, 0.2);
  const westButtress = new T.Mesh(westButtressGeo, matOchreStrata);
  westButtress.name = 'WestButtress';
  westButtress.position.set(-1.25, 1.15, 0.35);
  westButtress.rotation.set(0.12, 0.45, -0.1);
  westButtress.castShadow = westButtress.receiveShadow = true;
  group.add(westButtress);

  // --- Sub-shape 4: East fractured shearing block (Terracotta / Varnish blend) ---
  const eastShearGeo = createRockGeo(1.8, 2.3, 2.4, 1, 0.18);
  const eastShear = new T.Mesh(eastShearGeo, matTerracotta);
  eastShear.name = 'EastShearBlock';
  eastShear.position.set(1.3, 1.25, -0.4);
  eastShear.rotation.set(-0.1, -0.5, 0.15);
  eastShear.castShadow = eastShear.receiveShadow = true;
  group.add(eastShear);

  // --- Sub-shape 5: Low fallen fracture slab / stepped base at front (+Z) ---
  const frontSlabGeo = createRockGeo(2.4, 0.95, 1.6, 1, 0.16);
  const frontSlab = new T.Mesh(frontSlabGeo, matOchreStrata);
  frontSlab.name = 'FrontStepSlab';
  frontSlab.position.set(-0.25, 0.5, 1.25);
  frontSlab.rotation.set(0.08, 0.15, 0.04);
  frontSlab.castShadow = frontSlab.receiveShadow = true;
  group.add(frontSlab);

  // --- Sub-shape 6: Angular rear talus wedge & deep shadow crevice (-Z) ---
  const rearWedgeGeo = createRockGeo(2.1, 1.4, 1.5, 1, 0.2);
  const rearWedge = new T.Mesh(rearWedgeGeo, matCrevice);
  rearWedge.name = 'RearWedge';
  rearWedge.position.set(0.4, 0.75, -1.3);
  rearWedge.rotation.set(-0.15, 0.6, -0.05);
  rearWedge.castShadow = rearWedge.receiveShadow = true;
  group.add(rearWedge);

  // --- Sub-shape 7: Desert varnish overhang shelf on front-right face ---
  const shelfGeo = createRockGeo(1.6, 0.7, 1.4, 1, 0.15);
  const shelf = new T.Mesh(shelfGeo, matVarnish);
  shelf.name = 'VarnishShelf';
  shelf.position.set(0.9, 1.85, 0.85);
  shelf.rotation.set(0.18, -0.2, 0.12);
  shelf.castShadow = shelf.receiveShadow = true;
  group.add(shelf);

  // Measure current bounding box of the composite group
  const box = new T.Box3().setFromObject(group);
  const size = new T.Vector3();
  box.getSize(size);
  const center = new T.Vector3();
  box.getCenter(center);

  // Scale uniformly or per-axis to match exact target dimensions (4.20 x 3.10 x 3.80)
  const scaleX = targetW / size.x;
  const scaleY = targetH / size.y;
  const scaleZ = targetD / size.z;

  // Apply scaling to children
  group.children.forEach(child => {
    child.position.x = (child.position.x - center.x) * scaleX;
    child.position.y = (child.position.y - box.min.y) * scaleY;
    child.position.z = (child.position.z - center.z) * scaleZ;
    child.scale.set(
      child.scale.x * scaleX,
      child.scale.y * scaleY,
      child.scale.z * scaleZ
    );
  });

  // Re-verify bounds
  const finalBox = new T.Box3().setFromObject(group);
  const finalSize = new T.Vector3();
  finalBox.getSize(finalSize);
  const finalCenter = new T.Vector3();
  finalBox.getCenter(finalCenter);

  // Adjust so lowest point sits exactly at y = 0 and footprint is centered on X=0, Z=0
  const yShift = -finalBox.min.y;
  const xShift = -finalCenter.x;
  const zShift = -finalCenter.z;

  group.children.forEach(child => {
    child.position.x += xShift;
    child.position.y += yShift;
    child.position.z += zShift;
  });

  group.userData.size = [targetW, targetH, targetD];

  return group;
}

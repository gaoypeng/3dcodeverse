import * as THREE from 'three';
import { makeSandstoneMaterial } from '../shaders/sandstone.js';

// Deterministic PRNG
function mulberry32(a) {
  return function() {
    let t = a += 0x6D2B79F5;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// Pseudo-noise helper for procedural vertex deformation
function pseudoNoise(x, y, z) {
  return (
    Math.sin(x * 0.35 + y * 0.22) * Math.cos(z * 0.28) +
    0.5 * Math.sin(x * 0.8 + z * 0.7) * Math.cos(y * 0.5) +
    0.25 * Math.sin(x * 1.7 + y * 1.5 + z * 1.6)
  );
}

export function buildCliffSegment(T = THREE, opts = {}) {
  const seed = typeof opts === 'object' && opts.seed !== undefined ? opts.seed : 12345;
  const variant = typeof opts === 'object' && opts.variant !== undefined ? opts.variant : 0;
  const rng = mulberry32(seed + variant * 1013);

  const group = new T.Group();
  group.name = 'CliffSegment';

  // Overall bounds: width 12.0, height 26.0, depth 10.0 (centred footprint, base on y=0)
  // Palette of desert stratified red rock, ochre, sandstone layers, dark fissures
  const matSandstone = makeSandstoneMaterial(T, {
    color: 0xb55a36,
    darkColor: 0x6e2c1a,
    lightColor: 0xe09160,
    roughness: 0.88,
    strataScale: 0.35
  });

  const matDeepStrata = makeSandstoneMaterial(T, {
    color: 0x8f3c24,
    darkColor: 0x541c0e,
    lightColor: 0xc4693b,
    roughness: 0.90,
    strataScale: 0.5
  });

  const matOchreSand = makeSandstoneMaterial(T, {
    color: 0xcf824e,
    darkColor: 0x7a3a1e,
    lightColor: 0xebb482,
    roughness: 0.85,
    strataScale: 0.3
  });

  // Helper to construct a massive organic stepped rock bluff with strata terraces and crags
  // Using multi-segment cylinders/boxes with displaced vertices for jagged natural cliffs
  function createRockBluff(w, h, d, xSegs, ySegs, zSegs, mat, yBase, xOffset = 0, zOffset = 0) {
    const geo = new T.BoxGeometry(w, h, d, xSegs, ySegs, zSegs);
    const pos = geo.attributes.position;
    const v = new T.Vector3();

    for (let i = 0; i < pos.count; i++) {
      v.fromBufferAttribute(pos, i);

      // Normalise height fraction from 0 (bottom) to 1 (top)
      const hFrac = (v.y + h * 0.5) / h;
      
      // Step terraces: sandstone tends to form stepped horizontal cliffs
      const stepFactor = Math.floor(hFrac * 4.0) / 4.0;
      const stepTaper = (1.0 - hFrac * 0.28); // taper inwards going up

      // Jagged crag displacement
      const crag = pseudoNoise(
        (v.x + xOffset) * 0.5,
        (v.y + yBase) * 0.4,
        (v.z + zOffset) * 0.5
      );

      // Horizontal bedding planes / overhang shelves
      const shelf = Math.sin((v.y + yBase) * 1.8) * 0.35;

      let nx = v.x * stepTaper + crag * 0.8;
      let ny = v.y + shelf * 0.2 + (rng() - 0.5) * 0.1;
      let nz = v.z * stepTaper + crag * 0.85;

      pos.setXYZ(i, nx, ny, nz);
    }

    geo.computeVertexNormals();
    geo.translate(xOffset, yBase + h * 0.5, zOffset);

    const mesh = new T.Mesh(geo, mat);
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    return mesh;
  }

  // 1. Massive Primary Stepped Sandstone Bluff (rising 0 to 26m)
  // Lower massif (0 to 11m)
  const lowerMassif = createRockBluff(11.6, 11.2, 9.4, 10, 8, 8, matDeepStrata, 0, 0, 0);
  group.add(lowerMassif);

  // Middle stratified crag (9.5 to 19.5m)
  const midCrag = createRockBluff(11.2, 10.5, 8.6, 10, 8, 8, matSandstone, 9.5, (rng() - 0.5) * 0.4, -0.4);
  group.add(midCrag);

  // Upper sheer tower & jagged pinnacles (18.0 to 26.0m)
  const upperTower = createRockBluff(10.8, 8.2, 7.8, 8, 6, 6, matOchreSand, 18.0, (rng() - 0.5) * 0.6, -0.8);
  group.add(upperTower);

  // 2. Protruding Rock Buttresses / Vertical Spires to break any boxiness
  const spireGeo1 = new T.CylinderGeometry(0.8, 2.2, 20.0, 7, 8);
  const sPos1 = spireGeo1.attributes.position;
  for (let i = 0; i < sPos1.count; i++) {
    const vx = sPos1.getX(i);
    const vy = sPos1.getY(i);
    const vz = sPos1.getZ(i);
    const n = pseudoNoise(vx * 2.0, vy * 0.6, vz * 2.0) * 0.5;
    sPos1.setXYZ(i, vx + n, vy, vz + n);
  }
  spireGeo1.computeVertexNormals();
  spireGeo1.translate(-2.4, 10.0, 3.2);
  const spire1 = new T.Mesh(spireGeo1, matSandstone);
  spire1.castShadow = spire1.receiveShadow = true;
  group.add(spire1);

  const spireGeo2 = new T.CylinderGeometry(1.0, 2.4, 17.0, 7, 8);
  const sPos2 = spireGeo2.attributes.position;
  for (let i = 0; i < sPos2.count; i++) {
    const vx = sPos2.getX(i);
    const vy = sPos2.getY(i);
    const vz = sPos2.getZ(i);
    const n = pseudoNoise(vx * 2.0, vy * 0.6, vz * 2.0) * 0.5;
    sPos2.setXYZ(i, vx + n, vy, vz + n);
  }
  spireGeo2.computeVertexNormals();
  spireGeo2.translate(2.2, 8.5, 3.0);
  const spire2 = new T.Mesh(spireGeo2, matDeepStrata);
  spire2.castShadow = spire2.receiveShadow = true;
  group.add(spire2);

  // 3. Jagged weathered capstone boulders & pinnacles on cliff top (y: 24 to 26m)
  const topRockGeo = new T.DodecahedronGeometry(1.6, 1);
  const trPos = topRockGeo.attributes.position;
  for (let i = 0; i < trPos.count; i++) {
    const vx = trPos.getX(i);
    const vy = trPos.getY(i);
    const vz = trPos.getZ(i);
    trPos.setXYZ(i, vx * 1.3, vy * 0.85 + (rng() - 0.5) * 0.2, vz * 1.2);
  }
  topRockGeo.computeVertexNormals();

  const tr1 = new T.Mesh(topRockGeo, matOchreSand);
  tr1.position.set(-2.8, 25.2, -1.2);
  tr1.rotation.set(0.2, rng() * Math.PI, -0.1);
  tr1.castShadow = tr1.receiveShadow = true;
  group.add(tr1);

  const tr2 = new T.Mesh(topRockGeo, matSandstone);
  tr2.position.set(2.4, 25.0, -1.5);
  tr2.rotation.set(-0.15, rng() * Math.PI, 0.2);
  tr2.castShadow = tr2.receiveShadow = true;
  group.add(tr2);

  // 4. Talus base boulders and eroded scree at foot (y=0 to 3m)
  const baseBoulders = [
    { x: -3.8, z: 3.5, s: 1.8, mat: matDeepStrata },
    { x: 3.5, z: 3.2, s: 2.1, mat: matSandstone },
    { x: -0.2, z: 4.0, s: 1.5, mat: matOchreSand },
    { x: 4.6, z: 0.8, s: 1.9, mat: matDeepStrata },
    { x: -4.8, z: 0.5, s: 2.0, mat: matSandstone }
  ];

  baseBoulders.forEach((b, idx) => {
    const bGeo = new T.DodecahedronGeometry(b.s * 0.5, 1);
    const bp = bGeo.attributes.position;
    for (let i = 0; i < bp.count; i++) {
      const vx = bp.getX(i);
      const vy = bp.getY(i);
      const vz = bp.getZ(i);
      bp.setXYZ(i, vx + (rng() - 0.5) * 0.15, vy * 0.75, vz + (rng() - 0.5) * 0.15);
    }
    bGeo.computeVertexNormals();
    bGeo.translate(b.x, b.s * 0.35, b.z);
    const bMesh = new T.Mesh(bGeo, b.mat);
    bMesh.rotation.y = rng() * Math.PI * 2;
    bMesh.castShadow = bMesh.receiveShadow = true;
    group.add(bMesh);
  });

  group.userData.size = [12.0, 26.0, 10.0];
  return group;
}

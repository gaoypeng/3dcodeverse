// src/assets/string_lights.js — asset "StringLights"
// Criss-crossing black cable swags supporting 24 glowing warm-white spherical Edison bulbs with emissive bloom effect.
// Sized exactly 4.20 x 0.60 x 4.20 m (w x h x d), centered footprint, base at y = 0.
import * as THREE from 'three';

function mulberry32(a) {
  return function() {
    let t = (a += 0x6d2b79f5);
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function buildStringLights(T = THREE, opts = {}) {
  const seed = typeof opts.seed === 'number' ? opts.seed : 42;
  const rand = mulberry32(seed);

  const group = new T.Group();
  group.name = 'StringLights';

  // Materials
  const cableMat = new T.MeshStandardMaterial({
    color: 0x181818,
    roughness: 0.8,
    metalness: 0.1,
  });

  const bracketMat = new T.MeshStandardMaterial({
    color: 0x222222,
    roughness: 0.5,
    metalness: 0.8,
  });

  const socketMat = new T.MeshStandardMaterial({
    color: 0x4a3c28, // vintage dark brass / bronze socket
    roughness: 0.35,
    metalness: 0.7,
  });

  const bulbMat = new T.MeshStandardMaterial({
    color: 0xfff4d6,
    emissive: 0xffaa33,
    emissiveIntensity: 2.8,
    roughness: 0.15,
    metalness: 0.05,
    transparent: true,
    opacity: 0.88,
  });

  const filamentMat = new T.MeshStandardMaterial({
    color: 0xffffff,
    emissive: 0xffdd77,
    emissiveIntensity: 4.5,
    roughness: 0.1,
  });

  const glowHaloMat = new T.MeshBasicMaterial({
    color: 0xff9922,
    transparent: true,
    opacity: 0.12,
    side: T.BackSide,
    depthWrite: false,
  });

  // Cable layout definitions (4 major catenary swags forming criss-cross canopy)
  // Corner attachment points at height Y = 0.585 m with brackets reaching 0.60 m.
  const W = 4.20;
  const D = 4.20;
  const halfW = W / 2; // 2.10
  const halfD = D / 2; // 2.10
  const topY = 0.58;
  const sagLowestY = 0.145; // lowest cable height so bulb bottom touches 0.00

  // 4 swags covering the 4.2x4.2 area
  // 1: Diagonal NW to SE
  // 2: Diagonal NE to SW
  // 3: Mid-cross West to East with sag
  // 4: Mid-cross North to South with sag
  const swagsDef = [
    {
      start: new T.Vector3(-halfW, topY, -halfD),
      mid: new T.Vector3(0, sagLowestY + 0.02, 0),
      end: new T.Vector3(halfW, topY, halfD),
      bulbCount: 6,
    },
    {
      start: new T.Vector3(-halfW, topY, halfD),
      mid: new T.Vector3(0, sagLowestY, 0),
      end: new T.Vector3(halfW, topY, -halfD),
      bulbCount: 6,
    },
    {
      start: new T.Vector3(-halfW, topY, 0),
      mid: new T.Vector3(0, sagLowestY + 0.015, 0.7),
      end: new T.Vector3(halfW, topY, 0),
      bulbCount: 6,
    },
    {
      start: new T.Vector3(0, topY, -halfD),
      mid: new T.Vector3(-0.7, sagLowestY + 0.015, 0),
      end: new T.Vector3(0, topY, halfD),
      bulbCount: 6,
    },
  ];

  // Collect bulb transform positions
  const bulbTransforms = [];
  const cableCurves = [];

  // Build brackets at the 8 attachment anchor points
  const bracketGeo = new T.BoxGeometry(0.04, 0.04, 0.04);
  const bracketsGroup = new T.Group();
  bracketsGroup.name = 'MountingBrackets';

  const anchorPoints = [
    new T.Vector3(-halfW, topY, -halfD),
    new T.Vector3(halfW, topY, halfD),
    new T.Vector3(-halfW, topY, halfD),
    new T.Vector3(halfW, topY, -halfD),
    new T.Vector3(-halfW, topY, 0),
    new T.Vector3(halfW, topY, 0),
    new T.Vector3(0, topY, -halfD),
    new T.Vector3(0, topY, halfD),
  ];

  for (const pt of anchorPoints) {
    const bracket = new T.Mesh(bracketGeo, bracketMat);
    bracket.position.copy(pt);
    bracket.position.y = topY + 0.01; // top touches 0.60
    bracketsGroup.add(bracket);
  }
  group.add(bracketsGroup);

  // Generate cables and calculate bulb placement along each catenary curve
  const cablesGroup = new T.Group();
  cablesGroup.name = 'Cables';

  for (let sIdx = 0; sIdx < swagsDef.length; sIdx++) {
    const def = swagsDef[sIdx];
    // Quadratic / Catmull-Rom curve with natural sag
    const p0 = def.start;
    const p1 = new T.Vector3(
      def.start.x * 0.5 + def.mid.x * 0.5,
      (def.start.y + def.mid.y) * 0.5 - 0.03,
      def.start.z * 0.5 + def.mid.z * 0.5
    );
    const p2 = def.mid;
    const p3 = new T.Vector3(
      def.end.x * 0.5 + def.mid.x * 0.5,
      (def.end.y + def.mid.y) * 0.5 - 0.03,
      def.end.z * 0.5 + def.mid.z * 0.5
    );
    const p4 = def.end;

    const curve = new T.CatmullRomCurve3([p0, p1, p2, p3, p4], false, 'catmullrom', 0.5);
    cableCurves.push(curve);

    const tubeGeo = new T.TubeGeometry(curve, 32, 0.005, 8, false);
    const tubeMesh = new T.Mesh(tubeGeo, cableMat);
    tubeMesh.castShadow = false;
    cablesGroup.add(tubeMesh);

    // Distribute bulbs along the curve (avoiding extreme ends)
    const count = def.bulbCount;
    for (let b = 0; b < count; b++) {
      const u = (b + 1) / (count + 1); // evenly spaced between 0.14 and 0.86
      const pt = curve.getPointAt(u);
      const tangent = curve.getTangentAt(u);
      bulbTransforms.push({
        position: pt,
        tangent: tangent,
        swagIndex: sIdx,
        u: u,
      });
    }
  }
  group.add(cablesGroup);

  const totalBulbs = bulbTransforms.length; // exactly 24 bulbs

  // Instanced meshes for bulbs and sockets
  // 1. Socket & fixture cap (stepped cylinder)
  const socketCapGeo = new T.CylinderGeometry(0.016, 0.016, 0.035, 12);
  socketCapGeo.translate(0, -0.0175, 0); // origin at top where it connects to cable

  const socketInst = new T.InstancedMesh(socketCapGeo, socketMat, totalBulbs);
  socketInst.name = 'Sockets';
  socketInst.castShadow = true;

  // 2. Glass bulb (spherical Edison bulb with slight neck)
  const bulbGeo = new T.SphereGeometry(0.042, 16, 12);
  // Center of bulb sphere is 0.082 below cable attachment
  bulbGeo.translate(0, -0.082, 0);

  const bulbInst = new T.InstancedMesh(bulbGeo, bulbMat, totalBulbs);
  bulbInst.name = 'BulbGlass';
  bulbInst.castShadow = false;

  // 3. Filament inside bulb (bright vertical capsule)
  const filamentGeo = new T.CylinderGeometry(0.003, 0.003, 0.03, 8);
  filamentGeo.translate(0, -0.082, 0);

  const filamentInst = new T.InstancedMesh(filamentGeo, filamentMat, totalBulbs);
  filamentInst.name = 'Filaments';

  // 4. Glow halo (soft bloom shell)
  const haloGeo = new T.SphereGeometry(0.065, 12, 10);
  haloGeo.translate(0, -0.082, 0);
  const haloInst = new T.InstancedMesh(haloGeo, glowHaloMat, totalBulbs);
  haloInst.name = 'GlowHalos';

  // 5. Cable clamps (small ring at cable contact)
  const clampGeo = new T.CylinderGeometry(0.01, 0.01, 0.015, 8);
  clampGeo.rotateZ(Math.PI / 2);
  const clampInst = new T.InstancedMesh(clampGeo, cableMat, totalBulbs);
  clampInst.name = 'CableClamps';

  const dummy = new T.Object3D();
  const upVec = new T.Vector3(0, 1, 0);

  // Position each bulb
  for (let i = 0; i < totalBulbs; i++) {
    const info = bulbTransforms[i];
    const p = info.position;

    dummy.position.copy(p);
    dummy.rotation.set(0, 0, 0);
    dummy.scale.set(1, 1, 1);
    dummy.updateMatrix();

    socketInst.setMatrixAt(i, dummy.matrix);
    bulbInst.setMatrixAt(i, dummy.matrix);
    filamentInst.setMatrixAt(i, dummy.matrix);
    haloInst.setMatrixAt(i, dummy.matrix);

    // Orient clamp along cable tangent
    dummy.quaternion.setFromUnitVectors(upVec, info.tangent);
    dummy.updateMatrix();
    clampInst.setMatrixAt(i, dummy.matrix);
  }

  socketInst.instanceMatrix.needsUpdate = true;
  bulbInst.instanceMatrix.needsUpdate = true;
  filamentInst.instanceMatrix.needsUpdate = true;
  haloInst.instanceMatrix.needsUpdate = true;
  clampInst.instanceMatrix.needsUpdate = true;

  const fixturesGroup = new T.Group();
  fixturesGroup.name = 'LightFixtures';
  fixturesGroup.add(socketInst, bulbInst, filamentInst, haloInst, clampInst);
  group.add(fixturesGroup);

  // 4 warm subtle accent point lights distributed for ambient illumination
  const lightGroup = new T.Group();
  lightGroup.name = 'WarmLights';
  const lightPositions = [
    new T.Vector3(-0.9, 0.25, -0.9),
    new T.Vector3(0.9, 0.25, 0.9),
    new T.Vector3(-0.9, 0.25, 0.9),
    new T.Vector3(0.9, 0.25, -0.9),
  ];

  for (let li = 0; li < lightPositions.length; li++) {
    const pl = new T.PointLight(0xffaa44, 2.2, 3.8, 1.8);
    pl.position.copy(lightPositions[li]);
    pl.castShadow = false;
    lightGroup.add(pl);
  }
  group.add(lightGroup);

  // Measure and set userData size
  const bbox = new T.Box3().setFromObject(group);
  const size = new T.Vector3();
  bbox.getSize(size);
  group.userData.size = [size.x, size.y, size.z];

  // Subtle oscillation animation for the string lights
  group.userData.tick = (t, dt) => {
    const sway = Math.sin(t * 0.4 * Math.PI * 2) * 0.015;
    const swayZ = Math.cos(t * 0.35 * Math.PI * 2) * 0.012;
    cablesGroup.position.x = sway * 0.5;
    cablesGroup.position.z = swayZ * 0.5;
    fixturesGroup.position.x = sway;
    fixturesGroup.position.z = swayZ;
  };
  group.userData.update = group.userData.tick;

  return group;
}

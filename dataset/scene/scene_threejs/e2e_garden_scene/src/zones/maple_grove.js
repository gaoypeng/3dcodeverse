// src/zones/maple_grove.js — zone "MapleGrove"
// Lush mossy berms surrounding the garden edges, populated by Japanese maples
// with scarlet and gold foliage, a stone lantern, and bamboo boundary screening.
// Bbox: centre (-1.0, 2.0, 5.5) m, extents (22.0, 5.0, 12.0) m
// Contents: JapaneseMaple, StoneLantern, BambooFence, MossyRock

import * as THREE from 'three';
import { buildJapaneseMaple } from '../assets/japanese_maple.js';
import { buildStoneLantern } from '../assets/stone_lantern.js';
import { buildBambooFence } from '../assets/bamboo_fence.js';
import { buildMossyRock } from '../assets/mossy_rock.js';

function createRng(seed = 107) {
  let s = (seed | 0) + 0x6D2B79F5;
  return function() {
    let t = Math.imul(s ^ (s >>> 15), 1 | s);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    s = (s + 0x6D2B79F5) | 0;
    return ((t >>> 0) / 4294967296);
  };
}

export function build(ctx) {
  return buildMapleGrove(ctx);
}

export function buildMapleGrove(ctx) {
  const T = ctx.THREE || THREE;
  const heightAt = ctx.heightAt || ((x, z) => 0.3);
  const rand = ctx.rand || createRng(42);

  const zone = new T.Group();
  zone.name = 'MapleGrove';

  const animatedObjects = [];

  // Helper to place asset flush on ground
  function place(obj, x, z, ry = 0, scale = 1.0) {
    const y = heightAt(x, z);
    obj.position.set(x, y, z);
    obj.rotation.y = ry;
    if (scale !== 1.0) {
      obj.scale.set(scale, scale, scale);
    }
    zone.add(obj);
    if (obj.userData && typeof obj.userData.tick === 'function') {
      animatedObjects.push({ type: 'generic', tick: obj.userData.tick });
    }
    return obj;
  }

  // ---------------------------------------------------------------------------
  // 1. BAMBOO BOUNDARY FENCE SCREENING (Takegaki) along back and side perimeters
  // ---------------------------------------------------------------------------
  // Rear perimeter along Z ≈ 9.5 to 10.0 from X = -9.0 to +7.5
  const backFences = [
    { x: -8.8, z: 9.6, ry: 0.05 },
    { x: -6.8, z: 9.7, ry: -0.02 },
    { x: -4.8, z: 9.8, ry: 0.03 },
    { x: -2.8, z: 9.8, ry: -0.04 },
    { x: -0.8, z: 9.8, ry: 0.02 },
    { x: 1.2, z: 9.7, ry: -0.03 },
    { x: 3.2, z: 9.6, ry: 0.04 },
    { x: 5.2, z: 9.4, ry: -0.02 },
    { x: 7.2, z: 9.1, ry: -0.08 },
  ];

  for (let i = 0; i < backFences.length; i++) {
    const f = backFences[i];
    const fence = buildBambooFence(T, { seed: 100 + i * 17 });
    place(fence, f.x, f.z, f.ry);
  }

  // Side return fence on the far left (X ≈ -10.2, Z ≈ 3.5 to 7.5)
  const leftFences = [
    { x: -10.0, z: 7.5, ry: Math.PI / 2 + 0.05 },
    { x: -10.0, z: 5.5, ry: Math.PI / 2 - 0.03 },
    { x: -9.9, z: 3.5, ry: Math.PI / 2 + 0.02 },
  ];

  for (let i = 0; i < leftFences.length; i++) {
    const f = leftFences[i];
    const fence = buildBambooFence(T, { seed: 200 + i * 23 });
    place(fence, f.x, f.z, f.ry);
  }

  // ---------------------------------------------------------------------------
  // 2. JAPANESE MAPLE TREES (Canopies of scarlet, crimson, and amber)
  // ---------------------------------------------------------------------------
  // Carefully placed within the MapleGrove bbox and outside pond basin
  const maplePlacements = [
    // Grand feature maple on the north-west mossy knoll
    { x: -6.5, z: 6.2, ry: 0.4, scale: 1.15, seed: 101 },
    // Scarlet maple framing the north-central vista behind the bridge
    { x: -1.8, z: 7.4, ry: 1.8, scale: 1.05, seed: 202 },
    // Amber maple on the north-east rise
    { x: 2.8, z: 7.8, ry: 3.2, scale: 1.0, seed: 303 },
    // Golden-red maple anchoring the far east boundary
    { x: 7.0, z: 6.8, ry: 4.5, scale: 1.1, seed: 404 },
    // West flank maple creating lush background foliage behind gravel court
    { x: -8.8, z: 3.2, ry: 0.8, scale: 0.95, seed: 505 },
    // Mid-depth maple near the mossy slope
    { x: -4.5, z: 8.4, ry: 2.4, scale: 0.9, seed: 606 },
  ];

  const mapleGroups = [];
  for (let i = 0; i < maplePlacements.length; i++) {
    const m = maplePlacements[i];
    const maple = buildJapaneseMaple(T, { seed: m.seed });
    place(maple, m.x, m.z, m.ry, m.scale);
    mapleGroups.push(maple);
    animatedObjects.push({
      type: 'maple',
      mesh: maple,
      seed: m.seed
    });
  }

  // ---------------------------------------------------------------------------
  // 3. STONE LANTERNS (Toro) with warm twilight illumination
  // ---------------------------------------------------------------------------
  // Kasuga stone lantern resting on mossy bank in the grove (visible from pathways)
  const lantern1 = buildStoneLantern(T);
  place(lantern1, -4.2, 3.8, 0.6);

  // Warm glowing lantern point light
  const lanternLight = new T.PointLight(0xffa544, 2.6, 6.5, 1.4);
  lanternLight.position.set(-4.2, heightAt(-4.2, 3.8) + 1.15, 3.8);
  lanternLight.castShadow = false;
  zone.add(lanternLight);

  animatedObjects.push({
    type: 'lanternLight',
    light: lanternLight,
    baseIntensity: 2.6
  });

  // Secondary lantern deeper in the grove for ambient depth
  const lantern2 = buildStoneLantern(T);
  place(lantern2, 4.8, 5.8, -0.8);

  const lanternLight2 = new T.PointLight(0xffa544, 2.2, 5.5, 1.4);
  lanternLight2.position.set(4.8, heightAt(4.8, 5.8) + 1.15, 5.8);
  lanternLight2.castShadow = false;
  zone.add(lanternLight2);

  animatedObjects.push({
    type: 'lanternLight',
    light: lanternLight2,
    baseIntensity: 2.2
  });

  // ---------------------------------------------------------------------------
  // 4. MOSSY GRANITE BOULDER CLUSTERS (San-zon / Sani-iwa arrangements)
  // ---------------------------------------------------------------------------
  const rockPlacements = [
    // Major triad cluster near the west maple
    { x: -5.8, z: 4.8, ry: 0.3, scale: 1.25, variant: 0 },
    { x: -4.8, z: 5.3, ry: 1.7, scale: 0.85, variant: 1 },
    { x: -6.4, z: 4.1, ry: 2.9, scale: 0.65, variant: 2 },

    // North bank rock cluster
    { x: -0.6, z: 6.4, ry: 0.8, scale: 1.1, variant: 3 },
    { x: 0.5, z: 6.8, ry: 2.2, scale: 0.75, variant: 4 },

    // East boundary slope rocks
    { x: 5.5, z: 6.2, ry: 1.1, scale: 1.15, variant: 5 },
    { x: 6.4, z: 5.4, ry: 3.5, scale: 0.8, variant: 6 },
    { x: 4.5, z: 7.2, ry: 0.5, scale: 0.9, variant: 7 },

    // Deep grove accent stones
    { x: -3.2, z: 8.8, ry: 1.4, scale: 0.95, variant: 8 },
    { x: -8.2, z: 5.2, ry: 2.1, scale: 1.05, variant: 9 },
  ];

  for (let i = 0; i < rockPlacements.length; i++) {
    const rp = rockPlacements[i];
    const rock = buildMossyRock(T, { seed: 300 + i * 37, variant: rp.variant });
    place(rock, rp.x, rp.z, rp.ry, rp.scale);
  }

  // ---------------------------------------------------------------------------
  // 5. SCATTERED AUTUMN FALLEN LEAVES (Momiji foliage on mossy ground)
  // ---------------------------------------------------------------------------
  // We instance small colorful leaf discs strewn across the mossy grove floor
  const numFallenLeaves = 120;
  const leafGeo = new T.CircleGeometry(0.06, 5);
  leafGeo.rotateX(-Math.PI / 2);

  const autumnColors = [0x9b1b1b, 0xc4281b, 0xd95a16, 0xd97724, 0x8a1c14];
  const leafMats = autumnColors.map(col => new T.MeshStandardMaterial({
    color: col,
    roughness: 0.7,
    metalness: 0.02,
    side: T.DoubleSide
  }));

  const leafInstancesPerMat = Math.ceil(numFallenLeaves / leafMats.length);
  const leafMeshGroups = leafMats.map(mat => {
    const imesh = new T.InstancedMesh(leafGeo, mat, leafInstancesPerMat);
    imesh.castShadow = false;
    imesh.receiveShadow = true;
    return imesh;
  });

  const m4 = new T.Matrix4();
  const q = new T.Quaternion();
  const s = new T.Vector3();
  const p = new T.Vector3();

  const matCounts = new Array(leafMats.length).fill(0);
  const leafRng = createRng(777);

  for (let i = 0; i < numFallenLeaves; i++) {
    // Distribute around maple tree centers
    const mIdx = Math.floor(leafRng() * maplePlacements.length);
    const center = maplePlacements[mIdx];
    const rDist = 0.5 + leafRng() * 2.2;
    const rAngle = leafRng() * Math.PI * 2;
    const lx = center.x + Math.cos(rAngle) * rDist;
    const lz = center.z + Math.sin(rAngle) * rDist;
    const ly = heightAt(lx, lz) + 0.008;

    p.set(lx, ly, lz);
    q.setFromEuler(new T.Euler((leafRng() - 0.5) * 0.1, leafRng() * Math.PI * 2, (leafRng() - 0.5) * 0.1));
    const sc = 0.8 + leafRng() * 0.6;
    s.set(sc, sc, sc);
    m4.compose(p, q, s);

    const mIndex = i % leafMats.length;
    if (matCounts[mIndex] < leafInstancesPerMat) {
      leafMeshGroups[mIndex].setMatrixAt(matCounts[mIndex]++, m4);
    }
  }

  leafMeshGroups.forEach(mesh => {
    mesh.instanceMatrix.needsUpdate = true;
    zone.add(mesh);
  });

  // ---------------------------------------------------------------------------
  // 6. ZONE ANIMATION UPDATE (Maple canopy breeze, lantern flame flicker)
  // ---------------------------------------------------------------------------
  zone.userData.update = (t, dt) => {
    for (let i = 0; i < animatedObjects.length; i++) {
      const item = animatedObjects[i];
      if (item.type === 'maple') {
        if (item.mesh.userData.tick) {
          item.mesh.userData.tick(t);
        }
      } else if (item.type === 'lanternLight') {
        // Subtle natural flicker at ~2.2 Hz ±15%
        const flicker = 1.0 + 0.15 * Math.sin(t * 13.8 + item.baseIntensity * 7) + 0.05 * Math.sin(t * 22.4);
        item.light.intensity = item.baseIntensity * flicker;
      } else if (item.type === 'generic' && typeof item.tick === 'function') {
        item.tick(t, dt);
      }
    }
  };

  return zone;
}

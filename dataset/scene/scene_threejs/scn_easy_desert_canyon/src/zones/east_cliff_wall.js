// src/zones/east_cliff_wall.js — zone "EastCliffWall": Towering east-facing layered sandstone cliff face with sun-catching promontories and eroded alcoves.
// PLAN bbox: centre (24, 15, 0) extents (22 x 34 x 120) m. Contents: CliffSegment, TalusRock
// CONTRACT: export function build(ctx) → THREE.Group named 'EastCliffWall'.
//   ctx = { THREE, scene, renderer, loaders, env, heightAt, assets, rand }
import * as THREE from 'three';
import { buildCliffSegment } from '../assets/cliff_segment.js';
import { buildTalusRock } from '../assets/talus_rock.js';

// Deterministic PRNG
function mulberry32(a) {
  return function() {
    let t = (a += 0x6d2b79f5);
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function build(ctx) {
  const T = ctx.THREE || THREE;
  const heightAt = ctx.heightAt || ((x, z) => 0);

  const zone = new T.Group();
  zone.name = 'EastCliffWall';

  // Zone bounding specifications:
  // centre: (24.0, 15.0, 0.0), extents: (22.0, 34.0, 120.0) -> X in [13.0, 35.0], Z in [-60.0, 60.0]

  // Palette of natural desert rock materials for zone-level details
  const matSunlitRock = new T.MeshStandardMaterial({
    color: 0xd97e4d,
    roughness: 0.84,
    metalness: 0.03,
    flatShading: true
  });
  const matDarkStrata = new T.MeshStandardMaterial({
    color: 0x783420,
    roughness: 0.92,
    metalness: 0.05,
    flatShading: true
  });
  const matGravelApron = new T.MeshStandardMaterial({
    color: 0xaf6542,
    roughness: 0.96,
    metalness: 0.02
  });

  // 1. Continuous interlocking cliff wall along the Eastern flank
  // cliff segments facing west into the canyon (+X is set back, cliff fronts face inward towards wash)
  const cliffSpecs = [
    { x: 23.5, z: -48.0, ry: 0.08, scale: [1.02, 1.05, 1.0], seed: 101 },
    { x: 25.0, z: -38.0, ry: -0.12, scale: [0.98, 0.95, 1.05], seed: 102 }, // alcove
    { x: 22.0, z: -28.0, ry: 0.15, scale: [1.05, 1.12, 0.95], seed: 103 },  // sun-catching promontory
    { x: 24.2, z: -18.0, ry: -0.05, scale: [1.0, 1.0, 1.0], seed: 104 },
    { x: 25.5, z: -8.0, ry: 0.10, scale: [0.95, 0.92, 1.08], seed: 105 },  // alcove
    { x: 21.8, z: 2.0, ry: -0.18, scale: [1.08, 1.15, 1.0], seed: 106 },   // prominent promontory near center
    { x: 23.8, z: 12.0, ry: 0.06, scale: [1.0, 1.04, 0.96], seed: 107 },
    { x: 25.2, z: 22.0, ry: -0.10, scale: [0.96, 0.96, 1.02], seed: 108 }, // alcove
    { x: 22.2, z: 32.0, ry: 0.14, scale: [1.04, 1.08, 0.98], seed: 109 },  // promontory
    { x: 24.0, z: 42.0, ry: -0.08, scale: [1.0, 1.0, 1.05], seed: 110 },
    { x: 23.2, z: 50.0, ry: 0.05, scale: [1.02, 1.02, 0.95], seed: 111 }
  ];

  for (let i = 0; i < cliffSpecs.length; i++) {
    const cs = cliffSpecs[i];
    const cliff = buildCliffSegment(T, { seed: cs.seed, variant: i });
    cliff.scale.set(cs.scale[0], cs.scale[1], cs.scale[2]);
    cliff.rotation.y = cs.ry;
    const y = heightAt(cs.x, cs.z);
    cliff.position.set(cs.x, y, cs.z);
    zone.add(cliff);
  }

  // 2. Talus rock scree fields and clustered boulders along cliff base (X within [15.0, 20.0])
  const talusSpecs = [
    { x: 17.5, z: -45.0, ry: 0.4, s: 1.3, v: 1 },
    { x: 18.5, z: -37.0, ry: 1.2, s: 1.1, v: 2 },
    { x: 16.5, z: -30.0, ry: 2.1, s: 1.4, v: 3 },
    { x: 17.2, z: -22.0, ry: 0.8, s: 0.9, v: 4 },
    { x: 18.2, z: -15.0, ry: 2.8, s: 1.2, v: 5 },
    { x: 16.0, z: -7.0, ry: 0.3, s: 1.5, v: 6 },
    { x: 16.8, z: -1.0, ry: 1.7, s: 1.0, v: 7 },
    { x: 15.8, z: 6.0, ry: 2.4, s: 1.6, v: 8 },
    { x: 17.8, z: 15.0, ry: 0.9, s: 1.2, v: 9 },
    { x: 16.5, z: 24.0, ry: 3.1, s: 1.3, v: 10 },
    { x: 18.0, z: 30.0, ry: 1.5, s: 1.1, v: 11 },
    { x: 16.2, z: 37.0, ry: 0.5, s: 1.4, v: 12 },
    { x: 17.5, z: 46.0, ry: 2.2, s: 1.2, v: 13 }
  ];

  for (let i = 0; i < talusSpecs.length; i++) {
    const ts = talusSpecs[i];
    const rock = buildTalusRock(T, { seed: 500 + i * 23, variant: ts.v });
    rock.scale.set(ts.s, ts.s, ts.s);
    rock.rotation.y = ts.ry;
    const y = heightAt(ts.x, ts.z);
    rock.position.set(ts.x, y, ts.z);
    zone.add(rock);
  }

  // 3. Zone-level architectural details:
  // Layered sandstone scree aprons / talus slope wedges sloping down from cliff base
  // Using low-profile wedge geometry to avoid negative vertical overshoot
  const apronGeo = new T.ConeGeometry(5.0, 3.0, 5);
  apronGeo.translate(0, 1.5, 0);

  const apronPositions = [
    { x: 19.0, z: -35.0, ry: 0.6, scale: [1.1, 0.8, 0.8] },
    { x: 18.5, z: -12.0, ry: 1.1, scale: [1.0, 0.9, 0.9] },
    { x: 18.0, z: 10.0, ry: -0.4, scale: [1.2, 0.8, 0.8] },
    { x: 19.2, z: 35.0, ry: 0.8, scale: [1.0, 0.85, 0.85] }
  ];

  for (let i = 0; i < apronPositions.length; i++) {
    const ap = apronPositions[i];
    const apronMesh = new T.Mesh(apronGeo, matGravelApron);
    apronMesh.scale.set(ap.scale[0], ap.scale[1], ap.scale[2]);
    apronMesh.rotation.y = ap.ry;
    const y = heightAt(ap.x, ap.z);
    apronMesh.position.set(ap.x, y, ap.z);
    apronMesh.castShadow = true;
    apronMesh.receiveShadow = true;
    zone.add(apronMesh);
  }

  // Weathered rock ribs / sandstone outcroppings projecting from the wall
  const ribGeo = new T.DodecahedronGeometry(1.8, 1);
  ribGeo.scale(1.2, 2.2, 1.0);
  const ribMesh1 = new T.Mesh(ribGeo, matSunlitRock);
  ribMesh1.position.set(17.5, heightAt(17.5, -20.0) + 1.8, -20.0);
  ribMesh1.rotation.set(0.15, 0.4, -0.2);
  ribMesh1.castShadow = true;
  ribMesh1.receiveShadow = true;
  zone.add(ribMesh1);

  const ribMesh2 = new T.Mesh(ribGeo, matDarkStrata);
  ribMesh2.position.set(17.0, heightAt(17.0, 18.0) + 1.9, 18.0);
  ribMesh2.rotation.set(-0.1, -0.6, 0.25);
  ribMesh2.castShadow = true;
  ribMesh2.receiveShadow = true;
  zone.add(ribMesh2);

  zone.userData.update = (t, dt) => {
    // Static geology
  };

  return zone;
}
export const buildEastCliffWall = build;

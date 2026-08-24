// src/zones/west_cliff_wall.js — zone "WestCliffWall"
// Description: Continuous jagged, stepped red sandstone canyon wall rising 20 to 30 meters on the western flank with horizontal geological strata bands.
// PLAN bbox: centre (-24.000, 15.000, 0.000) m, extents (22.000, 34.000, 120.000) m
// Contents: CliffSegment, TalusRock
import * as THREE from 'three';
import { buildCliffSegment } from '../assets/cliff_segment.js';
import { buildTalusRock } from '../assets/talus_rock.js';

function mulberry32(a) {
  return function() {
    let t = (a += 0x6d2b79f5);
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function buildWestCliffWall(ctx = {}) {
  const T = ctx.THREE || THREE;
  const heightAt = ctx.heightAt || ((x, z) => 0);

  const zone = new T.Group();
  zone.name = 'WestCliffWall';

  // Zone bounds:
  // centre: (-24.0, 15.0, 0.0), extents: (22.0, 34.0, 120.0)
  // X: [-35.0, -13.0], Y: [-2.0, 32.0], Z: [-60.0, 60.0]

  // Shared zone materials for geological features and talus aprons
  const matSandstone = new T.MeshStandardMaterial({
    color: 0xba5e38,
    roughness: 0.88,
    metalness: 0.03,
    flatShading: true
  });
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

  // 1. Continuous interlocking cliff wall spanning along Z = -52 to +52
  // Cliff segment size: 12.0m (W) x 26.0m (H) x 10.0m (D)
  // Staggered with varied depths (X ~ -26.5 to -23.0) and rotations for promontories and alcoves
  const cliffSpecs = [
    { x: -24.5, z: -50.0, ry: -0.06, scale: [1.02, 1.04, 0.98], seed: 201 },
    { x: -26.0, z: -40.0, ry: 0.12,  scale: [0.98, 0.96, 1.04], seed: 202 }, // alcove
    { x: -23.2, z: -30.0, ry: -0.15, scale: [1.05, 1.10, 0.96], seed: 203 }, // promontory
    { x: -25.2, z: -20.0, ry: 0.05,  scale: [1.00, 1.02, 1.00], seed: 204 },
    { x: -22.8, z: -10.0, ry: -0.18, scale: [1.06, 1.14, 0.98], seed: 205 }, // prominent projection
    { x: -25.8, z: 0.0,   ry: 0.10,  scale: [0.96, 0.94, 1.06], seed: 206 }, // recessed alcove
    { x: -23.0, z: 10.0,  ry: -0.12, scale: [1.04, 1.08, 0.96], seed: 207 }, // promontory
    { x: -25.0, z: 20.0,  ry: 0.08,  scale: [1.00, 1.00, 1.02], seed: 208 },
    { x: -26.2, z: 30.0,  ry: -0.10, scale: [0.95, 0.95, 1.05], seed: 209 }, // alcove
    { x: -23.4, z: 40.0,  ry: 0.14,  scale: [1.04, 1.06, 0.98], seed: 210 }, // promontory
    { x: -24.8, z: 50.0,  ry: -0.05, scale: [1.02, 1.02, 1.00], seed: 211 }
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

  // 2. Talus rock scree fields and clustered boulders along cliff base (x ~ -19.5 to -15.5)
  const talusSpecs = [
    { x: -18.5, z: -46.0, ry: 0.5, s: 1.3, v: 1 },
    { x: -19.2, z: -38.0, ry: 1.4, s: 1.1, v: 2 },
    { x: -16.5, z: -32.0, ry: 2.3, s: 1.3, v: 3 },
    { x: -18.0, z: -24.0, ry: 0.7, s: 1.0, v: 4 },
    { x: -16.2, z: -16.0, ry: 2.9, s: 1.4, v: 5 },
    { x: -19.0, z: -8.0,  ry: 0.4, s: 1.2, v: 6 },
    { x: -17.0, z: -2.0,  ry: 1.8, s: 1.2, v: 7 },
    { x: -15.8, z: 5.0,   ry: 2.6, s: 1.4, v: 8 },
    { x: -18.2, z: 13.0,  ry: 1.1, s: 1.1, v: 9 },
    { x: -16.8, z: 21.0,  ry: 3.0, s: 1.3, v: 10 },
    { x: -19.0, z: 29.0,  ry: 1.6, s: 1.2, v: 11 },
    { x: -16.2, z: 38.0,  ry: 0.8, s: 1.4, v: 12 },
    { x: -18.0, z: 47.0,  ry: 2.1, s: 1.2, v: 13 }
  ];

  for (let i = 0; i < talusSpecs.length; i++) {
    const ts = talusSpecs[i];
    const rock = buildTalusRock(T, { seed: 800 + i * 29, variant: ts.v });
    rock.scale.set(ts.s, ts.s, ts.s);
    rock.rotation.y = ts.ry;
    const y = heightAt(ts.x, ts.z);
    rock.position.set(ts.x, y, ts.z);
    zone.add(rock);
  }

  // 3. Zone-level geological details:
  // Layered sandstone scree aprons / talus slope wedges sloping down from cliff base
  const talusApronGeo = new T.ConeGeometry(4.5, 3.5, 5);
  talusApronGeo.rotateX(Math.PI / 10);
  talusApronGeo.translate(0, 1.4, 0);

  const apronPositions = [
    { x: -19.5, z: -36.0, ry: -0.6, scale: [1.1, 0.9, 0.8] },
    { x: -19.0, z: -14.0, ry: -1.0, scale: [1.0, 1.0, 0.9] },
    { x: -18.5, z: 8.0,   ry: 0.5,  scale: [1.1, 0.85, 0.85] },
    { x: -19.8, z: 34.0,  ry: -0.7, scale: [1.0, 0.95, 0.9] }
  ];

  for (let i = 0; i < apronPositions.length; i++) {
    const ap = apronPositions[i];
    const apronMesh = new T.Mesh(talusApronGeo, matGravelApron);
    apronMesh.scale.set(ap.scale[0], ap.scale[1], ap.scale[2]);
    apronMesh.rotation.y = ap.ry;
    const y = heightAt(ap.x, ap.z);
    apronMesh.position.set(ap.x, y, ap.z);
    apronMesh.castShadow = true;
    apronMesh.receiveShadow = true;
    zone.add(apronMesh);
  }

  // Weathered rock ribs / sandstone outcroppings projecting forward from the western wall
  const ribGeo = new T.DodecahedronGeometry(1.6, 1);
  ribGeo.scale(1.2, 2.2, 1.0);

  const ribMesh1 = new T.Mesh(ribGeo, matSunlitRock);
  ribMesh1.position.set(-17.5, heightAt(-17.5, -18.0) + 1.6, -18.0);
  ribMesh1.rotation.set(0.12, -0.4, 0.2);
  ribMesh1.castShadow = true;
  ribMesh1.receiveShadow = true;
  zone.add(ribMesh1);

  const ribMesh2 = new T.Mesh(ribGeo, matDarkStrata);
  ribMesh2.position.set(-17.2, heightAt(-17.2, 16.0) + 1.8, 16.0);
  ribMesh2.rotation.set(-0.15, 0.5, -0.22);
  ribMesh2.castShadow = true;
  ribMesh2.receiveShadow = true;
  zone.add(ribMesh2);

  zone.userData.update = (t, dt) => {
    // Static geology
  };

  return zone;
}

export const build = buildWestCliffWall;

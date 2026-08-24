// src/zones/pond_basin.js — zone "PondBasin"
// Recessed organic-shaped pond basin with reflective animated water surface,
// bordered by natural stones and spanned centrally by the arched wooden bridge.
// PLAN bbox: centre (1.5, -0.1, -1.0) extents (12.0 x 2.5 x 11.0) m.
import * as THREE from 'three';
import { buildArchedBridge } from '../assets/arched_bridge.js';
import { buildPondRock } from '../assets/pond_rock.js';
import { buildStoneLantern } from '../assets/stone_lantern.js';
import { buildSteppingStone } from '../assets/stepping_stone.js';

function createRNG(seed = 107) {
  let s = (seed >>> 0) || 107;
  return () => {
    s = (s + 0x6d2b79f5) | 0;
    let t = Math.imul(s ^ (s >>> 15), 1 | s);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function buildPondBasin(ctx = {}) {
  const T = ctx.THREE || THREE;
  const heightAt = ctx.heightAt || (() => 0);
  const rng = ctx.rand || createRNG(333);

  const zone = new T.Group();
  zone.name = 'PondBasin';
  const animatedTickers = [];

  const place = (obj, x, z, ry = 0, yOff = 0) => {
    obj.position.set(x, heightAt(x, z) + yOff, z);
    obj.rotation.y = ry;
    zone.add(obj);
    if (obj.userData?.tick) animatedTickers.push(obj);
    return obj;
  };

  // 1. Central Arched Wooden Bridge (Taiko-bashi) spanning diagonally across basin
  const bridge = buildArchedBridge(T, { seed: 88 });
  bridge.position.set(0.70, 0.0, -0.95);
  bridge.rotation.y = 0.31;
  zone.add(bridge);

  const abutMat = new T.MeshStandardMaterial({ color: 0x4d504e, roughness: 0.92, metalness: 0.05 });
  const sAbut = new T.Mesh(new T.BoxGeometry(1.6, 0.35, 0.9), abutMat);
  sAbut.position.set(-0.02, -0.14, 1.30);
  sAbut.rotation.y = 0.31;
  sAbut.castShadow = sAbut.receiveShadow = true;
  zone.add(sAbut);

  const nAbut = new T.Mesh(new T.BoxGeometry(1.6, 0.40, 0.9), abutMat);
  nAbut.position.set(1.42, -0.16, -3.15);
  nAbut.rotation.y = 0.31;
  nAbut.castShadow = nAbut.receiveShadow = true;
  zone.add(nAbut);

  // 2. Yukimi-doro (snow-viewing) stone lanterns
  place(buildStoneLantern(T, { seed: 101 }), 3.5, -1.8, -0.6, 0.02);
  place(buildStoneLantern(T, { seed: 202 }), -1.2, 1.9, 0.85, 0.01);

  // 3. Natural Shoreline & Islet Rocks
  const rocks = [
    { x: -0.55, z: 1.55, ry: 0.4, s: 0.92, v: 11 },
    { x: 0.45, z: 1.70, ry: -0.7, s: 1.05, v: 12 },
    { x: -1.15, z: 0.95, ry: 1.1, s: 0.85, v: 13 },
    { x: -1.75, z: 1.65, ry: -0.3, s: 0.75, v: 14 },
    { x: -2.15, z: -0.50, ry: 2.0, s: 1.0, v: 21 },
    { x: -2.35, z: -1.75, ry: -1.4, s: 0.9, v: 22 },
    { x: -1.85, z: -2.75, ry: 0.6, s: 0.8, v: 23 },
    { x: 0.75, z: -3.55, ry: 1.7, s: 1.1, v: 31 },
    { x: 2.10, z: -3.75, ry: -0.5, s: 0.92, v: 32 },
    { x: 0.15, z: -3.15, ry: -2.1, s: 0.85, v: 33 },
    { x: 3.75, z: -0.75, ry: 0.25, s: 1.15, v: 41 },
    { x: 3.35, z: -2.35, ry: 2.7, s: 0.95, v: 42 },
    { x: 4.25, z: -1.55, ry: -1.0, s: 0.85, v: 43 },
    { x: 2.75, z: 0.45, ry: 1.3, s: 0.9, v: 44 },
    { x: 1.75, z: 1.25, ry: -0.8, s: 0.78, v: 45 },
    { x: 2.35, z: -1.25, ry: 0.85, s: 0.85, v: 51, yOff: 0.08 },
    { x: 2.70, z: -1.55, ry: -1.3, s: 0.68, v: 52, yOff: 0.06 },
  ];
  for (const r of rocks) {
    const rock = buildPondRock(T, { seed: r.v });
    rock.scale.setScalar(r.s);
    place(rock, r.x, r.z, r.ry, r.yOff || 0.02);
  }

  // 4. Stepping Stones Approach
  const steps = [
    { x: -2.40, z: 2.50, ry: 0.2 }, { x: -1.85, z: 2.25, ry: -0.3 },
    { x: -1.30, z: 1.95, ry: 0.4 }, { x: -0.75, z: 1.65, ry: -0.1 }, { x: -0.20, z: 1.40, ry: 0.3 },
  ];
  for (let i = 0; i < steps.length; i++) {
    place(buildSteppingStone(T, { seed: 500 + i * 17 }), steps[i].x, steps[i].z, steps[i].ry, 0.01);
  }

  // 5. Water Lily Pads & Blossoms (Suiren)
  const lilyMat = new T.MeshStandardMaterial({ color: 0x2e5c2d, roughness: 0.6, metalness: 0.02, side: T.DoubleSide });
  const flowerMat = new T.MeshStandardMaterial({ color: 0xffe8ea, roughness: 0.4 });
  const centerMat = new T.MeshStandardMaterial({ color: 0xffcc00, roughness: 0.3 });
  const lilyGroup = new T.Group();
  lilyGroup.name = 'WaterLilies';

  const lilyCenters = [{ cx: 2.1, cz: -0.5, n: 6 }, { cx: -0.9, cz: -1.8, n: 5 }, { cx: 3.1, cz: -2.1, n: 4 }];
  const padGeo = new T.CircleGeometry(0.16, 16, 0.2, Math.PI * 1.85);
  padGeo.rotateX(-Math.PI / 2);
  const petalGeo = new T.ConeGeometry(0.025, 0.06, 6);
  const coreGeo = new T.SphereGeometry(0.02, 8, 6);

  for (const c of lilyCenters) {
    for (let i = 0; i < c.n; i++) {
      const lx = c.cx + (rng() - 0.5) * 0.7;
      const lz = c.cz + (rng() - 0.5) * 0.7;
      const ls = 0.7 + rng() * 0.6;
      const pad = new T.Mesh(padGeo, lilyMat);
      pad.position.set(lx, -0.116, lz);
      pad.rotation.y = rng() * Math.PI * 2;
      pad.scale.set(ls, 1.0, ls);
      pad.receiveShadow = true;
      lilyGroup.add(pad);

      if (i === 0) {
        const fl = new T.Group();
        fl.position.set(lx + 0.04, -0.105, lz + 0.03);
        const core = new T.Mesh(coreGeo, centerMat);
        core.position.y = 0.015;
        fl.add(core);
        for (let p = 0; p < 8; p++) {
          const a = (p / 8) * Math.PI * 2;
          const petal = new T.Mesh(petalGeo, flowerMat);
          petal.position.set(Math.cos(a) * 0.03, 0.02, Math.sin(a) * 0.03);
          petal.rotation.z = 0.5;
          petal.rotation.y = -a;
          fl.add(petal);
        }
        lilyGroup.add(fl);
      }
    }
  }
  zone.add(lilyGroup);

  // 6. Floating Autumn Leaves (Koyo)
  const redMat = new T.MeshStandardMaterial({ color: 0xba2215, roughness: 0.55, side: T.DoubleSide });
  const ornMat = new T.MeshStandardMaterial({ color: 0xdb581a, roughness: 0.55, side: T.DoubleSide });
  const leafGeo = new T.PlaneGeometry(0.08, 0.08);
  leafGeo.rotateX(-Math.PI / 2);

  const floatingLeaves = [];
  const leafGroup = new T.Group();
  leafGroup.name = 'FloatingLeaves';

  for (let i = 0; i < 28; i++) {
    const ang = rng() * Math.PI * 2;
    const rad = 0.6 + rng() * 2.8;
    const lx = 1.2 + Math.cos(ang) * rad * 1.1;
    const lz = -1.0 + Math.sin(ang) * rad * 0.8;
    const leaf = new T.Mesh(leafGeo, rng() > 0.4 ? redMat : ornMat);
    leaf.position.set(lx, -0.118, lz);
    leaf.rotation.y = rng() * Math.PI * 2;
    const s = 0.7 + rng() * 0.6;
    leaf.scale.set(s, s, s);
    leafGroup.add(leaf);
    floatingLeaves.push({ mesh: leaf, bx: lx, bz: lz, ph: rng() * Math.PI * 2, sp: 0.3 + rng() * 0.4 });
  }
  zone.add(leafGroup);

  // 7. Shoreline Water Reeds (Kakitsubata)
  const reedMat = new T.MeshStandardMaterial({ color: 0x4a6b2d, roughness: 0.7, side: T.DoubleSide });
  const reedGeo = new T.ConeGeometry(0.02, 0.55, 4);
  const reedGroup = new T.Group();
  reedGroup.name = 'ShorelineReeds';

  const reedSpots = [{ x: -1.5, z: -0.2 }, { x: -1.7, z: -2.2 }, { x: 3.2, z: 0.1 }, { x: 1.3, z: 1.1 }];
  const animatedReeds = [];
  for (const rs of reedSpots) {
    for (let r = 0; r < 6; r++) {
      const rx = rs.x + (rng() - 0.5) * 0.35;
      const rz = rs.z + (rng() - 0.5) * 0.35;
      const reed = new T.Mesh(reedGeo, reedMat);
      reed.position.set(rx, heightAt(rx, rz) + 0.22, rz);
      reed.rotation.set((rng() - 0.5) * 0.18, 0, (rng() - 0.5) * 0.18);
      reed.castShadow = true;
      reedGroup.add(reed);
      animatedReeds.push({ mesh: reed, rz: reed.rotation.z, ph: rng() * Math.PI * 2 });
    }
  }
  zone.add(reedGroup);

  // 8. Bamboo Water Spout (Kakehi)
  const bamMat = new T.MeshStandardMaterial({ color: 0x768748, roughness: 0.65, metalness: 0.04 });
  const spoutGroup = new T.Group();
  spoutGroup.name = 'BambooSpout';
  const postL = new T.Mesh(new T.CylinderGeometry(0.03, 0.03, 0.65, 8), bamMat);
  postL.position.set(0, 0.28, -0.06); postL.rotation.z = -0.15;
  const postR = new T.Mesh(new T.CylinderGeometry(0.03, 0.03, 0.65, 8), bamMat);
  postR.position.set(0, 0.28, 0.06); postR.rotation.z = 0.15;
  const mainSpout = new T.Mesh(new T.CylinderGeometry(0.035, 0.035, 0.85, 8), bamMat);
  mainSpout.position.set(0.12, 0.42, 0); mainSpout.rotation.z = -1.25;
  spoutGroup.add(postL, postR, mainSpout);
  spoutGroup.position.set(3.8, heightAt(3.8, -0.5), -0.5);
  spoutGroup.rotation.y = -2.2;
  zone.add(spoutGroup);

  // Animation hook
  zone.userData.update = (t, dt) => {
    for (const ticker of animatedTickers) {
      if (ticker.userData?.tick) ticker.userData.tick(t, dt);
    }
    for (let i = 0; i < floatingLeaves.length; i++) {
      const fl = floatingLeaves[i];
      fl.mesh.position.x = fl.bx + Math.sin(t * fl.sp + fl.ph) * 0.04;
      fl.mesh.position.z = fl.bz + Math.cos(t * fl.sp * 0.8 + fl.ph) * 0.03;
      fl.mesh.rotation.y += dt * 0.05;
    }
    const sway = Math.sin(t * 1.8) * 0.04;
    for (let i = 0; i < animatedReeds.length; i++) {
      const rd = animatedReeds[i];
      rd.mesh.rotation.z = rd.rz + sway * Math.cos(t + rd.ph);
    }
  };

  return zone;
}

export { buildPondBasin as build };

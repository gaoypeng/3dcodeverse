// src/zones/gravel_court.js — zone "GravelCourt": Karesansui dry garden bed with raked ripples in fine granite gravel, bounded by granite borders and anchored by triad rock compositions.
// PLAN bbox: centre (-5.5, 0.3, -4.5) extents (10 x 1.5 x 11) m. Contents: MossyRock, BambooFence
// Bounding box range: X ∈ [-10.5, -0.5], Y ∈ [-0.45, 1.05], Z ∈ [-10.0, 1.0]
import * as THREE from 'three';
import { buildMossyRock } from '../assets/mossy_rock.js';
import { buildBambooFence } from '../assets/bamboo_fence.js';

export function build(ctx) {
  const T = (ctx && ctx.THREE) ? ctx.THREE : THREE;
  const heightAt = ctx.heightAt || ((x, z) => 0.28);
  const zone = new THREE.Group();
  zone.name = 'GravelCourt';

  // --- Materials ---
  const gravelBedMat = new T.MeshStandardMaterial({
    color: 0xc8c3ba,
    roughness: 0.88,
    metalness: 0.02
  });

  const rakeGrooveMat = new T.MeshStandardMaterial({
    color: 0xb5afa5,
    roughness: 0.92,
    metalness: 0.02
  });

  const borderStoneMat = new T.MeshStandardMaterial({
    color: 0x5a5854,
    roughness: 0.9,
    metalness: 0.04
  });

  const mossIslandMat = new T.MeshStandardMaterial({
    color: 0x3d5422,
    roughness: 0.95,
    metalness: 0.0
  });

  // Gravel Court center and size
  const courtCX = -5.8;
  const courtCZ = -4.5;
  const courtW = 8.6; // X span: -10.1 to -1.5
  const courtD = 8.8; // Z span: -8.9 to -0.1

  // 1. Raked Gravel Bed with subtle procedural ripple geometry
  const bedSegmentsX = 64;
  const bedSegmentsZ = 64;
  const gravelGeo = new T.PlaneGeometry(courtW, courtD, bedSegmentsX, bedSegmentsZ);
  gravelGeo.rotateX(-Math.PI / 2);
  const gPos = gravelGeo.attributes.position;
  const tempV = new T.Vector3();

  // Rock triad focal centers for ripple generation
  const triad1 = new T.Vector2(-7.2, -4.8);
  const triad2 = new T.Vector2(-4.2, -3.2);

  for (let i = 0; i < gPos.count; i++) {
    tempV.fromBufferAttribute(gPos, i);
    const wx = courtCX + tempV.x;
    const wz = courtCZ + tempV.z;
    const groundH = heightAt(wx, wz);

    // Linear parallel raking along Z axis
    const linearRake = Math.sin(wx * 22.0) * 0.012;

    // Concentric ripple rings around rock triad 1
    const d1 = triad1.distanceTo(new T.Vector2(wx, wz));
    const ripple1 = (d1 < 2.4) ? Math.cos(d1 * 18.0) * Math.exp(-d1 * 0.9) * 0.022 : 0;

    // Concentric ripple rings around rock triad 2
    const d2 = triad2.distanceTo(new T.Vector2(wx, wz));
    const ripple2 = (d2 < 2.0) ? Math.cos(d2 * 18.0) * Math.exp(-d2 * 1.1) * 0.018 : 0;

    const totalY = groundH + 0.035 + linearRake + ripple1 + ripple2;
    gPos.setY(i, totalY);
  }
  gravelGeo.computeVertexNormals();

  const gravelMesh = new T.Mesh(gravelGeo, gravelBedMat);
  gravelMesh.name = 'GravelBed';
  gravelMesh.position.set(courtCX, 0, courtCZ);
  gravelMesh.receiveShadow = true;
  zone.add(gravelMesh);

  // 2. Granite Border Kerbstones framing the gravel court
  // Perimeter segments
  const borderThickness = 0.16;
  const borderHeight = 0.14;
  const borderGeoX = new T.BoxGeometry(courtW + borderThickness * 2, borderHeight, borderThickness);
  const borderGeoZ = new T.BoxGeometry(borderThickness, borderHeight, courtD);

  const addBorder = (geo, x, z) => {
    const border = new T.Mesh(geo, borderStoneMat);
    border.position.set(x, heightAt(x, z) + borderHeight / 2 + 0.01, z);
    border.castShadow = true;
    border.receiveShadow = true;
    zone.add(border);
  };

  // North (z = -8.9), South (z = -0.1), West (x = -10.1), East (x = -1.5)
  addBorder(borderGeoX, courtCX, courtCZ - courtD / 2 - borderThickness / 2);
  addBorder(borderGeoX, courtCX, courtCZ + courtD / 2 + borderThickness / 2);
  addBorder(borderGeoZ, courtCX - courtW / 2 - borderThickness / 2, courtCZ);
  addBorder(borderGeoZ, courtCX + courtW / 2 + borderThickness / 2, courtCZ);

  // 3. Moss Islands around Rock Compositions
  const makeMossIsland = (cx, cz, rx, rz) => {
    const islandGeo = new T.CylinderGeometry(1.0, 1.2, 0.06, 24);
    islandGeo.scale(rx, 1, rz);
    const pos = islandGeo.attributes.position;
    for (let i = 0; i < pos.count; i++) {
      const vx = pos.getX(i);
      const vz = pos.getZ(i);
      const dist = Math.sqrt(vx * vx + vz * vz);
      if (pos.getY(i) > 0) {
        pos.setY(i, 0.03 * (1.0 - Math.min(1.0, dist * 0.8)));
      }
    }
    islandGeo.computeVertexNormals();
    const island = new T.Mesh(islandGeo, mossIslandMat);
    island.position.set(cx, heightAt(cx, cz) + 0.038, cz);
    island.receiveShadow = true;
    island.castShadow = true;
    zone.add(island);
  };

  makeMossIsland(triad1.x, triad1.y, 1.4, 1.2);
  makeMossIsland(triad2.x, triad2.y, 1.1, 0.9);

  // 4. Anchor Rock Compositions (Triads) using MossyRock
  // Triad 1: Principal, companion, and footing rock
  const placeRock = (x, z, ry, scale) => {
    const rock = buildMossyRock(T, { seed: Math.floor(Math.abs(x * 100 + z * 10)) });
    rock.position.set(x, heightAt(x, z) + 0.03, z);
    rock.rotation.y = ry;
    rock.scale.set(scale, scale, scale);
    zone.add(rock);
    return rock;
  };

  // Primary Triad 1 (West group)
  placeRock(triad1.x, triad1.y, 0.4, 1.15); // Main vertical standing stone
  placeRock(triad1.x + 0.85, triad1.y - 0.35, 1.8, 0.75); // Reclining companion
  placeRock(triad1.x - 0.65, triad1.y + 0.45, -0.9, 0.55); // Low flat footing stone

  // Secondary Triad 2 (East-central group)
  placeRock(triad2.x, triad2.y, -1.2, 0.95);
  placeRock(triad2.x - 0.65, triad2.y - 0.3, 0.8, 0.60);
  placeRock(triad2.x + 0.55, triad2.y + 0.4, 2.1, 0.45);

  // 5. Bamboo Fences framing the court boundary (West and North perimeter)
  // BambooFence dimensions: 2.20 x 1.80 x 0.15 m
  const placeFence = (x, z, ry) => {
    const fence = buildBambooFence(T, { seed: Math.floor(Math.abs(x * 70 + z * 30)) });
    fence.position.set(x, heightAt(x, z), z);
    fence.rotation.y = ry;
    zone.add(fence);
    return fence;
  };

  // Western fence boundary (spanning Z from ~ -8.5 to -1.5)
  placeFence(-9.8, -7.2, Math.PI / 2);
  placeFence(-9.8, -5.1, Math.PI / 2);
  placeFence(-9.8, -3.0, Math.PI / 2);

  // Northern fence boundary (spanning X from ~ -9.5 to -3.5)
  placeFence(-8.0, -9.0, 0);
  placeFence(-5.9, -9.0, 0);
  placeFence(-3.8, -9.0, 0);

  zone.userData.update = (t, dt) => {
    // Serene static dry garden elements
  };

  return zone;
}

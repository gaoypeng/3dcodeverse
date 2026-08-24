// src/zones/planter_perimeter.js — zone "PlanterPerimeter"
// Bordering garden zone flanking the parapet walls on the north, east, and west edges
// with rectangular planters, potted dwarf trees, and lush ornamental grasses.
//
// Bbox: centre (0.000, 1.500, 0.000) m, extents (22.000, 3.000, 22.000) m

import * as THREE from 'three';
import { buildRectangularPlanter } from '../assets/rectangular_planter.js';
import { buildPottedTree } from '../assets/potted_tree.js';

export function build(ctx) {
  const T = ctx.THREE || THREE;
  const heightAt = ctx.heightAt || (() => 0.15);
  const zone = new T.Group();
  zone.name = 'PlanterPerimeter';

  const animatedAssets = [];

  // Helper to instantiate and place assets
  function placeAsset(asset, x, z, ry = 0, scale = 1.0) {
    const y = heightAt(x, z);
    asset.position.set(x, y, z);
    asset.rotation.y = ry;
    if (scale !== 1.0) {
      asset.scale.set(scale, scale, scale);
    }
    zone.add(asset);
    if (asset.userData && typeof asset.userData.tick === 'function') {
      animatedAssets.push(asset);
    }
    return asset;
  }

  // --- Materials for Zone-Local Garden Bed Details ---
  const kerbMat = new T.MeshStandardMaterial({
    color: 0x383533,
    roughness: 0.85,
    metalness: 0.1,
    name: 'GardenKerb'
  });

  const gravelMat = new T.MeshStandardMaterial({
    color: 0x2e2926,
    roughness: 0.95,
    metalness: 0.05,
    name: 'GravelBed'
  });

  // --- 1. Garden Gravel Beds & Stone Kerbs along North, West, East, and South-flanks ---
  const gravelY = heightAt(0, 0) + 0.008;
  const kerbY = heightAt(0, 0) + 0.035;
  const kerbH = 0.07;
  const kerbW = 0.12;

  // North Gravel Strip (z ∈ [-10.8, -8.6], x ∈ [-10.8, 10.8])
  const northGravel = new T.Mesh(new T.BoxGeometry(21.4, 0.015, 2.0), gravelMat);
  northGravel.position.set(0, gravelY, -9.7);
  northGravel.receiveShadow = true;
  zone.add(northGravel);

  const northKerb = new T.Mesh(new T.BoxGeometry(21.4, kerbH, kerbW), kerbMat);
  northKerb.position.set(0, kerbY, -8.65);
  northKerb.castShadow = true;
  northKerb.receiveShadow = true;
  zone.add(northKerb);

  // West Gravel Strip (x ∈ [-10.8, -8.6], z ∈ [-8.6, 10.8])
  const westGravel = new T.Mesh(new T.BoxGeometry(2.0, 0.015, 19.4), gravelMat);
  westGravel.position.set(-9.7, gravelY, 0.0);
  westGravel.receiveShadow = true;
  zone.add(westGravel);

  const westKerb = new T.Mesh(new T.BoxGeometry(kerbW, kerbH, 17.4), kerbMat);
  westKerb.position.set(-8.65, kerbY, 0.05);
  westKerb.castShadow = true;
  westKerb.receiveShadow = true;
  zone.add(westKerb);

  // East Gravel Strip (x ∈ [8.6, 10.8], z ∈ [-8.6, 10.8])
  const eastGravel = new T.Mesh(new T.BoxGeometry(2.0, 0.015, 19.4), gravelMat);
  eastGravel.position.set(9.7, gravelY, 0.0);
  eastGravel.receiveShadow = true;
  zone.add(eastGravel);

  const eastKerb = new T.Mesh(new T.BoxGeometry(kerbW, kerbH, 17.4), kerbMat);
  eastKerb.position.set(8.65, kerbY, 0.05);
  eastKerb.castShadow = true;
  eastKerb.receiveShadow = true;
  zone.add(eastKerb);

  // South Gravel Flanks (x ∈ [-10.8, -3.2] and [3.2, 10.8], z ≈ 9.7)
  const southGravelW = new T.Mesh(new T.BoxGeometry(7.4, 0.015, 2.0), gravelMat);
  southGravelW.position.set(-7.0, gravelY, 9.7);
  southGravelW.receiveShadow = true;
  zone.add(southGravelW);

  const southKerbW = new T.Mesh(new T.BoxGeometry(7.4, kerbH, kerbW), kerbMat);
  southKerbW.position.set(-7.0, kerbY, 8.65);
  southKerbW.castShadow = true;
  zone.add(southKerbW);

  const southGravelE = new T.Mesh(new T.BoxGeometry(7.4, 0.015, 2.0), gravelMat);
  southGravelE.position.set(7.0, gravelY, 9.7);
  southGravelE.receiveShadow = true;
  zone.add(southGravelE);

  const southKerbE = new T.Mesh(new T.BoxGeometry(7.4, kerbH, kerbW), kerbMat);
  southKerbE.position.set(7.0, kerbY, 8.65);
  southKerbE.castShadow = true;
  zone.add(southKerbE);

  // --- 2. Placement of Perimeter Planters & Potted Trees ---

  // A. North Perimeter (Facing Sunset & Metropolis Skyline)
  placeAsset(buildPottedTree(T, { seed: 101, variant: 0 }), -9.6, -9.6, 0.2);
  placeAsset(buildRectangularPlanter(T, { seed: 102 }), -7.4, -9.7, 0);
  placeAsset(buildRectangularPlanter(T, { seed: 103 }), -5.4, -9.7, 0);
  placeAsset(buildPottedTree(T, { seed: 104, variant: 1 }), -3.4, -9.6, -0.4);
  placeAsset(buildRectangularPlanter(T, { seed: 105 }), -1.4, -9.7, 0);
  placeAsset(buildRectangularPlanter(T, { seed: 106 }), 1.4, -9.7, 0);
  placeAsset(buildPottedTree(T, { seed: 107, variant: 2 }), 3.4, -9.6, 0.5);
  placeAsset(buildRectangularPlanter(T, { seed: 108 }), 5.4, -9.7, 0);
  placeAsset(buildRectangularPlanter(T, { seed: 109 }), 7.4, -9.7, 0);
  placeAsset(buildPottedTree(T, { seed: 110, variant: 3 }), 9.6, -9.6, -0.2);

  // B. West Perimeter (Along parapet)
  placeAsset(buildRectangularPlanter(T, { seed: 201 }), -9.7, -7.4, Math.PI / 2);
  placeAsset(buildRectangularPlanter(T, { seed: 202 }), -9.7, -5.4, Math.PI / 2);
  placeAsset(buildPottedTree(T, { seed: 203, variant: 1 }), -9.6, -3.4, 0.8);
  placeAsset(buildRectangularPlanter(T, { seed: 204 }), -9.7, -1.4, Math.PI / 2);
  placeAsset(buildRectangularPlanter(T, { seed: 205 }), -9.7, 1.4, Math.PI / 2);
  placeAsset(buildPottedTree(T, { seed: 206, variant: 2 }), -9.6, 3.4, -0.6);
  placeAsset(buildRectangularPlanter(T, { seed: 207 }), -9.7, 5.4, Math.PI / 2);
  placeAsset(buildRectangularPlanter(T, { seed: 208 }), -9.7, 7.4, Math.PI / 2);
  placeAsset(buildPottedTree(T, { seed: 209, variant: 0 }), -9.6, 9.6, 1.1);

  // C. East Perimeter (Along parapet)
  placeAsset(buildRectangularPlanter(T, { seed: 301 }), 9.7, -7.4, -Math.PI / 2);
  placeAsset(buildRectangularPlanter(T, { seed: 302 }), 9.7, -5.4, -Math.PI / 2);
  placeAsset(buildPottedTree(T, { seed: 303, variant: 3 }), 9.6, -3.4, -0.7);
  placeAsset(buildRectangularPlanter(T, { seed: 304 }), 9.7, -1.4, -Math.PI / 2);
  placeAsset(buildRectangularPlanter(T, { seed: 305 }), 9.7, 1.4, -Math.PI / 2);
  placeAsset(buildPottedTree(T, { seed: 306, variant: 1 }), 9.6, 3.4, 0.4);
  placeAsset(buildRectangularPlanter(T, { seed: 307 }), 9.7, 5.4, -Math.PI / 2);
  placeAsset(buildRectangularPlanter(T, { seed: 308 }), 9.7, 7.4, -Math.PI / 2);
  placeAsset(buildPottedTree(T, { seed: 309, variant: 2 }), 9.6, 9.6, -1.2);

  // D. South Perimeter Flanks (Leaving center open for terrace entrance)
  placeAsset(buildRectangularPlanter(T, { seed: 401 }), -7.4, 9.7, Math.PI);
  placeAsset(buildRectangularPlanter(T, { seed: 402 }), -5.4, 9.7, Math.PI);
  placeAsset(buildPottedTree(T, { seed: 403, variant: 0 }), -3.4, 9.6, 0.3);

  placeAsset(buildPottedTree(T, { seed: 404, variant: 1 }), 3.4, 9.6, -0.5);
  placeAsset(buildRectangularPlanter(T, { seed: 405 }), 5.4, 9.7, Math.PI);
  placeAsset(buildRectangularPlanter(T, { seed: 406 }), 7.4, 9.7, Math.PI);

  // E. Intermediate Greenery Framing (Outside LoungeArea [-4, 4])
  // Creates foreground depth for cameras without obstructing walkways
  placeAsset(buildRectangularPlanter(T, { seed: 501 }), -5.8, -0.5, 0.1);
  placeAsset(buildPottedTree(T, { seed: 502, variant: 2 }), -5.8, 1.6, 0.9);

  // --- 3. Subtle Warm Garden Up-Lights (2 discreet accent lights on trees) ---
  const upLight1 = new T.PointLight(0xffa85c, 0.8, 4.0, 1.8);
  upLight1.position.set(-5.8, heightAt(-5.8, 1.6) + 0.3, 1.6);
  zone.add(upLight1);

  const upLight2 = new T.PointLight(0xffb068, 0.8, 4.0, 1.8);
  upLight2.position.set(-3.4, heightAt(-3.4, -9.6) + 0.3, -9.6);
  zone.add(upLight2);

  // --- 4. Animation Update Hook ---
  const update = (t, dt) => {
    for (let i = 0; i < animatedAssets.length; i++) {
      const asset = animatedAssets[i];
      if (asset.userData && typeof asset.userData.tick === 'function') {
        asset.userData.tick(t, dt);
      }
    }
  };

  zone.userData.update = update;
  zone.userData.tick = update;

  return zone;
}

export const buildPlanterPerimeter = build;

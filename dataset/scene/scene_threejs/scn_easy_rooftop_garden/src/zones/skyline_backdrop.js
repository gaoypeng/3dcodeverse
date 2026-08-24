// src/zones/skyline_backdrop.js — zone "SkylineBackdrop"
// Distant cityscape backdrop containing varied height skyscraper silhouettes, glass towers, and roof structures catching the golden hour rim light.
// PLAN Bbox: centre (0.000, 10.000, -35.000) m, extents (90.000, 45.000, 20.000) m
// Contents: CityTower

import * as THREE from 'three';
import { buildCityTower } from '../assets/city_tower.js';

export function buildSkylineBackdrop(ctx = {}) {
  const T = ctx.THREE || THREE;
  const heightAt = ctx.heightAt || (() => 0.15);

  const zone = new T.Group();
  zone.name = 'SkylineBackdrop';

  // Planned tower placements within bbox:
  // X extents: [-38, 38], Z extents: [-42, -28], Y base on ground (heightAt)
  // Heights and scales arranged to form an appealing layered city skyline silhouette
  const towersData = [
    // Far background ridge (Z ≈ -41 .. -39)
    { x: -34.0, z: -40.0, sx: 0.95, sy: 0.88, sz: 0.95, ry: 0.25, variant: 1 },
    { x: -20.0, z: -38.5, sx: 1.10, sy: 1.05, sz: 1.10, ry: -0.15, variant: 2 },
    { x: -7.0,  z: -41.0, sx: 0.85, sy: 0.80, sz: 0.85, ry: 0.40, variant: 3 },
    { x: 6.0,   z: -39.0, sx: 1.15, sy: 1.12, sz: 1.15, ry: 0.05, variant: 4 },
    { x: 21.0,  z: -40.5, sx: 1.00, sy: 0.98, sz: 1.00, ry: -0.30, variant: 5 },
    { x: 35.0,  z: -39.0, sx: 0.90, sy: 0.85, sz: 0.90, ry: 0.20, variant: 6 },

    // Midground iconic towers (Z ≈ -36 .. -33)
    { x: -28.0, z: -34.0, sx: 1.05, sy: 0.95, sz: 1.05, ry: -0.45, variant: 7 },
    { x: -14.0, z: -33.0, sx: 0.95, sy: 1.00, sz: 0.95, ry: 0.10, variant: 0 },
    { x: 0.0,   z: -35.0, sx: 1.20, sy: 1.18, sz: 1.20, ry: 0.00, variant: 8 },
    { x: 14.0,  z: -33.5, sx: 0.95, sy: 0.92, sz: 0.95, ry: 0.50, variant: 9 },
    { x: 28.0,  z: -34.5, sx: 1.10, sy: 1.08, sz: 1.10, ry: -0.20, variant: 10 },

    // Foreground skyline layering (Z ≈ -30 .. -28)
    { x: -38.0, z: -29.0, sx: 0.80, sy: 0.72, sz: 0.80, ry: 0.35, variant: 11 },
    { x: -22.0, z: -28.5, sx: 0.88, sy: 0.82, sz: 0.88, ry: -0.10, variant: 12 },
    { x: 22.0,  z: -29.0, sx: 0.90, sy: 0.80, sz: 0.90, ry: 0.15, variant: 13 },
    { x: 37.0,  z: -28.5, sx: 0.82, sy: 0.74, sz: 0.82, ry: -0.25, variant: 14 }
  ];

  const beacons = [];

  towersData.forEach((data, index) => {
    const tower = buildCityTower(T, { seed: 100 + index * 19, variant: data.variant });
    const groundY = heightAt(data.x, data.z);
    tower.position.set(data.x, groundY, data.z);
    tower.rotation.y = data.ry;
    tower.scale.set(data.sx, data.sy, data.sz);

    // Disable shadow casting on distant background towers to preserve performance and prevent shadow acne
    tower.traverse((child) => {
      if (child.isMesh) {
        child.castShadow = false;
        child.receiveShadow = false;
        if (child.material && child.material.emissive && child.geometry && child.geometry.type === 'SphereGeometry') {
          beacons.push({
            mat: child.material,
            phase: index * 0.5
          });
        }
      }
    });

    zone.add(tower);
  });

  // Tick beacon lights
  zone.userData.update = (t, dt) => {
    for (let i = 0; i < beacons.length; i++) {
      const b = beacons[i];
      const pulse = Math.sin(t * 3.14 + b.phase);
      b.mat.emissiveIntensity = pulse > 0.4 ? 2.5 : 0.3;
    }
  };

  return zone;
}

export const build = buildSkylineBackdrop;

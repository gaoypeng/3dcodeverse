// src/zones/bamboo_perimeter.js — zone "BambooPerimeter": Outer boundary screen composed of tightly spaced bamboo fencing and perimeter stone pavers defining the courtyard garden enclosure.
// PLAN bbox: centre (0, 1.2, 0) extents (25 x 3 x 25) m. Contents: BambooFence
// CONTRACT: export function build(ctx) / export function buildBambooPerimeter(ctx) → THREE.Group named 'BambooPerimeter'.
// ctx = { THREE, scene, renderer, loaders, env, heightAt, assets, MATS, rand }
import * as THREE from 'three';
import { bambooFenceParts } from '../assets/bamboo_fence.js';

export function buildBambooPerimeter(ctx) {
  const T = ctx.THREE || THREE;
  const heightAt = ctx.heightAt || (() => 0);
  const rand = ctx.rand || Math.random;

  const zone = new T.Group();
  zone.name = 'BambooPerimeter';

  // BambooFence dimensions: 3.00m (w) x 2.10m (h) x 0.15m (d)
  // Courtyard boundary enclosing approximately [-11.2, 11.2] x [-11.2, 11.2]
  // 4 sides with 7 fence panels each = 28 panels total.

  const wallOffsets = [-8.85, -5.9, -2.95, 0.0, 2.95, 5.9, 8.85];
  const fenceSpecs = [];

  // North wall (facing south, ry = 0)
  for (const x of wallOffsets) {
    fenceSpecs.push({ x, z: -11.2, ry: 0 });
  }
  // South wall (facing north, ry = Math.PI)
  for (const x of wallOffsets) {
    fenceSpecs.push({ x, z: 11.2, ry: Math.PI });
  }
  // West wall (facing east, ry = Math.PI / 2)
  for (const z of wallOffsets) {
    fenceSpecs.push({ x: -11.2, z, ry: Math.PI / 2 });
  }
  // East wall (facing west, ry = -Math.PI / 2)
  for (const z of wallOffsets) {
    fenceSpecs.push({ x: 11.2, z, ry: -Math.PI / 2 });
  }

  const numPanels = fenceSpecs.length;

  // Use InstancedMesh from bambooFenceParts for high rendering performance & low draw calls
  const parts = bambooFenceParts(T);
  const dummy = new T.Object3D();

  // Create an instanced mesh for each subpart in the fence
  const instancedParts = parts.map((part) => {
    const instMesh = new T.InstancedMesh(part.geometry, part.material, numPanels);
    instMesh.castShadow = true;
    instMesh.receiveShadow = true;
    return instMesh;
  });

  // Position all 28 panels
  for (let i = 0; i < numPanels; i++) {
    const spec = fenceSpecs[i];
    const y = heightAt(spec.x, spec.z);

    dummy.position.set(spec.x, y, spec.z);
    dummy.rotation.set(0, spec.ry, 0);
    dummy.scale.set(1, 1, 1);
    dummy.updateMatrix();

    for (let p = 0; p < instancedParts.length; p++) {
      instancedParts[p].setMatrixAt(i, dummy.matrix);
    }
  }

  for (let p = 0; p < instancedParts.length; p++) {
    instancedParts[p].instanceMatrix.needsUpdate = true;
    instancedParts[p].computeBoundingSphere();
    zone.add(instancedParts[p]);
  }

  // 2. Corner Wooden Pillars / Gate Posts (Hinoki cedar posts with copper caps)
  const cornerPostMat = new T.MeshStandardMaterial({ color: 0x3a2818, roughness: 0.85, metalness: 0.02 });
  const cornerCapMat = new T.MeshStandardMaterial({ color: 0x221a12, roughness: 0.7, metalness: 0.1 });
  const cornerStoneMat = new T.MeshStandardMaterial({ color: 0x686963, roughness: 0.9, metalness: 0.02 });

  const cornerPositions = [
    [-11.2, -11.2],
    [11.2, -11.2],
    [-11.2, 11.2],
    [11.2, 11.2]
  ];

  const pillarGeo = new T.BoxGeometry(0.22, 2.35, 0.22);
  const pillarCapGeo = new T.ConeGeometry(0.18, 0.12, 4);
  pillarCapGeo.rotateY(Math.PI / 4);
  const pillarPlinthGeo = new T.BoxGeometry(0.32, 0.12, 0.32);

  cornerPositions.forEach(([cx, cz]) => {
    const cy = heightAt(cx, cz);
    
    // Plinth
    const plinth = new T.Mesh(pillarPlinthGeo, cornerStoneMat);
    plinth.position.set(cx, cy + 0.06, cz);
    plinth.castShadow = true;
    plinth.receiveShadow = true;
    zone.add(plinth);

    // Pillar
    const pillar = new T.Mesh(pillarGeo, cornerPostMat);
    pillar.position.set(cx, cy + 1.18, cz);
    pillar.castShadow = true;
    pillar.receiveShadow = true;
    zone.add(pillar);

    // Cap
    const cap = new T.Mesh(pillarCapGeo, cornerCapMat);
    cap.position.set(cx, cy + 2.35 + 0.06, cz);
    cap.castShadow = true;
    zone.add(cap);
  });

  // 3. Perimeter Stone Paver Border / Kerb along the inner base of the bamboo fence
  const paverStoneMat = new T.MeshStandardMaterial({
    color: 0x767872,
    roughness: 0.88,
    metalness: 0.04
  });
  const paverGeo = new T.BoxGeometry(0.9, 0.08, 0.35);

  const paverCountPerSide = 22;
  const paverSpan = 20.0;
  const paverStep = paverSpan / (paverCountPerSide - 1);
  const innerOffset = 10.65;

  // Instanced mesh for perimeter pavers (4 sides * 22 = 88 pavers)
  const totalPavers = paverCountPerSide * 4;
  const instPavers = new T.InstancedMesh(paverGeo, paverStoneMat, totalPavers);
  instPavers.castShadow = true;
  instPavers.receiveShadow = true;

  let paverIdx = 0;
  // North & South
  for (let i = 0; i < paverCountPerSide; i++) {
    const posCoord = -paverSpan / 2 + i * paverStep;
    
    // North
    const yn = heightAt(posCoord, -innerOffset);
    dummy.position.set(posCoord, yn + 0.04, -innerOffset);
    dummy.rotation.set(0, 0, 0);
    dummy.updateMatrix();
    instPavers.setMatrixAt(paverIdx++, dummy.matrix);

    // South
    const ys = heightAt(posCoord, innerOffset);
    dummy.position.set(posCoord, ys + 0.04, innerOffset);
    dummy.rotation.set(0, 0, 0);
    dummy.updateMatrix();
    instPavers.setMatrixAt(paverIdx++, dummy.matrix);
  }

  // East & West (rotated 90 deg)
  for (let i = 0; i < paverCountPerSide; i++) {
    const posCoord = -paverSpan / 2 + i * paverStep;

    // West
    const yw = heightAt(-innerOffset, posCoord);
    dummy.position.set(-innerOffset, yw + 0.04, posCoord);
    dummy.rotation.set(0, Math.PI / 2, 0);
    dummy.updateMatrix();
    instPavers.setMatrixAt(paverIdx++, dummy.matrix);

    // East
    const ye = heightAt(innerOffset, posCoord);
    dummy.position.set(innerOffset, ye + 0.04, posCoord);
    dummy.rotation.set(0, Math.PI / 2, 0);
    dummy.updateMatrix();
    instPavers.setMatrixAt(paverIdx++, dummy.matrix);
  }

  instPavers.instanceMatrix.needsUpdate = true;
  instPavers.computeBoundingSphere();
  zone.add(instPavers);

  // 4. Subtle Bamboo Culm Clusters / Living bamboo shoots in perimeter corners
  const livingBambooMat = new T.MeshStandardMaterial({
    color: 0x5a7a32,
    roughness: 0.6,
    metalness: 0.05
  });
  const bambooLeafMat = new T.MeshStandardMaterial({
    color: 0x486b24,
    roughness: 0.5,
    metalness: 0.02,
    side: T.DoubleSide
  });

  const culmGeo = new T.CylinderGeometry(0.022, 0.026, 3.2, 8);
  culmGeo.translate(0, 1.6, 0);
  const leafClusterGeo = new T.ConeGeometry(0.35, 0.7, 5);

  const cornerGroveLocs = [
    [-9.8, -9.8],
    [9.8, -9.8],
    [-9.8, 9.8],
    [9.8, 9.8]
  ];

  cornerGroveLocs.forEach(([gx, gz]) => {
    for (let b = 0; b < 5; b++) {
      const bx = gx + (rand() - 0.5) * 0.9;
      const bz = gz + (rand() - 0.5) * 0.9;
      const by = heightAt(bx, bz);
      const culm = new T.Mesh(culmGeo, livingBambooMat);
      culm.position.set(bx, by, bz);
      culm.rotation.x = (rand() - 0.5) * 0.08;
      culm.rotation.z = (rand() - 0.5) * 0.08;
      culm.castShadow = true;
      zone.add(culm);

      // Leaves top
      const leaves = new T.Mesh(leafClusterGeo, bambooLeafMat);
      leaves.position.set(bx, by + 3.0, bz);
      leaves.rotation.y = rand() * Math.PI * 2;
      leaves.castShadow = true;
      zone.add(leaves);
    }
  });

  zone.userData.update = (t, dt) => {
    // Static / breeze update hook
  };

  return zone;
}

// Alias for standard zone loader contract
export const build = buildBambooPerimeter;

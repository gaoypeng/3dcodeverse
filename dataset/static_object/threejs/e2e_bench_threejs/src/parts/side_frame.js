// Part: SideFrame  (structural side leg, seat bracket, back support, and armrest casting)
// Cast-iron side frame assembly composed of scrolled front/back legs, an arched seat rail, a backward-slanted backrest support (slanted ~15°), and an integrated scrolled armrest at height 0.62 m. Cross-section is rectangular iron bar (35 mm wide × 20 mm thick) with ornate filigree spirals in the leg junctions.
// Plan bbox (world, meters): centre (0.72, 0.41, 0)  extents (0.05, 0.82, 0.68)
// Material: dark hunter green cast iron, satin metal finish
// Contract: export function buildSideFrame(THREE) -> THREE.Group named 'SideFrame', at WORLD pose.

import * as THREE from 'three';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';

function buildSingleFrameGeometry() {
  const geos = [];
  const MAIN_R = 0.0095;
  const FILIGREE_R = 0.0065;

  // 1. Front Foot Pad & Leg (lowest point touches y = 0.000, z = +0.270)
  const frontFootGeo = new THREE.CylinderGeometry(0.018, 0.022, 0.012, 16);
  frontFootGeo.translate(0, 0.006, 0.270);
  geos.push(frontFootGeo);

  // Front foot decorative scroll
  const frontFootScrollCurve = new THREE.CatmullRomCurve3([
    new THREE.Vector3(0, 0.008, 0.270),
    new THREE.Vector3(0, 0.018, 0.315),
    new THREE.Vector3(0, 0.045, 0.336),
    new THREE.Vector3(0, 0.065, 0.310),
    new THREE.Vector3(0, 0.055, 0.285),
    new THREE.Vector3(0, 0.080, 0.285),
  ], false, 'centripetal');
  geos.push(new THREE.TubeGeometry(frontFootScrollCurve, 16, 0.0065, 8, false));

  // Front Leg (from foot up to front seat rail junction at z = 0.315, y = 0.375)
  const frontLegCurve = new THREE.CatmullRomCurve3([
    new THREE.Vector3(0, 0.008, 0.270),
    new THREE.Vector3(0, 0.090, 0.285),
    new THREE.Vector3(0, 0.190, 0.285),
    new THREE.Vector3(0, 0.290, 0.295),
    new THREE.Vector3(0, 0.375, 0.315),
  ], false, 'centripetal');
  geos.push(new THREE.TubeGeometry(frontLegCurve, 24, MAIN_R, 12, false));

  // 2. Rear Foot Pad & Leg (lowest point touches y = 0.000, z = -0.320)
  const rearFootGeo = new THREE.CylinderGeometry(0.018, 0.022, 0.012, 16);
  rearFootGeo.translate(0, 0.006, -0.320);
  geos.push(rearFootGeo);

  // Rear foot decorative scroll
  const rearFootScrollCurve = new THREE.CatmullRomCurve3([
    new THREE.Vector3(0, 0.008, -0.320),
    new THREE.Vector3(0, 0.020, -0.336),
    new THREE.Vector3(0, 0.050, -0.335),
    new THREE.Vector3(0, 0.065, -0.305),
    new THREE.Vector3(0, 0.050, -0.280),
    new THREE.Vector3(0, 0.080, -0.285),
  ], false, 'centripetal');
  geos.push(new THREE.TubeGeometry(rearFootScrollCurve, 16, 0.0065, 8, false));

  // Rear Leg (connecting to bottom stretcher at y=0.16, z=-0.18, up to corner junction at y=0.350, z=-0.177)
  const rearLegCurve = new THREE.CatmullRomCurve3([
    new THREE.Vector3(0, 0.008, -0.320),
    new THREE.Vector3(0, 0.090, -0.285),
    new THREE.Vector3(0, 0.160, -0.180),
    new THREE.Vector3(0, 0.260, -0.170),
    new THREE.Vector3(0, 0.350, -0.177),
  ], false, 'centripetal');
  geos.push(new THREE.TubeGeometry(rearLegCurve, 24, MAIN_R, 12, false));

  // BottomStretcher hub at y=0.16, z=-0.18
  const stretcherBossGeo = new THREE.CylinderGeometry(0.016, 0.016, 0.026, 16);
  stretcherBossGeo.rotateZ(Math.PI / 2);
  stretcherBossGeo.translate(0, 0.160, -0.180);
  geos.push(stretcherBossGeo);

  // 3. Seat Support Rail (flush contact along underside of seat slats)
  const seatRailCurve = new THREE.CatmullRomCurve3([
    new THREE.Vector3(0, 0.375,  0.315),
    new THREE.Vector3(0, 0.383,  0.259),
    new THREE.Vector3(0, 0.405,  0.188),
    new THREE.Vector3(0, 0.399,  0.115),
    new THREE.Vector3(0, 0.385,  0.041),
    new THREE.Vector3(0, 0.372, -0.033),
    new THREE.Vector3(0, 0.367, -0.106),
    new THREE.Vector3(0, 0.350, -0.177),
  ], false, 'centripetal');
  const seatRailGeo = new THREE.TubeGeometry(seatRailCurve, 28, MAIN_R, 12, false);
  seatRailGeo.scale(1.2, 1.0, 1.0);
  geos.push(seatRailGeo);

  // 4. Backrest Support Stanchion (flush contact along back of backrest slats)
  const backrestCurve = new THREE.CatmullRomCurve3([
    new THREE.Vector3(0, 0.350, -0.177),
    new THREE.Vector3(0, 0.510, -0.166),
    new THREE.Vector3(0, 0.580, -0.196),
    new THREE.Vector3(0, 0.655, -0.236),
    new THREE.Vector3(0, 0.726, -0.276),
    new THREE.Vector3(0, 0.798, -0.313),
  ], false, 'centripetal');
  const backrestGeo = new THREE.TubeGeometry(backrestCurve, 28, MAIN_R, 12, false);
  geos.push(backrestGeo);

  // Top Crest spiral scroll (strictly behind slat 5, reaching y = 0.820)
  const crestScrollCurve = new THREE.CatmullRomCurve3([
    new THREE.Vector3(0, 0.798, -0.313),
    new THREE.Vector3(0, 0.814, -0.313),
    new THREE.Vector3(0, 0.820, -0.322),
    new THREE.Vector3(0, 0.812, -0.338),
    new THREE.Vector3(0, 0.785, -0.335),
    new THREE.Vector3(0, 0.775, -0.320),
    new THREE.Vector3(0, 0.790, -0.313),
  ], false, 'centripetal');
  geos.push(new THREE.TubeGeometry(crestScrollCurve, 20, 0.0065, 8, false));

  // 5. Complete Scrolled Armrest (attaches behind stanchion at z=-0.236, rises up and arches above seat to z=0.315 in front of slat 1)
  const armrestContinuousCurve = new THREE.CatmullRomCurve3([
    new THREE.Vector3(0, 0.610, -0.240),
    new THREE.Vector3(0, 0.635, -0.120),
    new THREE.Vector3(0, 0.630,  0.030),
    new THREE.Vector3(0, 0.620,  0.160),
    new THREE.Vector3(0, 0.605,  0.260),
    new THREE.Vector3(0, 0.585,  0.305),
    new THREE.Vector3(0, 0.555,  0.300),
    new THREE.Vector3(0, 0.465,  0.315),
    new THREE.Vector3(0, 0.375,  0.315),
  ], false, 'centripetal');
  const armrestGeo = new THREE.TubeGeometry(armrestContinuousCurve, 36, MAIN_R, 12, false);
  geos.push(armrestGeo);

  // 6. Victorian Filigree / Scrollwork in leg junctions (welded loops directly between legs)
  const underArchCurve = new THREE.CatmullRomCurve3([
    new THREE.Vector3(0, 0.240,  0.288),
    new THREE.Vector3(0, 0.285,  0.060),
    new THREE.Vector3(0, 0.240, -0.165),
  ], false, 'centripetal');
  geos.push(new THREE.TubeGeometry(underArchCurve, 20, FILIGREE_R, 8, false));

  const filigreeCircle = new THREE.CatmullRomCurve3([
    new THREE.Vector3(0, 0.320,  0.060),
    new THREE.Vector3(0, 0.285,  0.095),
    new THREE.Vector3(0, 0.250,  0.060),
    new THREE.Vector3(0, 0.285,  0.025),
    new THREE.Vector3(0, 0.320,  0.060),
  ], false, 'centripetal');
  geos.push(new THREE.TubeGeometry(filigreeCircle, 20, FILIGREE_R, 8, true));

  const merged = mergeGeometries(geos);
  merged.computeVertexNormals();
  return merged;
}

export function buildSideFrame(THREE_) {
  const group = new THREE.Group();
  group.name = 'SideFrame';

  const baseGeo = buildSingleFrameGeometry();
  const material = new THREE.MeshStandardMaterial({
    color: 0x183424,
    roughness: 0.45,
    metalness: 0.35,
  });

  // Right frame: SideFrame_0 at x = +0.720
  const geo0 = baseGeo.clone();
  geo0.translate(0.720, 0, 0);
  const mesh0 = new THREE.Mesh(geo0, material);
  mesh0.name = 'SideFrame_0';

  // Left frame: SideFrame_1 at x = -0.720
  const geo1 = baseGeo.clone();
  geo1.translate(-0.720, 0, 0);
  const mesh1 = new THREE.Mesh(geo1, material);
  mesh1.name = 'SideFrame_1';

  group.add(mesh0, mesh1);
  return group;
}

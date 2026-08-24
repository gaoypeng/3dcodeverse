// src/assets/water_lily_pad.js — asset "WaterLilyPad": Floating notched circular lily leaf cluster with small pale pink flower bud.
// Size: 0.80 x 0.08 x 0.80 m (w x h x d)
import * as THREE from 'three';

function mulberry32(a) {
  return function() {
    let t = (a += 0x6D2B79F5);
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/**
 * Creates a single notched lily pad leaf mesh with rim lip and central vein details.
 */
function createLilyPadLeaf(T, radius, notchAngleRad, rand, padMat, veinMat, rimMat) {
  const padGroup = new T.Group();

  // Create 2D notched circle profile
  const shape = new T.Shape();
  const segments = 32;
  const halfNotch = notchAngleRad * 0.5;
  const startAngle = halfNotch;
  const endAngle = Math.PI * 2 - halfNotch;

  // Center cleft inset point
  const cleftR = radius * 0.08;
  shape.moveTo(cleftR, 0);

  // Outer circular arc
  const sweep = endAngle - startAngle;
  for (let i = 0; i <= segments; i++) {
    const theta = startAngle + (i / segments) * sweep;
    // Add subtle organic undulating edge variation
    const rVar = radius * (1.0 + 0.025 * Math.sin(theta * 7.0 + rand() * 2.0));
    const px = Math.cos(theta) * rVar;
    const py = Math.sin(theta) * rVar;
    shape.lineTo(px, py);
  }
  // Return to center cleft
  shape.lineTo(cleftR, 0);

  const extrudeSettings = {
    depth: 0.008,
    bevelEnabled: true,
    bevelSegments: 2,
    steps: 1,
    bevelSize: 0.002,
    bevelThickness: 0.002,
  };

  const leafGeo = new T.ExtrudeGeometry(shape, extrudeSettings);
  // Lay flat in XZ plane: rotate around X by -PI/2
  leafGeo.rotateX(-Math.PI / 2);
  leafGeo.center();

  // Gentle upward cupping/dish towards the perimeter
  const pos = leafGeo.attributes.position;
  const v = new T.Vector3();
  for (let i = 0; i < pos.count; i++) {
    v.fromBufferAttribute(pos, i);
    const dist = Math.sqrt(v.x * v.x + v.z * v.z);
    const normDist = dist / radius;
    // Slight convex dish and upturned outer edge
    const lift = Math.pow(normDist, 2.2) * 0.009 - (1.0 - normDist) * 0.002;
    pos.setY(i, v.y + lift);
  }
  leafGeo.computeVertexNormals();

  const leafMesh = new T.Mesh(leafGeo, padMat);
  leafMesh.castShadow = true;
  leafMesh.receiveShadow = true;
  padGroup.add(leafMesh);

  // Raised edge rim (outer lip characteristic of water lilies)
  const rimGeo = new T.TorusGeometry(radius * 0.98, 0.0035, 6, 30, sweep);
  rimGeo.rotateX(-Math.PI / 2);
  rimGeo.rotateY(startAngle);
  const rimMesh = new T.Mesh(rimGeo, rimMat);
  rimMesh.position.y = 0.007;
  rimMesh.castShadow = true;
  padGroup.add(rimMesh);

  // Radiating vein ribs from center cleft
  const numVeins = 6;
  for (let j = 0; j < numVeins; j++) {
    const vAngle = startAngle + 0.2 + (j / (numVeins - 1)) * (sweep - 0.4);
    const veinLen = radius * (0.65 + 0.25 * rand());
    const veinGeo = new T.CylinderGeometry(0.0015, 0.0008, veinLen, 4);
    veinGeo.rotateZ(Math.PI / 2);
    veinGeo.translate(veinLen * 0.5, 0, 0);
    veinGeo.rotateY(-vAngle);

    const veinMesh = new T.Mesh(veinGeo, veinMat);
    veinMesh.position.y = 0.0055;
    padGroup.add(veinMesh);
  }

  // Central button / stem junction
  const hubGeo = new T.CylinderGeometry(0.009, 0.007, 0.006, 8);
  const hubMesh = new T.Mesh(hubGeo, rimMat);
  hubMesh.position.y = 0.005;
  padGroup.add(hubMesh);

  return padGroup;
}

/**
 * Creates a delicate water lily flower bud / semi-opened blossom.
 */
function createFlowerBloom(T, rand) {
  const flower = new T.Group();
  flower.name = 'FlowerBud';

  // Materials
  const petalOuterMat = new T.MeshStandardMaterial({
    color: 0xfce4ec, // Pale blush pink
    roughness: 0.45,
    metalness: 0.05,
    side: T.DoubleSide,
  });

  const petalInnerMat = new T.MeshStandardMaterial({
    color: 0xf8bbd0, // Soft rose pink
    roughness: 0.4,
    metalness: 0.05,
    side: T.DoubleSide,
  });

  const sepalMat = new T.MeshStandardMaterial({
    color: 0x3d6e2e, // Deep olive green sepals
    roughness: 0.7,
    metalness: 0.05,
  });

  const stamenMat = new T.MeshStandardMaterial({
    color: 0xfbc02d, // Golden yellow stamen core
    roughness: 0.35,
    metalness: 0.1,
    emissive: 0x553800,
  });

  // 1. Green outer sepals (4-5 cup-shaped sepals holding the bud base)
  const numSepals = 4;
  for (let i = 0; i < numSepals; i++) {
    const angle = (i / numSepals) * Math.PI * 2 + 0.1;
    const sepalGeo = new T.ConeGeometry(0.016, 0.045, 5);
    sepalGeo.translate(0, 0.022, 0);
    sepalGeo.scale(1.0, 1.0, 0.35); // flattened petal
    const sepalMesh = new T.Mesh(sepalGeo, sepalMat);
    sepalMesh.rotation.y = angle;
    sepalMesh.rotation.x = 0.45; // flared outward slightly
    sepalMesh.castShadow = true;
    flower.add(sepalMesh);
  }

  // 2. Outer petal ring (partially opened, curved outwards)
  const numOuterPetals = 8;
  for (let i = 0; i < numOuterPetals; i++) {
    const angle = (i / numOuterPetals) * Math.PI * 2 + (rand() * 0.1);
    const petalGeo = new T.ConeGeometry(0.015, 0.052, 6);
    petalGeo.translate(0, 0.026, 0);
    petalGeo.scale(1.1, 1.0, 0.28);
    const petalMesh = new T.Mesh(petalGeo, petalOuterMat);
    petalMesh.rotation.y = angle;
    petalMesh.rotation.x = 0.32 + 0.06 * rand();
    petalMesh.position.y = 0.005;
    petalMesh.castShadow = true;
    flower.add(petalMesh);
  }

  // 3. Inner petal ring (more upright, forming the tapered bud / core)
  const numInnerPetals = 7;
  for (let i = 0; i < numInnerPetals; i++) {
    const angle = ((i + 0.5) / numInnerPetals) * Math.PI * 2 + (rand() * 0.1);
    const petalGeo = new T.ConeGeometry(0.012, 0.048, 6);
    petalGeo.translate(0, 0.024, 0);
    petalGeo.scale(1.0, 1.0, 0.25);
    const petalMesh = new T.Mesh(petalGeo, petalInnerMat);
    petalMesh.rotation.y = angle;
    petalMesh.rotation.x = 0.16 + 0.04 * rand();
    petalMesh.position.y = 0.008;
    petalMesh.castShadow = true;
    flower.add(petalMesh);
  }

  // 4. Central golden stamen cluster
  const coreGeo = new T.CylinderGeometry(0.008, 0.006, 0.02, 8);
  const coreMesh = new T.Mesh(coreGeo, stamenMat);
  coreMesh.position.y = 0.018;
  flower.add(coreMesh);

  // Tiny stamen tips
  const numStamens = 10;
  for (let k = 0; k < numStamens; k++) {
    const stamenAngle = (k / numStamens) * Math.PI * 2;
    const stamenTipGeo = new T.SphereGeometry(0.0022, 4, 4);
    const stamenTip = new T.Mesh(stamenTipGeo, stamenMat);
    stamenTip.position.set(
      Math.cos(stamenAngle) * 0.006,
      0.028 + 0.003 * Math.sin(k * 2.0),
      Math.sin(stamenAngle) * 0.006
    );
    flower.add(stamenTip);
  }

  // Submerged short stem stub
  const stemGeo = new T.CylinderGeometry(0.006, 0.005, 0.03, 6);
  const stemMat = new T.MeshStandardMaterial({ color: 0x2e5222, roughness: 0.8 });
  const stemMesh = new T.Mesh(stemGeo, stemMat);
  stemMesh.position.y = -0.012;
  flower.add(stemMesh);

  return flower;
}

export function buildWaterLilyPad(T = THREE, opts = {}) {
  const seed = (opts.seed !== undefined ? opts.seed : 108) + (opts.variant !== undefined ? opts.variant * 733 : 0);
  const rand = mulberry32(seed);

  const group = new T.Group();
  group.name = 'WaterLilyPad';

  const targetW = 0.80;
  const targetH = 0.08;
  const targetD = 0.80;

  // Rich organic lily pad materials with subtle hue variations
  const mainPadMat = new T.MeshStandardMaterial({
    color: 0x336829, // Lush pond green
    roughness: 0.55,
    metalness: 0.08,
  });

  const secondaryPadMat = new T.MeshStandardMaterial({
    color: 0x2d5f26, // Slightly deeper olive green
    roughness: 0.58,
    metalness: 0.08,
  });

  const tertiaryPadMat = new T.MeshStandardMaterial({
    color: 0x3a7530, // Fresh vibrant green
    roughness: 0.52,
    metalness: 0.08,
  });

  const veinMat = new T.MeshStandardMaterial({
    color: 0x488a38, // Lighter leaf vein green
    roughness: 0.65,
    metalness: 0.02,
  });

  const rimMat = new T.MeshStandardMaterial({
    color: 0x417d32, // Pad rim lip
    roughness: 0.6,
    metalness: 0.05,
  });

  const assembly = new T.Group();
  assembly.name = 'LilyClusterAssembly';

  // 1. Primary large lily pad (dominant leaf)
  const pad1 = createLilyPadLeaf(T, 0.28, 0.42, rand, mainPadMat, veinMat, rimMat);
  pad1.name = 'MainLilyPad';
  pad1.position.set(-0.08, 0.008, -0.05);
  pad1.rotation.y = 0.35;
  assembly.add(pad1);

  // 2. Secondary medium lily pad (partially underlapping/overlapping)
  const pad2 = createLilyPadLeaf(T, 0.22, 0.48, rand, secondaryPadMat, veinMat, rimMat);
  pad2.name = 'SecondaryLilyPad';
  pad2.position.set(0.18, 0.004, 0.12);
  pad2.rotation.y = 2.4;
  assembly.add(pad2);

  // 3. Tertiary smaller lily pad
  const pad3 = createLilyPadLeaf(T, 0.16, 0.52, rand, tertiaryPadMat, veinMat, rimMat);
  pad3.name = 'TertiaryLilyPad';
  pad3.position.set(-0.16, 0.002, 0.20);
  pad3.rotation.y = 4.1;
  assembly.add(pad3);

  // 4. Fourth small juvenile pad nestled on the side
  const pad4 = createLilyPadLeaf(T, 0.12, 0.45, rand, secondaryPadMat, veinMat, rimMat);
  pad4.name = 'SmallLilyPad';
  pad4.position.set(0.22, 0.001, -0.16);
  pad4.rotation.y = 1.1;
  assembly.add(pad4);

  // 5. Water lily blossom / bud nestled at the natural junction between pads
  const flower = createFlowerBloom(T, rand);
  flower.position.set(0.04, 0.015, -0.02);
  flower.rotation.y = 0.5;
  flower.rotation.z = -0.08; // slightly tilted natural posture
  assembly.add(flower);

  // 6. Optional small second closed bud just emerging
  const budSmall = new T.Group();
  budSmall.name = 'EmergingBud';
  const budSepalMat = new T.MeshStandardMaterial({ color: 0x386127, roughness: 0.7 });
  const budPetalMat = new T.MeshStandardMaterial({ color: 0xf8bbd0, roughness: 0.45 });
  const bCone1 = new T.Mesh(new T.ConeGeometry(0.01, 0.03, 5), budSepalMat);
  bCone1.rotation.x = 0.2;
  const bCone2 = new T.Mesh(new T.ConeGeometry(0.008, 0.028, 5), budPetalMat);
  bCone2.position.y = 0.004;
  budSmall.add(bCone1, bCone2);
  budSmall.position.set(-0.24, 0.008, -0.06);
  budSmall.rotation.z = 0.25;
  budSmall.rotation.x = 0.15;
  assembly.add(budSmall);

  // Measure initial bounds and scale to exact target dimensions
  const bbox = new T.Box3().setFromObject(assembly);
  const curSize = new T.Vector3();
  bbox.getSize(curSize);

  // Scale assembly precisely to 0.80 x 0.08 x 0.80
  const scaleX = targetW / curSize.x;
  const scaleY = targetH / curSize.y;
  const scaleZ = targetD / curSize.z;
  assembly.scale.set(scaleX, scaleY, scaleZ);

  // Re-measure after scaling and center horizontally, seat base at y = 0
  const finalBox = new T.Box3().setFromObject(assembly);
  const center = new T.Vector3();
  finalBox.getCenter(center);

  assembly.position.x = -center.x;
  assembly.position.y = -finalBox.min.y;
  assembly.position.z = -center.z;

  group.add(assembly);

  group.userData.size = [targetW, targetH, targetD];
  group.userData.tick = (t) => {
    // Gentle floating bob & sway
    assembly.position.y = -finalBox.min.y + Math.sin(t * 1.5 + seed) * 0.002;
    assembly.rotation.y = Math.sin(t * 0.8 + seed) * 0.02;
  };

  return group;
}

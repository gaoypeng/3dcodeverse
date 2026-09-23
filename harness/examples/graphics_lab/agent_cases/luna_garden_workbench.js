import { makeSteam } from '../lib/smoke.js';
import { makeTree } from '../lib/tree.js';
import { sunRig } from '../lib/environment.js';
import { makeSky } from '../lib/sky.js';
import { weatheredWood, paintedWood, cobble, granite, terracotta, soil as soilFinish, foliage, paintedIron } from '../lib/materials.js';

export function createScene({ THREE: T }) {
  const scene = new T.Scene();
  scene.background = new T.Color(0x9bb6bd);
  scene.fog = new T.Fog(0xa9bdb9, 24, 62);
  const rig = sunRig({ mood: 'day', azimuth: 218, elevation: 14, bounds: 20,
    sunColor: 0xffc991, fillSky: 0xa7c2ce, fillGround: 0x827b69, intensity: 2.0 });
  scene.add(rig.sun, rig.sun.target, rig.fill);
  if (rig.sunDisc) scene.add(rig.sunDisc);
  scene.environment = rig.envTex;
  makeSky(scene, { rig, exposure: 0.20, contrast: 1.04, scale: 90 });

  const garden = new T.Group(); garden.name = 'GardenWorkbench'; scene.add(garden);
  const soilMat = soilFinish({ color: 0x554838, seed: 33, scale: 18, contrast: 0.42, bump: 0.025 });
  const gravelMat = cobble({ color: 0x77766c, seed: 17, scale: 22, contrast: 0.38, bump: 0.018 });
  const stoneMats = [
    cobble({ color: 0x77796e, seed: 41, scale: 16, contrast: 0.32, bump: 0.018 }),
    granite({ color: 0x85837a, seed: 62, scale: 18, contrast: 0.36, bump: 0.014 }),
    cobble({ color: 0x686e69, seed: 83, scale: 21, contrast: 0.38, bump: 0.018 }),
  ];
  const mortar = new T.MeshStandardMaterial({ color: 0x403f36, roughness: 1 });
  const wood = weatheredWood({ color: 0x927455, seed: 101, scale: 6, contrast: 0.18, bump: 0.0015 });
  const sunWood = weatheredWood({ color: 0xa0805b, seed: 119, scale: 6, contrast: 0.20, bump: 0.0015 });
  const oldPaint = paintedWood({ color: 0x596153, seed: 129, scale: 6, contrast: 0.15, bump: 0.001 });
  const iron = paintedIron({ color: 0x303b39, seed: 5, scale: 10, contrast: 0.22 });
  const clay = [0x9d6248, 0x80604a, 0xb27652].map((color, i) => terracotta({ color, seed: 212 + i, scale: 10, contrast: 0.34, bump: 0.006 }));
  const leafDark = foliage({ color: 0x435d34, seed: 18, scale: 16, contrast: 0.35 });
  const leafMid = foliage({ color: 0x667546, seed: 22, scale: 14, contrast: 0.34 });
  const leafLight = foliage({ color: 0x89915b, seed: 25, scale: 17, contrast: 0.34 });

  const box = (name, size, position, material, rotation = [0, 0, 0], parent = garden) => {
    const mesh = new T.Mesh(new T.BoxGeometry(...size), material); mesh.name = name;
    mesh.position.set(...position); mesh.rotation.set(...rotation);
    mesh.castShadow = mesh.receiveShadow = true; parent.add(mesh); return mesh;
  };

  // A single, gently uneven garden bed anchors every planted object and the path.
  const earth = new T.Mesh(new T.PlaneGeometry(14, 16, 64, 64), soilMat);
  earth.name = 'GardenSoil'; earth.rotation.x = -Math.PI / 2; earth.position.set(0, -0.008, -0.15);
  earth.receiveShadow = true; garden.add(earth);
  const gravel = new T.Mesh(new T.PlaneGeometry(2.35, 8.4), gravelMat);
  gravel.name = 'LooseGravelWorkPath'; gravel.rotation.x = -Math.PI / 2; gravel.position.set(0.0, 0.003, 2.2);
  gravel.receiveShadow = true; garden.add(gravel);

  // Three distinct retaining walls support successive planted shelves. Small irregular gaps read as dry laid joints.
  const wallZ = [-4.25, -2.75, -1.22], deckY = [0.31, 0.49, 0.67];
  for (let row = 0; row < wallZ.length; row++) {
    const top = deckY[row], front = wallZ[row], bedDepth = 1.25;
    box(`MortarCourse_${row}`, [9.65, 0.025, 0.42], [0, top - 0.29, front], mortar);
    const courses = stoneMats.map(() => []);
    let x = -4.78, i = 0;
    while (x < 4.72) {
      const width = Math.min(0.54 + ((i * 7 + row * 3) % 5) * 0.105, 4.76 - x);
      const h = 0.31 + 0.035 * Math.sin(i * 1.71 + row * 0.9);
      const depth = 0.38 + 0.025 * ((i + row) % 3);
      const materialIndex = (i + row * 2) % stoneMats.length;
      const rotation = [0.008 * Math.sin(i + row), 0.014 * Math.sin(i * 2.3 + row), 0.01 * Math.cos(i * 1.4)];
      courses[materialIndex].push({ size: [width - 0.014, h, depth], position: [x + width / 2, top - h / 2, front], rotation });
      x += width; i++;
    }
    const blockGeometry = new T.BoxGeometry(1, 1, 1);
    for (let materialIndex = 0; materialIndex < courses.length; materialIndex++) {
      const blocks = courses[materialIndex];
      if (!blocks.length) continue;
      const batch = new T.InstancedMesh(blockGeometry, stoneMats[materialIndex], blocks.length);
      batch.name = `DryStoneCourse_${row}_${materialIndex}`;
      const dummy = new T.Object3D();
      for (let j = 0; j < blocks.length; j++) {
        const block = blocks[j]; dummy.position.set(...block.position); dummy.rotation.set(...block.rotation);
        dummy.scale.set(...block.size); dummy.updateMatrix(); batch.setMatrixAt(j, dummy.matrix);
      }
      batch.instanceMatrix.needsUpdate = true; batch.castShadow = batch.receiveShadow = true; garden.add(batch);
    }
    const shelf = new T.Mesh(new T.BoxGeometry(9.45, 0.11, bedDepth), soilMat);
    shelf.name = `RaisedSoilShelf_${row}`; shelf.position.set(0, top - 0.055, front - 0.82);
    shelf.receiveShadow = true; garden.add(shelf);
  }

  // A weather-softened fence is a useful garden boundary and gives the steam a dark, legible backdrop.
  const fenceZ = -1.48, fenceBottom = 0.91;
  for (const x of [-1.55, 0, 1.55]) box('FencePost', [0.085, 1.23, 0.085], [x, fenceBottom + 0.615, fenceZ], wood, [0, 0, (x === 0 ? 0.008 : -0.012)]);
  for (const y of [1.05, 1.73]) box('FenceRail', [3.12, 0.065, 0.07], [0, y, fenceZ + 0.012], oldPaint);
  for (let i = 0; i < 14; i++) {
    const x = -1.43 + i * 0.22;
    const h = 0.83 + 0.10 * Math.sin(i * 1.76) + 0.06 * Math.sin(i * 0.67 + 1);
    const slat = box(`WeatheredPicket_${i}`, [0.126, h, 0.044], [x, fenceBottom + h / 2 + 0.025, fenceZ + 0.065], oldPaint,
      [0, 0.008 * Math.sin(i * 2), 0.012 * Math.sin(i * 1.43)]);
    slat.geometry.translate(0, -0.025, 0);
  }
  // Two climbing stems and instanced paired leaves soften the trellis silhouette.
  const vineGeometry = new T.SphereGeometry(1, 8, 6);
  const vineDark = new T.InstancedMesh(vineGeometry, leafDark, 16); vineDark.name = 'FenceVineLeavesDark';
  const vineMid = new T.InstancedMesh(vineGeometry, leafMid, 8); vineMid.name = 'FenceVineLeavesMid';
  let vineDarkIndex = 0, vineMidIndex = 0; const vineDummy = new T.Object3D();
  for (let vine = 0; vine < 2; vine++) {
    const baseX = vine === 0 ? -0.88 : 0.92;
    const points = Array.from({ length: 7 }, (_, i) => new T.Vector3(
      baseX + 0.095 * Math.sin(i * 0.91 + vine), 0.94 + i * 0.145, fenceZ + 0.112));
    const stem = new T.Mesh(new T.TubeGeometry(new T.CatmullRomCurve3(points), 24, 0.006, 5, false),
      new T.MeshStandardMaterial({ color: 0x53613e, roughness: 0.96 }));
    stem.name = `FenceVineStem_${vine}`; garden.add(stem);
    for (let i = 1; i < 7; i++) for (const side of [-1, 1]) {
      vineDummy.position.set(points[i].x + side * 0.075, points[i].y + side * 0.022, fenceZ + 0.115);
      vineDummy.rotation.set(0, side * 0.45, side * (0.40 + 0.08 * (i % 2)));
      vineDummy.scale.set(0.047 * 0.48, 0.047 * 1.22, 0.047 * 0.23); vineDummy.updateMatrix();
      if (i % 3) vineDark.setMatrixAt(vineDarkIndex++, vineDummy.matrix); else vineMid.setMatrixAt(vineMidIndex++, vineDummy.matrix);
    }
  }
  vineDark.instanceMatrix.needsUpdate = true; vineMid.instanceMatrix.needsUpdate = true; garden.add(vineDark, vineMid);

  const birch = makeTree({ name: 'FramingBirch', species: 'birch', height: 6.1, crownRadius: 2.55,
    seed: 3206, leafDensity: 1.0, leafSize: 0.09, maxLeaves: 16000,
    wind: { dir: [0.92, 0.38], strength: 0.32, speed: 0.7 }, shadows: true });
  birch.position.set(-5.25, 0, -1.45); garden.add(birch);

  // Bench proportions follow an ordinary potting bench: 1.45 m wide, 0.54 m deep, 0.85 m to the top.
  const tableZ = 1.18, topY = 0.85;
  const topBoards = [
    { z: tableZ - 0.17, width: 0.18 }, { z: tableZ + 0.015, width: 0.18 }, { z: tableZ + 0.20, width: 0.16 },
  ];
  topBoards.forEach(({ z, width }, i) => box(`BenchtopPlank_${i}`, [1.45, 0.048, width], [0, topY, z], i === 1 ? sunWood : wood));
  for (const x of [-0.62, 0.62]) {
    for (const z of [tableZ - 0.20, tableZ + 0.20]) box('BenchLeg', [0.058, 0.79, 0.058], [x, 0.435, z], wood);
    box('BenchApron', [0.07, 0.13, 0.43], [x, 0.73, tableZ], oldPaint);
  }
  box('BenchFrontStretcher', [1.16, 0.064, 0.06], [0, 0.34, tableZ + 0.19], wood);
  box('LowerSlattedShelf', [1.27, 0.034, 0.43], [0, 0.40, tableZ], sunWood);
  for (let i = 0; i < 6; i++) {
    const bolt = new T.Mesh(new T.CylinderGeometry(0.008, 0.008, 0.008, 10), iron);
    bolt.name = 'BenchJoineryPin'; bolt.rotation.x = Math.PI / 2;
    bolt.position.set(i < 3 ? -0.62 : 0.62, 0.78 - (i % 3) * 0.04, tableZ + 0.239); garden.add(bolt);
  }
  // Soil smudges and small cut marks make the top feel used rather than newly assembled.
  const workDust = new T.Mesh(new T.CircleGeometry(0.095, 24), soilMat);
  workDust.name = 'PottingSoilSmudge'; workDust.rotation.x = -Math.PI / 2; workDust.scale.set(1.55, 0.56, 1);
  workDust.position.set(-0.38, topY + 0.025, tableZ - 0.12); garden.add(workDust);
  for (let i = 0; i < 4; i++) {
    const scratch = new T.Mesh(new T.BoxGeometry(0.0015, 0.0008, 0.038 + i * 0.006), mortar);
    scratch.name = 'BenchWearMark'; scratch.position.set(-0.2 + i * 0.06, topY + 0.025, tableZ + 0.01); scratch.rotation.y = 0.28; garden.add(scratch);
  }

  // Small hand trowel laid on the bench, scaled to a real 22 cm garden tool.
  const trowel = new T.Group(); trowel.name = 'GardenTrowel'; trowel.position.set(-0.43, topY + 0.026, tableZ + 0.10); trowel.rotation.y = -0.66; garden.add(trowel);
  const handle = new T.Mesh(new T.CapsuleGeometry(0.012, 0.105, 4, 8), wood);
  handle.name = 'TrowelHandle'; handle.rotation.z = Math.PI / 2; handle.position.x = -0.066; handle.castShadow = true; trowel.add(handle);
  const blade = new T.Mesh(new T.SphereGeometry(0.048, 12, 8), paintedIron({ color: 0x78817c, seed: 74, scale: 15, roughness: 0.53, metalness: 0.34 }));
  blade.name = 'TrowelBlade'; blade.scale.set(1.25, 0.16, 0.72); blade.position.x = 0.08; blade.castShadow = true; trowel.add(blade);

  // A handmade mug: about 9 cm across and 9.5 cm tall, with a thin rolled rim and a dark tea surface.
  const mug = new T.Group(); mug.name = 'MorningMug'; mug.position.set(0.28, topY + 0.024, tableZ - 0.08); garden.add(mug);
  const cupProfile = [[0, 0], [0.026, 0], [0.038, 0.006], [0.043, 0.018], [0.046, 0.078],
    [0.045, 0.092], [0.039, 0.092], [0.038, 0.078], [0.034, 0.019], [0, 0.014]];
  const cup = new T.Mesh(new T.LatheGeometry(cupProfile.map(p => new T.Vector2(...p)), 48),
    new T.MeshStandardMaterial({ color: 0xb6b19d, roughness: 0.24, metalness: 0.01 }));
  cup.name = 'CeramicCup'; cup.castShadow = cup.receiveShadow = true; mug.add(cup);
  const lip = new T.Mesh(new T.TorusGeometry(0.0423, 0.0023, 8, 40), new T.MeshStandardMaterial({ color: 0xd2cdbd, roughness: 0.21 }));
  lip.name = 'GlazedRolledRim'; lip.rotation.x = Math.PI / 2; lip.position.y = 0.089; mug.add(lip);
  const cupHandle = new T.Mesh(new T.TorusGeometry(0.027, 0.006, 12, 28, Math.PI * 1.72), cup.material);
  cupHandle.name = 'CupHandle'; cupHandle.rotation.z = Math.PI * 0.82; cupHandle.position.set(0.047, 0.048, 0); mug.add(cupHandle);
  const tea = new T.Mesh(new T.CircleGeometry(0.036, 32), new T.MeshStandardMaterial({ color: 0x39271b, roughness: 0.19 }));
  tea.name = 'TeaSurface'; tea.rotation.x = -Math.PI / 2; tea.position.y = 0.079; mug.add(tea);
  const steam = makeSteam({ name: 'MugSteam', height: 0.38, radius: 0.040, spread: 0.20,
    riseSpeed: 0.16, wind: [0.030, -0.010], density: 13.0, turbulence: 0.50,
    dissipation: 1.1, color: 0xf0f0e7, anisotropy: 0.56, quality: 'high', seed: 52 });
  steam.name = 'MugSteam'; steam.position.set(0.28, topY + 0.024 + 0.094, tableZ - 0.08); garden.add(steam);
  const steamRimLight = new T.PointLight(0xffcc93, 0.8, 0.9, 2);
  steamRimLight.name = 'SteamBacklight'; steamRimLight.intensity = 14.0; steamRimLight.distance = 4.0; steamRimLight.position.set(0.31, 1.43, -0.88); garden.add(steamRimLight);


  // Tapered handmade pots, some planted and some recently emptied. Their spacing follows the actual shelves.
  const addPot = (name, x, y, z, radius, height, material, plantName = null, type = 0) => {
    const group = new T.Group(); group.name = name; group.position.set(x, y, z); garden.add(group);
    const profile = [[radius * 0.58, 0], [radius * 0.77, 0.006], [radius * 0.94, height * 0.83],
      [radius, height], [radius * 0.82, height], [radius * 0.75, height * 0.82], [radius * 0.62, 0.018], [0, 0.015]];
    const vessel = new T.Mesh(new T.LatheGeometry(profile.map(p => new T.Vector2(...p)), 28), material);
    vessel.name = 'ThrownClayPot'; vessel.castShadow = vessel.receiveShadow = true; group.add(vessel);
    const dirt = new T.Mesh(new T.CircleGeometry(radius * 0.74, 24), soilMat);
    dirt.name = 'VisiblePottingSoil'; dirt.rotation.x = -Math.PI / 2; dirt.position.y = height * 0.81; group.add(dirt);
    if (plantName) addHerb(plantName, radius, height, type);
    function addHerb(herbName, potRadius, potHeight, variety) {
      const herbs = new T.Group(); herbs.name = herbName; herbs.position.y = potHeight * 0.78; group.add(herbs);
      const count = variety === 2 ? 9 : 6;
      const stemGeo = new T.CylinderGeometry(0.003, 0.004, 1, 5);
      const stemMat = new T.MeshStandardMaterial({ color: 0x596541, roughness: 0.94 });
      const stems = new T.InstancedMesh(stemGeo, stemMat, count); stems.name = 'HerbStems';
      const leafRadius = variety === 2 ? 0.026 : 0.034;
      const leafGeo = new T.SphereGeometry(1, 7, 5);
      const leafMat = variety === 2 ? leafLight : (variety === 1 ? leafMid : leafDark);
      const leafCount = count * (variety === 2 ? 2 : 1);
      const leaves = new T.InstancedMesh(leafGeo, leafMat, leafCount); leaves.name = 'HerbLeaves';
      const dummy = new T.Object3D(); let leafIndex = 0;
      for (let i = 0; i < count; i++) {
        const angle = i * 2.39996 + variety * 0.57;
        const h = (variety === 1 ? 0.18 : 0.22) + (i % 3) * 0.045;
        dummy.position.set(Math.cos(angle) * potRadius * 0.23, h * 0.49, Math.sin(angle) * potRadius * 0.23);
        dummy.rotation.set(Math.sin(angle) * 0.19, 0, Math.cos(angle) * 0.19); dummy.scale.set(1, h, 1); dummy.updateMatrix(); stems.setMatrixAt(i, dummy.matrix);
        const leafY = h * 0.80, leafX = Math.cos(angle) * potRadius * 0.43, leafZ = Math.sin(angle) * potRadius * 0.43;
        dummy.position.set(leafX, leafY, leafZ); dummy.rotation.set(0, -angle, Math.cos(angle) * 0.36);
        dummy.scale.set(leafRadius * (variety === 2 ? 0.62 : 0.48), leafRadius * (variety === 2 ? 1.5 : 0.8), leafRadius * 0.34);
        dummy.updateMatrix(); leaves.setMatrixAt(leafIndex++, dummy.matrix);
        if (variety === 2) {
          dummy.position.x += Math.cos(angle + 0.35) * 0.025; dummy.position.y -= 0.04; dummy.rotation.y += 1.0;
          dummy.updateMatrix(); leaves.setMatrixAt(leafIndex++, dummy.matrix);
        }
      }
      stems.instanceMatrix.needsUpdate = true; leaves.instanceMatrix.needsUpdate = true;
      herbs.add(stems, leaves);
    }
    return group;
  };
  const shelves = [
    { y: deckY[0], z: -5.05, plants: [[-3.8, 0.22, 0.20, 0], [-1.8, 0.17, 0.19, 1], [1.7, 0.25, 0.23, 2], [3.55, 0.18, 0.19, 0]] },
    { y: deckY[1], z: -3.5, plants: [[-3.4, 0.20, 0.23, 1], [-0.9, 0.26, 0.22, 2], [2.6, 0.18, 0.20, 0]] },
    { y: deckY[2], z: -1.97, plants: [[-3.7, 0.23, 0.20, 0], [-1.55, 0.16, 0.17, 1], [0.55, 0.21, 0.20, 2], [3.45, 0.18, 0.19, 1]] },
  ];
  shelves.forEach((shelf, row) => shelf.plants.forEach(([x, r, h, variety], i) => {
    const z = shelf.z + ((i % 2) ? 0.19 : -0.08);
    addPot(`TerracePot_${row}_${i}`, x, shelf.y, z, r, h, clay[(row + i) % clay.length], `GardenHerb_${row}_${i}`, variety);
  }));
  // A compact rosemary mound sits just behind the mug, giving the low steam a darker, living backdrop.
  const rosemary = new T.Group(); rosemary.name = 'RearTerraceRosemary'; rosemary.position.set(-1.37, deckY[2], -1.98); garden.add(rosemary);
  const rosemaryStem = new T.Mesh(new T.CylinderGeometry(0.014, 0.021, 0.22, 7), new T.MeshStandardMaterial({ color: 0x5a4935, roughness: 0.95 }));
  rosemaryStem.name = 'RosemaryMainStem'; rosemaryStem.position.y = 0.11; rosemary.add(rosemaryStem);
  const rosemaryGeometry = new T.SphereGeometry(1, 8, 6);
  const rosemaryDark = new T.InstancedMesh(rosemaryGeometry, leafDark, 27); rosemaryDark.name = 'RosemaryNeedlesDark';
  const rosemaryLight = new T.InstancedMesh(rosemaryGeometry, leafLight, 7); rosemaryLight.name = 'RosemaryNeedlesLight';
  const rosemaryDummy = new T.Object3D(); let rosemaryDarkIndex = 0, rosemaryLightIndex = 0;
  for (let i = 0; i < 34; i++) {
    const branch = i % 6, side = Math.floor(i / 6), angle = branch * (Math.PI * 2 / 6) + side * 0.31;
    const height = 0.075 + (i % 5) * 0.024, radius = 0.042 + side * 0.018;
    rosemaryDummy.position.set(Math.cos(angle) * radius, height, Math.sin(angle) * radius);
    rosemaryDummy.rotation.set(Math.sin(angle) * 0.58, -angle, Math.cos(angle) * 0.58);
    rosemaryDummy.scale.set(0.042 * 0.26, 0.042 * 1.38, 0.042 * 0.22); rosemaryDummy.updateMatrix();
    if (i % 5 === 0) rosemaryLight.setMatrixAt(rosemaryLightIndex++, rosemaryDummy.matrix);
    else rosemaryDark.setMatrixAt(rosemaryDarkIndex++, rosemaryDummy.matrix);
  }
  rosemaryDark.instanceMatrix.needsUpdate = true; rosemaryLight.instanceMatrix.needsUpdate = true;
  rosemary.add(rosemaryDark, rosemaryLight);
  // Shallow seedling flats and an empty pot make the bench look mid-task; a second small pot sits on the lower shelf.
  const tray = box('SeedlingTray', [0.28, 0.025, 0.18], [-0.48, topY + 0.027, tableZ - 0.13], oldPaint, [0, -0.11, 0]);
  const seedlingPlugs = new T.InstancedMesh(new T.SphereGeometry(0.018, 7, 5), leafMid, 5);
  seedlingPlugs.name = 'SeedlingPlugs'; const seedlingDummy = new T.Object3D();
  for (let i = 0; i < 5; i++) {
    seedlingDummy.position.set(-0.58 + (i % 3) * 0.075, topY + 0.055, tableZ - 0.18 + Math.floor(i / 3) * 0.075);
    seedlingDummy.scale.set(1, 1.7, 1); seedlingDummy.updateMatrix(); seedlingPlugs.setMatrixAt(i, seedlingDummy.matrix);
  }
  seedlingPlugs.instanceMatrix.needsUpdate = true; garden.add(seedlingPlugs);
  addPot('LowerShelfHerbPot', -0.36, 0.417, tableZ, 0.074, 0.082, clay[1], 'SparePottedHerb', 1);
  addPot('EmptyBenchPot', -0.34, topY + 0.024, tableZ + 0.205, 0.052, 0.067, clay[2], null, 0);

  // Irregular local stone treads curve through the gravel toward the workbench.
  const pavers = [
    [-0.76, 5.7, 0.66, 0.44, -0.10], [0.22, 4.95, 0.72, 0.46, 0.05], [-0.32, 4.12, 0.62, 0.43, -0.16],
    [0.57, 3.33, 0.74, 0.48, 0.13], [0.12, 2.54, 0.58, 0.42, -0.08],
  ];
  const treadGeometry = new T.BoxGeometry(1, 1, 1);
  const treadBatches = stoneMats.map((material) => new T.InstancedMesh(treadGeometry, material, 2));
  const treadIndices = [0, 0, 0]; const treadDummy = new T.Object3D();
  pavers.forEach(([x, z, w, d, yaw], i) => {
    const materialIndex = i % 3, batch = treadBatches[materialIndex], index = treadIndices[materialIndex]++;
    treadDummy.position.set(x, 0.03, z); treadDummy.rotation.set(i % 2 ? 0.018 : -0.012, yaw, 0);
    treadDummy.scale.set(w, 0.075, d); treadDummy.updateMatrix(); batch.setMatrixAt(index, treadDummy.matrix);
  });
  treadBatches.forEach((batch, i) => { batch.count = treadIndices[i]; batch.name = `GardenTreads_${i}`; batch.instanceMatrix.needsUpdate = true; batch.castShadow = batch.receiveShadow = true; garden.add(batch); });
  const pebbleGeometry = new T.DodecahedronGeometry(0.045, 0);
  const pebbleBatches = stoneMats.map((material) => new T.InstancedMesh(pebbleGeometry, material, 3));
  for (let i = 0; i < 9; i++) {
    const materialIndex = i % 3, batch = pebbleBatches[materialIndex], index = Math.floor(i / 3);
    treadDummy.position.set(-1.42 + (i % 3) * 0.16, 0.018, 1.2 + i * 0.42);
    treadDummy.rotation.set(0, i * 0.6, 0); treadDummy.scale.set(1.3, 0.54, 0.9); treadDummy.updateMatrix(); batch.setMatrixAt(index, treadDummy.matrix);
  }
  pebbleBatches.forEach((batch, i) => { batch.name = `PathEdgePebbles_${i}`; batch.instanceMatrix.needsUpdate = true; batch.castShadow = batch.receiveShadow = true; garden.add(batch); });

  const animated = [birch, steam];
  return {
    scene,
    cameras: [
      { name: 'WorkbenchGarden', position: [4.6, 2.95, 6.4], lookAt: [0, 1.02, -1.9], fov: 43 },
      { name: 'SteamAndCup', position: [0.82, 1.18, 2.10], lookAt: [0.23, 1.02, 1.03], fov: 38 },
      { name: 'BirchTerrace', position: [-5.8, 2.85, 5.4], lookAt: [-0.8, 1.05, -2.25], fov: 45 },
    ],
    update(t) { for (const item of animated) item.userData.update(t); },
  };
}

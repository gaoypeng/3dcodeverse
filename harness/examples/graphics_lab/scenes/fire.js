/** Candlelit still life + a wood hearth. No image textures or external assets. */
import * as THREE from 'three';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import { makeFire, makeCandle } from '../lib/fire.js';
import { patchStandard } from '../lib/shader.js';
import { mulberry32 } from '../lib/noise.js';

export function createScene({ renderer }) {
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x080b10);
  scene.fog = new THREE.FogExp2(0x080b10, 0.025);
  const pmrem = new THREE.PMREMGenerator(renderer), room = new RoomEnvironment();
  const envTarget = pmrem.fromScene(room, 0.04);
  scene.environment = envTarget.texture; scene.environmentIntensity = 0.12;
  pmrem.dispose(); room.dispose();
  scene.add(new THREE.HemisphereLight(0x607b99, 0x1b120d, 0.35));
  const moon = new THREE.DirectionalLight(0x9bbbd6, 1.4);
  moon.position.set(-3, 4, -1); scene.add(moon);

  const stone = new THREE.MeshStandardMaterial({ color: 0x434443, roughness: 0.96 });
  patchStandard(stone, { name: 'LabFireStone', vertexHead: 'varying vec3 vStone;',
    vertexBody: 'vStone = (modelMatrix * vec4(position, 1.0)).xyz;',
    fragmentHead: 'varying vec3 vStone;', fragmentBody: `
      float n = astraFbm2(vStone.xy * 23.0 + vStone.z * 0.17, 4) * 0.5
        + astraFbm2(vStone.xz * 31.0, 3) * 0.5;
      diffuseColor.rgb *= 0.55 + n * 1.2;
    ` });
  const floor = new THREE.Mesh(new THREE.PlaneGeometry(20, 20), stone);
  floor.name = 'SlateFloor'; floor.rotation.x = -Math.PI / 2; floor.receiveShadow = true; scene.add(floor);
  const wall = new THREE.Mesh(new THREE.BoxGeometry(24, 6, 0.3), stone);
  wall.name = 'StoneWall'; wall.position.set(0, 2, -2.3); scene.add(wall);

  const oak = new THREE.MeshStandardMaterial({ color: 0x654026, roughness: 0.7 });
  patchStandard(oak, { name: 'LabFireOak', vertexHead: 'varying vec3 vOak;',
    vertexBody: 'vOak = (modelMatrix * vec4(position, 1.0)).xyz;',
    fragmentHead: 'varying vec3 vOak;', fragmentBody: `
      float warp = astraNoise2(vOak.xz * vec2(1.8, 9.0)) * 0.007;
      float ring = sin((vOak.z + warp) * 850.0 + astraNoise2(vOak.xz * 14.0) * 5.0);
      float fine = astraNoise2(vOak.xz * vec2(65.0, 1200.0));
      diffuseColor.rgb *= 0.70 + ring * 0.11 + fine * 0.38;
    ` });
  const table = new THREE.Group(); table.name = 'CandleTable';
  for (let i = 0; i < 5; i++) {
    const plank = new THREE.Mesh(new THREE.BoxGeometry(1.18, 0.075, 0.182), oak);
    plank.name = `OakPlank_${i}`; plank.position.set(0.03 * Math.sin(i), 0.77, (i - 2) * 0.185);
    plank.castShadow = true; plank.receiveShadow = true; table.add(plank);
  }
  for (const x of [-0.43, 0.43]) for (const z of [-0.32, 0.32]) {
    const leg = new THREE.Mesh(new THREE.BoxGeometry(0.085, 0.74, 0.085), oak);
    leg.name = 'TableLeg'; leg.position.set(x, 0.37, z); table.add(leg);
  }
  scene.add(table);
  const brass = new THREE.MeshStandardMaterial({ color: 0x9c793a, metalness: 0.87, roughness: 0.32 });
  const actors = [];
  const candleSpecs = [[-0.12, -0.025, 0.075, 0.30, 15], [0.10, 0.08, 0.055, 0.185, 8], [-0.02, -0.20, 0.053, 0.225, 2]];
  for (const [x, z, radius, height, seed] of candleSpecs) {
    const tray = new THREE.Mesh(new THREE.CylinderGeometry(radius * 1.45, radius * 1.38, 0.012, 64), brass);
    tray.name = 'BrassCandleSaucer'; tray.position.set(x, 0.814, z); tray.receiveShadow = true; scene.add(tray);
    const rim = new THREE.Mesh(new THREE.TorusGeometry(radius * 1.40, 0.0045, 8, 64), brass);
    rim.name = 'SaucerRolledEdge'; rim.rotation.x = Math.PI / 2; rim.position.set(x, 0.824, z); scene.add(rim);
    const candle = makeCandle({ radius, height, seed, drips: 10, flameHeight: 0.071,
      color: 0xe6d2a8, lightIntensity: 0.035, quality: 'high' });
    candle.position.set(x, 0.821, z); scene.add(candle); actors.push(candle);
  }
  // A used brass wick trimmer and matches anchor the scale of the still life.
  for (const x of [0.29, 0.34]) {
    const ring = new THREE.Mesh(new THREE.TorusGeometry(0.025, 0.003, 8, 32), brass);
    ring.name = 'WickTrimmerHandle'; ring.rotation.x = Math.PI / 2;
    ring.position.set(x, 0.815, 0.14); scene.add(ring);
    const shaft = new THREE.Mesh(new THREE.BoxGeometry(0.007, 0.006, 0.115), brass);
    shaft.name = 'WickTrimmerArm'; shaft.rotation.y = x < 0.3 ? -0.22 : 0.22;
    shaft.position.set(x + (x < 0.3 ? 0.01 : -0.01), 0.815, 0.055); scene.add(shaft);
  }

  const hearth = new THREE.Group(); hearth.name = 'WoodHearth'; hearth.position.set(-4.0, 0.04, -0.9);
  const random = mulberry32(49);
  for (let i = 0; i < 13; i++) {
    const angle = i / 13 * Math.PI * 2;
    const rock = new THREE.Mesh(new THREE.DodecahedronGeometry(0.15 + random() * 0.045, 1), stone);
    rock.name = `HearthStone_${i}`;
    rock.scale.set(1.25, 0.72, 1); rock.rotation.set(random(), random(), random());
    rock.position.set(Math.cos(angle) * 0.58, 0.075, Math.sin(angle) * 0.58);
    rock.castShadow = true; rock.receiveShadow = true; hearth.add(rock);
  }
  const char = new THREE.MeshStandardMaterial({ color: 0x231710, roughness: 0.97, emissive: 0x160200 });
  patchStandard(char, { name: 'LabCharcoal', vertexHead: 'varying vec3 vChar;',
    vertexBody: 'vChar = position;', fragmentHead: 'varying vec3 vChar;',
    fragmentBody: `
      float grain = astraNoise2(vChar.xy * vec2(80.0, 13.0));
      diffuseColor.rgb *= 0.5 + grain;
    `,
    outputBody: `
      float angle = atan(vChar.z, vChar.x);
      vec2 cell = vec2(angle * 3.9, vChar.y * 24.0);
      cell += vec2(astraNoise2(cell * 0.7), astraNoise2(cell * 0.81 + 12.7)) * 0.5;
      vec2 edge = abs(fract(cell) - 0.5);
      float cracks = smoothstep(0.456, 0.49, max(edge.x, edge.y));
      float live = astraNoise2(vChar.xy * 27.0);
      gl_FragColor.rgb += vec3(0.45, 0.035, 0.001) * cracks * smoothstep(0.33, 0.77, live);
    ` });
  for (let i = 0; i < 7; i++) {
    const log = new THREE.Mesh(new THREE.CylinderGeometry(0.055, 0.08, 0.83, 13, 7), char);
    const lp = log.geometry.attributes.position;
    for(let j=0;j<lp.count;j++) {
      const s = 1 + 0.11*Math.sin(lp.getY(j)*21 + i) + 0.06*Math.cos(lp.getX(j)*87 + lp.getY(j)*45);
      lp.setXYZ(j, lp.getX(j)*s, lp.getY(j), lp.getZ(j)*s);
    }
    log.geometry.computeVertexNormals();
    log.name = `CharredLog_${i}`; log.rotation.set(Math.PI / 2 + (random() - 0.5) * 0.45, 0, i * 1.79);
    log.position.set((random() - 0.5) * 0.22, 0.10 + i * 0.022, (random() - 0.5) * 0.22);
    log.castShadow = true; log.receiveShadow = true; hearth.add(log);
  }
  const coals = new THREE.MeshStandardMaterial({ color: 0x180a04, roughness: 1, emissive: 0xf33102, emissiveIntensity: 0.75 });
  for (let i = 0; i < 25; i++) {
    const coal = new THREE.Mesh(new THREE.DodecahedronGeometry(0.028 + random() * 0.025, 0), coals);
    coal.name = `Coal_${i}`; coal.position.set((random() - 0.5) * 0.65, 0.026, (random() - 0.5) * 0.65);
    coal.scale.y = 0.6; hearth.add(coal);
  }
  const fire = makeFire({ radius: 0.28, height: 0.85, seed: 27, quality: 'high',
    lightIntensity: 2.5, embers: 32, wind: [0.055, 0.02] });
  fire.position.y = 0.15; hearth.add(fire); scene.add(hearth); actors.push(fire);
  const cameras = [
    { name: 'candle_macro', position: [0.49, 1.22, 0.71], lookAt: [-0.01, 0.995, -0.025], fov: 37 },
    { name: 'hearth', position: [-2.78, 1.22, 1.0], lookAt: [-4.0, 0.54, -0.9], fov: 37 },
  ];
  return { scene, cameras, update(t, dt) { actors.forEach((a) => a.userData.update(t, dt)); } };
}

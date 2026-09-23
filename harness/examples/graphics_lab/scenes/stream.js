/** A close study of a clear brook, wet granite, gravel and continuous meadow banks. */
import * as THREE from 'three';
import { makeStream } from '../lib/stream.js';
import { makeRock } from '../lib/rock.js';
import { makeMeadow } from '../lib/meadow.js';
import { sunRig } from '../lib/environment.js';
import { makeSky } from '../lib/sky.js';
import { patchStandard } from '../lib/shader.js';
import { mulberry32 } from '../lib/noise.js';

export const BOUNDS = { min: [-18, -2, -22], max: [18, 5, 22] };
export function heightAt() {
  return 0;
}

export async function createScene() {
  const scene = new THREE.Scene();
  const rig = sunRig({
    mood: 'day',
    azimuth: 145,
    elevation: 39,
    bounds: 23,
    intensity: 4.6,
    fill: 1.25,
    disc: false,
  });
  scene.add(rig.sun, rig.fill);
  scene.environment = rig.envTex;
  makeSky(scene, { rig, turbidity: 3.8, scale: 2500 });
  scene.fog = new THREE.FogExp2(0xb4c7b6, 0.012);
  const obstacles = [
    { u: 0.3, lateral: -0.3, radius: 0.36 },
    { u: 0.48, lateral: 0.4, radius: 0.4 },
    { u: 0.66, lateral: -0.3, radius: 0.35 },
    { u: 0.78, lateral: 0.36, radius: 0.24 },
  ];

  const stream = makeStream({
    points: [
      [-2, 1, -19],
      [0.7, 0.7, -10],
      [-1.2, 0.35, -2],
      [1, 0.12, 7],
      [2, 0, 20],
    ],
    width: 3.8,
    widthVariation: 0.22,
    depth: 0.43,
    speed: 1.25,
    roughness: 0.6,
    segments: 320,
    widthSegments: 36,
    stoneCount: 1200,
    seed: 81,
    waterColor: 0x376b57,
    attenuationDistance: 4.5,
    reflectionSize: 1024,
    bedColor: 0x4f422a,
    obstacles,
  });
  scene.add(stream);
  // The same hydrodynamic obstacles get fractured, wet granite silhouettes in
  // this composition. Their positions and wake parameters remain unchanged.
  obstacles.forEach((obstacle, i) => {
    stream.getObjectByName(`StreamObstacle_${i}`).visible = false;
    const sample = stream.userData.sample(obstacle.u, obstacle.lateral);
    const r = obstacle.radius;
    const rock = makeRock({
      seed: 405 + i,
      type: 'granite',
      size: [r * 1.9, r * 1.1, r * 2.3],
      detail: 5,
      color: 0x5c625c,
      moisture: 0.9,
      moss: 0.08,
      weathering: 1.4,
    });
    rock.position.copy(sample.position);
    rock.position.y -= r * 0.65;
    rock.rotation.y = i * 1.65;
    scene.add(rock);
  });

  // One continuous terrain surface avoids visible outer berms or ribbon seams.
  // Its channel is below the separate stream bed, and its bank grows from the
  // exact authored water boundary rather than intersecting a rectangular slab.
  const samples = Array.from({ length: 241 }, (_, i) => stream.userData.sample(i / 240));
  function nearest(x, z) {
    const index = Math.max(0, Math.min(240, Math.round(((z + 19) / 39) * 240)));
    let best = samples[index],
      distance = Infinity;
    for (let i = Math.max(0, index - 14); i <= Math.min(240, index + 14); i++) {
      const sample = samples[i];
      const d = (x - sample.position.x) ** 2 + (z - sample.position.z) ** 2;
      if (d < distance) {
        best = sample;
        distance = d;
      }
    }
    return { sample: best, offset: Math.sqrt(distance) - best.width * 0.5 };
  }
  function terrainHeight(x, z) {
    const { sample, offset } = nearest(x, z);
    const outside = Math.max(0, offset);
    const bank =
      sample.position.y +
      (1 - Math.exp(-outside * 0.8)) * 0.55 +
      Math.sin(x * 0.71 + z * 0.28) * Math.min(outside * 0.15, 0.15) -
      0.045;
    const blend = THREE.MathUtils.smoothstep(offset, -0.24, 0.06);
    return THREE.MathUtils.lerp(sample.position.y - 0.66, bank, blend);
  }
  const terrainGeometry = new THREE.PlaneGeometry(54, 65, 240, 260);
  terrainGeometry.rotateX(-Math.PI / 2);
  const p = terrainGeometry.attributes.position;
  for (let i = 0; i < p.count; i++) p.setY(i, terrainHeight(p.getX(i), p.getZ(i)));
  terrainGeometry.computeVertexNormals();
  const terrainMaterial = new THREE.MeshStandardMaterial({ color: 0x414629, roughness: 1 });
  patchStandard(terrainMaterial, {
    name: 'MossAndLitter',
    vertexHead: 'varying vec3 bankP;',
    vertexBody: 'bankP=position;',
    fragmentHead: 'varying vec3 bankP;',
    fragmentBody: `
      float moss=astraNoise2(bankP.xz*1.8)*.60+astraNoise2(bankP.xz*6.2)*.27+astraNoise2(bankP.xz*25.0)*.13;
      diffuseColor.rgb*=.42+moss*.75;
      diffuseColor.rgb=mix(diffuseColor.rgb,vec3(.046,.031,.014),smoothstep(.39,.68,moss)*.55);
    `,
  });
  const terrain = new THREE.Mesh(terrainGeometry, terrainMaterial);
  terrain.name = 'ContinuousBrookBanks';
  terrain.receiveShadow = true;
  scene.add(terrain);

  const rng = mulberry32(801);
  for (const side of [-1, 1]) {
    for (let i = 0; i < 42; i++) {
      // Deposits form loose groups with open gravel between them, rather than
      // two evenly spaced rows enclosing a landscaped water channel.
      const cluster = Math.floor(i / 3);
      const u = 0.05 + (cluster / 14) * .9 + (rng() - .5) * .035;
      if (rng() < .22) continue;
      const sample = stream.userData.sample(u, side);
      const across = new THREE.Vector3(sample.tangent.z, 0, -sample.tangent.x)
        .normalize()
        .multiplyScalar(side);
      const size = 0.20 + rng() ** 1.8 * 1.05;
      const rock = makeRock({
        seed: 122 + i + (side + 1) * 30,
        type: 'granite',
        size: [size * 1.2, size * 0.62, size],
        detail: 4,
        color: 0x62615b,
        moisture: 0.8,
        moss: 0.3,
      });
      rock.position.copy(sample.position).addScaledVector(across, -.07 + rng() * .92);
      rock.position.y = terrainHeight(rock.position.x, rock.position.z) - 0.08;
      rock.rotation.y = rng() * Math.PI * 2;
      scene.add(rock);
    }
  }
  const shingleGeometry = new THREE.IcosahedronGeometry(1, 1);
  const shingle = new THREE.InstancedMesh(shingleGeometry,
    new THREE.MeshStandardMaterial({color:0x827b68,roughness:.83}), 1800);
  shingle.name = 'BankShingle';
  shingle.receiveShadow = true;
  const shinglePose = new THREE.Object3D(), shingleColour = new THREE.Color();
  for (let i=0;i<shingle.count;i++) {
    const side = rng()<.5 ? -1 : 1;
    const sample=stream.userData.sample(.02+rng()*.96,side);
    const across=new THREE.Vector3(sample.tangent.z,0,-sample.tangent.x).normalize();
    shinglePose.position.copy(sample.position).addScaledVector(across,side*(.02+rng()**1.9*.9));
    const radius=.013+rng()**2*.067;
    shinglePose.position.y=terrainHeight(shinglePose.position.x,shinglePose.position.z)+radius*.12;
    shinglePose.scale.set(radius*(.8+rng()*.6),radius*(.3+rng()*.25),radius);
    shinglePose.rotation.set((rng()-.5)*.3,rng()*Math.PI*2,(rng()-.5)*.3);
    shinglePose.updateMatrix();shingle.setMatrixAt(i,shinglePose.matrix);
    shingleColour.setHSL(.07+rng()*.055,.06+rng()*.2,.13+rng()*.23);
    shingle.setColorAt(i,shingleColour);
  }
  scene.add(shingle);
  // Two spatially continuous banks keep a high local blade density while
  // respecting the per-field memory bound. Their masks meet at the channel.
  const grasses = [-1, 1].map((side) => {
    const xOffset = side * 4.5;
    const grass = makeMeadow({
      size: [9, 26],
      density: 1400,
      maxBlades: 300000,
      height: 0.26,
      bladeWidth: 0.013,
      seed: 193 + (side + 1) * 3,
      color: 0x405717,
      dry: 0.1,
      ground: false,
      shadows: false,
      heightAt: (x, z) => terrainHeight(x + xOffset, z),
      mask(x, z) {
        x += xOffset;
        const { offset } = nearest(x, z);
        const raggedEdge=.16+.14*Math.sin(z*1.17+x*2.8)+.12*Math.sin(z*3.3-x*1.1);
        const edge = THREE.MathUtils.smoothstep(offset, Math.max(.03,raggedEdge), raggedEdge+.48);
        const thin = .7+.3*Math.sin(x*1.9+z*.7)*Math.sin(z*1.2-x*.4);
        return edge * thin;
      },
      wind: { direction: [0.8, 0.3], strength: 0.5, speed: 0.8 },
    });
    grass.position.x = xOffset;
    scene.add(grass);
    return grass;
  });

  return {
    scene,
    cameras: [
      { name: 'stream', position: [4.4, 4.7, 7.5], lookAt: [0.1, 0.15, 1.4], fov: 43 },
      { name: 'riffles', position: [2.6, 2.5, 5.8], lookAt: [0.3, 0.12, 2], fov: 40 },
    ],
    update(t, dt) {
      stream.userData.update(t, dt);
      grasses.forEach((grass) => grass.userData.update(t, dt));
    },
  };
}

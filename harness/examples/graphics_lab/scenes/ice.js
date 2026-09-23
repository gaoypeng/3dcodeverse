/** Winter thaw: fractured lake ice, trapped air and a snow-covered rocky shore. */
import * as THREE from 'three';
import { makeFracturedIce } from '../lib/ice.js';
import { makeWaterNormals } from '../lib/water.js';
import { makeRock } from '../lib/rock.js';
import { patchSnow } from '../lib/accumulation.js';
import { patchMicroBreakup } from '../lib/surface_wear.js';
import { sunRig } from '../lib/environment.js';
import { makeSky } from '../lib/sky.js';
import { fbm2, mulberry32 } from '../lib/noise.js';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';

const smooth = (a, b, value) => {
  const t = Math.max(0, Math.min(1, (value - a) / (b - a)));
  return t * t * (3 - 2 * t);
};

export function createScene() {
  const scene = new THREE.Scene(), owned = [], rocks = [], rand = mulberry32(314);
  const rig = sunRig({ mood: 'overcast', elevation: 27, azimuth: -128, bounds: 35,
    intensity: 2.2, fill: 1.7, disc: false });
  rig.sun.shadow.mapSize.set(4096, 4096); rig.sun.shadow.normalBias = 0.015;
  scene.add(rig.sun, rig.fill); scene.environment = rig.envTex;
  makeSky(scene, { rig, turbidity: 2.2, rayleigh: 1.4 });
  scene.fog = new THREE.FogExp2(0xcbd7dd, 0.009);
  const floorHeight = (x, z) => {
    const angle=Math.atan2(z+0.3,x);
    const r = Math.hypot(x / 5.6, (z + 0.3) / 4.6)
      + Math.sin(angle*5+.4)*.027 + fbm2(x*.9,z*.9,{seed:119,octaves:2})*.055;
    return -0.69 + smooth(0.82, 1.2, r) * 1.15
      + fbm2(x * 0.45, z * 0.45, { seed: 34, octaves: 3 }) * 0.17
      + smooth(10, 46, -z) * (2.6 + fbm2(x * 0.022, z * 0.034, {seed:18,octaves:4}) * 11);
  };
  const groundGeometry = new THREE.PlaneGeometry(180, 180, 320, 320);
  groundGeometry.rotateX(-Math.PI / 2);
  const p = groundGeometry.attributes.position, colors = [], color = new THREE.Color();
  const mud = new THREE.Color(0x28312e), snow = new THREE.Color(0xbac8d0);
  for (let i = 0; i < p.count; i++) {
    // Spend vertices at the pond contact rather than on distant snow.
    const focus=value=>Math.sign(value)*90*Math.pow(Math.abs(value)/90,1.65);
    const x = focus(p.getX(i)), z = focus(p.getZ(i)), h = floorHeight(x, z);
    p.setX(i,x);p.setZ(i,z);
    p.setY(i, h); color.copy(mud).lerp(snow, smooth(-0.4, 0.14, h));
    colors.push(color.r, color.g, color.b);
  }
  groundGeometry.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
  groundGeometry.computeVertexNormals();
  const groundMaterial = new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.91 });
  patchMicroBreakup(groundMaterial, { scale: 0.11, strength: 0.12, hue: 0.018, seed: 31 });
  const ground = new THREE.Mesh(groundGeometry, groundMaterial);
  ground.name = 'SnowAndLakebed'; ground.receiveShadow = true; scene.add(ground);
  owned.push(groundGeometry, groundMaterial);

  const ice = makeFracturedIce({
    outline: [[-4.7,-1.2],[-3.3,-3.4],[-0.2,-4.0],[2.5,-3.5],[4.4,-1.5],
      [4.6,1.0],[3.1,2.8],[0.5,3.6],[-2.5,3.0],[-4.4,1.3]].map(([x,z])=>[x*1.3,z*1.3]),
    thickness: 0.24, crackDensity: 0.42, gap: 0.009, heave: 0.012,
    frost: 0.26, bubbles: 0.85, chipping: 0.18, seed: 73, attenuationDistance: 1.3,
  });
  scene.add(ice);
  // Water occupies only the open fissures and the narrow exposed perimeter.
  // It sits just below the ice freeboard, while the clear volume retains a
  // view of the lakebed and trapped air through its own transmission pass.
  const rim = Array.from({length:96},(_,i)=>{
    const angle=i/96*Math.PI*2;return [Math.cos(angle)*7,Math.sin(angle)*6-0.3];
  });
  const waterShape = new THREE.Shape(rim.map(([x,z]) => new THREE.Vector2(x,-z)));
  for (const polygon of ice.userData.floeOutlines) {
    waterShape.holes.push(new THREE.Path(polygon.map(([x,z]) => new THREE.Vector2(x,-z))));
  }
  const waterGeometry = new THREE.ShapeGeometry(waterShape);
  waterGeometry.rotateX(-Math.PI/2);
  const waterNormals = makeWaterNormals(256, {broadband:true});
  waterNormals.repeat.set(0.3,0.3);
  const waterMaterial = new THREE.MeshStandardMaterial({color:0x0a2025,roughness:0.19,metalness:0,
    normalMap:waterNormals,normalScale:new THREE.Vector2(0.09,0.09)});
  const water = new THREE.Mesh(waterGeometry, waterMaterial); water.position.y = -0.027;
  water.name = 'WaterBetweenFloes'; scene.add(water); owned.push(waterGeometry,waterMaterial,waterNormals);
  const addRock = (x, z, s, seed, snowy = true) => {
    const rock = makeRock({ type: 'granite', seed, size: [s * 1.3, s * 0.7, s],
      detail: s > 1 ? 7 : 3, color: 0x606664, moisture: 0.65, weathering: 1.0, burial: 0.1 });
    rock.position.set(x, floorHeight(x, z), z); rock.rotation.y = rand() * Math.PI;
    if (snowy) patchSnow(rock.material, { depth: 0.06, color: 0xc0cbd0, seed: seed + 13 });
    scene.add(rock); rocks.push(rock); return rock;
  };
  addRock(-4.6, -1.9, 2.2, 304); addRock(4.1, -2.4, 1.8, 32);
  addRock(-3.2, -3.8, 1.45, 102); addRock(0.4, -4.7, 1.0, 59);
  addRock(3.0, 3.15, 0.8, 930); addRock(-3.3, 2.65, 0.7, 12);
  // The bed remains visible through clear portions, making refraction and
  // trapped air depth observable rather than relying on a blue color alone.
  for (let i = 0; i < 65; i++) {
    const a = rand() * Math.PI * 2, r = Math.sqrt(rand()) * 4;
    const x = Math.cos(a) * r, z = Math.sin(a) * r * 0.78;
    addRock(x, z, 0.06 + rand() * 0.22, 2000 + i, false);
  }
  for (let i = 0; i < 22; i++) {
    const a = rand() * Math.PI * 2, r = 5.4 + rand() * 2.5;
    addRock(Math.cos(a) * r, Math.sin(a) * r * 0.86,
      0.18 + rand() * 0.56, 700 + i);
  }
  // Broken pressure slabs expose a thick edge beside the main sheet.
  const slab = makeFracturedIce({ outline: [[-1.0,-0.5],[-0.1,-0.85],[0.85,-0.42],
    [1.03,0.3],[0.3,0.76],[-0.85,0.48]], thickness: 0.23, crackDensity: 0,
    gap: 0, frost: 0.42, bubbles: 0.8, chipping: 1, seed: 49, attenuationDistance: 0.85 });
  slab.position.set(-2.0, 0.16, 2.8); slab.rotation.set(-0.06, 0.42, -0.15);
  scene.add(slab);
  // A lower floe makes the pressure ridge's support visible. Seat the
  // raised slab on it using actual transformed vertices and local height.
  const support=makeFracturedIce({outline:[[-.9,-.55],[.35,-.8],[.95,-.05],
    [.6,.62],[-.55,.72]],thickness:.16,crackDensity:0,gap:0,
    frost:.82,bubbles:.35,chipping:.7,seed:59});
  support.position.set(-2.7,.055,3.12);scene.add(support);
  slab.updateMatrixWorld(true);
  const sp=slab.getObjectByName('IceVolume').geometry.attributes.position;
  const contact=new THREE.Vector3();let lift=-Infinity;
  for(let i=0;i<sp.count;i++){
    contact.fromBufferAttribute(sp,i).applyMatrix4(slab.matrixWorld);
    const height=support.userData.sampleHeight(contact.x-support.position.x,contact.z-support.position.z);
    if(height!==null)lift=Math.max(lift,height+support.position.y-contact.y);
    const sheet=ice.userData.sampleHeight(contact.x,contact.z);
    if(sheet!==null)lift=Math.max(lift,sheet-contact.y);
  }
  if(Number.isFinite(lift))slab.position.y+=lift+.001;

  // Sparse dead reeds break the shoreline silhouette without obscuring ice.
  const reedParts = [];
  for (let i = 0; i < 65; i++) {
    const x = -5.0 + rand() * 1.2, z = -2 + rand() * 5;
    const y = floorHeight(x, z), h = 0.36 + rand() * 0.62;
    const curve = new THREE.QuadraticBezierCurve3(new THREE.Vector3(x,y,z),
      new THREE.Vector3(x + 0.04,y+h*0.55,z), new THREE.Vector3(x+0.12,y+h,z+0.04));
    reedParts.push(new THREE.TubeGeometry(curve, 5, 0.004, 3, false));
  }
  const reedGeometry = mergeGeometries(reedParts); reedParts.forEach(g => g.dispose());
  const reedMaterial = new THREE.MeshStandardMaterial({ color: 0x6a5740, roughness: 0.85 });
  const reeds = new THREE.Mesh(reedGeometry, reedMaterial); reeds.name = 'WinterReeds';
  reeds.castShadow = true; scene.add(reeds); owned.push(reedGeometry, reedMaterial);

  // A distant broken ridge provides reflected shape and a winter horizon.
  for (let i = 0; i < 9; i++) {
    const rock = makeRock({ type: 'granite', seed: 803 + i,
      size: [8 + rand()*6, 3 + rand()*3, 8], detail: 4, color: 0x7c8588 });
    const x=-65+i*16,z=-63-rand()*16;
    rock.position.set(x,floorHeight(x,z)-1.8,z);
    patchSnow(rock.material, { depth: 0.1, color: 0xc6d2d8, seed: i + 81 });
    scene.add(rock); rocks.push(rock);
  }
  scene.userData.grade = { exposure: 0.94, contrast: 1.04, saturation: 0.92 };
  return { scene, cameras: [
    { name: 'frozen_shore', position: [6.9, 3.2, 7.7], lookAt: [-0.3, 0.1, -0.5], fov: 48 },
    { name: 'clear_ice', position: [1.8, 1.1, 3.2], lookAt: [0.15, -0.07, 0.65], fov: 48 },
    { name: 'fracture_edge', position: [-0.2, 0.64, 5.0], lookAt: [-1.9, 0.08, 2.6], fov: 42 },
  ], update(t, dt) { ice.userData.update(t, dt); slab.userData.update(t, dt);support.userData.update(t,dt);
    waterNormals.offset.set(t*0.008,t*0.004); },
  dispose() { ice.userData.dispose(); slab.userData.dispose();support.userData.dispose();rocks.forEach(r => r.userData.dispose());
    owned.forEach(r => r.dispose()); rig.envTex.dispose(); } };
}

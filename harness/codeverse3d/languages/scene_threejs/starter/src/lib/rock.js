/** Closed fractured stones with mineral-scale shading, erosion and a seated foot.
 * The source form is an intersection of fracture planes, not a stretched sphere.
 * Shared-edge displacement keeps the surface watertight after tessellation.
 */
import * as THREE from 'three';
import { ConvexGeometry } from 'three/addons/geometries/ConvexGeometry.js';
import { fbm3, mulberry32 } from './noise.js';
import { patchStandard } from './shader.js';

const TYPES = {
  sandstone: { color: 0x9e7250, erosion: 0.028, roughness: 0.91, index: 0 },
  granite: { color: 0x777970, erosion: 0.040, roughness: 0.79, index: 1 },
  basalt: { color: 0x535651, erosion: 0.020, roughness: 0.86, index: 2 },
};
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const key = (p) => p.toArray().map((x) => Math.round(x * 1e6)).join(',');

function fracturePlanes(rand, type) {
  // A broken block has several unrelated joint sets. Evenly spaced upright
  // planes make every seed a column, so distribute fractures in solid angle.
  const planes = [{ n: new THREE.Vector3(0, -1, 0), d: 0.56 }];
  const rotation = rand() * Math.PI * 2, count = type === 'basalt' ? 15 : 19;
  for (let i = 0; i < count; i++) {
    const y = 1 - 2 * (i + 0.5) / count, r = Math.sqrt(1 - y * y);
    const a = rotation + i * 2.39996323 + (rand() - 0.5) * 0.4;
    const n = new THREE.Vector3(Math.cos(a) * r, y + (rand() - 0.5) * 0.26, Math.sin(a) * r).normalize();
    planes.push({ n, d: 0.61 + rand() * 0.32 });
  }
  // Bedding is a broad tilted break, not an axis-aligned lid.
  planes.push({ n: new THREE.Vector3((rand() - 0.5) * 0.7, 1, (rand() - 0.5) * 0.7).normalize(), d: 0.62 + rand() * 0.2 });
  return planes;
}

function planeHull(planes) {
  const points = [], seen = new Set();
  for (let i = 0; i < planes.length; i++) for (let j = i + 1; j < planes.length; j++) for (let k = j + 1; k < planes.length; k++) {
    const a = planes[i], b = planes[j], c = planes[k];
    const bc = new THREE.Vector3().crossVectors(b.n, c.n), det = a.n.dot(bc);
    if (Math.abs(det) < 1e-7) continue;
    const p = bc.multiplyScalar(a.d)
      .addScaledVector(new THREE.Vector3().crossVectors(c.n, a.n), b.d)
      .addScaledVector(new THREE.Vector3().crossVectors(a.n, b.n), c.d).divideScalar(det);
    if (!planes.every((plane) => plane.n.dot(p) <= plane.d + 1e-6)) continue;
    const id = key(p);
    if (!seen.has(id)) { seen.add(id); points.push(p); }
  }
  return new ConvexGeometry(points);
}

function rockGeometry(opts, type, size, rand) {
  const segments = clamp(Math.round(opts.detail ?? 6), 1, 12) * 2;
  // Keep the exact plane intersections at every detail level. Projecting a
  // sampled sphere misses the corners and makes small rocks look like pillows.
  const planes = fracturePlanes(rand, type), firstHull = planeHull(planes);
  const corners = [], cornerKeys = new Set();
  for (let i = 0; i < firstHull.attributes.position.count; i++) {
    const p = new THREE.Vector3().fromBufferAttribute(firstHull.attributes.position, i), id = key(p);
    if (!cornerKeys.has(id)) { cornerKeys.add(id); corners.push(p); }
  }
  // Smaller secondary breaks chip selected corners of the main joint block.
  // Their true bevel faces preserve a usable silhouette even at detail 1.
  for (let i = 0; i < 9; i++) {
    const p = corners[Math.floor(rand() * corners.length)];
    const n = p.clone().normalize().add(new THREE.Vector3(rand() - 0.5, rand() - 0.5, rand() - 0.5).multiplyScalar(0.3)).normalize();
    planes.push({ n, d: Math.max(0.38, n.dot(p) - 0.045 - rand() * 0.095) });
  }
  firstHull.dispose();
  const hull = planeHull(planes);
  const src = hull.attributes.position;
  const vertices = [], cavities = [], normalGroups = [], welded = new Map();
  const seed = opts.seed ?? 31, erosion = TYPES[type].erosion * clamp(opts.weathering ?? 1, 0, 2);
  const chips = [];
  for (let i = 0; i < 15; i++) {
    const a = rand() * Math.PI * 2, y = rand() * 1.5 - 0.5;
    const dir = new THREE.Vector3(Math.cos(a), y, Math.sin(a)).normalize();
    const u = new THREE.Vector3(-dir.z, 0, dir.x).normalize().applyAxisAngle(dir, rand() * Math.PI);
    const v = new THREE.Vector3().crossVectors(dir, u);
    chips.push({ dir, u, v, width: 0.13 + rand() * (type === 'sandstone' ? 0.28 : 0.17),
      aspect: 0.18 + rand() * (type === 'sandstone' ? 0.52 : 0.27),
      depth: 0.015 + rand() * (type === 'sandstone' ? 0.09 : 0.05) });
  }
  const beds = Array.from({ length: 3 }, () => ({
    height: -0.31 + rand() * 0.85,
    width: 0.024 + rand() * 0.025, depth: 0.025 + rand() * 0.04,
  }));
  const deform = (p) => {
    const id = key(p);
    if (welded.has(id)) return welded.get(id);
    let radius = p.length();
    const dir = p.clone().divideScalar(radius);
    const baseFade = THREE.MathUtils.smoothstep(p.y, -0.56, -0.4);
    let cavity = 0;
    for (const chip of chips) {
      if (dir.dot(chip.dir) < 0.6) continue;
      // Straight cleavage lips with sloped floors. A smooth radial falloff
      // creates circular craters, which are inappropriate for broken granite.
      const u = dir.dot(chip.u) / chip.width;
      const v = dir.dot(chip.v) / (chip.width * chip.aspect);
      const footprint = Math.max(Math.abs(u), Math.abs(v));
      const cut = clamp((1 - footprint) * 2.8, 0, 1);
      cavity += cut * chip.depth;
    }
    const coarse = fbm3(p.x * 3.8, p.y * 3.8, p.z * 3.8, { seed, octaves: 3 });
    const grain = fbm3(p.x * 16, p.y * 16, p.z * 16, { seed: seed + 87, octaves: 2 });
    let relief = (coarse * 0.48 + grain * 0.11) * erosion, bedInset = 0;
    const distances = planes.map((plane) => Math.abs(plane.n.dot(p) - plane.d)).sort((a, b) => a - b);
    const edgeWear = 1 - THREE.MathUtils.smoothstep(distances[1], 0, 0.12);
    relief += grain * erosion * edgeWear * 0.85;
    if (type === 'sandstone') {
      const bed = p.y + p.x * 0.18 - p.z * 0.11;
      const weakBed = fbm3(p.x * 0.65, bed * 9, p.z * 0.65, { seed: seed + 12, octaves: 2 });
      relief -= Math.max(0, weakBed + 0.08) * erosion * 0.85;
      // A few weak bedding planes form actual recessed ledges. The joint
      // spacing and depth vary per seed; this is not a periodic stripe shader.
      const warp = fbm3(p.x * 2, p.y * 0.4, p.z * 2, { seed: seed + 36, octaves: 2 }) * 0.025;
      for (const joint of beds) {
        const inset = clamp(1 - Math.abs(bed + warp - joint.height) / joint.width, 0, 1);
        bedInset += inset * joint.depth * clamp(opts.weathering ?? 1, 0, 2) * (1 - Math.abs(dir.y) * 0.65);
      }
    }
    radius += (relief - Math.min(0.28, cavity) * clamp(opts.weathering ?? 1, 0, 2)) * baseFade;
    p.copy(dir).multiplyScalar(radius);
    p.x *= 1 - bedInset * baseFade; p.z *= 1 - bedInset * baseFade;
    const result = { p, cavity: clamp(cavity * 5 * baseFade, 0, 1), id };
    welded.set(id, result);
    return result;
  };
  const a = new THREE.Vector3(), b = new THREE.Vector3(), c = new THREE.Vector3();
  for (let f = 0; f < src.count; f += 3) {
    a.fromBufferAttribute(src, f); b.fromBufferAttribute(src, f + 1); c.fromBufferAttribute(src, f + 2);
    const center = a.clone().add(b).add(c).divideScalar(3);
    let group = 0, error = Infinity;
    planes.forEach((plane, i) => {
      const distance = Math.abs(plane.n.dot(center) - plane.d);
      if (distance < error) { error = distance; group = i; }
    });
    const at = (i, j) => a.clone().multiplyScalar(1 - (i + j) / segments)
      .addScaledVector(b, i / segments).addScaledVector(c, j / segments);
    const emit = (...points) => points.forEach((p) => {
      const sample = deform(p);
      vertices.push(sample.p.x, sample.p.y, sample.p.z); cavities.push(sample.cavity);
      normalGroups.push(`${group}:${sample.id}`);
    });
    for (let i = 0; i < segments; i++) for (let j = 0; j < segments - i; j++) {
      emit(at(i, j), at(i + 1, j), at(i, j + 1));
      if (i + j + 1 < segments) emit(at(i + 1, j), at(i + 1, j + 1), at(i, j + 1));
    }
  }
  hull.dispose();
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(vertices, 3));
  geo.setAttribute('rockCavity', new THREE.Float32BufferAttribute(cavities, 1));
  geo.computeBoundingBox();
  const bb = geo.boundingBox, span = bb.getSize(new THREE.Vector3()), center = bb.getCenter(new THREE.Vector3());
  const p = geo.attributes.position;
  const burial = clamp(opts.burial ?? 0.06, 0, 0.5);
  for (let i = 0; i < p.count; i++) p.setXYZ(i,
    (p.getX(i) - center.x) / span.x * size[0],
    (p.getY(i) - bb.min.y) / span.y * size[1] - burial * size[1],
    (p.getZ(i) - center.z) / span.z * size[2]);
  // Smooth within each original fracture plane, retaining its sharp boundary.
  // A per-triangle crease threshold produces sawtooth seams through a cavity;
  // these groups depend on geological planes rather than tessellation angles.
  const accumulated = new Map(), shared = new Map(), ab = new THREE.Vector3(), ac = new THREE.Vector3();
  for (let i = 0; i < p.count; i += 3) {
    a.fromBufferAttribute(p, i); b.fromBufferAttribute(p, i + 1); c.fromBufferAttribute(p, i + 2);
    const n = ab.copy(b).sub(a).cross(ac.copy(c).sub(a));
    for (let j = 0; j < 3; j++) {
      const id = normalGroups[i + j];
      if (!accumulated.has(id)) accumulated.set(id, new THREE.Vector3());
      accumulated.get(id).add(n);
      const vertex = id.slice(id.indexOf(':') + 1);
      if (!shared.has(vertex)) shared.set(vertex, new THREE.Vector3());
      shared.get(vertex).add(n);
    }
  }
  const normals = new Float32Array(p.count * 3);
  const edgeSoftness = clamp(opts.weathering ?? 1, 0, 2) * 0.24;
  for (let i = 0; i < p.count; i++) {
    const id = normalGroups[i], vertex = id.slice(id.indexOf(':') + 1);
    accumulated.get(id).clone().normalize().lerp(shared.get(vertex).clone().normalize(), edgeSoftness)
      .normalize().toArray(normals, i * 3);
  }
  geo.setAttribute('normal', new THREE.BufferAttribute(normals, 3));
  geo.computeBoundingBox(); geo.computeBoundingSphere();
  return geo;
}

function rockMaterial(opts, type, height) {
  const def = TYPES[type];
  const mat = new THREE.MeshStandardMaterial({ color: opts.color ?? def.color, roughness: def.roughness });
  mat.name = `${type}Stone`;
  patchStandard(mat, {
    name: 'fracturedRockV2',
    uniforms: {
      uRockKind: { value: def.index }, uRockSeed: { value: (opts.seed ?? 31) % 997 },
      uRockHeight: { value: height }, uRockMoisture: { value: clamp(opts.moisture ?? 0, 0, 1) },
      uRockMoss: { value: clamp(opts.moss ?? 0, 0, 1) },
    },
    vertexHead: 'attribute float rockCavity; varying vec3 vRockLocal; varying vec3 vRockLocalNormal; varying float vRockCavity;',
    vertexBody: 'vRockLocal = position; vRockLocalNormal = normal; vRockCavity = rockCavity;',
    fragmentHead: `
      varying vec3 vRockLocal, vRockLocalNormal;
      varying float vRockCavity;
      uniform float uRockKind, uRockSeed, uRockHeight, uRockMoisture, uRockMoss;
      float rockHash(vec3 p) {
        p = fract(p * 0.1031);
        p += dot(p, p.yzx + 33.33);
        return fract((p.x + p.y) * p.z);
      }
      // Value and analytic spatial gradient. The gradient is filtered before
      // normal perturbation; differentiating sampled noise makes visible
      // 2x2 pixel blocks at grazing angles and when the camera approaches.
      vec4 rockNoiseGradient(vec3 p) {
        vec3 i = floor(p), f = fract(p);
        vec3 u = f*f*f*(f*(f*6.0-15.0)+10.0);
        vec3 du = 30.0*f*f*(f*(f-2.0)+1.0);
        float a=rockHash(i), b=rockHash(i+vec3(1,0,0));
        float c=rockHash(i+vec3(0,1,0)), d=rockHash(i+vec3(1,1,0));
        float e=rockHash(i+vec3(0,0,1)), f1=rockHash(i+vec3(1,0,1));
        float g=rockHash(i+vec3(0,1,1)), h=rockHash(i+vec3(1,1,1));
        float x0=mix(a,b,u.x), x1=mix(c,d,u.x), x2=mix(e,f1,u.x), x3=mix(g,h,u.x);
        float y0=mix(x0,x1,u.y), y1=mix(x2,x3,u.y);
        return vec4(mix(y0,y1,u.z),
          mix(mix(b-a,d-c,u.y),mix(f1-e,h-g,u.y),u.z)*du.x,
          mix(x1-x0,x3-x2,u.z)*du.y,(y1-y0)*du.z);
      }
      float rockNoise(vec3 p) { return rockNoiseGradient(p).x; }
    `,
    fragmentBody: `
      vec3 rockP = vRockLocal + vec3(uRockSeed * 0.17, 0.0, uRockSeed * 0.31);
      float rockMacro = rockNoise(rockP * 1.4);
      vec4 rockMesoscale = rockNoiseGradient(rockP * 17.0);
      float rockMedium = rockMesoscale.x;
      float rockFootprint = max(length(dFdx(rockP)), length(dFdy(rockP)));
      float rockFineFade = 1.0 - smoothstep(0.001, 0.009, rockFootprint);
      vec4 rockMicro = rockNoiseGradient(rockP * 220.0);
      float rockFine = mix(0.5, rockMicro.x, rockFineFade);
      float rockBumpFade = 1.0 - smoothstep(0.014, 0.055, rockFootprint);
      vec3 rockGradient = rockMesoscale.yzw * (17.0 * 0.0024 * rockBumpFade)
        + rockMicro.yzw * (220.0 * 0.00035 * rockFineFade);
      float rockBase = 1.0 - smoothstep(-uRockHeight * 0.04, uRockHeight * 0.52, vRockLocal.y);
      float rockWet = uRockMoisture * clamp(0.16 + 0.77 * rockBase + vRockCavity * 0.16, 0.0, 1.0);
      // Large alteration patches and crevice deposits are separate from
      // mineral grains. Their physical scales survive changes in view range.
      diffuseColor.rgb *= 0.68 + 0.48 * rockMacro;
      diffuseColor.rgb *= 1.0 - vRockCavity * 0.17;
      if (uRockKind < 0.5) {
        vec3 rockBedDirection = vec3(0.18, 1.0, -0.11);
        float rockBedCoord = dot(vRockLocal, rockBedDirection) * 4.8
          + rockNoise(rockP * 0.6) * 0.75;
        float rockBedFade = 1.0 - smoothstep(0.12, 0.48, fwidth(rockBedCoord));
        vec4 rockBed = rockNoiseGradient(vec3(rockP.x * 0.65, rockBedCoord, rockP.z * 0.65));
        float rockIron = smoothstep(0.47, 0.75, rockBed.x) * rockBedFade;
        diffuseColor.rgb *= mix(vec3(1.05, 1.02, 0.96), vec3(0.87, 0.76, 0.65), rockIron * 0.56);
        diffuseColor.rgb *= 0.96 + (rockMedium - 0.5) * 0.12 + (rockFine - 0.5) * 0.16;
        rockGradient += (rockBed.yzw * vec3(0.65, 0.0, 0.65)
          + rockBed.z * rockBedDirection * 4.8) * (0.004 * rockBedFade);
      } else if (uRockKind < 1.5) {
        vec4 rockCrystal = rockNoiseGradient(rockP * 92.0);
        float rockCrystalFade = 1.0 - smoothstep(0.002, 0.013, rockFootprint);
        float rockQuartz = smoothstep(0.57, 0.8, rockCrystal.x) * rockCrystalFade;
        float rockMica = (1.0 - smoothstep(0.19, 0.4, rockCrystal.x)) * rockCrystalFade;
        diffuseColor.rgb *= 0.95 + rockQuartz * 0.17 - rockMica * 0.19;
        float rockOxide = smoothstep(0.42, 0.7, rockNoise(rockP * 2.1));
        diffuseColor.rgb *= mix(vec3(0.88, 0.96, 1.0), vec3(1.15, 0.93, 0.71), rockOxide * 0.72);
        rockGradient += rockCrystal.yzw * (92.0 * 0.00055 * rockCrystalFade);
      } else {
        float rockPores = 1.0 - smoothstep(0.1, 0.34, rockFine);
        diffuseColor.rgb *= 0.88 + rockMedium * 0.16 - rockPores * 0.19;
        float rockOxide = smoothstep(0.53, 0.78, rockNoise(rockP * 3.8));
        diffuseColor.rgb *= mix(vec3(0.95, 0.98, 1.0), vec3(1.16, 0.95, 0.77), rockOxide * 0.42);
      }
      float rockShelter = clamp(0.36 + normalize(vRockLocalNormal).y * 0.5 + rockBase * 0.2 + vRockCavity * 0.4, 0.0, 1.0);
      float rockMoss = smoothstep(0.46, 0.67, rockNoise(rockP * 3.5) + (rockMedium - 0.5) * 0.1)
        * rockShelter * uRockMoss;
      diffuseColor.rgb = mix(diffuseColor.rgb, vec3(0.058, 0.086, 0.027) * (0.72 + rockMedium * 0.5), rockMoss);
      diffuseColor.rgb *= 1.0 - rockWet * 0.36;
    `,
    roughnessBody: 'roughnessFactor = clamp(roughnessFactor + (rockMedium - 0.5) * 0.16 - rockWet * 0.48, 0.24, 0.98);',
    normalBody: `
      vec3 rockDx = dFdx(-vViewPosition), rockDy = dFdy(-vViewPosition);
      vec3 rockR1 = cross(rockDy, normal), rockR2 = cross(normal, rockDx);
      float rockDet = dot(rockDx, rockR1);
      normal = normalize(abs(rockDet) * normal - sign(rockDet) *
        (dot(rockGradient, dFdx(vRockLocal)) * rockR1 + dot(rockGradient, dFdy(vRockLocal)) * rockR2));
    `,
  });
  return mat;
}

/** A closed Mesh. type sandstone|granite|basalt; seed; size metres or [x,y,z];
 * detail 1..12 surface resolution; weathering 0..2; moisture/moss 0..1;
 * burial fraction (default .06). Default origin is just above the buried foot.
 * Shading follows local coordinates after parenting, rotation and scaling.
 * userData.update(t,dt) and userData.dispose() match the animated effects.
 */
export function makeRock(opts = {}) {
  const type = opts.type ?? 'granite';
  if (!TYPES[type]) throw new RangeError('rock type must be sandstone, granite or basalt');
  const s = opts.size ?? 1.8;
  const size = Array.isArray(s) ? s : [s * 1.2, s * 0.82, s];
  if (size.length !== 3 || !size.every((v) => Number.isFinite(v) && v > 0)) throw new RangeError('rock size must be positive metres');
  const geometry = rockGeometry(opts, type, size, mulberry32(opts.seed ?? 31));
  const material = rockMaterial(opts, type, size[1]);
  const rock = new THREE.Mesh(geometry, material);
  rock.name = opts.name ?? `${type[0].toUpperCase() + type.slice(1)}Rock`;
  rock.castShadow = true; rock.receiveShadow = true;
  rock.userData.update = () => {};
  let disposed = false;
  rock.userData.dispose = () => {
    if (disposed) return;
    disposed = true;
    geometry.dispose(); material.dispose();
  };
  rock.userData.seed = opts.seed ?? 31;
  rock.userData.rockType = type;
  return rock;
}

/** Seeded scatter; heightAt(x,z) is local to the returned group. Each rock owns
 * its geometry and material; cap count for close foreground dressing, use
 * instancing.js for distant repetitions of a few shared prototypes.
 */
export function makeRockField(opts = {}) {
  const rand = mulberry32(opts.seed ?? 31), group = new THREE.Group();
  const owned = [];
  group.name = opts.name ?? 'RockField';
  const count = clamp(Math.round(opts.count ?? 24), 0, 160), radius = Math.max(0.1, opts.radius ?? 9);
  for (let i = 0; i < count; i++) {
    const a = rand() * Math.PI * 2, r = Math.sqrt(rand()) * radius;
    const x = Math.cos(a) * r, z = Math.sin(a) * r;
    const seed = Math.floor(rand() * 100000), factor = 0.4 + rand() * 0.9;
    const size = Array.isArray(opts.size) ? opts.size.map((v) => v * factor) : (opts.size ?? 0.6) * factor;
    const rock = makeRock({ ...opts, seed, size, detail: opts.detail ?? 3, name: `${group.name}_${i + 1}` });
    rock.position.set(x, opts.heightAt?.(x, z) ?? 0, z); rock.rotation.y = rand() * Math.PI * 2;
    group.add(rock); owned.push(rock);
  }
  group.userData.update = () => {};
  let disposed = false;
  group.userData.dispose = () => {
    if (disposed) return;
    disposed = true;
    owned.forEach((rock) => rock.userData.dispose());
  };
  return group;
}

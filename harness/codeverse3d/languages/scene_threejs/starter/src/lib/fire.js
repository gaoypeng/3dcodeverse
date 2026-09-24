/**
 * View-independent fire and candles, in metres, Y up.
 *
 * makeFire({radius:.35, height:1.1, seed:7, quality:'high'}) returns a Group
 * standing on y=0. Call group.userData.update(t, dt) with absolute seconds.
 * A bounded, advected density field is integrated front to back; no textures,
 * camera-facing flame cards or accumulated simulation state are needed.
 * This is an artistic combustion/temperature model, not a fluid solver.
 *
 * quality: low=32 / medium=56 / high=88 ray samples. Cost scales with screen
 * coverage. Share a few fires, rather than hundreds of close-up volumes.
 * Normal transparency cannot correctly interleave this volume with transparent
 * glass or another volume. Solid occluders in front are depth-tested; geometry
 * embedded in the volume should be placed at its base (logs, wick).
 * Accurate luminous-surface depth requires the default non-logarithmic depth
 * buffer. With logarithmic depth enabled, the proxy back face supplies depth
 * and embedded opaque objects can incorrectly clip the flame.
 * dispose() owns only resources created here. It is safe to call twice.
 */
import * as THREE from 'three';
import { attachDisposal, snapshotResources } from './lifecycle.js';
import { mulberry32 } from './noise.js';
import { makeShaderMaterial, keepOutOfDepthPasses, patchStandard, readWind } from './shader.js';

const finite = (v, fallback, lo, hi) => Number.isFinite(v)
  ? THREE.MathUtils.clamp(v, lo, hi) : fallback;
const QUALITY = { low: 32, medium: 56, high: 88 };

const FIELD = /* glsl */`
uniform float uRadius, uHeight, uIntensity, uCandle, uSeed;
uniform vec2 uWind;
uniform vec3 uBoxMin, uBoxMax, uCameraLocal;
uniform mat4 uLocalToClip;
float fireHash(vec3 p) {
  p = fract(p * 0.1031);
  p += dot(p, p.yzx + 33.33);
  return fract((p.x + p.y) * p.z);
}
float fireNoise(vec3 p) {
  vec3 i = floor(p), f = fract(p);
  f = f * f * (3.0 - 2.0 * f);
  return mix(mix(mix(fireHash(i), fireHash(i + vec3(1,0,0)), f.x),
                 mix(fireHash(i + vec3(0,1,0)), fireHash(i + vec3(1,1,0)), f.x), f.y),
             mix(mix(fireHash(i + vec3(0,0,1)), fireHash(i + vec3(1,0,1)), f.x),
                 mix(fireHash(i + vec3(0,1,1)), fireHash(i + vec3(1,1,1)), f.x), f.y), f.z);
}
float fireFbm(vec3 p) {
  return fireNoise(p) * 0.57 + fireNoise(p * 2.07 + 13.4) * 0.28
       + fireNoise(p * 4.21 - 7.1) * 0.15;
}
// Local density and normalized temperature. Advection moves upward as time
// increases. Domain warping gives the crest detached, rolling tongues.
vec2 fireField(vec3 p) {
  float flameScale = mix(0.90 + 0.06 * sin(uTime * 6.2 + uSeed)
    + 0.04 * sin(uTime * 9.7 + uSeed * 1.4),
    0.965 + 0.025 * sin(uTime * 7.1 + uSeed), uCandle);
  float y = p.y / (uHeight * flameScale);
  if (y <= 0.0 || y >= 1.08) return vec2(0.0);
  float t = uTime * mix(1.7, 1.1, uCandle);
  vec2 bend = uWind * uHeight * y * y;
  bend += uRadius * y * y * vec2(sin(t * 2.7 + uSeed), cos(t * 1.9 + uSeed))
    * mix(0.5, 0.24, uCandle);
  vec3 q = vec3((p.xz - bend) / uRadius, y).xzy;
  float r = length(q.xz);
  vec3 flow = vec3(q.x * 2.7, y * 5.1 - t * 2.0, q.z * 2.7) + uSeed;
  float coarse = fireFbm(flow);
  float curl = fireNoise(flow * 0.72 + vec3(0.0, t * 0.42, 4.0));
  // Five irregular fuel tongues share one smooth 3-D density volume.
  float campProfile = pow(max(0.0, 1.0 - y), 0.82) * 0.30;
  float campDistance = r;
  for (int k = 0; k < 4; k++) {
    float a = float(k) * 1.570796 + uSeed;
    float tongueY = y * (1.23 + 0.27 * sin(float(k) * 7.7 + uSeed));
    vec2 centre = vec2(sin(a), cos(a)) * (0.50 - y * 0.13);
    centre += vec2(sin(y * 8.0 - t * 3.0 + a), cos(y * 6.0 - t * 2.3 + a)) * y * 0.2;
    float tongue = pow(max(0.0, 1.0 - tongueY), 0.9) * 0.26;
    campDistance = min(campDistance, length(q.xz - centre) + campProfile - tongue);
  }
  float candleProfile = pow(max(0.0, sin(3.14159 * pow(min(y, 1.0), 0.62))), 0.86);
  float profile = mix(campProfile, candleProfile, uCandle);
  float disturbance = mix((coarse - 0.49) * (0.35 + y * 0.5)
    + (curl - 0.5) * 0.08, (coarse - 0.5) * 0.12, uCandle);
  float surface = profile + disturbance - mix(campDistance, r, uCandle);
  float density = smoothstep(-0.025, 0.075, surface);
  // Combustion is strongest on the mixing interface, not in the unburned
  // centre. Thin rolling reaction sheets retain transparent interior gaps.
  density *= mix(0.16 + 0.84 * (1.0 - smoothstep(0.08, 0.36, surface)), 1.0, uCandle);
  density *= smoothstep(0.0, mix(0.07, 0.025, uCandle), y)
    * (1.0 - smoothstep(0.88, 1.075, y));
  // Candle's unburned vapour pocket and blue reaction zone near the wick.
  float pocket = (1.0 - smoothstep(0.10, 0.36, y))
    * (1.0 - smoothstep(0.18, 0.52, r));
  density *= 1.0 - pocket * uCandle * 0.88;
  float temperature = clamp(0.23 + 0.69 * density + 0.12 * coarse - 0.24 * y, 0.0, 1.0);
  return vec2(density, temperature);
}
vec3 fireEmission(float heat) {
  vec3 c = mix(vec3(1.8, 0.055, 0.002), vec3(4.0, 0.76, 0.025),
    smoothstep(0.08, 0.46, heat));
  c = mix(c, vec3(6.2, 3.1, 0.68), smoothstep(0.40, 0.80, heat));
  return mix(c, vec3(7.0, 5.1, 2.3), smoothstep(0.78, 1.0, heat));
}
`;

function fireMaterial({ radius, height, wind, intensity, candle, seed, steps, box }) {
  const mat = makeShaderMaterial({
    name: 'FireVolume', fog: true,
    uniforms: {
      uRadius: { value: radius }, uHeight: { value: height },
      uWind: { value: new THREE.Vector2(...wind) },
      uIntensity: { value: intensity }, uCandle: { value: candle ? 1 : 0 },
      uSeed: { value: (seed >>> 0) % 997 * 0.173 },
      uBoxMin: { value: box.min.clone() }, uBoxMax: { value: box.max.clone() },
      uCameraLocal: { value: new THREE.Vector3() },
      uLocalToClip: { value: new THREE.Matrix4() },
    },
    varyings: 'varying vec3 vFireLocal;',
    vertexMain: 'vFireLocal = position;',
    fragmentHead: FIELD,
    fragmentMain: /* glsl */`
      if (uIntensity <= 0.0) discard;
      vec3 ray = normalize(vFireLocal - uCameraLocal);
      vec2 range = astraRayBox(uCameraLocal, ray, uBoxMin, uBoxMax);
      float enter = max(0.0, range.x), leave = range.y;
      if (leave <= enter) discard;
      float ds = (leave - enter) / float(${steps});
      // Static sub-pixel jitter avoids axial banding without temporal noise.
      float jitter = fireHash(vec3(gl_FragCoord.xy, 0.13));
      vec3 color = vec3(0.0);
      float transmittance = 1.0;
      vec3 firstHit = uCameraLocal + ray * leave;
      bool hit = false;
      for (int i = 0; i < ${steps}; i++) {
        vec3 p = uCameraLocal + ray * (enter + (float(i) + jitter) * ds);
        vec2 field = fireField(p);
        float optical = field.x * ds * mix(2.8, 4.2, uCandle) / uRadius;
        float a = 1.0 - exp(-optical);
        if (!hit && field.x > 0.10) { firstHit = p; hit = true; }
        vec3 emitted = fireEmission(field.y);
        emitted *= mix(vec3(0.36, 0.13, 0.025), vec3(0.65, 0.57, 0.43), uCandle);
        float blue = uCandle * (1.0 - smoothstep(0.025, 0.19, p.y / uHeight));
        emitted = mix(emitted, vec3(0.08, 0.28, 1.5), blue);
        color += transmittance * a * emitted * uIntensity;
        transmittance *= 1.0 - a;
        if (transmittance < 0.008) break;
      }
      float alpha = 1.0 - transmittance;
      if (alpha < 0.008) discard;
      // Depth from the luminous field, not its empty bounding cube. This also
      // makes the volume visible to a camera inside its proxy geometry.
      #ifndef USE_LOGDEPTHBUF
        gl_FragDepth = astraClipDepth(uLocalToClip, firstHit);
      #endif
      gl_FragColor = vec4(color / max(alpha, 0.001), alpha);
    `,
    transparent: true, depthWrite: false, side: THREE.BackSide,
  });
  mat.userData.bloom = true;
  return mat;
}

function installDispose(group) {
  const owned = snapshotResources(group);
  group.traverse((o) => { if (o.isLight && o.dispose) owned.add(o); });
  attachDisposal(group, owned);
  const dispose = group.userData.dispose;
  group.userData.dispose = () => {
    if (dispose()) group.userData.disposed = true;
  };
}

/**
 * @param {object} [opts]
 * radius=.35, height=1.1: flame envelope in metres; style='campfire'|'candle'.
 * wind=[x,z] (or any shader.js readWind spelling): lateral lean / height, bounded to ±.6. intensity=1: emission.
 * quality='medium'; seed=7; embers=26 (0 for candle), max 256.
 * light=true, lightIntensity=14 (candela), lightDistance=height*8.
 * Flame geometry only: add your own logs/burner. userData.bounds is local and
 * includes ember trajectories. update(t) is deterministic even when rewound.
 */
export function makeFire(opts = {}) {
  const candle = opts.style === 'candle';
  const radius = finite(opts.radius, candle ? 0.009 : 0.35, 0.001, 20);
  const height = finite(opts.height, candle ? 0.055 : 1.1, 0.004, 50);
  const intensity = finite(opts.intensity, 1, 0, 8);
  const seed = finite(opts.seed, 7, -2147483648, 2147483647) | 0;
  const w = readWind(opts.wind, [0, 0], 'makeFire wind');
  // bounded to ±.6 per axis by scaling both, so a strong wind still leans the way it blows
  const lean = 0.6 / Math.max(0.6, Math.abs(w.x), Math.abs(w.z));
  const wind = [w.x * lean, w.z * lean];
  const steps = Object.hasOwn(QUALITY, opts.quality) ? QUALITY[opts.quality] : QUALITY.medium;
  const extentX = radius * 1.65 + Math.abs(wind[0]) * height * 1.2;
  const extentZ = radius * 1.65 + Math.abs(wind[1]) * height * 1.2;
  const box = new THREE.Box3(new THREE.Vector3(-extentX, 0, -extentZ),
    new THREE.Vector3(extentX, height * 1.1, extentZ));
  const group = new THREE.Group(); group.name = candle ? 'CandleFlame' : 'Fire';
  const geometry = new THREE.BoxGeometry(extentX * 2, height * 1.1, extentZ * 2);
  geometry.translate(0, height * 0.55, 0);
  const material = fireMaterial({ radius, height, wind, intensity, candle, seed, steps, box });
  const volume = new THREE.Mesh(geometry, material); volume.name = 'FlameVolume';
  volume.renderOrder = 2;
  keepOutOfDepthPasses(volume);
  const guard = volume.onBeforeRender;
  const inverse = new THREE.Matrix4(), cameraLocal = material.uniforms.uCameraLocal.value;
  volume.onBeforeRender = (r, s, cam, geo, mat, draw) => {
    guard.call(volume, r, s, cam, geo, mat, draw);
    inverse.copy(volume.matrixWorld).invert();
    cameraLocal.setFromMatrixPosition(cam.matrixWorld).applyMatrix4(inverse);
    material.uniforms.uLocalToClip.value.copy(cam.projectionMatrix)
      .multiply(cam.matrixWorldInverse).multiply(volume.matrixWorld);
  };
  group.add(volume);

  const random = mulberry32(seed);
  const count = Math.round(finite(opts.embers, candle ? 0 : 26, 0, 256));
  const particles = Array.from({ length: count }, () => ({
    phase: random(), life: 1.5 + random() * 2.0,
    x: (random() - 0.5) * radius * 1.5, z: (random() - 0.5) * radius * 1.5,
    drift: random() * 6.283, size: radius * (0.006 + random() * 0.01),
  }));
  let embers;
  const emberMat = count ? new THREE.MeshBasicMaterial({ color: 0xffffff }) : null;
  const dummy = new THREE.Object3D(), color = new THREE.Color();
  if (count) {
    emberMat.userData.bloom = true;
    embers = new THREE.InstancedMesh(new THREE.SphereGeometry(1, 5, 4), emberMat, count);
    embers.name = 'FireEmbers'; embers.visible = intensity > 0; embers.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    embers.frustumCulled = false;
    group.add(embers);
    box.expandByPoint(new THREE.Vector3(-radius * 1.2 - Math.abs(wind[0]) * height * 2.1,
      height * 2.5 + radius * 0.05, -radius * 1.2 - Math.abs(wind[1]) * height * 2.1));
    box.expandByPoint(new THREE.Vector3(radius * 1.2 + Math.abs(wind[0]) * height * 2.1,
      height * 2.5 + radius * 0.05, radius * 1.2 + Math.abs(wind[1]) * height * 2.1));
  }
  const lightIntensity = finite(opts.lightIntensity, candle ? 0.9 : 14, 0, 10000);
  let light;
  if (opts.light !== false) {
    light = new THREE.PointLight(0xffab53, lightIntensity,
      finite(opts.lightDistance, height * 8, 0, 1000), 2);
    light.name = 'FireLight'; light.position.y = height * 0.27;
    group.add(light);
  }
  // `out` lets update() reuse one record instead of allocating per ember.
  const sampleEmber = (index, t, out = { position: new THREE.Vector3() }) => {
    if (!particles.length) return null;
    const p = particles[THREE.MathUtils.clamp(index | 0, 0, count - 1)];
    const a = ((t / p.life + p.phase) % 1 + 1) % 1;
    out.position.set(
      p.x + wind[0] * height * a * 2 + Math.sin(a * 5 + p.drift) * radius * a * 0.3,
      height * (0.15 + a * 2.2), p.z + wind[1] * height * a * 2 + Math.cos(a * 4 + p.drift) * radius * a * 0.3);
    out.scale = Math.pow(Math.sin(a * Math.PI), 0.7) * p.size; out.age = a;
    return out;
  };
  const ember = { position: new THREE.Vector3() };
  group.userData.update = (t, _dt) => {
    if (group.userData.disposed) return;
    t = Number.isFinite(t) ? t : 0;
    material.uniforms.uTime.value = t;
    const flicker = 0.88 + 0.07 * Math.sin(t * 11 + seed)
      + 0.05 * Math.sin(t * 17.3 + seed * 0.31);
    if (light) light.intensity = lightIntensity * flicker * intensity;
    for (let i = 0; i < count; i++) {
      const p = sampleEmber(i, t, ember);
      dummy.position.copy(p.position); dummy.scale.set(p.scale, p.scale * 2.7, p.scale);
      dummy.rotation.z = Math.sin(t + i) * 0.25; dummy.updateMatrix();
      embers.setMatrixAt(i, dummy.matrix);
      color.setRGB(3.5 * (1 - p.age * 0.75), 0.7 * (1 - p.age), 0.025).multiplyScalar(intensity);
      embers.setColorAt(i, color);
    }
    if (embers) { embers.instanceMatrix.needsUpdate = true; embers.instanceColor.needsUpdate = true; }
  };
  group.userData.tick = group.userData.update;
  installDispose(group);
  group.userData.bounds = box.clone();
  group.userData.sampleEmber = sampleEmber;
  group.userData.quality = steps;
  group.userData.update(0, 0);
  return group;
}

function waxGeometry(radius, height, seed) {
  // Continuous turned wall and dished top: no coincident cylinder cap/pool.
  const vertices = [], uv = [], indices = [];
  const segments = 80;
  const profile = [
    [0, 0], [radius * 0.94, 0], [radius, height * 0.015],
    [radius, height * 0.89], [radius * 0.998, height * 0.973],
    [radius * 0.91, height], [radius * 0.78, height * 0.993],
    [radius * 0.66, height * 0.953], [radius * 0.38, height * 0.943], [0, height * 0.943],
  ];
  for (let j = 0; j < profile.length; j++) {
    for (let i = 0; i <= segments; i++) {
      const a = i / segments * Math.PI * 2;
      const rough = Math.sin(a * 3 + seed) * 0.38 + Math.sin(a * 7 + seed * 0.2) * 0.19;
      const [r, y] = profile[j];
      const rim = j >= 4 && j <= 7 ? rough * Math.min(height * 0.019, radius * 0.09) : 0;
      const rr = r * (1 + 0.007 * Math.sin(a * 11 + j * 0.7));
      vertices.push(Math.sin(a) * rr, y + rim, Math.cos(a) * rr);
      uv.push(i / segments, j / (profile.length - 1));
      if (j && i) {
        const b = j * (segments + 1) + i;
        indices.push(b, b - 1, b - segments - 1, b - 1, b - segments - 2, b - segments - 1);
      }
    }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(vertices, 3));
  g.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2));
  g.setIndex(indices); g.computeVertexNormals(); g.computeBoundingSphere();
  return g;
}

/**
 * A candle whose base is y=0: radius=.032, height=.16, color=0xe9cf9b,
 * drips=7, seed=13, lit=true, flameHeight=.047, quality='high'.
 * Includes melted lip, glossy wax pool, cooled runoff and a charred curved wick.
 * Returned Group has update(t,dt), tick(t), dispose(), bounds and flame handles.
 * lightIntensity=.8 candela approximates one candle; scene exposure is unchanged.
 */
export function makeCandle(opts = {}) {
  const radius = finite(opts.radius, 0.032, 0.004, 2);
  const height = finite(opts.height, 0.16, 0.015, 8);
  const seed = finite(opts.seed, 13, -2147483648, 2147483647) | 0;
  const lit = opts.lit !== false;
  const group = new THREE.Group(); group.name = 'Candle';
  const wax = new THREE.MeshPhysicalMaterial({ color: opts.color ?? 0xe9cf9b,
    roughness: 0.41, metalness: 0, clearcoat: 0.08, clearcoatRoughness: 0.4 });
  wax.name = 'WarmTranslucentWax';
  patchStandard(wax, { name: 'CandleWax', util: true,
    uniforms: { uWaxHeight: { value: height }, uWaxRadius: { value: radius },
      uWaxLit: { value: lit ? 1 : 0 } },
    vertexHead: 'varying vec3 vWaxLocal;', vertexBody: 'vWaxLocal = position;',
    fragmentHead: 'varying vec3 vWaxLocal; uniform float uWaxHeight, uWaxRadius, uWaxLit;',
    fragmentBody: `
      float grain = astraNoise2(vWaxLocal.xy * 720.0) - 0.5;
      diffuseColor.rgb *= 1.0 + grain * 0.035;
    `,
    outputBody: `
      float waxGlow = exp(-(uWaxHeight - vWaxLocal.y) / max(uWaxRadius * 0.8, 0.001));
      gl_FragColor.rgb += uWaxLit * waxGlow * vec3(0.24, 0.045, 0.008);
    `,
  });
  const body = new THREE.Mesh(waxGeometry(radius, height, seed), wax);
  body.name = 'CandleWaxBody'; body.castShadow = true; body.receiveShadow = true; group.add(body);
  const pool = new THREE.Mesh(new THREE.CircleGeometry(radius * 0.59, 64),
    new THREE.MeshPhysicalMaterial({ color: opts.color ?? 0xe9cf9b, roughness: 0.10,
      clearcoat: 1, clearcoatRoughness: 0.04, metalness: 0 }));
  pool.name = 'MoltenWaxPool'; pool.rotation.x = -Math.PI / 2; pool.position.y = height * 0.947;
  group.add(pool);

  const random = mulberry32(seed), drips = Math.round(finite(opts.drips, 7, 0, 24));
  const dripMat = wax;
  for (let i = 0; i < drips; i++) {
    const angle = random() * Math.PI * 2;
    const length = height * (0.08 + random() * 0.46);
    const width = radius * (0.04 + random() * 0.045);
    const points = [];
    for (let j = 0; j < 9; j++) {
      const a = angle + Math.sin(j / 8 * Math.PI) * 0.03;
      const r = radius * (j === 0 ? 0.93 : 1.004);
      points.push(new THREE.Vector3(Math.sin(a) * r, height * 0.988 - length * j / 8, Math.cos(a) * r));
    }
    const drip = new THREE.Mesh(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(points), 20, width, 8, false), dripMat);
    drip.name = `WaxDrip_${i}`; drip.castShadow = true; group.add(drip);
    const bead = new THREE.Mesh(new THREE.SphereGeometry(width * 1.32, 10, 8), dripMat);
    bead.name = `WaxDripBead_${i}`; bead.position.copy(points[8]); bead.scale.y = 1.55; group.add(bead);
  }
  const wickHeight = Math.min(radius * 0.29, height * 0.11);
  const wickCurve = new THREE.CatmullRomCurve3([
    new THREE.Vector3(0, height * 0.944, 0),
    new THREE.Vector3(radius * 0.02, height * 0.944 + wickHeight * 0.65, 0),
    new THREE.Vector3(radius * 0.10, height * 0.944 + wickHeight, radius * 0.025),
  ]);
  const wick = new THREE.Mesh(new THREE.TubeGeometry(wickCurve, 12, radius * 0.021, 8, false),
    new THREE.MeshStandardMaterial({ color: 0x17100b, roughness: 1,
      emissive: lit ? 0x6e1901 : 0, emissiveIntensity: lit ? 0.35 : 0 }));
  wick.name = 'CharredWick'; group.add(wick);
  let flame;
  if (lit) {
    const flameHeight = finite(opts.flameHeight, radius * 1.47, 0.01, 3);
    flame = makeFire({ radius: flameHeight * 0.17, height: flameHeight, style: 'candle',
      seed, wind: opts.wind, quality: opts.quality || 'high', light: opts.light,
      lightIntensity: finite(opts.lightIntensity, 0.8, 0, 10000), lightDistance: Math.max(1, height * 14),
      intensity: opts.intensity });
    flame.position.y = height * 0.944 + wickHeight * 0.6;
    group.add(flame);
  }
  group.userData.flame = flame || null;
  group.userData.update = (t, dt) => {
    if (!group.userData.disposed) flame?.userData.update(t, dt);
  };
  group.userData.tick = group.userData.update;
  installDispose(group);
  group.updateMatrixWorld(true);
  group.userData.bounds = new THREE.Box3().setFromObject(group);
  return group;
}

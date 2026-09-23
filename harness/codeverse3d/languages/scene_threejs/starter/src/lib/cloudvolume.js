/**
 * Bounded, ray-marched cumulus clouds. A seeded 3D texture separates a
 * stationary billow scaffold from periodic advected density and erosion; Beer extinction and secondary light marches
 * supply thickness, sunward shading and silver linings. Three quality tiers
 * trade integration accuracy for cost without changing the density field.
 *
 * This is authored weather, not a fluid solver. Multiple scattering is a
 * bounded two-lobe approximation. It does not cast terrain shadows, receive
 * scene shadow maps or truncate its integration at intersecting geometry.
 * Ordinary depth testing uses the first contributing density sample, so
 * opaque objects behind the cloud do not erase its front. Geometry embedded
 * in density still needs scene-depth-aware integration, which is not supplied.
 * Logarithmic-depth rendering retains proxy-box depth as a fallback.
 * Transparent objects follow ordinary Three.js object sorting. No temporal
 * reconstruction or external textures are required.
 */
import * as THREE from 'three';
import { makeShaderMaterial, keepOutOfDepthPasses } from './shader.js';
import { attachDisposal, snapshotResources } from './lifecycle.js';
import { bakeFbm3, dataTexture3D, mulberry32, sampleGrid3 } from './noise.js';

const TIERS = {
  low: { steps: 40, lightSteps: 6 },
  balanced: { steps: 72, lightSteps: 10 },
  high: { steps: 112, lightSteps: 16 },
};
const RESOLUTION = 80;
const clamp = (x, a = 0, b = 1) => Math.max(a, Math.min(b, x));
const smooth = (a, b, x) => {
  const t = clamp((x - a) / (b - a));
  return t * t * (3 - 2 * t);
};

function vector(value, fallback, length, label) {
  const values = value?.toArray ? value.toArray() : value ?? fallback;
  if (!Array.isArray(values) || values.length !== length || !values.every(Number.isFinite)) {
    throw new RangeError(`makeCloudVolume: ${label} must contain ${length} finite numbers`);
  }
  return values;
}

function densityTexture(seed) {
  const random = mulberry32(seed);
  const { values: noiseField, lattices } = bakeFbm3(random, RESOLUTION, [4, 8, 16, 32]);
  // Unequal convection towers grow from a connected low bank. Small
  // satellite billows belong to each tower instead of a regular 3-by-2 grid.
  const lobes = [{ x: 0, y: -.15, z: 0, rx: .31, ry: .16, rz: .25 }];
  for (let cluster = 0; cluster < 7; cluster++) {
    const angle = cluster * 2.399963 + random() * .5;
    const spread = cluster === 0 ? 0 : Math.sqrt(cluster / 7);
    const x = Math.cos(angle) * .22 * spread + (random() - .5) * .025;
    const z = Math.sin(angle) * .16 * spread + (random() - .5) * .025;
    const radius = .115 + random() * .075;
    const y = -.10 + random() * .13;
    lobes.push({ x, y, z, rx: radius * 1.12, ry: radius * 1.45, rz: radius });
    for (let lobe = 0; lobe < 7; lobe++) {
      const azimuth = random() * Math.PI * 2;
      const elevation = -.15 + random() * 1.25;
      const r = radius * (.32 + random() * .30);
      lobes.push({
        x: x + Math.cos(azimuth) * Math.cos(elevation) * radius * .88,
        y: y + Math.sin(elevation) * radius * 1.38,
        z: z + Math.sin(azimuth) * Math.cos(elevation) * radius * .88,
        rx: r, ry: r * (1.0 + random() * .45), rz: r,
      });
    }
  }
  const data = new Uint8Array(RESOLUTION ** 3 * 4);
  let i = 0;
  for (let z = 0; z < RESOLUTION; z++) for (let y = 0; y < RESOLUTION; y++) for (let x = 0; x < RESOLUTION; x++) {
    const u = (x + .5) / RESOLUTION, v = (y + .5) / RESOLUTION, w = (z + .5) / RESOLUTION;
    const noise = noiseField[i / 4];
    let shape = -4;
    for (const lobe of lobes) {
      const distance = Math.hypot((u - .5 - lobe.x) / lobe.rx,
        (v - .5 - lobe.y) / lobe.ry, (w - .5 - lobe.z) / lobe.rz);
      shape = Math.max(shape, 1 - distance);
    }
    const detail = sampleGrid3(lattices[2].data, 16, u * 16, v * 16, w * 16);
    // Stationary nested breakup leaves a scalloped boundary. The ellipsoid
    // only limits the outer support; its zero band stays inside the proxy.
    shape += (detail - .5) * .70;
    shape = Math.min(shape, 1 - Math.hypot((u - .5) / .48, (v - .49) / .48, (w - .5) / .48));
    const value = clamp(.53 + shape * .66);
    // Keep the lower fade out of this thresholded texture. Applying it
    // here turns a soft boundary into a nearly flat opaque cloud-base lip.
    const border = smooth(0, .07, u) * (1 - smooth(.93, 1, u))
      * (1 - smooth(.93, 1, v))
      * smooth(0, .07, w) * (1 - smooth(.93, 1, w));
    data[i++] = Math.round(value * border * 255);
    data[i++] = Math.round(clamp(noise) * 255);
    data[i++] = Math.round(clamp(detail) * 255);
    // Coverage and advected noise may erode this support but cannot create
    // new cloud on a box face. Alpha is support, not rendered opacity.
    data[i++] = Math.round(smooth(0, .10, shape) * 255);
  }
  return dataTexture3D(data, RESOLUTION);
}

const HEAD = /* glsl */`
precision highp sampler3D;
uniform sampler3D uDensityMap;
uniform vec3 uSize;
uniform vec3 uCameraLocal;
uniform vec3 uSunLocal;
uniform vec3 uSunWorld;
uniform vec3 uSunColor;
uniform vec3 uSkyColor;
uniform vec2 uWind;
uniform mat3 uLocalToWorld;
uniform mat4 uLocalToClip;
uniform float uCoverage;
uniform float uDetail;
uniform float uExtinction;
uniform float uTime;
varying vec3 vLocal;

vec2 cloudBox(vec3 origin, vec3 direction) {
  vec3 safe = sign(direction) * max(abs(direction), vec3(1e-6));
  safe += vec3(equal(direction, vec3(0.0))) * 1e-6;
  vec3 a = (-uSize * .5 - origin) / safe;
  vec3 b = (uSize * .5 - origin) / safe;
  vec3 near = min(a, b), far = max(a, b);
  return vec2(max(max(near.x, near.y), near.z), min(min(far.x, far.y), far.z));
}
float cloudDensity(vec3 position) {
  vec3 uv = position / uSize + .5;
  if (any(lessThan(uv, vec3(0.0))) || any(greaterThan(uv, vec3(1.0)))) return 0.0;
  vec3 drift = vec3(uWind.x / uSize.x, 0.0, uWind.y / uSize.z) * uTime;
  if (uCoverage <= 0.0) return 0.0;
  vec4 field = texture(uDensityMap, uv - drift);
  // Keep support in local space while only periodic noise advects.
  vec4 scaffold = texture(uDensityMap, uv);
  float shape = scaffold.r;
  float noise = clamp(shape + (field.g - .5) * 1.15 - (field.b - .5) * .18, 0.0, 1.0);
  float baseFade = smoothstep(0.0, .18, uv.y);
  float height = baseFade * baseFade * (1.0 - smoothstep(.92, 1.0, uv.y));
  float edge = smoothstep(0.0, .10, uv.x) * (1.0 - smoothstep(.90, 1.0, uv.x))
      * smoothstep(0.0, .10, uv.z) * (1.0 - smoothstep(.90, 1.0, uv.z));
  float threshold = .72 - uCoverage * .40;
  vec3 detailUv = (uv - drift) * 5.0 + vec3(.137,.291,.417);
  vec4 detailSample = texture(uDensityMap, detailUv);
  float fine = clamp((detailSample.g - .22) / .56, 0.0, 1.0);
  float erosion = (1.0 - fine) * uDetail * 1.10 * (1.0 - noise);
  // Fade the resolved density, not the field before thresholding. This
  // makes the bottom lose optical thickness gradually rather than crop flat.
  return clamp((noise - threshold - erosion) * 9.0, 0.0, 1.0) * edge * height * scaffold.a;
}
float cloudHG(float cosine, float g) {
  float gg = g * g;
  return (1.0 - gg) / pow(max(.01, 1.0 + gg - 2.0 * g * cosine), 1.5);
}
float cloudSunDepth(vec3 position, float jitter) {
  float end = max(cloudBox(position, uSunLocal).y, 0.0);
  float metric = length(uLocalToWorld * uSunLocal);
  float opticalDepth = 0.0;
  for (int j = 0; j < CLOUD_LIGHT_STEPS; j++) {
    // Quadratic spacing resolves the billow immediately around the sample;
    // uniform long steps skip its skin and erase small-scale self-shadowing.
    float a = float(j) / float(CLOUD_LIGHT_STEPS);
    float b = float(j + 1) / float(CLOUD_LIGHT_STEPS);
    float start = a * a * end, stop = b * b * end;
    vec3 p = position + uSunLocal * mix(start, stop, jitter);
    opticalDepth += cloudDensity(p) * (stop - start) * metric * uExtinction;
  }
  return opticalDepth;
}
`;

/**
 * size: local [x,y,z] metres (default [600,180,360]), seed: integer,
 * coverage: 0..1 (.55), density: extinction / metre (.028),
 * quality: 'low'|'balanced'|'high', wind: local [x,z] metres/second,
 * advecting density detail through a bounded stationary form. Move the Mesh
 * itself for whole-cloud travel; the cloud scaffold never wraps across its box.
 * sunDirection: WORLD direction toward the sun, sunColor and skyColor:
 * linear-light THREE.Color or sRGB hex. sunIntensity scales the strongest
 * visible directional light (1.25 at the standard 5.4-unit daylight rig).
 * Ambient/hemisphere lights and scene.environment supply the sky contribution.
 *
 * The returned Mesh is centred at its local origin. update(t) accepts absolute
 * seconds; sampleDensity(x,y,z,t) returns the same local 0..1 field sampled by
 * the GPU before optical extinction. setSunDirection() supports moving light.
 */
export function makeCloudVolume(opts = {}) {
  const size = vector(opts.size, [600, 180, 360], 3, 'size');
  const wind = vector(opts.wind, [2.5, .4], 2, 'wind');
  const direction = vector(opts.sunDirection, [-.5, .7, -.3], 3, 'sunDirection');
  const seed = opts.seed ?? 19;
  const coverage = opts.coverage ?? .55;
  const extinction = opts.density ?? .028;
  const detail = opts.detail ?? .65;
  const intensity = opts.sunIntensity ?? 1.25;
  const quality = opts.quality ?? 'balanced';
  if (!Number.isSafeInteger(seed) || size.some((v) => v <= 0 || v > 100000) ||
      ![coverage, extinction, intensity, detail].every(Number.isFinite) || coverage < 0 || coverage > 1 ||
      extinction < 0 || extinction > 10 || intensity < 0 || detail < 0 || detail > 1 || !Object.hasOwn(TIERS, quality) || Math.hypot(...direction) < 1e-9) {
    throw new RangeError('makeCloudVolume: invalid dimensions, seed, coverage, density, sun intensity or quality');
  }
  const tier = TIERS[quality];
  const texture = densityTexture(seed);
  let sunPinned = opts.sunDirection !== undefined;
  const sun = new THREE.Vector3(...direction).normalize();
  const sunTint = new THREE.Color(opts.sunColor ?? 0xfff3dc);
  const skyTint = new THREE.Color(opts.skyColor ?? 0x9eb9da);
  const lightPosition = new THREE.Vector3(), lightTarget = new THREE.Vector3();
  const skyLight = new THREE.Color(0, 0, 0);
  const keyColor = new THREE.Color(0, 0, 0), scratch = new THREE.Color();
  const material = makeShaderMaterial({
    name: 'CloudVolumeMaterial', transparent: true, depthWrite: false,
    side: THREE.BackSide, fog: false,
    defines: { CLOUD_STEPS: tier.steps, CLOUD_LIGHT_STEPS: tier.lightSteps },
    uniforms: {
      uDensityMap: { value: texture }, uSize: { value: new THREE.Vector3(...size) },
      uCameraLocal: { value: new THREE.Vector3() }, uSunLocal: { value: sun.clone() },
      uSunWorld: { value: sun }, uLocalToWorld: { value: new THREE.Matrix3() },
      uLocalToClip: { value: new THREE.Matrix4() },
      uSunColor: { value: new THREE.Color(opts.sunColor ?? 0xfff3dc).multiplyScalar(intensity) },
      uSkyColor: { value: new THREE.Color(opts.skyColor ?? 0x9eb9da) },
      uCoverage: { value: coverage }, uExtinction: { value: extinction },
      uDetail: { value: detail },
      uWind: { value: new THREE.Vector2(...wind) }, uTime: { value: 0 },
    },
    varyings: 'varying vec3 vLocal;',
    vertexMain: 'vLocal = position;',
    fragmentHead: HEAD,
    fragmentMain: /* glsl */`
      vec3 direction = normalize(vLocal - uCameraLocal);
      vec2 bounds = cloudBox(uCameraLocal, direction);
      float start = max(bounds.x, 0.0), end = bounds.y;
      if (end <= start || uExtinction <= 0.0 || uCoverage <= 0.0) discard;
      float stepLength = (end - start) / float(CLOUD_STEPS);
      float metric = length(uLocalToWorld * direction);
      vec3 worldDirection = normalize(uLocalToWorld * direction);
      float cosine = dot(worldDirection, uSunWorld);
      float phase = min(3.0, .82 * cloudHG(cosine, .58) + .18 * cloudHG(cosine, -.24));
      // Fixed screen-space jitter is deterministic at a fixed camera/time;
      // it breaks integration bands without requiring temporal accumulation.
      float jitter = astraHash21(gl_FragCoord.xy) * .8 + .1;
      float transmittance = 1.0;
      vec3 radiance = vec3(0.0);
      vec3 firstDensity = uCameraLocal + direction * end;
      bool densityHit = false;
      for (int i = 0; i < CLOUD_STEPS; i++) {
        vec3 position = uCameraLocal + direction * (start + (float(i) + jitter) * stepLength);
        float density = cloudDensity(position);
        if (density > .001) {
          if (!densityHit) { firstDensity = position; densityHit = true; }
          float tau = cloudSunDepth(position, .5);
          float single = exp(-tau);
          // Two attenuated orders approximate broad multiple scattering.
          // They preserve dark cores without the black smoke of single scatter.
          float multiple = .50 * exp(-tau * .22) + .20 * exp(-tau * .05);
          float height = clamp(position.y / uSize.y + .5, 0.0, 1.0);
          vec3 source = uSunColor * (phase * .65 * single + multiple)
              + uSkyColor * (.20 + .58 * height);
          float alpha = 1.0 - exp(-density * uExtinction * stepLength * metric);
          radiance += transmittance * alpha * source;
          transmittance *= 1.0 - alpha;
          if (transmittance < .012) break;
        }
      }
      float alpha = 1.0 - transmittance;
      if (alpha < .003) discard;
      // The back face only bounds the integration ray. Test the visible
      // density against opaque depth, not the often much farther proxy exit.
      #ifndef USE_LOGDEPTHBUF
        vec4 densityClip = uLocalToClip * vec4(firstDensity, 1.0);
        gl_FragDepth = clamp(densityClip.z / densityClip.w * .5 + .5, 0.0, 1.0);
      #endif
      gl_FragColor = vec4(radiance / max(alpha, .001), alpha);
    `,
  });
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(...size), material);
  mesh.name = opts.name ?? 'CloudVolume';
  keepOutOfDepthPasses(mesh);
  const beforeRender = mesh.onBeforeRender;
  const inverse = new THREE.Matrix4();
  mesh.onBeforeRender = (...args) => {
    beforeRender.apply(mesh, args);
    const camera = args[2], scene = args[1];
    let key = null;
    skyLight.setRGB(0, 0, 0);
    scene.traverseVisible((light) => {
      if (!light.isLight || !(light.intensity > 0)) return;
      if (light.isDirectionalLight && (!key || light.intensity > key.intensity)) key = light;
      if (light.isAmbientLight) skyLight.add(scratch.copy(light.color).multiplyScalar(light.intensity));
      if (light.isHemisphereLight) {
        skyLight.add(scratch.copy(light.color).multiplyScalar(light.intensity * .75));
        skyLight.add(scratch.copy(light.groundColor).multiplyScalar(light.intensity * .25));
      }
    });
    keyColor.setRGB(0, 0, 0);
    if (key) {
      keyColor.copy(key.color).multiply(sunTint).multiplyScalar(key.intensity / 5.4 * intensity);
      if (!sunPinned) {
        key.getWorldPosition(lightPosition);key.target.getWorldPosition(lightTarget);
        sun.copy(lightPosition).sub(lightTarget).normalize();
      }
    }
    // The scene environment is an independent source of diffuse sky light;
    // removing a directional light does not remove that authored environment.
    if (scene.environment) {
      const environmentLight = .35 * (scene.environmentIntensity ?? 1);
      skyLight.r += environmentLight;skyLight.g += environmentLight;skyLight.b += environmentLight;
    }
    material.uniforms.uSunColor.value.copy(keyColor);
    material.uniforms.uSkyColor.value.copy(skyTint).multiply(skyLight);
    inverse.copy(mesh.matrixWorld).invert();
    camera.getWorldPosition(material.uniforms.uCameraLocal.value).applyMatrix4(inverse);
    material.uniforms.uSunLocal.value.copy(sun).transformDirection(inverse);
    material.uniforms.uLocalToWorld.value.setFromMatrix4(mesh.matrixWorld);
    material.uniforms.uLocalToClip.value.copy(camera.projectionMatrix)
      .multiply(camera.matrixWorldInverse).multiply(mesh.matrixWorld);
  };
  mesh.userData.update = (t) => {
    if (!Number.isFinite(t)) throw new RangeError('CloudVolume.update: time must be finite');
    material.uniforms.uTime.value = t;
  };
  mesh.userData.setSunDirection = (value) => {
    const values = vector(value, null, 3, 'sunDirection');
    if (Math.hypot(...values) < 1e-9) throw new RangeError('CloudVolume: sunDirection must be nonzero');
    sun.fromArray(values).normalize();
    sunPinned = true;
  };
  mesh.userData.sampleDensity = (x, y, z, t = material.uniforms.uTime.value) => {
    if (![x, y, z, t].every(Number.isFinite)) throw new RangeError('CloudVolume.sampleDensity: coordinates/time must be finite');
    const uv = [x / size[0] + .5, y / size[1] + .5, z / size[2] + .5];
    if (uv.some((v) => v < 0 || v > 1) || coverage === 0) return 0;
    const driftingNoise = sampleGrid3(texture.image.data, RESOLUTION,
      (uv[0] - wind[0] * t / size[0]) * RESOLUTION - .5,
      uv[1] * RESOLUTION - .5,
      (uv[2] - wind[1] * t / size[2]) * RESOLUTION - .5, 4, 1) / 255;
    const shape = sampleGrid3(texture.image.data, RESOLUTION,
      uv[0] * RESOLUTION - .5, uv[1] * RESOLUTION - .5, uv[2] * RESOLUTION - .5, 4, 0) / 255;
    const driftingDetail = sampleGrid3(texture.image.data, RESOLUTION,
      (uv[0] - wind[0] * t / size[0]) * RESOLUTION - .5, uv[1] * RESOLUTION - .5,
      (uv[2] - wind[1] * t / size[2]) * RESOLUTION - .5, 4, 2) / 255;
    const noise = clamp(shape + (driftingNoise - .5) * 1.15 - (driftingDetail - .5) * .18);
    const support = sampleGrid3(texture.image.data, RESOLUTION,
      uv[0] * RESOLUTION - .5, uv[1] * RESOLUTION - .5, uv[2] * RESOLUTION - .5, 4, 3) / 255;
    const baseFade = smooth(0, .18, uv[1]);
    const height = baseFade * baseFade * (1 - smooth(.92, 1, uv[1]));
    const edge = smooth(0, .10, uv[0]) * (1 - smooth(.90, 1, uv[0]))
      * smooth(0, .10, uv[2]) * (1 - smooth(.90, 1, uv[2]));
    const detailUv = [(uv[0] - wind[0] * t / size[0]) * 5 + .137,
      uv[1] * 5 + .291, (uv[2] - wind[1] * t / size[2]) * 5 + .417];
    const fine = clamp((sampleGrid3(texture.image.data, RESOLUTION,
      detailUv[0] * RESOLUTION - .5, detailUv[1] * RESOLUTION - .5,
      detailUv[2] * RESOLUTION - .5, 4, 1) / 255 - .22) / .56);
    const erosion = (1 - fine) * detail * 1.10 * (1 - noise);
    return clamp((noise - (.72 - coverage * .40) - erosion) * 9) * edge * height * support;
  };
  mesh.userData.quality = quality;
  mesh.userData.steps = { view: tier.steps, light: tier.lightSteps };
  const owned = snapshotResources(mesh);
  owned.add(texture);
  return attachDisposal(mesh, owned);
}

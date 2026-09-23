/** Wind-shaped dunes in metres. Geometry and surface detail share local coordinates.
 * `sampleHeight(x,z)` interpolates the actual rendered triangles, so stones and
 * footsteps can be seated without a second, subtly different height function.
 */
import * as THREE from 'three';
import { fbm2, mulberry32 } from './noise.js';
import { patchStandard } from './shader.js';

const TAU = Math.PI * 2;
const clamp = (x, lo, hi) => Math.max(lo, Math.min(hi, x));

function sandMaterial(opts, wind, seed) {
  const mat = new THREE.MeshStandardMaterial({
    color: opts.color ?? 0xc9a16b, roughness: 0.87, metalness: 0,
  });
  mat.name = 'AeolianSand';
  patchStandard(mat, {
    name: 'aeolianSand',
    uniforms: {
      uSandWind: { value: wind }, uSandSeed: { value: seed % 997 },
      uSandRipple: { value: Math.max(0.025, opts.rippleSpacing ?? 0.14) },
      uSandDetail: { value: clamp(opts.detailStrength ?? 1, 0, 2) },
    },
    vertexHead: 'varying vec3 vSandLocal;',
    vertexBody: 'vSandLocal = position;',
    fragmentHead: `
      varying vec3 vSandLocal;
      uniform vec2 uSandWind;
      uniform float uSandSeed, uSandRipple, uSandDetail;
      vec2 sandRippleGradient(vec2 p) {
        vec2 q = vec2(dot(p, uSandWind), dot(p, vec2(-uSandWind.y, uSandWind.x)));
        float warp = astraNoise2(q * vec2(0.3, 0.8) + uSandSeed) * 0.22;
        float c = (q.x + warp) / uSandRipple;
        // Filter BEFORE differentiating. dFdx(sin(highFrequency)) makes a
        // visible 2x2 quad lattice; an analytic derivative has no such grid.
        float fade = 1.0 - smoothstep(0.07, 0.28, fwidth(c));
        float slope = (cos(c * 6.2831853) + 0.34 * cos(c * 12.5663706 + 0.6))
          * fade * uSandRipple * 0.025 * 6.2831853;
        return slope * vec2(dFdx(c), dFdy(c));
      }
    `,
    fragmentBody: `
      float sandMacro = astraFbm2(vSandLocal.xz * 0.11 + uSandSeed, 3);
      float sandGrainFoot = max(length(dFdx(vSandLocal)), length(dFdy(vSandLocal)));
      float sandGrainFade = 1.0 - smoothstep(0.0007, 0.008, sandGrainFoot);
      float sandGrain = astraHash21(floor(vSandLocal.xz * 1400.0) + uSandSeed);
      vec2 sandRippleDH = sandRippleGradient(vSandLocal.xz);
      diffuseColor.rgb *= 0.87 + 0.30 * sandMacro;
      diffuseColor.rgb *= 1.0 + (sandGrain - 0.5) * sandGrainFade * 0.24;
      diffuseColor.rgb *= vec3(1.0 + sandMacro * 0.055, 1.0, 1.0 - sandMacro * 0.065);
    `,
    roughnessBody: `
      roughnessFactor = clamp(roughnessFactor - sandGrainFade * smoothstep(0.93, 1.0, sandGrain) * 0.32, 0.48, 0.98);
    `,
    normalBody: `
      vec3 sandDx = dFdx(-vViewPosition), sandDy = dFdy(-vViewPosition);
      vec3 sandR1 = cross(sandDy, normal), sandR2 = cross(normal, sandDx);
      float sandDet = dot(sandDx, sandR1);
      normal = normalize(abs(sandDet) * normal - sign(sandDet) *
        (sandRippleDH.x * sandR1 + sandRippleDH.y * sandR2) * uSandDetail);
    `,
  });
  return mat;
}

/**
 * @param {object} opts seed; size number or [width,depth]; segments (default
 * 256, max512); duneHeight metres; duneSpacing metres; windDirection [x,z];
 * rippleSpacing metres; color; detailStrength; grains (0 by default, max1200);
 * windSpeed metres/sec. A group, with userData.sampleHeight/update/dispose.
 * sampleHeight takes LOCAL x,z; parent assets to this group after seating them.
 */
export function makeSandTerrain(opts = {}) {
  const seed = opts.seed ?? 17;
  const rand = mulberry32(seed);
  const size = Array.isArray(opts.size) ? opts.size : [opts.size ?? 80, opts.size ?? 80];
  if (size.length !== 2 || !size.every((n) => Number.isFinite(n) && n > 0)) throw new RangeError('sand size must be positive metres');
  const [width, depth] = size;
  const nx = clamp(Math.round(opts.segments ?? 256), 8, 512);
  const nz = clamp(Math.round(nx * depth / width), 8, 512);
  const wind = new THREE.Vector2(...(opts.windDirection ?? [1, 0.18]));
  if (wind.lengthSq() < 1e-8) wind.set(1, 0);
  wind.normalize();
  const height = Math.max(0, opts.duneHeight ?? 3.8);
  const spacing = Math.max(1, opts.duneSpacing ?? 19);
  const phase = rand() * spacing;
  const field = (x, z) => {
    const u = x * wind.x + z * wind.y, v = -x * wind.y + z * wind.x;
    const bend = Math.sin(v / spacing * 1.7 + phase) * spacing * 0.15
      + fbm2(u / spacing * 0.7, v / spacing * 1.1, { seed, octaves: 2 }) * spacing * 0.35;
    const f = ((u + bend + phase) / spacing % 1 + 1) % 1;
    // Long shallow stoss face; short steep lee face. A continuous sharp crest
    // gives the surface a direction which symmetric sine hills do not have.
    const ridge = f < 0.77 ? Math.pow(f / 0.77, 1.75) : Math.pow((1 - f) / 0.23, 0.92);
    const envelope = 0.84 + 0.16 * Math.sin(v / spacing * 1.3 + phase);
    return height * (ridge * envelope + 0.16 * fbm2(x / spacing * 1.7, z / spacing * 1.7, { seed: seed + 83, octaves: 3 }));
  };
  const geometry = new THREE.PlaneGeometry(width, depth, nx, nz);
  geometry.rotateX(-Math.PI / 2);
  const p = geometry.attributes.position;
  for (let i = 0; i < p.count; i++) p.setY(i, field(p.getX(i), p.getZ(i)));
  geometry.computeVertexNormals();
  geometry.computeBoundingBox();
  geometry.computeBoundingSphere();
  const surface = new THREE.Mesh(geometry, sandMaterial(opts, wind, seed));
  surface.name = 'DuneSurface';
  surface.castShadow = true;
  surface.receiveShadow = true;
  surface.userData.placement = 'free';
  const group = new THREE.Group();
  group.name = opts.name ?? 'SandTerrain';
  group.userData.placement = 'free';
  group.add(surface);

  const sampleHeight = (x, z) => {
    const gx = clamp((x / width + 0.5) * nx, 0, nx);
    const gz = clamp((z / depth + 0.5) * nz, 0, nz);
    const ix = Math.min(nx - 1, Math.floor(gx)), iz = Math.min(nz - 1, Math.floor(gz));
    const fx = gx - ix, fz = gz - iz, a = iz * (nx + 1) + ix;
    const h00 = p.getY(a), h10 = p.getY(a + 1), h01 = p.getY(a + nx + 1), h11 = p.getY(a + nx + 2);
    return fx + fz <= 1 ? h00 + fx * (h10 - h00) + fz * (h01 - h00)
      : h11 + (1 - fx) * (h01 - h11) + (1 - fz) * (h10 - h11);
  };

  // Sparse saltating grains: CPU updates cost a few hundred points, not a
  // second terrain shader. Their vertical trajectory follows the same mesh.
  const count = clamp(Math.round(opts.grains ?? 0), 0, 1200);
  let grains = null, seeds = [];
  if (count) {
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.Float32BufferAttribute(new Float32Array(count * 3), 3));
    seeds = Array.from({ length: count }, () => [rand() * width - width / 2, rand() * depth - depth / 2, rand(), 0.02 + rand() * 0.10]);
    const mat = new THREE.PointsMaterial({ color: opts.color ?? 0xe2c79f, size: 0.009,
      transparent: true, opacity: 0.40, depthWrite: false, sizeAttenuation: true });
    grains = new THREE.Points(geo, mat);
    grains.name = 'WindblownGrains';
    grains.frustumCulled = false;
    grains.userData.placement = 'free';
    group.add(grains);
  }
  const update = (t) => {
    if (!grains) return;
    const pos = grains.geometry.attributes.position;
    const speed = opts.windSpeed ?? 0.85;
    const wrap = (v, n) => ((v + n / 2) % n + n) % n - n / 2;
    for (let i = 0; i < count; i++) {
      const [x0, z0, ph, hop] = seeds[i];
      const x = wrap(x0 + t * speed * wind.x, width), z = wrap(z0 + t * speed * wind.y, depth);
      pos.setXYZ(i, x, sampleHeight(x, z) + 0.008 + Math.abs(Math.sin(t * 7 + ph * TAU)) * hop, z);
    }
    pos.needsUpdate = true;
  };
  group.userData.sampleHeight = sampleHeight;
  group.userData.update = update;
  // Assets are explicitly allowed to be parented to this terrain. Snapshot
  // only the resources constructed here, so caller-owned attachments survive.
  const owned = [geometry, surface.material, grains?.geometry, grains?.material].filter(Boolean);
  let disposed = false;
  group.userData.dispose = () => {
    if (disposed) return;
    disposed = true;
    owned.forEach((resource) => resource.dispose());
  };
  group.userData.seed = seed;
  update(0);
  return group;
}

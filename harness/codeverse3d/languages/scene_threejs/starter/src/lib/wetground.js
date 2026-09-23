/**
 * Mirror and rain-wet floors: a real planar reflection under a rough
 * overlay. A bare Reflector is SHARP (reads as marble, never damp
 * asphalt), so the overlay sets the finish. COST: the Reflector
 * re-renders the WHOLE scene per frame — use a mirror floor OR a water
 * plane, never both.
 *
 * PORT NOTE (2026-09-01, our renderer: ACES, exposure 1.0, no post
 * chain). The shipped pair blends the two layers by a CONSTANT, and
 * measured here that is the one thing that stops the effect reading as
 * ground: at the mid camera a 30 x 22 m plaza came back as a milky
 * sheet, mean luminance 0.687 and p95 0.884 over its near half — the
 * sky, mirrored 1:1, brighter than the ground it lay in. Real water
 * films obey Fresnel: a few per cent looking down at them, near-total
 * at grazing. So the pair now works like a film over a substrate.
 *
 * - The OVERLAY carries the Fresnel, in its alpha, through
 *   `shader.js`'s patchStandard so a `patch*` module can still chain
 *   onto the same floor. Once, and only there: attenuating the mirror
 *   as well squares the falloff and puts the night plaza at dark_frac
 *   0.14 — a black hole where lit wet asphalt belongs.
 * - The MIRROR ships its own Reflector shader: a swell that slides the
 *   reflection, a short vertical smear (which also hides the 512 px
 *   RTT's stair-stepping on every reflected edge), and the scene's own
 *   fog and dithering, which a ShaderMaterial opts out of — so the
 *   stock mirror stayed crisp and banded while the world around it
 *   fogged.
 * - Both read ONE ripple field, so the highlight rides the wobble that
 *   made it.
 *
 * Same fixture after: near half 0.523 mean / 0.647 p95, night
 * dark_frac 0.001, and the pools read as water rather than smoke.
 */

import * as THREE from 'three';
import { attachDisposal, snapshotResources } from './lifecycle.js';
import { Reflector } from 'three/addons/objects/Reflector.js';
import { fbm2, noiseDataTexture } from './noise.js';
import { GLSL_UTIL, patchStandard, withRendererState } from './shader.js';

/**
 * The film's swell, shared verbatim by the mirror and the overlay.
 *
 * Both layers have to agree on it: the overlay's Fresnel decides HOW
 * MUCH reflection a pixel shows and the mirror decides WHAT it shows,
 * so two different noise fields would slide the highlight off the
 * wobble that made it. One string, included in both shaders.
 */
const WET_RIPPLE_GLSL = /* glsl */`
  // Slope of the film at a world XZ point. dist fades the amplitude
  // with range: this is a per-pixel noise with no mip chain, so left
  // at full strength across a 30 m floor the far half shimmers into
  // salt-and-pepper. Fading it is the LOD this effect can afford.
  vec2 wetRippleGrad(vec2 xz, float scale, float amp, float dist) {
    vec2 p = xz * scale;
    float h0 = astraFbm2(p, 2);
    float a = amp / (1.0 + dist * 0.08);
    return vec2(astraFbm2(p + vec2(0.3, 0.0), 2) - h0,
                astraFbm2(p + vec2(0.0, 0.3), 2) - h0) * a;
  }

  // Schlick for a water film. The exponent is 3, not the textbook 5:
  // a real film is a stack of slopes, not one plane, and every slope
  // that faces you admits more — measured at 5 a 30 m floor is dry to
  // its last two metres and the wet read is lost, at 4 the mid-ground
  // is a flat grey card, and at 3 the reflection comes in as a
  // gradient the way a photograph of wet ground does.
  float wetFresnel(vec3 n, vec3 v, float strength) {
    float ndv = clamp(dot(normalize(n), normalize(v)), 0.0, 1.0);
    return mix(1.0, 0.03 + 0.97 * pow(1.0 - ndv, 3.0), strength);
  }
`;

/**
 * Reflector shader with a ripple, a smear, fog and dithering. It
 * decides WHAT the film reflects; the overlay's Fresnel decides how
 * much of it survives. Everything in here is the SCENE's own radiance
 * — no authored water colour — so the floor re-grades itself with the
 * sky, the hour and the lights above it.
 */
const WET_REFLECTOR_SHADER = {
  name: 'WetReflectorShader',

  uniforms: THREE.UniformsUtils.merge([
    THREE.UniformsLib.fog,
    {
      color: { value: null },
      tDiffuse: { value: null },
      textureMatrix: { value: null },
      uBlur: { value: 0.010 },
      uRipple: { value: 1.0 },
      uRippleScale: { value: 4.0 },
    },
  ]),

  vertexShader: /* glsl */`
    uniform mat4 textureMatrix;
    varying vec4 vUv;
    varying vec3 vWorldPos;
    varying vec3 vWorldNormal;

    #include <common>
    #include <fog_pars_vertex>
    #include <logdepthbuf_pars_vertex>

    void main() {
      vUv = textureMatrix * vec4(position, 1.0);
      vec4 worldPosition = modelMatrix * vec4(position, 1.0);
      vWorldPos = worldPosition.xyz;
      vWorldNormal = inverseTransformDirection(normalize(normalMatrix * normal), viewMatrix);
      vec4 mvPosition = viewMatrix * worldPosition;
      gl_Position = projectionMatrix * mvPosition;
      #include <logdepthbuf_vertex>
      #include <fog_vertex>
    }`,

  fragmentShader: /* glsl */`
    uniform vec3 color;
    uniform sampler2D tDiffuse;
    uniform float uBlur;
    uniform float uRipple;
    uniform float uRippleScale;
    varying vec4 vUv;
    varying vec3 vWorldPos;
    varying vec3 vWorldNormal;

    #include <common>
    #include <logdepthbuf_pars_fragment>
    #include <fog_pars_fragment>
    #include <dithering_pars_fragment>
` + GLSL_UTIL + WET_RIPPLE_GLSL + /* glsl */`

    float wetBlendOverlay(float base, float blend) {
      return base < 0.5 ? (2.0 * base * blend)
                        : (1.0 - 2.0 * (1.0 - base) * (1.0 - blend));
    }

    vec3 wetBlendOverlay(vec3 base, vec3 blend) {
      return vec3(wetBlendOverlay(base.r, blend.r),
                  wetBlendOverlay(base.g, blend.g),
                  wetBlendOverlay(base.b, blend.b));
    }

    // texture2DProj divides by w, so an offset in normalised projected
    // space has to be pre-multiplied by w or the smear length swings
    // with depth across the plane.
    vec3 wetTap(vec2 o) {
      return texture2DProj(
          tDiffuse, vUv + vec4(o * vUv.w, 0.0, 0.0)).rgb;
    }

    void main() {
      #include <logdepthbuf_fragment>

      vec3 V = normalize(cameraPosition - vWorldPos);
      float ndv = clamp(dot(normalize(vWorldNormal), V), 0.0, 1.0);

      // The film is never a plane, and its swell slides what it
      // reflects. HOW MUCH of this layer survives is the overlay's job
      // (Fresnel there composites once; doing it here as well squared
      // it and put the night plaza at dark_frac 0.14) — this layer only
      // decides what the reflection looks like.
      vec2 grad = wetRippleGrad(vWorldPos.xz, uRippleScale, uRipple,
                                distance(cameraPosition, vWorldPos));
      // A short vertical smear on top: a wet surface is never a clean
      // mirror, and it is also what hides the 512 px RTT's
      // stair-stepping on every reflected edge.
      float smear = uBlur * (0.35 + 0.65 * (1.0 - ndv));
      vec2 o = grad * 0.12;
      vec3 refl = wetTap(o + vec2(0.0, -2.0 * smear)) * 0.12
                + wetTap(o + vec2(0.0, -smear)) * 0.22
                + wetTap(o) * 0.32
                + wetTap(o + vec2(0.0, smear)) * 0.22
                + wetTap(o + vec2(0.0, 2.0 * smear)) * 0.12;

      gl_FragColor = vec4(wetBlendOverlay(refl, color), 1.0);

      #include <tonemapping_fragment>
      #include <colorspace_fragment>
      #include <fog_fragment>
      #include <dithering_fragment>
    }`,
};

/**
 * Make the overlay's cover view-dependent — the change that turns the
 * pair into wet ground rather than a sheet of glass with grit on it.
 *
 * A water film is not a constant blend. Looking DOWN at it you see
 * through to the substrate (3% reflected) and looking ALONG it you see
 * nothing but reflection. So the authored `overlayOpacity` (times the
 * puddle mask) is the GRAZING limit, and the cover rises towards fully
 * dry as the view steepens: `a = 1 - F * (1 - a0)`.
 *
 * Two consequences worth stating, both measured on our host:
 * - It composites ONCE. Attenuating the mirror layer as well squared
 *   the falloff and dropped the night plaza to dark_frac 0.14 — a
 *   black hole where the near floor should show lit wet asphalt.
 * - `fresnel: 0` reproduces the pre-port constant blend exactly, which
 *   is what a polished indoor floor under its own lighting wants.
 *
 * `alphamap_fragment` runs after this hook and multiplies the mask in
 * again, so the mask is divided back out here; that is why the patch
 * samples the map itself instead of reading `diffuseColor.a`.
 */
function patchWetFilm(material, opts) {
  return patchStandard(material, {
    name: 'WetGroundFilm',
    uniforms: {
      uWgFresnel: { value: opts.fresnel },
      uWgRipple: { value: opts.ripple },
      uWgRippleScale: { value: opts.rippleScale },
    },
    vertexHead: 'varying vec3 vWgWorldPos;',
    vertexBody: 'vWgWorldPos = (modelMatrix * vec4(transformed, 1.0)).xyz;',
    fragmentHead: [
      'uniform float uWgFresnel;',
      'uniform float uWgRipple;',
      'uniform float uWgRippleScale;',
      'varying vec3 vWgWorldPos;',
      WET_RIPPLE_GLSL,
    ].join('\n'),
    fragmentBody: [
      'float wgMask = 1.0;',
      '#ifdef USE_ALPHAMAP',
      '  wgMask = texture2D(alphaMap, vAlphaMapUv).g;',
      '#endif',
      // The factory lays this plane flat, so the film's normal is world
      // up tilted by the swell — and the same swell the mirror slides
      // its reflection by, so highlight and wobble stay married.
      'vec3 wgV = cameraPosition - vWgWorldPos;',
      'vec2 wgGrad = wetRippleGrad(vWgWorldPos.xz, uWgRippleScale,',
      '                            uWgRipple, length(wgV));',
      'vec3 wgN = normalize(vec3(-wgGrad.x, 1.0, -wgGrad.y));',
      'float wgF = wetFresnel(wgN, wgV, uWgFresnel);',
      'float wgA0 = diffuseColor.a * wgMask;',
      'diffuseColor.a = (1.0 - wgF * (1.0 - wgA0)) / max(wgMask, 0.001);',
    ].join('\n'),
  });
}

/** The damp field: one seeded fBm all three overlay maps read, so a
 * pool is darker, smoother AND more mirror in the same place. 0 dry,
 * 1 pooled. */
function dampField(u, v, seed) {
  const n = fbm2(u * 4, v * 4, { seed: seed });
  // The shipped band was 0.24 wide, which over a 30 m floor put the
  // whole shoreline in a 6 m gradient: the pools read as smoke, not
  // water. 0.11 still leaves 2-3 texels of softness at 256 px.
  const t = (n + 0.02) / 0.11;
  const k = Math.min(1, Math.max(0, t));
  return 1 - k * k * (3 - 2 * k);
}

/**
 * A mirror-reflective floor with a rough overlay that sets its finish:
 * low overlay opacity reads as polished marble, 0.7+ with `puddleMask`
 * as damp asphalt where only puddles hold the mirror.
 *
 * @param {number} w Floor width (X) in metres.
 * @param {number} d Floor depth (Z) in metres.
 * @param {object} [opts] `rttSize` (default 512) reflection target
 *   resolution; `color` (default 0x6f767c) tints/darkens the mirror
 *   like damp ground — an overlay blend, so a value under 0.5 grey
 *   darkens and one over it brightens; `overlayOpacity` (default 0.65,
 *   use 0.7-0.8 for asphalt, 0.4-0.55 for gloss marble); `overlayColor`
 *   (default 0x4a4642, the graded wet-asphalt tone); `puddleMask`
 *   (default false) breaks the overlay with a seeded fBm alpha mask so
 *   the mirror shows only in puddle pools; `seed` (default 1) shapes
 *   the puddles; `fresnel` (default 1, 0..1) how view-dependent the
 *   film is — the authored opacity becomes the GRAZING limit and the
 *   cover closes up as you look straight down, and 0 restores the flat
 *   blend a polished indoor floor wants; `ripple` (default 1.0) slope
 *   of the film's swell, which both slides the reflection and tilts
 *   the Fresnel that admits it — 0 for still water; `rippleScale`
 *   (default 4.0) ripples per metre; `detail` (default 1) scales the
 *   overlay's colour, damp and roughness break-up, 0 for a flat sheet.
 * @returns {THREE.Group} Group named `MirrorFloor`, resting at y = 0.
 */
export function makeMirrorFloor(w, d, opts = {}) {
  const rttSize = opts.rttSize === undefined ? 512 : opts.rttSize;
  // 0x889199 shipped as the default and it is 0.53 grey: blendOverlay
  // BRIGHTENS with it, so "tints/darkens like damp ground" did the
  // opposite. 0x6f767c is the same cool damp hue below the 0.5 pivot.
  const color = opts.color === undefined ? 0x6f767c : opts.color;
  const overlayOpacity =
      opts.overlayOpacity === undefined ? 0.65 : opts.overlayOpacity;
  const overlayColor =
      opts.overlayColor === undefined ? 0x4a4642 : opts.overlayColor;
  const seed = opts.seed === undefined ? 1 : opts.seed;
  const fresnel = opts.fresnel === undefined ? 1 : opts.fresnel;
  const ripple = opts.ripple === undefined ? 1.0 : opts.ripple;
  const rippleScale =
      opts.rippleScale === undefined ? 4.0 : opts.rippleScale;
  const detail = opts.detail === undefined ? 1 : opts.detail;

  const group = new THREE.Group();
  group.name = 'MirrorFloor';

  const mirror = new Reflector(new THREE.PlaneGeometry(w, d), {
    clipBias: 0.003,
    // MSAA on a 512 px reflection under a rough overlay is
    // invisible and measured 39 ms a frame on software GL.
    multisample: 0,
    textureWidth: rttSize,
    textureHeight: rttSize,
    color: color,
    shader: WET_REFLECTOR_SHADER,
  });
  mirror.rotation.x = -Math.PI / 2;
  mirror.name = 'MirrorSurface';
  // BOTH uniforms, or the mirror's swell and the overlay's Fresnel
  // stop describing the same water.
  mirror.material.uniforms.uRipple.value = ripple;
  mirror.material.uniforms.uRippleScale.value = rippleScale;
  // A ShaderMaterial opts OUT of both by default: without these the
  // mirror is the one surface in the scene that neither fogs nor
  // dithers, and a 20 m sky reflection is exactly the smooth gradient
  // that bands in 8 bits.
  mirror.material.fog = true;
  mirror.material.dithering = true;
  group.add(mirror);

  // The addon extracts a rotation from its world matrix, which does not
  // transform a plane normal correctly under nonuniform scale or shear.
  // Capture in a rigid frame, then map its local projection back to the
  // actual affine geometry used by the ordinary draw.
  const capture = mirror.onBeforeRender;
  const captureMaterial = mirror.material;
  const actualWorld = new THREE.Matrix4();
  const rigidWorld = new THREE.Matrix4();
  const inverseRigid = new THREE.Matrix4();
  const normalMatrix = new THREE.Matrix3();
  const worldNormal = new THREE.Vector3();
  const worldPoint = new THREE.Vector3();
  const cameraPoint = new THREE.Vector3();
  const rotation = new THREE.Quaternion();
  const axis = new THREE.Vector3(0, 0, 1);
  let capturing = false;
  mirror.onBeforeRender = function (renderer, scene, camera, geometry, material) {
    if (scene.overrideMaterial || capturing || this.material !== captureMaterial
        || (material && material !== captureMaterial)) return;
    actualWorld.copy(this.matrixWorld);
    normalMatrix.getNormalMatrix(actualWorld);
    worldNormal.copy(axis).applyMatrix3(normalMatrix).normalize();
    if (worldNormal.lengthSq() < 0.5) return;
    worldPoint.setFromMatrixPosition(actualWorld);
    cameraPoint.setFromMatrixPosition(camera.matrixWorld);
    if (cameraPoint.sub(worldPoint).dot(worldNormal) < 0 && !this.forceUpdate) return;
    rotation.setFromUnitVectors(axis, worldNormal);
    rigidWorld.makeRotationFromQuaternion(rotation).setPosition(worldPoint);
    const autoUpdate = this.matrixWorldAutoUpdate;
    const needsUpdate = this.matrixWorldNeedsUpdate;
    const visible = this.visible;
    // Nested scene updates must not propagate the temporary rigid frame
    // into caller-owned descendants (including cameras and bones).
    const descendants = [];
    for (const child of this.children) child.traverse((node) => {
      descendants.push([node, node.matrixWorldAutoUpdate, node.matrixWorldNeedsUpdate]);
      node.matrixWorldAutoUpdate = false;
    });
    capturing = true;
    this.matrixWorldAutoUpdate = false;
    this.matrixWorld.copy(rigidWorld);
    try {
      withRendererState(renderer, () => {
        capture.call(this, renderer, scene, camera);
        inverseRigid.copy(rigidWorld).invert();
        captureMaterial.uniforms.textureMatrix.value
          .multiply(inverseRigid).multiply(actualWorld);
      });
    } finally {
      this.matrixWorld.copy(actualWorld);
      this.matrixWorldAutoUpdate = autoUpdate;
      this.matrixWorldNeedsUpdate = needsUpdate;
      for (const [node, childAutoUpdate, childNeedsUpdate] of descendants) {
        node.matrixWorldAutoUpdate = childAutoUpdate;
        node.matrixWorldNeedsUpdate = childNeedsUpdate;
      }
      this.visible = visible;
      capturing = false;
    }
  };

  const overlayMat = new THREE.MeshStandardMaterial({
    color: overlayColor,
    roughness: 1,
    metalness: 0,
    transparent: true,
    opacity: overlayOpacity,
  });
  if (opts.puddleMask) {
    // alphaMap: bright = dry overlay, dark = puddle showing the mirror.
    // Smoothstep gives pooled shapes with soft shorelines; the 0.15
    // floor keeps a thin damp film even inside puddles.
    overlayMat.alphaMap = noiseDataTexture(256, (u, v) => (
      0.15 + 0.85 * (1 - dampField(u, v, seed))
    ));
  }
  if (detail > 0) {
    // One flat albedo over 600 m2 is the tell of a fake floor. Ground
    // carries broad warm/cool patches (grit, oil, old repairs) and the
    // damp field darkens what it wets, so the map is the two together.
    overlayMat.map = noiseDataTexture(256, (u, v) => {
      const h = fbm2(u * 3 + 17, v * 3 - 9, { seed: seed + 11 }) * detail;
      const b = fbm2(u * 1.7 - 5, v * 1.7 + 3, { seed: seed + 41 }) * detail;
      const g = fbm2(u * 13, v * 13, { seed: seed + 29 }) * detail;
      const wet = dampField(u, v, seed) * detail;
      // Water fills the pores and traps the light: a wet patch of any
      // ground is about half the albedo of the dry patch beside it.
      // Looking straight DOWN that darkening is the only thing that
      // says "puddle" — Fresnel has admitted almost no reflection at
      // that angle, so an albedo field this strong is not decoration,
      // it is the whole near-field read. The 1.22 divides the peak
      // back to 1, which makes `overlayColor` the driest crest rather
      // than the average.
      const l = (1 - 0.50 * wet) * (1 + 0.24 * b) * (1 + 0.18 * g) / 1.22;
      // Warm where it is dry, cool where the water sits — the swing a
      // photograph of wet asphalt actually carries, and the reason
      // this floor does not read as one die-cut grey. Not a hue
      // rotation: red up and blue down about a fixed green, so the
      // tone stays put while the cast moves.
      const c = 0.55 * h - 0.30 * (wet - 0.5);
      return [l * (1 + c), l * (1 + 0.05 * c), l * (1 - c)];
    });
    // Base roughness stays 1 (the material contract); the map only
    // takes it DOWN, and only a little. The SMOOTH thing here is the
    // water surface, which is the mirror layer — dropping the
    // substrate to 0.45 as well double-counts the film's specular and
    // measured +20/255 of flat sky sheen over the whole plaza, which
    // is what turned it into a light grey card.
    overlayMat.roughnessMap = noiseDataTexture(256, (u, v) => {
      const wet = dampField(u, v, seed) * detail;
      const g = fbm2(u * 13, v * 13, { seed: seed + 29 }) * detail;
      return Math.max(0.05, 0.97 - 0.18 * wet + 0.06 * g);
    });
  }
  patchWetFilm(overlayMat, { fresnel, ripple, rippleScale });
  const overlay = new THREE.Mesh(new THREE.PlaneGeometry(w, d), overlayMat);
  overlay.rotation.x = -Math.PI / 2;
  overlay.position.y = 0.002;
  overlay.receiveShadow = true;
  overlay.name = 'GroundOverlay';
  group.add(overlay);

  const owned = snapshotResources(group);
  owned.add(mirror.getRenderTarget());
  for (const texture of [overlayMat.map, overlayMat.alphaMap, overlayMat.roughnessMap]) {
    if (texture) owned.add(texture);
  }
  return attachDisposal(group, owned);
}

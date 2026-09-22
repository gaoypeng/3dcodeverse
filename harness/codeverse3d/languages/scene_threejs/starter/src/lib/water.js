/**
 * Reflective water plane: the addon Water shader with a procedural
 * wave-normal map (npm three ships no waternormals.jpg, so
 * `makeWaterNormals()` synthesizes a deterministic tiling one).
 * Every Water instance re-renders the WHOLE scene per frame:
 * contract is ONE water plane, never alongside a Reflector floor.
 * Stills freeze `uniforms.time`; animate via `mesh.userData.update(t)`.
 *
 * The addon's fragment shader is REGRADED here (see `_regrade`): its
 * stock look is a 30%-reflective milk with a flat +0.1 veil, which on
 * our pipeline (ACES, exposure 1.0, no post chain) renders a pool as
 * pale plastic — measured 2026-09-01, a still pool read mean_lum 0.73
 * at BOTH a steep and a grazing view, i.e. no Fresnel at all.
 */

import * as THREE from 'three';
import { Water } from 'three/addons/objects/Water.js';

// Battery-graded freeze point: mid-phase, waves clearly formed.
const FROZEN_TIME = 7.3;

// Default sun matches the day rig in lib/environment.js (azimuth 35,
// elevation 48, same axis convention), so a scene that forgot to pass
// sunDir still gets specular broadly agreeing with the shipped light.
const _AZ = 35 * Math.PI / 180;
const _EL = 48 * Math.PI / 180;
const _DAY_SUN = new THREE.Vector3(
    Math.cos(_EL) * Math.cos(_AZ), Math.sin(_EL),
    Math.cos(_EL) * Math.sin(_AZ));

let _oceans = 0;

/**
 * Synthesize the tiling wave-normal DataTexture the Water shader
 * needs (npm three ships no waternormals.jpg). A sum of integer-
 * frequency sines guarantees a seamless wrap; finite differences at
 * strength 2.2 turn the heightfield into tangent-space normals.
 *
 * @param {number} [size] Texture width and height in px (default 256).
 * @returns {THREE.DataTexture} RGBA, RepeatWrapping, needsUpdate set.
 */
export function makeWaterNormals(size = 256, opts = {}) {
  // Tiling heightfield: sum of integer-frequency sines.
  const h = new Float32Array(size * size);
  // Eight pure tones read as CORDUROY at a grazing angle — it is
  // the periodicity of the SET that shows, not any one wave. The
  // broadband set spreads 28 directions at 1/f^2 and kills it, at
  // the cost of a calmer surface; only wide water needs it.
  const waves = opts.broadband ? [
    [1, 1, 1.3000, 5.34], [0, -5, 0.1040, 3.33],
    [1, -1, 1.3000, 3.01], [4, -1, 0.1529, 4.21],
    [2, -4, 0.1300, 1.12], [-3, -1, 0.2600, 2.37],
    [1, -5, 0.1000, 3.75], [4, 4, 0.0812, 4.83],
    [-2, 2, 0.3250, 1.58], [-6, 3, 0.0578, 6.14],
    [-3, -5, 0.0765, 4.67], [-2, 1, 0.5200, 3.62],
    [4, -5, 0.0634, 5.34], [-6, -6, 0.0361, 5.28],
    [-3, 0, 0.2889, 4.61], [-5, 2, 0.0897, 0.28],
    [-1, 2, 0.5200, 6.18], [-1, 0, 2.6000, 2.22],
    [-1, 3, 0.2600, 1.89], [1, -2, 0.5200, 5.29],
    [-4, -5, 0.0634, 3.96], [2, -5, 0.0897, 2.32],
    [-2, 6, 0.0650, 5.36], [2, -1, 0.5200, 5.77],
    [-2, 0, 0.6500, 0.05], [5, -3, 0.0765, 2.56],
    [-5, -5, 0.0520, 4.68], [1, 0, 2.6000, 0.65]
  ] : [
    [3, 1, 1.0, 0.0], [5, 2, 0.7, 1.3], [2, 4, 0.55, 2.1],
    [7, 3, 0.4, 4.2], [4, 6, 0.35, 0.7], [9, 5, 0.25, 3.3],
    [11, 8, 0.18, 5.1], [6, 10, 0.15, 2.6],
  ];
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const u = (x / size) * Math.PI * 2;
      const v = (y / size) * Math.PI * 2;
      let s = 0;
      for (const [fx, fy, a, p] of waves) {
        s += a * Math.sin(fx * u + fy * v + p);
      }
      h[y * size + x] = s;
    }
  }
  // Finite-difference normals -> RGB.
  const data = new Uint8Array(size * size * 4);
  const amp = 2.2; // normal strength (the battery-graded value)
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const xm = (x - 1 + size) % size, xp = (x + 1) % size;
      const ym = (y - 1 + size) % size, yp = (y + 1) % size;
      const dx = (h[y * size + xp] - h[y * size + xm]) * amp;
      const dy = (h[yp * size + x] - h[ym * size + x]) * amp;
      const inv = 1 / Math.hypot(dx, dy, 1);
      const i = (y * size + x) * 4;
      data[i] = (-dx * inv * 0.5 + 0.5) * 255;
      data[i + 1] = (-dy * inv * 0.5 + 0.5) * 255;
      data[i + 2] = (inv * 0.5 + 0.5) * 255;
      data[i + 3] = 255;
    }
  }
  const tex = new THREE.DataTexture(data, size, size, THREE.RGBAFormat);
  tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
  // DataTexture defaults to NearestFilter with no mipmaps, which prints
  // a fine dither over water seen from any distance. Only visible once
  // the wave size stopped moireing over the top of it.
  tex.minFilter = THREE.LinearMipmapLinearFilter;
  tex.magFilter = THREE.LinearFilter;
  tex.generateMipmaps = true;
  tex.anisotropy = 16;
  tex.needsUpdate = true;
  return tex;
}

// The regraded tail of the addon's fragment shader, spliced in over the
// range `vec2 distortion = ...` .. `gl_FragColor = ...` (see `_regrade`).
const _TAIL = /* glsl */`
	// ---- 3dcode regrade ------------------------------------------------
	// The addon scales its screen-space reflection offset by 1/distance
	// with no ceiling, so water 3 m from the eye samples the mirror ONE
	// FULL FRAME away (0.33 * distortionScale) and prints slivers of sky
	// through the near surface. Cap the offset at the value the far half
	// already uses; beyond ~1/maxDistort metres nothing changes at all.
	vec2 distortion = surfaceNormal.xz
		* min( maxDistort, 0.001 + 1.0 / distance ) * distortionScale;
	vec3 reflectionSample = vec3( texture2D( mirrorSampler,
		mirrorCoord.xy / mirrorCoord.w + distortion ) );

	// Fresnel: the addon hardcodes rf0 = 0.3, so its water is 30%
	// mirror even looked straight down into — the single reason a stock
	// Water pool reads as pale milk. Dielectric water is F0 = 0.02 and
	// only turns mirror at grazing, which is what gives a body of water
	// its dark near half and bright far half.
	float theta = max( dot( eyeDirection, surfaceNormal ), 0.0 );
	float reflectance = rf0 + ( 1.0 - rf0 ) * pow( 1.0 - theta, 5.0 );

	// A body colour with a HUE FIELD, not one flat value: a slow read of
	// the same wave map drifts the mix between the deep tone and a
	// shallower, greener one. The period is turbScale METRES (set from
	// the plane's extent), not a constant — pinned to one, a pond samples
	// a single texel of the field and the whole basin comes out one
	// colour, which is the flatness this term exists to break.
	vec3 turbSample = texture2D( normalSampler,
		worldPosition.xz / turbScale
		+ vec2( time / 190.0, time / 230.0 ) ).rgb;
	float turbid = clamp( 0.5 + ( turbSample.x - 0.5 ) * 1.3, 0.0, 1.0 );
	float wMag = max( max( waterColor.r, waterColor.g ), waterColor.b );
	// The deep tone gets a dark NEUTRAL floor. The graded 0x0e3f5c has a
	// red channel of 0.0045 linear — 4% of its blue — and a body with one
	// dead channel reads electric rather than deep (measured at night:
	// mean RGB 0.034/0.140/0.276, saturation 0.85). Real water always
	// carries some neutral scatter, and the floor is still under the
	// 0.02 the aesthetic brief calls the bottom of a sane albedo.
	vec3 body = mix( max( waterColor * 0.62, vec3( 0.015, 0.022, 0.030 ) ),
		waterColor * 1.9 + vec3( 0.05, 0.30, 0.16 ) * wMag, turbid );

	// Lit as the dielectric it is: sky ambient so water in shadow is
	// never black, plus the key light's real irradiance, shadowed by the
	// scene. Both come from the scene itself (see makeOcean's sniff).
	float shade = getShadowMask();
	float lambert = max( dot( sunDirection, surfaceNormal ), 0.0 );
	vec3 bodyLit = body * ( ambientColor + keyColor * lambert * shade );
	// Backlit crests glow — the one cue that reads as TRANSLUCENT, and
	// it only fires when the eye is looking into the key light.
	float back = pow( max( 0.0, dot( eyeDirection, -sunDirection ) ), 3.0 );
	bodyLit += body * keyColor * back * 0.55 * shade;

	// Glitter is ADDITIVE. The addon gates its whole specular behind
	// the mirror sample, so a sun track can only appear where the
	// mirror was already bright — i.e. never on dark water, which is
	// exactly where a sun track is the brightest thing in a frame.
	// Two lobes: a tight glint on the crests, a broad sheen holding them
	// together. Peak ~2.6, inside the 1.5-4 a bloom pass takes cleanly.
	vec3 sunReflect = normalize( reflect( -sunDirection, surfaceNormal ) );
	float glint = max( 0.0, dot( eyeDirection, sunReflect ) );
	vec3 glitter = sunColor * glitterScale * shade
		* ( 0.3 + 0.7 * reflectance )
		* ( pow( glint, 420.0 ) * 2.6 + pow( glint, 28.0 ) * 0.22 );

	vec3 outgoingLight =
		mix( bodyLit, reflectionSample, reflectance ) + glitter;
	// A mirror this smooth bands in 8 bits; a sub-LSB of hash noise in
	// linear costs one mad and removes it.
	float dth = fract( sin( dot( gl_FragCoord.xy,
		vec2( 12.9898, 78.233 ) ) ) * 43758.5453 );
	outgoingLight += ( dth - 0.5 ) * 0.0022;
	gl_FragColor = vec4( outgoingLight, alpha );`;

/**
 * Splice `_TAIL` over the addon shader's own shading tail and declare the
 * uniforms it adds. Version-tolerant: if either anchor is missing (a
 * three release rewrote Water.js) the material is left exactly as the
 * addon built it and the caller gets a warning, never a broken shader.
 *
 * @param {THREE.ShaderMaterial} material The Water material.
 * @returns {boolean} true when the regrade was applied.
 */
function _regrade(material) {
  const src = material.fragmentShader;
  const END = 'gl_FragColor = vec4( outgoingLight, alpha );';
  const a = src.indexOf('vec2 distortion =');
  const b = src.indexOf(END);
  if (a < 0 || b < 0 || b < a) {
    console.warn(
        'makeOcean: this three build\'s Water shader does not carry the '
        + 'anchors the 3dcode regrade splices over — shipping the addon '
        + 'look unchanged (pale, no Fresnel).');
    return false;
  }
  material.fragmentShader =
      src.slice(0, a).replace(
          'uniform vec3 waterColor;',
          'uniform vec3 waterColor;\n\t\t\t\tuniform vec3 ambientColor;'
          + '\n\t\t\t\tuniform vec3 keyColor;\n\t\t\t\tuniform float rf0;'
          + '\n\t\t\t\tuniform float glitterScale;'
          + '\n\t\t\t\tuniform float maxDistort;'
          + '\n\t\t\t\tuniform float turbScale;')
      + _TAIL + src.slice(b + END.length);
  return true;
}

/**
 * Read the scene's own lighting once, at the first render, and point the
 * water's key light at it. Water is built before the scene exists, so
 * without this a night pool glitters for a noon sun in a colour nothing
 * else in the frame is lit by — the failure the aesthetic brief calls
 * "hardcoded light colours".
 *
 * Only uniforms the caller did NOT pin are touched.
 */
const _V1 = new THREE.Vector3();
const _V2 = new THREE.Vector3();

function _readScene(scene, uniforms, pinned) {
  let key = null, keyLum = -1, hemi = null, amb = null;
  scene.traverse((o) => {
    if (!o.visible) return;
    if (o.isDirectionalLight) {
      const c = o.color;
      const l = o.intensity
          * (c.r * 0.2126 + c.g * 0.7152 + c.b * 0.0722);
      if (l > keyLum) { keyLum = l; key = o; }
    } else if (o.isHemisphereLight) {
      if (!hemi || o.intensity > hemi.intensity) hemi = o;
    } else if (o.isAmbientLight) {
      if (!amb || o.intensity > amb.intensity) amb = o;
    }
  });
  // three's Lambert BRDF divides irradiance by PI; the water body is a
  // Lambert term too, so the same 1/PI is what puts it on the same scale
  // as every MeshStandardMaterial around it.
  const INV_PI = 1 / Math.PI;
  if (key) {
    uniforms.keyColor.value.copy(key.color)
        .multiplyScalar(Math.min(6, key.intensity) * INV_PI);
    if (!pinned.sunColor) {
      // HUE only: the glitter lobes are graded artistic values (a sun
      // disc we do not model), so they must not be scaled by whatever
      // watts the rig runs at — only tinted by its colour.
      const c = key.color;
      const peak = Math.max(c.r, c.g, c.b, 1e-4);
      uniforms.sunColor.value.copy(c).multiplyScalar(1 / peak);
    }
    if (!pinned.sunDir) {
      // A directional light aims from its world position at its TARGET,
      // and a rig that moved the target (a low sun tracked onto a
      // subject) would otherwise hand the glitter a direction the
      // shadows do not use. An un-parented target sits at the origin,
      // which is exactly what `position` alone assumes.
      const p = _V1.setFromMatrixPosition(key.matrixWorld);
      const q = key.target
          ? _V2.setFromMatrixPosition(key.target.matrixWorld)
          : _V2.set(0, 0, 0);
      p.sub(q);
      if (p.lengthSq() > 1e-12) uniforms.sunDirection.value.copy(p).normalize();
    }
  }
  if (!pinned.ambient) {
    const t = uniforms.ambientColor.value;
    if (hemi) t.copy(hemi.color).multiplyScalar(hemi.intensity * INV_PI);
    else if (amb) t.copy(amb.color).multiplyScalar(amb.intensity * INV_PI);
    else if (scene.fog) t.copy(scene.fog.color).multiplyScalar(0.35);
    t.r = Math.min(t.r, 1); t.g = Math.min(t.g, 1); t.b = Math.min(t.b, 1);
  }
}

/**
 * Build the scene's ONE reflective water plane (XZ, faces +Y).
 * Returned mesh is at y = 0; set `position.y` to the water level.
 * Defaults are graded values — keep them unless the brief says not to.
 *
 * @param {number} width X extent in metres.
 * @param {number} depth Z extent in metres.
 * @param {object} [opts]
 *   `sunDir` normalized THREE.Vector3 toward the sun — pass the SAME
 *   direction the key light uses so the glitter track agrees with the
 *   shadows (default: the day rig's axis);
 *   `waterColor` hex (default 0x0e3f5c);
 *   `sunColor` hex (default 0xffffff);
 *   `distortionScale` reflection wobble (default 2.8);
 *   `rttSize` reflection render-target px (default 256);
 *   `size` wave-pattern density uniform (default: scaled to the plane);
 *   `waterNormals` texture override (default `makeWaterNormals(256)`);
 *   `fog` fold scene fog into the shader (default true);
 *   `rf0` Fresnel reflectance head-on (default 0.02, real water);
 *   `glitter` sun-track strength, 0 kills it (default 1);
 *   `ambient` hex sky ambient for the water BODY (default: read off the
 *   scene's hemisphere/ambient light, else its fog).
 * @returns {THREE.Mesh} The Water mesh, rotated flat, named 'Ocean',
 *   with `userData.update(t)` driving the wave phase for `tick`.
 */
/** Water wider than this gets the broadband (28-direction) wave set. */
export const BROADBAND_EXTENT_M = 300;

export function makeOcean(width, depth, opts = {}) {
  _oceans++;
  if (_oceans > 1) {
    console.warn(
        'makeOcean: ' + _oceans + ' oceans in one scene — each one ' +
        're-renders the whole scene per frame. Contract is ONE RTT ' +
        'surface (one ocean, never alongside a Reflector).');
  }
  const sunDir =
      (opts.sunDir ? opts.sunDir.clone() : _DAY_SUN.clone()).normalize();
  // 512, not 256: a 256px mirror stretched across half a 1280x720 frame
  // tears into stair-stepped blocks at grazing angles — 9 confirmed
  // sightings across the 2026-08-04 gallery audit. Cost is one extra
  // scene pass at 4x the pixels, still the cheapest thing in the frame.
  const rtt = opts.rttSize || 512;
  const _extent = Math.max(width, depth);
  const water = new Water(new THREE.PlaneGeometry(width, depth), {
    textureWidth: rtt,
    textureHeight: rtt,
    waterNormals: opts.waterNormals
        // 300 m, not 1500: a 380 m and a 440 m storm sea (two lighthouse runs,
        // 2026-09-09) read as "a checkerboard of white crescents" from every
        // overview and the establishing shot — the eight-tone set's own period
        // shows wherever the water is wide enough to be seen at a grazing angle,
        // and a scene's sea is exactly that.  Pools and ponds keep the lively set.
        || makeWaterNormals(256, { broadband: _extent > BROADBAND_EXTENT_M }),
    sunDirection: sunDir,
    sunColor: opts.sunColor === undefined ? 0xffffff : opts.sunColor,
    waterColor:
        opts.waterColor === undefined ? 0x0e3f5c : opts.waterColor,
    distortionScale: opts.distortionScale === undefined ?
        2.8 : opts.distortionScale,
    fog: opts.fog === undefined ? true : opts.fog,
  });
  water.rotation.x = -Math.PI / 2;
  water.name = 'Ocean';
  const uniforms = water.material.uniforms;
  uniforms.time.value = FROZEN_TIME;
  // The shader samples the normals at worldPosition.xz * size / 103, so
  // one wave period is ~103/size metres. Hold that at a twelfth of the
  // plane and the graded 6 falls out at 200 m while a 2800 m harbor gets
  // 0.44 — measured as the largest value that does not moire into radial
  // stripes at grazing angles (0.70 stripes, 1.2 is unusable).
  //
  // The law used to be capped at 6, which froze it for anything under
  // 206 m and gave an 18 m pool ONE wave period across the whole basin —
  // an ocean swell in a bathtub (measured 2026-09-01: ripple contrast
  // 1.22 at size 6, 2.51 at 30). The cap is now 30, the largest value
  // that stays clean at a 1024 px grazing close-up; 68 speckles the far
  // band into per-pixel sparkle. Big water is untouched.
  const _autoSize = Math.min(30, 1236 / Math.max(_extent, 1));
  uniforms.size.value = opts.size === undefined ? _autoSize : opts.size;
  water.userData.update = (t) => {
    uniforms.time.value = FROZEN_TIME + t;
  };

  // ---- the regrade: our pipeline, and the scene's own light ---------
  if (_regrade(water.material)) {
    uniforms.rf0 = { value: opts.rf0 === undefined ? 0.02 : opts.rf0 };
    uniforms.glitterScale =
        { value: opts.glitter === undefined ? 1 : opts.glitter };
    uniforms.maxDistort =
        { value: opts.maxDistort === undefined ? 0.05 : opts.maxDistort };
    // Two-and-a-bit patches of turbidity across the plane, never finer
    // than 8 m: any tighter and the colour field starts competing with
    // the waves instead of sitting under them.
    uniforms.turbScale = { value: Math.max(8, _extent / 2.5) };
    uniforms.keyColor = { value: new THREE.Color(1, 1, 1) };
    // Fallback ambient ≈ the day rig's hemisphere at 1/PI, used only
    // until `_readScene` finds the scene's real one on the first frame.
    uniforms.ambientColor = {
      value: opts.ambient === undefined
          ? new THREE.Color(0.149, 0.215, 0.364)
          : new THREE.Color(opts.ambient),
    };
    const pinned = {
      sunColor: opts.sunColor !== undefined,
      sunDir: opts.sunDir !== undefined,
      ambient: opts.ambient !== undefined,
    };
    const base = water.onBeforeRender;
    let first = true;
    water.onBeforeRender = function (renderer, scene, camera) {
      if (first) {
        first = false;
        _readScene(scene, uniforms, pinned);
        // The mirror target is 8-bit LINEAR (three disables tone mapping
        // and sRGB encoding when rendering to a non-sRGB target), so the
        // bottom of its range quantises to a handful of steps: a night
        // sky at ~0.002 linear reflects as two flat bands. Half-float
        // costs 2 bytes a texel on ONE 512px target and removes it.
        const mirror = uniforms.mirrorSampler.value;
        if (renderer.extensions.has('EXT_color_buffer_float')
            || renderer.extensions.has('EXT_color_buffer_half_float')) {
          mirror.type = THREE.HalfFloatType;
        }
      }
      base.call(this, renderer, scene, camera);
    };
  }
  return water;
}

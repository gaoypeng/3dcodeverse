// THE post chain for scene renders (scene_host.mjs; object renders stay raw —
// object judging was calibrated without it).  One EffectComposer:
//
//   RenderPass -> finite clamp -> GTAO (blend) -> glow bloom -> grade -> OutputPass
//
// Why each stage exists, all four measured on the effect-library showcase:
//
//   clamp   a specular spike past half-float range rides the blur mips and blanks
//           part of the frame's glow; only finite, non-negative colour continues.
//
//   GTAO    without contact darkening correct geometry reads as stickers.  It
//           MULTIPLIES the final image, so a surface lit only by the hemisphere
//           fill goes to zero at full strength — `ao` is the dial, and the
//           Poisson denoise is turned up from three's defaults because at a blend
//           that actually reads, the raw AO dots flat walls.
//
//   bloom   SELECTIVE, from an emissive mask — NOT a luminance bright-pass.
//           Measured 2026-09-01 on our renderer: every daylight scene tops out at
//           1.88 linear (the sky dome) with 38% of the frame above 0.9, while the
//           brightest authored emissive in the library is 1.23.  A global bright
//           pass therefore cannot separate "a neon sign" from "the sky": at the
//           reference's threshold (0.85) a daylit frame lost a QUARTER of its
//           saturation (0.313 -> 0.070) and read as fog soup.  So the bloom source
//           is a second, cheap render in which every material is replaced by its
//           EMISSION only — the sky, the sun-lit ground and diffuse albedo are
//           black there by construction, and brightness stops being the question.
//           A scene with nothing emissive pays nothing: the mask render is skipped.
//
//   grade   exposure trim, a real S-curve (anchored at 0 and 1, so it deepens the
//           mids without crushing blacks or blowing whites), saturation, white
//           balance, split-tone and a shadow lift.  IDENTITY unless the scene
//           asks: a scene sets `scene.userData.grade = {exposure, contrast, ...}`.
//
//   output  the renderer's own ACES + sRGB, unchanged from the raw path.
//
// MSAA: the renderer's `antialias` flag is DEAD once a composer renders into its
// own target, so the composer's target carries `samples` itself — otherwise every
// judged frame is 1-sample raw and reads as severe aliasing.
//
//   import { makePostChain, GRADE_DEFAULTS } from '/__runtime/lib/browser/post.js';

import * as THREE from 'three';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { ShaderPass } from 'three/addons/postprocessing/ShaderPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';
import { FullScreenQuad } from 'three/addons/postprocessing/Pass.js';
import { GTAOPass } from 'three/addons/postprocessing/GTAOPass.js';

/** Chain shape.  `ao: 0` drops the GTAO pass, `bloom: 0` drops the glow bloom. */
export const POST_DEFAULTS = Object.freeze({
  // 0.8 measured on the indoor showcase: corners, the wall/floor junction and the
  // column base gain contact shading for -0.004 mean luminance — the shadows are
  // shaped, not crushed.  At 0.45 it does not read; three's raw AO at this blend
  // dots a flat wall, which is what the denoise settings below are for.
  ao: 0.8,                  // GTAOPass.blendIntensity
  aoRadius: 1.6,            // world units: architectural scale, not a turntable prop
  aoDistanceExponent: 1.2,
  aoThickness: 1.0,
  aoSamples: 16,
  aoDenoiseRings: 3,        // raised from three's 2: kills the AO dots on flat walls
  aoDenoiseSamples: 16,     // ... and so does this (three's default is 8)
  // 0.15 measured on the neon showcase: the sign haloes and the light trails read
  // as light while the letters stay legible.  At 0.30 both bleach out.
  bloom: 0.15,              // how much of the blurred emission is added back
  bloomLevels: 5,           // blur pyramid depth = how WIDE the veil spreads
  bloomThreshold: 0.02,     // emission below this is not a light source, it is noise
  samples: 4,               // MSAA samples on the composer target
  clampMax: 64.0,           // finite-colour ceiling entering the chain
});

/**
 * Grade uniforms.  Every default is the IDENTITY — an ungraded scene renders
 * through the grade stage unchanged (`tests/scene_runtime/test_post.py` pins
 * that), so the only shift the chain introduces is AO + bloom.
 */
export const GRADE_DEFAULTS = Object.freeze({
  exposure: 1.0,        // linear multiplier, applied before the curve
  contrast: 1.0,        // 1 = off; S-curve strength around the mid tones
  saturation: 1.0,      // 1 = off
  warmth: 0.0,          // -1 cool (blue) .. +1 warm (amber), luminance preserving
  tint: 0.0,            // -1 green .. +1 magenta, luminance preserving
  shadowTint: [0, 0, 0],      // split-tone: multiplicative shift on the low tones
  highlightTint: [0, 0, 0],   // ... and on the high tones
  shadowLift: 0.0,      // additive floor under the darkest tones (kills pure black)
  vignette: 0.0,        // 0 = off .. 1 = strong corner falloff
});

const QUAD_VERT = /* glsl */`
  varying vec2 vUv;
  void main() {
    vUv = uv;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }`;

const GRADE_SHADER = {
  uniforms: {
    tDiffuse: { value: null },
    exposure: { value: 1.0 },
    contrast: { value: 1.0 },
    saturation: { value: 1.0 },
    warmth: { value: 0.0 },
    tint: { value: 0.0 },
    shadowTint: { value: new THREE.Vector3(0, 0, 0) },
    highlightTint: { value: new THREE.Vector3(0, 0, 0) },
    shadowLift: { value: 0.0 },
    vignette: { value: 0.0 },
  },
  vertexShader: QUAD_VERT,
  fragmentShader: /* glsl */`
    varying vec2 vUv;
    uniform sampler2D tDiffuse;
    uniform float exposure, contrast, saturation, warmth, tint, shadowLift, vignette;
    uniform vec3 shadowTint, highlightTint;

    const vec3 LUMA = vec3(0.2126, 0.7152, 0.0722);

    void main() {
      vec4 texel = texture2D(tDiffuse, vUv);
      vec3 c = max(texel.rgb, vec3(0.0));

      // white balance — channel gains renormalised by luma, so a warm or cool
      // push changes the hue and never the overall brightness
      if (warmth != 0.0 || tint != 0.0) {
        vec3 wb = vec3(1.0 + 0.20 * warmth + 0.05 * tint,
                       1.0 - 0.10 * tint,
                       1.0 - 0.20 * warmth + 0.05 * tint);
        wb /= max(dot(wb, LUMA), 1e-4);
        c *= wb;
      }

      c *= exposure;

      // S-CURVE: into a bounded tonal domain, smoothstep there (anchored at 0 and
      // 1 — blacks stay black, whites do not clip), back out.
      if (contrast != 1.0) {
        vec3 t = c / (c + vec3(1.0));
        vec3 s = t * t * (vec3(3.0) - 2.0 * t);
        t = clamp(mix(t, s, contrast - 1.0), vec3(0.0), vec3(0.999));
        c = t / (vec3(1.0) - t);
      }

      if (saturation != 1.0) c = mix(vec3(dot(c, LUMA)), c, saturation);

      // split-tone: shadows and highlights pull apart in hue.  Multiplicative, so
      // it tints what is there and never paints light into black.
      float l = dot(c, LUMA);
      float hi = smoothstep(0.15, 0.75, l / (l + 1.0));
      c *= vec3(1.0) + mix(shadowTint, highlightTint, hi);

      // lift: a dielectric ambient floor, strongest where the frame is darkest
      if (shadowLift > 0.0) c += shadowLift * (1.0 - smoothstep(0.0, 0.35, l));

      if (vignette > 0.0) {
        float r = length(vUv - 0.5) * 1.4142;
        c *= 1.0 - vignette * smoothstep(0.45, 1.0, r);
      }

      gl_FragColor = vec4(max(c, vec3(0.0)), texel.a);
    }`,
};

// Only FINITE, non-negative colour may continue down the chain.
const clampShader = (maxValue) => ({
  uniforms: { tDiffuse: { value: null }, maxValue: { value: maxValue } },
  vertexShader: QUAD_VERT,
  fragmentShader: /* glsl */`
    varying vec2 vUv;
    uniform sampler2D tDiffuse;
    uniform float maxValue;
    void main() {
      vec4 c = texture2D(tDiffuse, vUv);
      vec3 v = clamp(c.rgb, vec3(0.0), vec3(maxValue));
      // NaN never compares equal to itself
      if (v.r != v.r) v.r = 0.0;
      if (v.g != v.g) v.g = 0.0;
      if (v.b != v.b) v.b = 0.0;
      gl_FragColor = vec4(v, c.a);
    }`,
});

// base + strength * blurred emission
const BLOOM_COMPOSITE_SHADER = {
  uniforms: { tDiffuse: { value: null }, tGlow: { value: null }, strength: { value: 0 } },
  vertexShader: QUAD_VERT,
  fragmentShader: /* glsl */`
    varying vec2 vUv;
    uniform sampler2D tDiffuse;
    uniform sampler2D tGlow;
    uniform float strength;
    void main() {
      vec4 base = texture2D(tDiffuse, vUv);
      vec3 glow = max(texture2D(tGlow, vUv).rgb, vec3(0.0));
      gl_FragColor = vec4(base.rgb + strength * glow, base.a);
    }`,
};

// Dual-filter (Kawase) down/up blur: the cheap way to a WIDE, soft veil.  A
// straight Gaussian at this radius would cost an order of magnitude more taps.
const DOWN_SHADER = {
  uniforms: { tDiffuse: { value: null }, texel: { value: new THREE.Vector2() } },
  vertexShader: QUAD_VERT,
  fragmentShader: /* glsl */`
    varying vec2 vUv;
    uniform sampler2D tDiffuse;
    uniform vec2 texel;
    void main() {
      vec4 s = texture2D(tDiffuse, vUv) * 4.0;
      s += texture2D(tDiffuse, vUv - texel);
      s += texture2D(tDiffuse, vUv + texel);
      s += texture2D(tDiffuse, vUv + vec2(texel.x, -texel.y));
      s += texture2D(tDiffuse, vUv - vec2(texel.x, -texel.y));
      gl_FragColor = s / 8.0;
    }`,
};

const UP_SHADER = {
  uniforms: { tDiffuse: { value: null }, texel: { value: new THREE.Vector2() } },
  vertexShader: QUAD_VERT,
  fragmentShader: /* glsl */`
    varying vec2 vUv;
    uniform sampler2D tDiffuse;
    uniform vec2 texel;
    void main() {
      vec4 s = texture2D(tDiffuse, vUv + vec2(-texel.x * 2.0, 0.0));
      s += texture2D(tDiffuse, vUv + vec2(-texel.x, texel.y)) * 2.0;
      s += texture2D(tDiffuse, vUv + vec2(0.0, texel.y * 2.0));
      s += texture2D(tDiffuse, vUv + vec2(texel.x, texel.y)) * 2.0;
      s += texture2D(tDiffuse, vUv + vec2(texel.x * 2.0, 0.0));
      s += texture2D(tDiffuse, vUv + vec2(texel.x, -texel.y)) * 2.0;
      s += texture2D(tDiffuse, vUv + vec2(0.0, -texel.y * 2.0));
      s += texture2D(tDiffuse, vUv + vec2(-texel.x, -texel.y)) * 2.0;
      gl_FragColor = s / 12.0;
    }`,
};

/**
 * What a material contributes to the bloom mask.  Three answers:
 *
 *   'keep'                 the material ALREADY draws light and nothing else —
 *                          an additive veil, shaft, halo or flare, or an unlit
 *                          decal marked `toneMapped = false`.  It renders into
 *                          the mask AS ITSELF, so a custom shader's own falloff,
 *                          alpha and animation survive.  Replacing these with a
 *                          flat proxy is what turns a thin neon tube into a white
 *                          blob, so we do not.
 *   {color, map}           a LIT material (anything with `.emissive`): only its
 *                          emission belongs in the mask, so it is swapped for an
 *                          unlit proxy carrying emissive * emissiveIntensity.
 *   null                   not a light source: black in the mask.
 *
 * `material.userData.bloom` overrides all of it — `false` to opt out, `true` to
 * say "this material IS the light" (a custom shader has no other way to say so),
 * or a colour to name the emission outright.
 *
 * @returns {'keep'|{color: THREE.Color, map: THREE.Texture|null}|null}
 */
export function glowRoleOf(material, threshold = 0) {
  if (!material) return null;
  const ud = material.userData || {};
  if (ud.bloom === false) return null;
  if (ud.bloom === true) return 'keep';
  if (ud.bloom) {
    const c = new THREE.Color();
    try { c.set(ud.bloom); } catch (e) { c.set(0xffffff); }
    return { color: c, map: material.emissiveMap || material.map || null };
  }

  if (material.emissive && material.emissive.isColor) {
    const i = Number.isFinite(material.emissiveIntensity) ? material.emissiveIntensity : 1;
    const c = material.emissive.clone().multiplyScalar(Math.max(0, i));
    // three multiplies emissiveMap BY emissive, so a black emissive emits nothing
    // however bright its map — a map alone must never invent a light source.
    return Math.max(c.r, c.g, c.b) > threshold
      ? { color: c, map: material.emissiveMap || null }
      : null;
  }

  // Order matters: a LIT material answered above on its emissive alone, so an
  // additive lit material with nothing emissive stays out of the mask — additive
  // is only evidence of light for a material that carries no lighting of its own.
  if (material.blending === THREE.AdditiveBlending) return 'keep';
  const unlit = material.isMeshBasicMaterial || material.isSpriteMaterial || material.isPointsMaterial;
  if (unlit && material.toneMapped === false) return 'keep';
  return null;
}

/**
 * The bloom source: the scene re-rendered with every material replaced by its
 * EMISSION (see `emissionOf`), then blurred down/up into a wide soft veil.
 *
 * The proxy is a MeshBasicMaterial, so a custom vertex program (the library's
 * wind bend, for instance) is NOT reproduced — an emissive that is also vertex
 * displaced glows from its rest pose.  Inside a blur that wide it is invisible,
 * and the alternative (cloning materials with live uniform closures) is a much
 * worse trade.
 */
function makeGlowLayer(renderer, scene, { width, height, levels, threshold }) {
  const opts = { type: THREE.HalfFloatType, depthBuffer: true };
  const targets = [];
  let w = Math.max(2, width >> 1), h = Math.max(2, height >> 1);
  for (let i = 0; i < Math.max(1, levels); i++) {
    targets.push(new THREE.WebGLRenderTarget(w, h, i === 0 ? opts : { type: THREE.HalfFloatType }));
    w = Math.max(2, w >> 1);
    h = Math.max(2, h >> 1);
  }
  const quad = new FullScreenQuad();
  const down = new THREE.ShaderMaterial({ ...DOWN_SHADER, uniforms: THREE.UniformsUtils.clone(DOWN_SHADER.uniforms) });
  const up = new THREE.ShaderMaterial({
    ...UP_SHADER,
    uniforms: THREE.UniformsUtils.clone(UP_SHADER.uniforms),
    blending: THREE.AdditiveBlending,   // each level ADDS: a long, soft falloff
    depthTest: false,
    depthWrite: false,
  });

  const proxies = new Map();   // material.uuid -> MeshBasicMaterial | null (= black)
  const black = new THREE.MeshBasicMaterial({ color: 0x000000, fog: false });
  const swapped = [];
  let sources = 0;

  /** 'keep' | MeshBasicMaterial proxy | null (black), cached per material. */
  function proxyFor(material) {
    if (proxies.has(material.uuid)) return proxies.get(material.uuid);
    const role = glowRoleOf(material, threshold);
    let proxy = role === 'keep' ? 'keep' : null;
    if (role && role !== 'keep') {
      proxy = new THREE.MeshBasicMaterial({
        color: role.color,
        map: role.map || null,
        fog: false,
        toneMapped: false,
        transparent: !!material.transparent,
        opacity: Number.isFinite(material.opacity) ? material.opacity : 1,
        alphaTest: material.alphaTest || 0,
        alphaMap: material.alphaMap || null,
        side: material.side,
        depthTest: material.depthTest !== false,
        depthWrite: material.depthWrite !== false,
        blending: material.blending === THREE.AdditiveBlending ? THREE.AdditiveBlending : THREE.NormalBlending,
        vertexColors: !!material.vertexColors,
      });
    }
    proxies.set(material.uuid, proxy);
    return proxy;
  }

  /** Swap in the emission proxies; returns how many real light sources were found. */
  function swapIn() {
    swapped.length = 0;
    sources = 0;
    scene.traverse((o) => {
      if (!o.visible || !o.material) return;
      if (!(o.isMesh || o.isPoints || o.isSprite || o.isLine || o.isInstancedMesh)) return;
      const mats = Array.isArray(o.material) ? o.material : [o.material];
      const next = mats.map((m, i) => {
        const p = proxyFor(m);
        if (p) sources += 1;
        return p === 'keep' ? mats[i] : (p || black);
      });
      if (next.every((m, i) => m === mats[i])) return;   // all 'keep': nothing to restore
      swapped.push([o, o.material]);
      o.material = Array.isArray(o.material) ? next : next[0];
    });
    return sources;
  }

  function swapOut() {
    for (const [o, m] of swapped) o.material = m;
    swapped.length = 0;
  }

  return {
    get texture() { return targets[0].texture; },
    get sources() { return sources; },

    /** Render + blur the emission mask.  Returns false when nothing glows. */
    render(camera) {
      const found = swapIn();
      if (!found) { swapOut(); return false; }
      const savedBg = scene.background;
      const savedFog = scene.fog;
      const savedClear = renderer.getClearColor(new THREE.Color());
      const savedAlpha = renderer.getClearAlpha();
      const savedTarget = renderer.getRenderTarget();
      try {
        scene.background = null;
        scene.fog = null;          // fog would grey the mask into a global glow
        renderer.setClearColor(0x000000, 1);
        renderer.setRenderTarget(targets[0]);
        renderer.clear();
        renderer.render(scene, camera);
      } finally {
        scene.background = savedBg;
        scene.fog = savedFog;
        renderer.setClearColor(savedClear, savedAlpha);
        swapOut();
      }

      // down the pyramid ...
      for (let i = 1; i < targets.length; i++) {
        down.uniforms.tDiffuse.value = targets[i - 1].texture;
        down.uniforms.texel.value.set(1 / targets[i - 1].width, 1 / targets[i - 1].height);
        quad.material = down;
        renderer.setRenderTarget(targets[i]);
        renderer.clear();
        quad.render(renderer);
      }
      // ... and back up, adding every level into the one above it
      const autoClear = renderer.autoClear;
      renderer.autoClear = false;
      for (let i = targets.length - 1; i > 0; i--) {
        up.uniforms.tDiffuse.value = targets[i].texture;
        up.uniforms.texel.value.set(1 / targets[i].width, 1 / targets[i].height);
        quad.material = up;
        renderer.setRenderTarget(targets[i - 1]);
        quad.render(renderer);
      }
      renderer.autoClear = autoClear;
      renderer.setRenderTarget(savedTarget);
      return true;
    },
  };
}

function num(v, fallback) {
  return Number.isFinite(v) ? Number(v) : fallback;
}

function vec3From(v, target) {
  if (Array.isArray(v) && v.length >= 3 && v.every((x) => Number.isFinite(x))) {
    target.set(Number(v[0]), Number(v[1]), Number(v[2]));
  } else if (v && Number.isFinite(v.r) && Number.isFinite(v.g) && Number.isFinite(v.b)) {
    target.set(v.r, v.g, v.b);   // a THREE.Color reads as a tint triple
  }
  return target;
}

/**
 * Read `scene.userData.grade` into a grade pass's uniforms.  Unknown keys are
 * ignored and a malformed one keeps its identity default, so a scene can never
 * break the chain by asking for nonsense.
 * @returns {object} the applied values (for the census)
 */
export function applyGrade(pass, grade) {
  const u = pass.uniforms;
  const g = (grade && typeof grade === 'object') ? grade : {};
  u.exposure.value = Math.min(8, Math.max(0.05, num(g.exposure, GRADE_DEFAULTS.exposure)));
  u.contrast.value = Math.min(2, Math.max(0.2, num(g.contrast, GRADE_DEFAULTS.contrast)));
  u.saturation.value = Math.min(2.5, Math.max(0, num(g.saturation, GRADE_DEFAULTS.saturation)));
  u.warmth.value = Math.min(1, Math.max(-1, num(g.warmth, GRADE_DEFAULTS.warmth)));
  u.tint.value = Math.min(1, Math.max(-1, num(g.tint, GRADE_DEFAULTS.tint)));
  u.shadowLift.value = Math.min(0.25, Math.max(0, num(g.shadowLift, GRADE_DEFAULTS.shadowLift)));
  u.vignette.value = Math.min(1, Math.max(0, num(g.vignette, GRADE_DEFAULTS.vignette)));
  vec3From(g.shadowTint, u.shadowTint.value).clampScalar(-0.5, 0.5);
  vec3From(g.highlightTint, u.highlightTint.value).clampScalar(-0.5, 0.5);
  return {
    exposure: u.exposure.value, contrast: u.contrast.value, saturation: u.saturation.value,
    warmth: u.warmth.value, tint: u.tint.value, shadow_lift: u.shadowLift.value,
    vignette: u.vignette.value,
    shadow_tint: u.shadowTint.value.toArray().map((x) => +x.toFixed(3)),
    highlight_tint: u.highlightTint.value.toArray().map((x) => +x.toFixed(3)),
  };
}

/** Is this grade the identity (nothing to report, nothing shifted)? */
export function isNeutralGrade(applied) {
  return applied.exposure === 1 && applied.contrast === 1 && applied.saturation === 1
    && applied.warmth === 0 && applied.tint === 0 && applied.shadow_lift === 0 && applied.vignette === 0
    && applied.shadow_tint.every((x) => x === 0) && applied.highlight_tint.every((x) => x === 0);
}

/**
 * Build the post chain for `scene` on `renderer`.
 *
 * @param {THREE.WebGLRenderer} renderer
 * @param {THREE.Scene} scene
 * @param {{width:number, height:number, options?:object}} spec
 * @returns {{render(camera):void, info:object, refreshGrade():void}}
 */
export function makePostChain(renderer, scene, { width, height, options = {} } = {}) {
  const opt = { ...POST_DEFAULTS, ...(options || {}) };
  const warnings = [];
  const target = new THREE.WebGLRenderTarget(width, height, {
    type: THREE.HalfFloatType,
    samples: Math.max(0, opt.samples | 0),
  });
  const composer = new EffectComposer(renderer, target);
  composer.setSize(width, height);

  const renderPass = new RenderPass(scene, null);
  composer.addPass(renderPass);
  composer.addPass(new ShaderPass(clampShader(opt.clampMax)));

  let gtao = null;
  if (opt.ao > 0) {
    try {
      // GTAOPass reads projection flags at construction; the real camera is
      // swapped in before every render (its uniforms refresh per frame).
      const aoCam = new THREE.PerspectiveCamera(50, width / height, 0.1, 1000);
      gtao = new GTAOPass(scene, aoCam, width, height);
      gtao.updateGtaoMaterial({
        radius: opt.aoRadius,
        distanceExponent: opt.aoDistanceExponent,
        thickness: opt.aoThickness,
        scale: 1.0,
        samples: opt.aoSamples,
      });
      gtao.updatePdMaterial({ rings: opt.aoDenoiseRings, samples: opt.aoDenoiseSamples });
      gtao.blendIntensity = opt.ao;
      composer.addPass(gtao);
    } catch (e) {
      gtao = null;
      warnings.push(`GTAO unavailable: ${String((e && e.message) || e).slice(0, 200)}`);
    }
  }

  let glow = null;
  let bloomPass = null;
  if (opt.bloom > 0) {
    glow = makeGlowLayer(renderer, scene, {
      width, height, levels: opt.bloomLevels, threshold: opt.bloomThreshold,
    });
    bloomPass = new ShaderPass(BLOOM_COMPOSITE_SHADER);
    composer.addPass(bloomPass);
  }

  const gradePass = new ShaderPass(GRADE_SHADER);
  composer.addPass(gradePass);
  composer.addPass(new OutputPass());

  let applied = applyGrade(gradePass, scene.userData && scene.userData.grade);

  const info = {
    enabled: true,
    ao: gtao ? opt.ao : 0,
    ao_radius: opt.aoRadius,
    bloom: glow ? opt.bloom : 0,
    bloom_levels: opt.bloomLevels,
    bloom_sources: 0,
    samples: Math.max(0, opt.samples | 0),
    passes: composer.passes.length,
    grade: applied,
    grade_neutral: isNeutralGrade(applied),
    warnings,
  };

  return {
    info,
    /** Re-read scene.userData.grade (a scene may set it during createScene or update). */
    refreshGrade() {
      applied = applyGrade(gradePass, scene.userData && scene.userData.grade);
      info.grade = applied;
      info.grade_neutral = isNeutralGrade(applied);
    },
    render(camera) {
      if (glow) {
        const lit = glow.render(camera);
        info.bloom_sources = glow.sources;
        // keep the sampler bound either way (an unbound sampler2D is a warning);
        // strength 0 is what makes a scene with nothing emissive pay nothing.
        bloomPass.uniforms.tGlow.value = glow.texture;
        // the up-chain ADDS every mip into the one above it, so the veil already
        // carries ~one unit of energy per level: normalise, or `bloom` would mean
        // something different at every pyramid depth.
        bloomPass.uniforms.strength.value = lit ? opt.bloom / Math.max(1, opt.bloomLevels) : 0;
      }
      renderPass.camera = camera;
      if (gtao) gtao.camera = camera;
      composer.render();
    },
  };
}

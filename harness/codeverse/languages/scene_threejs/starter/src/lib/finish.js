/**
 * Two finishes a built-in material cannot reach: light coming THROUGH a
 * thin solid, and the colour a thin film shifts with viewing angle.
 *
 * Both go through `patchStandard`, so lighting, shadows, fog and the
 * depth chunks survive, both shade from world position and world normal
 * (no UV needed), and both chain with `surface_wear`, `aging`,
 * `terrain_shade` and `waterside` on one material.
 *
 * Both lift `totalEmissiveRadiance` rather than the albedo, which is the
 * one decision that separates them from a pale repaint. The only
 * fragment hook is `<color_fragment>`, so a patch may either tint ALBEDO
 * — capped at the light already landing on the surface — or ADD light,
 * the choice `windows.js` documents for a lit room. A lantern at dusk
 * and an oil sheen on a shaded cobble are both brighter than their own
 * front lighting, so neither can be albedo.
 */

import * as THREE from 'three';
import { patchStandard, composeRoughness } from './shader.js';

// Named as terrain_shade, waterside, surface_wear and aging name them,
// so a material wearing several libraries declares ONE pair; the vertex
// locals are fn* because those four own astraWp, wsP, wrP and agP.
const WORLD_VARYINGS = [
  'varying vec3 vAstraWorld;',
  'varying vec3 vAstraWorldN;',
].join('\n');

// `transformed` is still object-space after <begin_vertex>, so the
// instance transform is folded in by hand or every scattered copy is
// shaded as though it stood at the world origin.
const BASE = {
  name: 'finish:base',
  vertexHead: WORLD_VARYINGS,
  vertexBody: [
    '  vec4 fnP = vec4(transformed, 1.0);',
    '  vec3 fnN = normal;',
    '#ifdef USE_INSTANCING',
    '  fnP = instanceMatrix * fnP;',
    '  fnN = mat3(instanceMatrix) * fnN;',
    '#endif',
    '  vAstraWorld = (modelMatrix * fnP).xyz;',
    '  vAstraWorldN = normalize((modelMatrix * vec4(fnN, 0.0)).xyz);',
  ].join('\n'),
  fragmentHead: WORLD_VARYINGS,
};

/** Take a THREE.Color, a hex or nothing, never sharing the instance. */
function toColor(value, fallback) {
  return new THREE.Color(
      value === undefined || value === null ? fallback : value);
}

/** Clamp to 0..1 without importing MathUtils for one call. */
function unit(value) {
  return Math.max(0, Math.min(1, value));
}

/** fract(), which JS's % gets wrong for a negative seed. */
function frac(x) {
  return x - Math.floor(x);
}

/** astraHash11 from GLSL_UTIL, so CPU and shader agree on a seed. */
function hash11(x) {
  let p = frac(x * 0.1031);
  p *= p + 33.33;
  return frac(p * (p + p));
}

/**
 * Turn a seed into a noise-space offset, so two materials differ.
 *
 * The offset is a UNIFORM: a seed baked into the GLSL would be fixed for
 * every other material that shares this patch's cache key, because the
 * first one to compile a key decides the source for all of them.
 */
function seedOffset(seed) {
  return new THREE.Vector3(
      hash11(seed + 0.29), hash11(seed + 5.11), hash11(seed + 9.67))
      .multiplyScalar(48);
}

const TRANS_HEAD = [
  'uniform float uTrsThick;',
  'uniform vec3 uTrsColor;',
  'uniform float uTrsAmt;',
  'uniform float uTrsPow;',
  'uniform vec3 uTrsSeed;',
  // Metres of material per e-fold. This is the ABSORPTION scale, not the
  // millimetre free path between scatters: light random-walks far past
  // one scatter, and wax, jade, paper and skin all absorb over cm.
  'const float ASTRA_TRS_MFP = 0.030;',
  // Paper and wax scatter almost isotropically, so a share of what gets
  // through leaves in EVERY direction. Without it a lantern's silhouette
  // goes dark while its face glows, which reads as a lamp behind it.
  'const float ASTRA_TRS_ISO = 0.45;',
  'const float ASTRA_TRS_BEND = 0.30;',
  // Beer-Lambert is PER CHANNEL, so the medium's hue DEEPENS along the
  // path: a wax rim is pale gold and its shoulder is amber, one body.
  // The exponent starts below 1 because even the thinnest crossing is
  // already tinted (lantern paper is warm at half a millimetre), and it
  // is capped because past ~2 mean free paths nothing gets through to
  // colour anyway.
  'const float ASTRA_TRS_TINT0 = 0.55;',
  'const float ASTRA_TRS_TINTK = 1.30;',
  // Bloom-friendly ceiling. A shade is a DIFFUSER: what it cannot pass
  // it scatters back, it does not pile up. Our host tone-maps in the
  // fragment tail with no post chain, so an uncompressed lamp against
  // paper lands on pure white and the warmth that IS the effect is the
  // first thing lost. The knee opens at half the ceiling, so every
  // ordinary glow passes through untouched.
  'const float ASTRA_TRS_PEAK = 3.0;',
  // Backlit means the light is on the FAR face, which is the gate; the
  // forward lobe on top is why an eye looking into the light through the
  // object sees the most. `n` must already face the viewer.
  'float astraTrsWeight(vec3 n, vec3 v, vec3 l, float p) {',
  '  float b = max(-dot(n, l), 0.0);',
  '  vec3 h = normalize(-l + n * ASTRA_TRS_BEND);',
  '  return b * (ASTRA_TRS_ISO + pow(max(dot(v, h), 0.0), p));',
  '}',
].join('\n');

const TRANS_BODY = [
  '  vec3 trV = normalize(cameraPosition - vAstraWorld);',
  // Turned toward the eye, so "behind" means behind from HERE: a
  // double-sided sheet seen from its back face is still backlit.
  '  vec3 trN = normalize(vAstraWorldN);',
  '  trN *= sign(dot(trN, trV) + 1e-6);',
  // The chord through a convex body is its widest crossing times how
  // squarely it is faced, blotched by the grain wax, paper and jade
  // all have. Metres of material this ray has to cross.
  '  float trG = 0.55 + 0.90 * astraFbm2(',
  '      (vAstraWorld.xz + vAstraWorld.y) * 6.0 + uTrsSeed.xy, 3);',
  '  float trPath = uTrsThick * astraFacing(trN, trV) * trG;',
  '  float trT = exp(-trPath / ASTRA_TRS_MFP);',
  '  vec3 trIn = vec3(0.0);',
  '#if NUM_DIR_LIGHTS > 0',
  '  for (int i = 0; i < NUM_DIR_LIGHTS; i++) {',
  // three keeps light vectors in VIEW space; v * mat3(viewMatrix) is
  // that matrix's transpose applied, which is its inverse rotation.
  '    vec3 trL = directionalLights[i].direction * mat3(viewMatrix);',
  '    trIn += directionalLights[i].color',
  '        * astraTrsWeight(trN, trV, trL, uTrsPow);',
  '  }',
  '#endif',
  '#if NUM_POINT_LIGHTS > 0',
  '  for (int i = 0; i < NUM_POINT_LIGHTS; i++) {',
  '    vec3 trP = pointLights[i].position * mat3(viewMatrix)',
  '        + cameraPosition;',
  '    vec3 trD = trP - vAstraWorld;',
  '    float trR = length(trD);',
  '    trIn += pointLights[i].color * getDistanceAttenuation(',
  '        trR, pointLights[i].distance, pointLights[i].decay)',
  '        * astraTrsWeight(trN, trV, trD / max(trR, 1e-4), uTrsPow);',
  '  }',
  '#endif',
  // The colour light comes out as is the colour of the path it crossed,
  // not one flat tint: thin gives a pale wash of the medium, thick gives
  // the medium itself, and the gradient between them is what reads as a
  // BODY rather than a painted glow.
  '  vec3 trTint = pow(uTrsColor, vec3(ASTRA_TRS_TINT0',
  '      + ASTRA_TRS_TINTK * min(trPath / ASTRA_TRS_MFP, 2.0)));',
  // trIn is the irradiance reaching the far face; what leaves this one
  // is that spread over the hemisphere, so a lamp held against the
  // shade blows out exactly as it does in a photograph.
  '  vec3 trOut = trTint * (uTrsAmt * trT * 0.3183 * trIn);',
  // ...blows out, but in COLOUR. One scalar knee on the peak channel:
  // scaling all three by the same number moves the value and leaves the
  // hue exactly where it was, which a per-channel clamp does not.
  '  float trPk = max(max(trOut.r, trOut.g), trOut.b);',
  '  trOut *= ASTRA_TRS_PEAK / (ASTRA_TRS_PEAK',
  '      + max(trPk - 0.5 * ASTRA_TRS_PEAK, 0.0));',
  '  totalEmissiveRadiance += trOut;',
].join('\n');

/**
 * Light coming THROUGH a thin solid: a lantern, wax, jade, a petal.
 *
 * The cue is that the object glows where it is THIN and where the light
 * is BEHIND it, and stays opaque where it is thick. Both halves are here
 * and both are needed: a glow with no thickness term is a coat of paint,
 * and one with no direction term is a lamp.
 *
 * THICKNESS is the chord through a convex body — the widest crossing
 * times how squarely the surface is faced — so a sphere or a cylinder is
 * exact and a flat sheet is merely harmless, its path being negligible
 * at any angle. What survives is Beer-Lambert over that path against an
 * absorption scale of 3 cm — the range wax, jade, paper and skin share.
 * So 0.5 mm of lantern paper is lit everywhere, a 4 cm wax candle is
 * dim through its core and bright around its rim, and a 30 cm block is
 * opaque but for the last sliver of its silhouette.
 *
 * COLOUR runs with that path too, because absorption is per channel: a
 * thin crossing comes out a pale wash of `color` and a long one comes
 * out `color` itself, so one body carries a gradient of its own hue
 * instead of a single flat tint. The result is capped by a scalar knee
 * near radiance 3 — the same scale for all three channels, so a lamp
 * pressed against a shade blows out in COLOUR rather than to white,
 * which is what it does on a host that tone-maps with no post chain.
 *
 * DIRECTION comes from the scene's own lights — the `directionalLights`
 * and `pointLights` three already declares — so a lamp INSIDE a shade
 * lights it and a sun behind a leaf lights that, with no vector to pass
 * and nothing to keep in step. Spot and area lights are not summed.
 *
 * It ADDS light rather than lifting albedo, because a lantern at dusk is
 * brighter than anything falling on its front; an albedo lift would cap
 * it at the ambient and read as pale paper.
 *
 * WHICH PATCH: `foliage_shade.js` `patchLeafSSS` is the thin-leaf
 * special case — one thickness for the whole crown, a stated sun, an
 * fbm for the leaf mass the light crossed, and an ALBEDO lift, which is
 * right for a leaf that already sits in the light it transmits. Use this
 * one when the thickness VARIES over the body (a candle's rim against
 * its core), when the light is a lamp rather than the stated sun, or
 * when the object has to out-glow its own front lighting.
 *
 * @param {THREE.Material} material A lit built-in material (Standard,
 *   Physical, Phong — it needs `totalEmissiveRadiance` and the light
 *   uniforms), patched in place. A shared material from `materials.js`
 *   patches every mesh wearing it, so clone it first if that is not what
 *   you want.
 * @param {object} [opts] `thickness` metres of material the light
 *   crosses at the THICKEST point — a wall for a shell, a diameter for a
 *   solid (default 0.02); `color` THREE.Color or hex, the colour light
 *   comes out as, which is the material's own not the lamp's (default a
 *   warm parchment); `strength` how bright the transmitted light goes
 *   (default 1.0); `power` how tight the forward lobe is around the
 *   light — lower spreads the glow further off axis (default 3); `seed`
 *   moves the grain that blotches the thickness (default 1).
 * @returns {THREE.Material} The same material, its uniforms live on
 *   `material.userData.uniforms` for a scene that dims the lamp.
 */
export function patchTranslucency(material, opts = {}) {
  const thickness = opts.thickness === undefined ? 0.02 : opts.thickness;
  const strength = opts.strength === undefined ? 1.0 : opts.strength;
  const power = opts.power === undefined ? 3 : opts.power;
  const seed = opts.seed === undefined ? 1 : opts.seed;
  if (!material.emissive) {
    console.warn('patchTranslucency: needs a lit material (Standard, '
        + 'Physical or Phong) — transmitted light is light, not paint.');
  }
  patchStandard(material, BASE);
  return patchStandard(material, {
    name: 'finish:translucency',
    uniforms: {
      uTrsThick: { value: Math.max(0, thickness) },
      uTrsColor: { value: toColor(opts.color, 0xffd7a8) },
      uTrsAmt: { value: Math.max(0, strength) },
      uTrsPow: { value: Math.max(0.1, power) },
      uTrsSeed: { value: seedOffset(seed) },
    },
    vertexHead: WORLD_VARYINGS,
    fragmentHead: [WORLD_VARYINGS, TRANS_HEAD].join('\n'),
    fragmentBody: TRANS_BODY,
  });
}

const IRID_HEAD = [
  'uniform float uIriAmt;',
  'uniform float uIriScale;',
  'uniform float uIriIor;',
  'uniform vec3 uIriSeed;',
  // Nanometres of film: the first two interference orders, where an oil
  // slick and a soap bubble both live. A deeper film stacks fringes
  // until they average back to white.
  'const float ASTRA_IRI_D0 = 470.0;',
  // 60, not 45: at the default index the ANGLE sweeps 0.30 of the path
  // and the field 0.26 of it, so the field is still the smaller of the
  // two — the law that keeps this a film and not a rainbow decal — while
  // a FLAT film (a puddle, a wet flagstone), where the angle barely
  // moves across the whole surface, now carries visible bands instead of
  // one held hue.
  'const float ASTRA_IRI_DV = 60.0;',
  // A film SPLITS the light it reflects; it does not make any. So what
  // it adds is the scene's own light, hue-split around its mean: VEIL is
  // the share left as a plain lift (a soap bubble is brighter than what
  // is under it), the rest is a swing that shifts hue at constant level
  // — the only way the colour survives on a puddle already reflecting a
  // bright sky. GAIN restores the weight `strength` used to mean once
  // the mean is taken out; REF is the light level at which the film is
  // half strength, so a moonlit slick is a hint and a noon one is not.
  'const float ASTRA_IRI_VEIL = 0.35;',
  'const float ASTRA_IRI_GAIN = 1.5;',
  'const float ASTRA_IRI_REF = 1.0;',
  // A punctual light reaches a film as a GLINT around the mirror
  // direction plus the share the air and the ground scatter back — a
  // real slick is rainbow at the reflected lamp and dark beside it.
  'const float ASTRA_IRI_HAZE = 0.10;',
  'const float ASTRA_IRI_LOBE = 8.0;',
  'vec3 astraIriFilm(float opd) {',
  // Interference sampled at three wavelengths in nm; the half turn is
  // the phase flip on reflection off the denser layer.
  '  vec3 lam = vec3(612.0, 549.0, 464.0);',
  '  vec3 c = 0.5 + 0.5 * cos(6.2831853 * opd / lam + 3.1415927);',
  // Held at a constant mean: a film shifts HUE with angle, and a fringe
  // that swings brightness as well reads as a stain.
  '  c /= max((c.r + c.g + c.b) / 3.0, 0.26);',
  // Daylight is a BAND at every wavelength, not a line, so neighbouring
  // orders overlap and the fringes wash toward white as the order
  // climbs. Without it the third fringe is as lurid as the first.
  '  float ord = opd / 550.0;',
  '  return mix(vec3(1.0), c, exp(-0.12 * ord * ord));',
  '}',
].join('\n');

const IRID_BODY = [
  '  vec3 irV = normalize(cameraPosition - vAstraWorld);',
  '  vec3 irN = normalize(vAstraWorldN);',
  '  irN *= sign(dot(irN, irV) + 1e-6);',
  '  float irCos = clamp(dot(irN, irV), 0.0, 1.0);',
  // Snell: the ray bends INSIDE the film, so the path it crosses
  // shortens toward grazing. That shortening IS the colour sweep — the
  // hue rides the angle, not the position.
  '  float irCt = sqrt(max(1.0 - (1.0 - irCos * irCos)',
  '      / (uIriIor * uIriIor), 0.0));',
  '  float irD = ASTRA_IRI_D0 + ASTRA_IRI_DV * (2.0 * astraFbm2(',
  '      (vAstraWorld.xz + vAstraWorld.y) / uIriScale',
  '      + uIriSeed.xy, 3) - 1.0);',
  '  float irOpd = 2.0 * uIriIor * irD * irCt;',
  // Two-beam interference peaks at four times the single-surface
  // Fresnel face-on and runs to total reflection at grazing: the colour
  // is present head-on and takes the surface over at the rim.
  '  float irR0 = (uIriIor - 1.0) / (uIriIor + 1.0);',
  '  irR0 = 4.0 * irR0 * irR0;',
  '  float irW = mix(irR0, 1.0, astraFresnel(irN, irV, 4.0));',
  // The light the film has to split. The hemisphere fill IS the sky for
  // a scene built on `sunRig`, and a film reflects the whole of it, so
  // that one arrives as a wash. A sun or a lamp does NOT: what a mirror
  // returns of a punctual light is a glint around the mirror direction,
  // which is why a wet street at night is rainbow near the reflected
  // lamp and black a metre away. Weighting them as a wash instead is
  // what turned one lamp two metres off into a rainbow over an entire
  // puddle (measured on the night render, before this line).
  // Reading these rather than assuming white is what makes a slick go
  // amber at golden hour and blue at night with nothing passed in.
  '  vec3 irRef = reflect(-irV, irN);',
  '  vec3 irLit = ambientLightColor;',
  '#if NUM_HEMI_LIGHTS > 0',
  '  for (int i = 0; i < NUM_HEMI_LIGHTS; i++) {',
  '    vec3 irHd = hemisphereLights[i].direction * mat3(viewMatrix);',
  '    irLit += mix(hemisphereLights[i].groundColor,',
  '                 hemisphereLights[i].skyColor,',
  '                 0.5 + 0.5 * dot(irN, irHd));',
  '  }',
  '#endif',
  '#if NUM_DIR_LIGHTS > 0',
  '  for (int i = 0; i < NUM_DIR_LIGHTS; i++) {',
  '    vec3 irLd = directionalLights[i].direction * mat3(viewMatrix);',
  '    irLit += directionalLights[i].color',
  '        * (ASTRA_IRI_HAZE + (1.0 - ASTRA_IRI_HAZE)',
  '           * pow(max(dot(irRef, irLd), 0.0), ASTRA_IRI_LOBE));',
  '  }',
  '#endif',
  '#if NUM_POINT_LIGHTS > 0',
  '  for (int i = 0; i < NUM_POINT_LIGHTS; i++) {',
  '    vec3 irPp = pointLights[i].position * mat3(viewMatrix)',
  '        + cameraPosition;',
  '    vec3 irPd = irPp - vAstraWorld;',
  '    float irPr = length(irPd);',
  '    irLit += pointLights[i].color * getDistanceAttenuation(',
  '        irPr, pointLights[i].distance, pointLights[i].decay)',
  '        * (ASTRA_IRI_HAZE + (1.0 - ASTRA_IRI_HAZE)',
  '           * pow(max(dot(irRef, irPd / max(irPr, 1e-4)), 0.0),',
  '                 ASTRA_IRI_LOBE));',
  '  }',
  '#endif',
  // Split into the scene's COLOUR and the scene's LEVEL: the tint is
  // unit-luminance so `strength` keeps meaning what the docs say, and
  // the level saturates rather than scaling, so a lamp two metres away
  // does not turn one slick into a second lamp.
  '  float irLum = dot(irLit, vec3(0.2126, 0.7152, 0.0722));',
  '  vec3 irTint = irLit / max(irLum, 1e-4);',
  '  float irLevel = irLum / (irLum + ASTRA_IRI_REF);',
  '  totalEmissiveRadiance += irTint',
  '      * ((astraIriFilm(irOpd) - (1.0 - ASTRA_IRI_VEIL))',
  '         * (uIriAmt * irW * irLevel * ASTRA_IRI_GAIN));',
].join('\n');

/**
 * The colour that shifts with viewing angle on a thin film.
 *
 * An oil slick, a soap bubble, a beetle's back, a coating on wet stone.
 * The hue rides the FRESNEL angle, not the surface position: the ray
 * bends inside the film, so the optical path it crosses shortens toward
 * grazing and the fringe order slides as the camera moves. A hue that
 * merely varies over the surface is a rainbow decal — that is the whole
 * difference, and it is why the film thickness field is kept slow and
 * shallow while the angle does the sweeping.
 *
 * It ADDS light, because a film's colour is a REFLECTION sitting on top
 * of the surface. What it adds is the light the scene actually has —
 * ambient plus the hemisphere fill (the sky, for anything built on
 * `sunRig`) plus a broad share of every sun and lamp — split around its
 * own mean rather than laid on as a white veil. So the sheen carries the
 * scene's colour, fades with the light instead of glowing in the dark,
 * and still reads on a wet floor that is already reflecting a bright
 * sky, where a veil only washes out. Shadows are not readable from this
 * hook, so a slick inside one keeps its sun share; that is the one
 * remaining place `strength` has to be spent carefully. A scene lit ONLY
 * by `scene.environment` declares no light uniforms for this to read (a
 * PMREM is not reachable from `<color_fragment>`), so give it at least a
 * dim HemisphereLight — `sunRig` always does — or the film has nothing
 * to split and stays invisible.
 *
 * METALNESS is the limit. Per-pixel metalness is unreachable (there is
 * no `composeMetalness` — `<color_fragment>` is the only hook), so this
 * cannot do what a real coating does to metal, which is to tint how the
 * METAL reflects. Above about 0.6 metalness the material has no diffuse
 * left and the film reads as a flat wash over a reflection that has not
 * changed. For a beetle's back or anodised steel, drop metalness to
 * 0.2-0.4 and let the film carry the colour.
 *
 * @param {THREE.Material} material A lit built-in material (it needs
 *   `totalEmissiveRadiance`), patched in place — a shared material
 *   patches every mesh wearing it.
 * @param {object} [opts] `strength` how strong the sheen goes at
 *   grazing, 0..1 (default 0.35 — an oil slick, not a hologram);
 *   `scale` metres per cycle of the film's own thickness swirl (default
 *   0.35); `ior` the film's refractive index, which sets both how fast
 *   the hue sweeps with angle and how much it returns face-on (default
 *   1.40 — oil on water; a soap film is 1.33, a beetle's chitin 1.56);
 *   `seed` moves the swirl (default 1).
 * @returns {THREE.Material} The same material, with its uniforms live
 *   on `material.userData.uniforms`.
 */
export function patchIridescence(material, opts = {}) {
  const strength = opts.strength === undefined ? 0.35 : opts.strength;
  const scale = opts.scale === undefined ? 0.35 : opts.scale;
  const ior = opts.ior === undefined ? 1.40 : opts.ior;
  const seed = opts.seed === undefined ? 1 : opts.seed;
  if (!material.emissive) {
    console.warn('patchIridescence: needs a lit material (Standard, '
        + 'Physical or Phong) — a film reflects, it does not repaint.');
  }
  // A film only shows on a surface smooth enough to keep one coherent
  // reflection; roughness is per MATERIAL, so this is the reachable
  // half of that (see composeRoughness).
  composeRoughness(material, 'iridescence', 1 - 0.30 * unit(strength));
  patchStandard(material, BASE);
  return patchStandard(material, {
    name: 'finish:iridescence',
    uniforms: {
      uIriAmt: { value: unit(strength) },
      uIriScale: { value: Math.max(1e-3, scale) },
      uIriIor: { value: Math.max(1.01, ior) },
      uIriSeed: { value: seedOffset(seed + 0.5) },
    },
    vertexHead: WORLD_VARYINGS,
    fragmentHead: [WORLD_VARYINGS, IRID_HEAD].join('\n'),
    fragmentBody: IRID_BODY,
  });
}

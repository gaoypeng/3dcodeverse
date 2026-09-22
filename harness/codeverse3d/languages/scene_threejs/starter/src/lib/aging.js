/**
 * Aging: the three marks that say a surface has STOOD somewhere.
 *
 * `surface_wear` breaks up the flat colour and takes the finish off the
 * edges; these three add the part only weather writes — what ran DOWN
 * the face, what corroded where water sat, and what settled on top. All
 * three go through `patchStandard`, so lighting, shadows, fog and the
 * depth chunks survive; all three shade from world position and world
 * normal, so none needs a UV, gravity stays world Y whatever the
 * surface is doing, and two objects sharing a scale share one weather.
 *
 * The constraint that shapes all of them: a fragment sees its own
 * position, its own normal and its own screen derivatives, and nothing
 * else — it cannot look UP the surface to find the ledge the water
 * poured over. So the vertical structure rides fields that are
 * STRETCHED along world Y (drips) or CONSTANT along it (rust), which is
 * what makes a stain continue below the rim that made it; and
 * `patchDripStains(m, { from })` is the one way to pin the source to an
 * exact sill.
 *
 * Gloss is per MATERIAL, not per pixel — all three write only at
 * `<color_fragment>`, before `<roughnessmap_fragment>`, and none uses
 * `patchStandard`'s `roughnessBody` — so each patch composes one
 * roughening factor through `composeRoughness`: dirt, rust and dust are
 * all matte.
 *
 * COLOUR: no effect here is one tone. Weather is a mixture — the dirt
 * that ran down is not the salt it leached out of the wall, the pit in
 * a corrosion patch is not the ochre bloom at its edge, and a drift of
 * dust is warmer than the veil between drifts. So each patch carries a
 * PAIR (drips, dust) or a TRIPLE (rust) built off its one colour
 * option, hue-broken across the field by `astraHueBreak`, and the
 * lighter member of each pair is derived from the SURFACE's own albedo
 * rather than named here — a leached halo belongs to whatever it is
 * leaching out of, and nothing in this file may hardcode a light.
 */

import {
  patchStandard, composeRoughness, glslAxes, glslCurv, matteFactor,
  seedVec3, toColor, unit, upVector, worldBase,
} from './shader.js';

// All three patches read these, so they are declared once, in the base.
// Names are age-prefixed rather than shared with surface_wear's: a
// neighbour's helper is only there when that neighbour was applied.
const AGE_HEAD = [
  glslAxes('astraAgeAxes'),
  // The vertical-streak field: three world-plane projections blended by
  // the normal, world Y compressed by `lift` in the two upright ones so
  // a feature is taller than wide on ANY face. lift 1.0 = blotches.
  'float astraAgeStreak(vec3 p, vec3 w, float lift) {',
  '  return astraNoise2(vec2(p.z, p.y * lift) + 17.3) * w.x',
  '       + astraNoise2(p.xz + 41.9) * w.y',
  '       + astraNoise2(vec2(p.x, p.y * lift) + 73.1) * w.z;',
  '}',
  // Positive curvature is convex (swept clean), negative the concave
  // lee that holds.
  glslCurv('astraAgeCurv'),
].join('\n');

// One base for all three, on the world varyings every library shares;
// the vertex locals are ag* because terrain_shade, waterside and
// surface_wear own astraWp, wsP and wrP.
const BASE = worldBase('aging:base', 'agP', 'agN', AGE_HEAD);

// The constants differ from surface_wear's, or one seed would land this
// library's stains on that one's blotches.
const seedOffset = (seed, salt) =>
  seedVec3(seed + salt, 0.29, 5.13, 9.47, 48);

// Every effect here is matte, so each raises roughness through
// `matteFactor`. The `add` each patch passes used to be token — a fully
// rusted steel tank moved from 0.45 to 0.50 and went on mirroring the
// sky, which washed the oxide off the frame at every distance. A crust
// of oxide, dust or dried grime is one of the matte-est things there
// is, so these now buy real roughness: the cap keeps a surface that
// already starts rough from moving at all.

/**
 * The vertical streaks that run down from every ledge, sill and joint.
 *
 * Rain does not wash a facade evenly: it collects on the horizontals,
 * pours over their lips, and carries the dirt off them down the face in
 * runs — so an exterior with clean verticals under dirty ledges reads
 * as a model of a building rather than a building. This lays those runs
 * over the albedo, and it is the single strongest "this has stood
 * outside" cue there is.
 *
 * DOWN IS WORLD Y — the field is projected on the world planes and
 * stretched ~11x along Y, so a wall at any angle, a pipe and a tank all
 * streak vertically with no UV to author and no per-object setup. Three
 * thresholds cut it into a pale leached rim, the body of the run and a
 * narrow dark core, so the runs come out wide, hairline or absent; and
 * the field is sampled a second time 4 * `scale` HIGHER and faded in,
 * which drags every feature downward and tapers its tail — that
 * asymmetry is what reads as flow rather than as stripes.
 *
 * The rim is the surface's OWN albedo lifted, not a colour chosen here:
 * rain leaches a wall before it dirties it, and a pale edge beside a
 * dark core is what makes a run legible at any distance — with the
 * grime alone the whole face just dims (measured on the showcase wall:
 * the run signal, the high-pass of the column-mean luminance, went
 * 0.0111 -> 0.0126 and its peak-to-peak 0.049 -> 0.061 when the rim
 * was split out of the wash).
 *
 * A fragment cannot see the surface above it, so `from` is the only way
 * to say where the water came over: given, the runs start just under
 * that world Y (each a little lower than its neighbour) and die out at
 * their own length; left out, they take their tops from the field
 * itself and read as general staining down the whole face. Faces that
 * shed nothing — level tops, level soffits — are left alone.
 *
 * @param {THREE.Material} material A built-in material, patched in
 *   place — a shared material from `materials.js` streaks every mesh
 *   wearing it, so clone it first if that is not what you want.
 * @param {object} [opts] `strength` how far a run goes toward `color`,
 *   0..1 (default 0.35); `color` THREE.Color or hex, the washed-down
 *   dirt, hue-broken per run so no two are the same brown (default a
 *   sooty grey-brown); `scale` metres between runs
 *   (default 0.6; their length scales with it, ~2 to 7 m); `from` world
 *   Y the runs start below — a ledge top, a sill, a joint (default:
 *   none, staining down the whole face); `seed` moves them (default 1).
 * @returns {THREE.Material} The same material. Its uniforms stay live
 *   on `material.userData.uniforms`, so `uDripFrom` can follow a sill.
 */
export function patchDripStains(material, opts = {}) {
  const strength = opts.strength === undefined ? 0.35 : opts.strength;
  const scale = opts.scale === undefined ? 0.6 : opts.scale;
  const seed = opts.seed === undefined ? 1 : opts.seed;
  const from = opts.from;
  const gated = Number.isFinite(from);
  composeRoughness(material, 'aging:drip',
                   matteFactor(material, 0.22 * unit(strength)));
  patchStandard(material, BASE);
  return patchStandard(material, {
    name: 'aging:drip',
    uniforms: {
      uDripAmt: { value: unit(strength) },
      // A grime that is DARKER than the wall, not a hole in it: the old
      // 0x4a4139 lands at 0.074/0.058/0.041 linear and the hue break
      // takes the low side of that to 0.04, which reads as a shadow
      // rather than as soot. This sits at 0.098/0.072/0.051.
      uDripColor: { value: toColor(opts.color, 0x584c40) },
      uDripScale: { value: Math.max(1e-3, scale) },
      uDripFrom: { value: gated ? from : 0 },
      uDripGate: { value: gated ? 1 : 0 },
      uDripSeed: { value: seedOffset(seed, 0) },
    },
    fragmentHead: [
      'uniform float uDripAmt;',
      'uniform vec3 uDripColor;',
      'uniform float uDripScale;',
      'uniform float uDripFrom;',
      'uniform float uDripGate;',
      'uniform vec3 uDripSeed;',
    ].join('\n'),
    fragmentBody: [
      '  vec3 drN = normalize(vAstraWorldN);',
      '  vec3 drP = vAstraWorld / uDripScale + uDripSeed;',
      // Water runs only where it cannot sit: a level top sheds nothing
      // down its own surface, and neither does a level soffit.
      '  float drFlow = 1.0 - smoothstep(0.30, 0.92, abs(drN.y));',
      '  vec3 drW = astraAgeAxes(drN);',
      '  float drA = astraAgeStreak(drP, drW, 0.09);',
      // The same field 4 * scale HIGHER, faded in: every feature is
      // dragged down from where it started and tapers as it goes.
      '  float drB = astraAgeStreak(drP + vec3(0.0, 4.0, 0.0), drW, 0.09);',
      // A finer field, stretched even harder, ADDED before the
      // threshold rather than multiplied after: it tears the edge of
      // every run into fibres that run WITH it.
      '  float drC = astraAgeStreak(drP * 3.3, drW, 0.045);',
      '  float drF = max(drA, drB * 0.80) + (drC - 0.5) * 0.28;',
      // Two thresholds on one field: a broad faint wash with a narrow
      // dark core inside it, which is a run rather than a stripe. The
      // widths vary because the peaks the thresholds cut do.
      //
      // fwidth here is a PIXEL guard, not a softener. A wall seen at a
      // grazing angle carries a screen gradient many times the width of
      // either threshold band, so the old 0.5 cap let it swallow both
      // and every run came out an airbrushed smudge with no edge at all
      // (measured on the showcase wall: the runs had no local contrast
      // to speak of). Capped near one band's width it antialiases and
      // stops there.
      '  float drAA = clamp(fwidth(drF), 0.0, 0.045);',
      // THREE cuts, not two. The outer rim and the body used to be one
      // band, so the pale leach and the dark grime landed on the same
      // pixels and cancelled — the run lost contrast instead of gaining
      // it (wall lum_std 0.0468 -> 0.0415 on the first attempt). Split
      // them and the rim frames the body instead of bleaching it.
      '  float drWash = smoothstep(0.50 - drAA, 0.60 + drAA, drF);',
      '  float drBody = smoothstep(0.60 - drAA, 0.70 + drAA, drF);',
      '  float drCore = smoothstep(0.715 - drAA, 0.775 + drAA, drF);',
      '  float drRim = drWash * (1.0 - drBody);',
      // The sill: full just under it, nothing above it, each run
      // starting a little lower and dying at its own length.
      '  float drL = mix(3.0, 12.0, drA) * uDripScale;',
      '  float drH = uDripFrom - vAstraWorld.y - drB * uDripScale;',
      '  float drS = smoothstep(0.0, uDripScale * 0.5, drH)',
      '            * (1.0 - smoothstep(drL * 0.4, drL, drH));',
      '  float drG = mix(1.0, drS, uDripGate) * drFlow * uDripAmt;',
      // What the rain LEACHED, before what it carried: the run's pale
      // halo is the surface's own colour lifted, so it belongs to
      // whatever it is running on — concrete, render, rusting steel —
      // instead of being a grey chosen here. It sits BESIDE the core,
      // never under it, which is the light-then-dark pairing that makes
      // a run read as a run at any distance.
      '  vec3 drSalt = diffuseColor.rgb * 1.34 + 0.030;',
      '  diffuseColor.rgb = mix(diffuseColor.rgb, drSalt,',
      '                         clamp(drRim * 0.85 * drG, 0.0, 1.0));',
      // ...and the dirt itself, hue-broken along the wall: two runs off
      // one sill are never the same brown, and one flat tone is the
      // tell that a texture was painted rather than deposited.
      '  vec3 drDirt = astraHueBreak(uDripColor, drP.xz * 0.7 + drP.y,',
      '                              0.35, 0.55) * (0.70 + 0.60 * drA);',
      '  float drAmt = clamp((0.42 * drBody + 0.58 * drCore) * drG,',
      '                      0.0, 1.0);',
      '  diffuseColor.rgb = mix(diffuseColor.rgb, drDirt, drAmt);',
    ].join('\n'),
  });
}

/**
 * Corrode where water SITS and RUNS, and bleed it downward.
 *
 * Rust sprayed evenly over a tank is a red tank; rust reads as rust
 * because it starts where water cannot leave — the upward faces, the
 * lee of a rim, the inside of a corner — and then bleeds down the face
 * below in the same patches that fed it. Both halves are here: a
 * pooling term from the world normal and the surface's own signed
 * curvature, and a running term on the faces that shed.
 *
 * THE BLEED IS THE FIELD'S DOING — the corrosion map is read from world
 * XZ only, so a whole vertical column of surface shares one value, and
 * a patch that eats the rim of a tank keeps eating straight down the
 * side under it. That is the one way a fragment, which cannot see the
 * surface above it, can know what drained onto it. A second field, its
 * vertical period in METRES rather than in patches and dragged 2 m
 * downward, ends the trail — without it a coarse `scale` paints one
 * column floor to ceiling — and THREE tones ride that field rather than
 * one brightness ramp: a near-black pitted crust in its lee, the oxide
 * through the body, and an ochre bloom where the crust is thinnest.
 * That grading is what stops a small subject — a 2.6 m tank, a pipe —
 * from reading as a single dark bruise.
 *
 * Curvature reads ZERO across a hard, unwelded edge (`BoxGeometry`'s
 * corners, anything flat-shaded), so the lee term needs bevelled or
 * smooth-shaded geometry; the pooling and bleeding terms work on
 * anything. And rust is a dielectric CRUST: per-pixel metalness is as
 * unreachable as per-pixel roughness from this hook, so on a shiny
 * `metalness: 1` tank the oxide tints the metal's own reflection and
 * comes out pink — give a rusting surface a low metalness yourself.
 * What this CAN do is take the whole material matte, and at full
 * strength it does (roughness x1.6, capped at fully rough): a corroded
 * tank that goes on mirroring the sky washes the oxide out of the frame
 * at every distance.
 *
 * @param {THREE.Material} material A built-in material, patched in
 *   place — a shared material rusts every mesh wearing it.
 * @param {object} [opts] `strength` how far the worst of it goes toward
 *   `color`, 0..1 (default 0.4); `color` THREE.Color or hex (default an
 *   iron oxide); `scale` metres across a corrosion patch (default 1.2,
 *   which also sets the corner radius the lee term looks for); `seed`
 *   moves the patches (default 1).
 * @returns {THREE.Material} The same material, with its uniforms live
 *   on `material.userData.uniforms`.
 */
export function patchRust(material, opts = {}) {
  const strength = opts.strength === undefined ? 0.4 : opts.strength;
  const scale = opts.scale === undefined ? 1.2 : opts.scale;
  const seed = opts.seed === undefined ? 1 : opts.seed;
  composeRoughness(material, 'aging:rust',
                   matteFactor(material, 0.60 * unit(strength)));
  patchStandard(material, BASE);
  return patchStandard(material, {
    name: 'aging:rust',
    uniforms: {
      uRustAmt: { value: unit(strength) },
      // An iron oxide with a blue channel. 0x70320f is 0.157/0.034/0.005
      // linear — the blue is BLACK, so the mid tone had nowhere to go
      // but a bruise, and the pit tone under it went to zero. A real
      // goethite/hematite crust sits near 0.25/0.09/0.035.
      uRustColor: { value: toColor(opts.color, 0x87542f) },
      uRustScale: { value: Math.max(1e-3, scale) },
      uRustSeed: { value: seedOffset(seed, 2.3) },
    },
    fragmentHead: [
      'uniform float uRustAmt;',
      'uniform vec3 uRustColor;',
      'uniform float uRustScale;',
      'uniform vec3 uRustSeed;',
    ].join('\n'),
    fragmentBody: [
      '  vec3 rsN = normalize(vAstraWorldN);',
      '  vec3 rsP = vAstraWorld / uRustScale + uRustSeed;',
      // Y-INDEPENDENT on purpose: one column of surface, one value, so
      // what corrodes a rim goes on corroding the face beneath it.
      '  float rsF = astraFbm2(rsP.xz, 3);',
      // The bleed's own texture, its vertical period in METRES rather
      // than in patches: how far a stain trails is a physical distance,
      // so a coarse map must not paint one column floor to ceiling.
      '  vec3 rsQ = vec3(rsP.x, vAstraWorld.y * 0.25, rsP.z) * 1.6;',
      '  vec3 rsA = astraAgeAxes(rsN);',
      '  float rsG = astraAgeStreak(rsQ, rsA, 1.0);',
      // The same field 2 m higher, faded in: the stain trails DOWN off
      // whatever fed it and dies a couple of metres on.
      '  float rsH = astraAgeStreak(rsQ + vec3(0.0, 0.8, 0.0), rsA, 1.0);',
      '  float rsB = max(rsG, rsH * 0.82);',
      // Where water SITS: on what faces up, and in the concave lee a
      // shape shelters from the rain that would rinse it.
      '  float rsC = astraAgeCurv(rsN, vAstraWorld);',
      '  float rsR = 1.0 / uRustScale;',
      '  float rsLee = smoothstep(rsR * 0.5, rsR * 2.0, -rsC);',
      '  float rsSit = clamp(smoothstep(-0.05, 0.55, rsN.y)',
      '                      + rsLee * 0.8, 0.0, 1.0);',
      // Where it RUNS: the faces that shed keep the patch that drained
      // onto them, textured — not gated — by the streak field.
      '  float rsDown = 1.0 - smoothstep(0.30, 0.90, abs(rsN.y));',
      '  float rsRun = rsDown',
      '              * (0.12 + 0.88 * smoothstep(0.40, 0.62, rsB));',
      // The patch edge is torn by the finer field, or corrosion has a
      // soft airbrushed boundary no oxide ever had.
      '  float rsK = smoothstep(0.46, 0.62, rsF + (rsB - 0.5) * 0.30)',
      '            * clamp(rsSit + rsRun, 0.0, 1.0);',
      '  float rsAmt = clamp(rsK * uRustAmt, 0.0, 1.0);',
      // THREE tones, not one brightness ramp. Corrosion is a stack: a
      // near-black pitted crust where it has eaten in, the oxide itself
      // across the body of the patch, and a thin ochre bloom at the
      // leading edge where it is still only a stain. A single colour
      // scaled 0.45..1.05 gives the last of those none of its hue and
      // reads as a dark bruise on anything cool-coloured.
      '  vec3 rsBase = astraHueBreak(uRustColor, rsP.xz, 0.8, 0.40);',
      '  vec3 rsPit = rsBase * vec3(0.38, 0.32, 0.30);',
      '  vec3 rsGlow = min(rsBase * vec3(1.30, 1.95, 1.70), vec3(0.82));',
      // The three tones ride the STREAK field, so the whole patch is
      // graded across rather than one flat oxide with a rim: pits in
      // its lee, the oxide through the body, ochre where the crust is
      // thin and freshly wet. Confining the bloom to the boundary (a
      // first attempt) left the patch reading as one dark bruise on
      // anything as small as a 2.6 m tank.
      '  vec3 rsCol = mix(rsPit, rsBase, smoothstep(0.16, 0.50, rsB));',
      '  rsCol = mix(rsCol, rsGlow, smoothstep(0.50, 0.78, rsB) * 0.60',
      '              + clamp(rsK * (1.0 - rsK) * 4.0, 0.0, 1.0) * 0.30);',
      '  diffuseColor.rgb = mix(diffuseColor.rgb, rsCol, rsAmt);',
    ].join('\n'),
  });
}

/**
 * Settle dust on what faces up, thickest where it is flat and sheltered.
 *
 * An interior or a long-idle exterior loses its contrast from the top
 * down: every upward face gets the same pale film, which is why a
 * spotless top surface reads as new. This is the cheapest of the three
 * — one cosine against the up axis, broken by a blotch field so it is a
 * settling rather than a wash — and the one that most changes how OLD a
 * room looks.
 *
 * The film is two tones at ONE value: the coarse grit that fell out
 * first sits warm in the drifts, the fine powder the sky keeps lighting
 * sits cool in the veil between them, both split by the same field that
 * sets the coverage. Equal value keeps the surface lightening evenly
 * while the hue moves under it (measured on the showcase slab: hue
 * spread 0.066 -> 0.080 at unchanged mean luminance) — a single grey
 * mixed over everything is a coat of paint, not a settling.
 *
 * `up` is a uniform vector, not world Y: dust settles along whatever
 * gravity the scene is telling, so a tilted asset, a capsized hull or a
 * deck under a listing ship dusts on the faces that actually point up.
 *
 * SHELTER is the surface's own signed curvature: a convex edge is swept
 * by every passing hand and by the rain, a concave corner keeps what
 * lands in it. Curvature reads ZERO across a hard unwelded edge, so on
 * `BoxGeometry` the shelter term simply does nothing and the cosine
 * carries it.
 *
 * @param {THREE.Material} material A built-in material, patched in
 *   place — a shared material dusts every mesh wearing it.
 * @param {object} [opts] `strength` how far the film goes toward
 *   `color` on a flat top, 0..1 (default 0.3); `color` THREE.Color or
 *   hex (default a pale grey); `up` THREE.Vector3 or [x, y, z], the
 *   world up (default +Y); `seed` moves the blotches (default 1).
 * @returns {THREE.Material} The same material, with its uniforms live
 *   on `material.userData.uniforms`, so `uDustUp` can follow a tilt.
 */
export function patchDust(material, opts = {}) {
  const strength = opts.strength === undefined ? 0.3 : opts.strength;
  const seed = opts.seed === undefined ? 1 : opts.seed;
  const up = upVector(opts.up);
  composeRoughness(material, 'aging:dust',
                   matteFactor(material, 0.55 * unit(strength)));
  patchStandard(material, BASE);
  return patchStandard(material, {
    name: 'aging:dust',
    uniforms: {
      uDustAmt: { value: unit(strength) },
      uDustColor: { value: toColor(opts.color, 0xb8b2a6) },
      uDustUp: { value: up },
      uDustSeed: { value: seedOffset(seed, 4.7) },
    },
    fragmentHead: [
      'uniform float uDustAmt;',
      'uniform vec3 uDustColor;',
      'uniform vec3 uDustUp;',
      'uniform vec3 uDustSeed;',
    ].join('\n'),
    fragmentBody: [
      '  vec3 duN = normalize(vAstraWorldN);',
      // Settling is a cosine against the scene's own up, not world Y.
      '  float duLay = smoothstep(0.02, 0.70,',
      '                           dot(duN, normalize(uDustUp)));',
      // Swept off what sticks out, held in what is sheltered.
      '  float duC = astraAgeCurv(duN, vAstraWorld);',
      '  float duHold = 1.0 - 0.85 * smoothstep(3.0, 10.0, duC)',
      '               + 0.35 * smoothstep(2.0, 8.0, -duC);',
      // Drifts at ~2 m with a grain inside them, or the film is a wash
      // of paint rather than something that landed.
      '  vec3 duP = vAstraWorld * 0.55 + uDustSeed;',
      '  vec3 duW = astraAgeAxes(duN);',
      '  float duB = astraAgeStreak(duP, duW, 1.0) * 0.65',
      '            + astraAgeStreak(duP * 3.7, duW, 1.0) * 0.35;',
      '  float duK = clamp(duLay * duHold',
      '                    * mix(0.30, 1.0, smoothstep(0.28, 0.78, duB)),',
      '                    0.0, 1.0);',
      // A settled film is not one grey. The drifts are the coarse warm
      // grit that fell out first; the veil between them is the fine
      // pale powder the sky keeps lighting. Splitting them by the same
      // field that sets the coverage costs nothing and is the whole
      // difference between dust and a coat of paint — the two tones sit
      // at the same VALUE, so the film still lightens evenly.
      '  vec3 duCol = astraHueBreak(uDustColor, duP.xz, 0.5, 0.35);',
      // Rec.709-matched on purpose (0.962 against 0.967): a warm/cool
      // pair that also differs in VALUE reads as a stain, not a film.
      '  duCol = mix(duCol * vec3(0.92, 0.96, 1.10),',
      '              duCol * vec3(1.12, 0.94, 0.78),',
      '              smoothstep(0.22, 0.80, duB));',
      '  diffuseColor.rgb = mix(diffuseColor.rgb, duCol,',
      '                         duK * uDustAmt);',
    ].join('\n'),
  });
}

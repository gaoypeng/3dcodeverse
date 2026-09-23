/**
 * Damp: what water leaves on ground and stone it never covers.
 *
 * `waterside` draws the waterline itself. These three are the metres
 * AROUND it — the growth on the side that never dries, the darker
 * ground that says water is close, and the polygons a bed cracks into
 * once the water has gone. All three go through `patchStandard`, so
 * lighting, shadows, fog and the depth chunks survive, and all three
 * shade from world position and world normal, so none needs a UV and
 * two objects sharing a scale share one climate.
 *
 * DIRECTION IS THE WHOLE EFFECT. Moss sprayed evenly is green paint;
 * what reads as moss is that it is on ONE side of every rock, wall and
 * trunk in the frame at once — so `north` (the world direction that
 * stays shaded) and `up` are uniforms every material can share, and the
 * sun-baked face is gated MULTIPLICATIVELY, where no amount of noise
 * can put growth on it.
 *
 * None of them ASSIGNS a colour: each lands on a material that may
 * already carry `patchTriplanar`, `patchSlopeSplat`, `patchMicroBreakup`
 * or an `aging` mark, and whichever assigned last would erase the rest
 * (measured: a splat over a triplanar did exactly that). Every one here
 * multiplies or mixes with what it is handed.
 *
 * Surface finish follows the local material masks through late PBR hooks.
 * Moss and dry silt are matte; damp ground and water-filled cracks are glossy.
 * Uncovered substrate keeps its own roughness and metalness.
 */

import * as THREE from 'three';
import {
  patchStandard, frac, glslAxes, glslCurv,
  glslTriNoise, seedVec3, toColor, unit, upVector,
  worldBase,
} from './shader.js';

/** Perturb the lit view normal from a local coating height in world metres. */
function coatingNormal(prefix, height) {
  return [
    `vec3 ${prefix}Dx = dFdx(-vViewPosition);`,
    `vec3 ${prefix}Dy = dFdy(-vViewPosition);`,
    `vec3 ${prefix}R1 = cross(${prefix}Dy, normal);`,
    `vec3 ${prefix}R2 = cross(normal, ${prefix}Dx);`,
    `float ${prefix}Det = dot(${prefix}Dx, ${prefix}R1);`,
    `float ${prefix}Height = ${height};`,
    `vec3 ${prefix}Gradient = sign(${prefix}Det) *`,
    `    (dFdx(${prefix}Height) * ${prefix}R1`,
    `    + dFdy(${prefix}Height) * ${prefix}R2);`,
    `if (abs(${prefix}Det) > 1e-12)`,
    `  normal = normalize(abs(${prefix}Det) * normal - ${prefix}Gradient);`,
  ].join('\n');
}

// All three patches read these, so they are declared once, in the base.
// astraDamp*, not a neighbour's spelling: patchStandard THROWS when two
// chained patches give one function name two different bodies.
const DAMP_HEAD = [
  glslAxes('astraDampAxes'),
  // Three world-plane projections blended by the normal: growth has to
  // break up the same way on a boulder, a wall and a bridge soffit.
  glslTriNoise('astraDampNoise', 29.3, 63.1, 97.7),
  // NEGATIVE curvature is the concave crevice that holds water.
  glslCurv('astraDampCurv'),
  'vec2 astraDampSite(vec2 c) {',
  '  return c + vec2(astraHash21(c), astraHash21(c + 37.7));',
  '}',
  // Voronoi, measured to the nearest cell BORDER rather than by
  // F2 - F1, which rounds every corner into a blob. Mud dries into
  // POLYGONS with straight edges and sharp corners, and that is the
  // whole difference between cracked mud and a dark noise.
  'vec2 astraDampCells(vec2 p) {',
  '  vec2 ci = floor(p);',
  '  vec2 cb = vec2(0.0), cr = vec2(0.0);',
  '  float cd = 8.0;',
  '  for (int y = -1; y <= 1; y++) {',
  '    for (int x = -1; x <= 1; x++) {',
  '      vec2 g = vec2(float(x), float(y));',
  '      vec2 r = astraDampSite(ci + g) - p;',
  '      float d = dot(r, r);',
  '      if (d < cd) { cd = d; cb = g; cr = r; }',
  '    }',
  '  }',
  '  cd = 8.0;',
  '  for (int y = -1; y <= 1; y++) {',
  '    for (int x = -1; x <= 1; x++) {',
  '      vec2 g = cb + vec2(float(x), float(y));',
  '      vec2 r = astraDampSite(ci + g) - p;',
  '      vec2 e = r - cr;',
  '      if (dot(e, e) > 1e-5) {',
  '        cd = min(cd, dot(0.5 * (cr + r), normalize(e)));',
  '      }',
  '    }',
  '  }',
  '  return vec2(cd, astraHash21(ci + cb + 0.37));',
  '}',
].join('\n');

// One base for all three, on the world varyings every library shares;
// the vertex locals are dm* because terrain_shade, waterside,
// surface_wear, aging and accumulation own astraWp, wsP, wrP, agP and acP.
const BASE = worldBase('damp:base', 'dmP', 'dmN', DAMP_HEAD);

// The constants differ from surface_wear's, aging's and accumulation's,
// or one seed would grow this library's moss along that library's
// blotches.
const seedOffset = (seed, salt) =>
  seedVec3(seed + salt, 2.71, 6.83, 11.29, 40);

/**
 * Read `north` as the HORIZONTAL direction that stays shaded.
 *
 * A compass direction has no component along `up`: one tipped toward
 * the sky would grow moss on every ceiling in the scene. Dropping that
 * component leaves a north parallel to up with no direction at all, so
 * that degenerate case takes any perpendicular instead.
 */
function northVector(value, up) {
  const v = new THREE.Vector3(0, 0, -1);
  if (value) v.fromArray(value.toArray ? value.toArray() : value);
  if (!(v.lengthSq() > 1e-9)) v.set(0, 0, -1);
  v.addScaledVector(up, -v.dot(up));
  if (!(v.lengthSq() > 1e-6)) {
    const axis = Math.abs(up.z) < 0.9
        ? new THREE.Vector3(0, 0, 1) : new THREE.Vector3(1, 0, 0);
    v.crossVectors(up, axis);
  }
  return v.normalize();
}



/**
 * Move one colour in HSL, wrapping the hue so a shift may cross 0.
 *
 * Every tone below is DERIVED, never a second option: a caller sets one
 * `color` per patch and the family is read off it, so a scene retuning
 * its moss cannot end up with a cushion and a crust from two different
 * plants — and so no tone can be lost by a caller who set only one.
 */
function shifted(base, dh, ds, lightness) {
  const hsl = { h: 0, s: 0, l: 0 };
  base.getHSL(hsl);
  return new THREE.Color().setHSL(
      frac(hsl.h + dh), Math.min(1, hsl.s * ds), unit(lightness(hsl.l)));
}

/**
 * The four tones of a moss cushion, and the two of a dried plate.
 *
 * ONE FLAT VALUE IS THE TELL. A cushion photographs as a hue RANGE, not
 * a colour: blue-black in the depth between the clumps, the body green
 * where it is thick, a yellow-green crown on the tips that see the sky,
 * and — where the moss thins — crustose lichen, which is a different
 * organism and a different colour, pale sage in its middle and ochre at
 * its rim. Painted as one green with a brightness ramp over it (which
 * is what this was), a metre of it reads as felt.
 *
 * Same for a bed that cracked: the plates did not all dry at the same
 * hour, so the ones that went first are pale and grey and the ones that
 * held their water are darker and warmer, and the CELL is what carries
 * that difference — a per-plate brightness alone reads as dirt on a
 * single chip of paint.
 *
 * THE SIGN OF EVERY HUE SHIFT IS THE PART THAT IS EASY TO GET WRONG
 * (measured: the first pass had both moss shifts inverted and rendered
 * an acid-green cushion with lavender crust). In HSL, hue RISES from
 * yellow through yellow-green to green to blue-green — so the CROWN,
 * which is new growth and yellower, shifts DOWN, and the depth, which
 * is shaded and blue-green, shifts UP. Same on the bed: the plate that
 * dried first is yellower (up from an orange-brown), the one that held
 * its water is redder (down).
 *
 * And the spread is SMALL. These are two tones of one substance, not
 * two substances: a swing wide enough to name reads as tiling.
 */
const lichenTone = (base) =>
    shifted(base, 0.020, 0.42, (l) => Math.min(0.50, l * 1.40 + 0.12));
const crustTone = (base) =>
    shifted(base, -0.150, 0.62, (l) => Math.min(0.46, l * 1.45 + 0.10));
const mossDeep = (base) =>
    shifted(base, 0.045, 1.22, (l) => Math.max(0.04, l * 0.50));
const mossCrown = (base) =>
    shifted(base, -0.045, 0.86, (l) => Math.min(0.52, l * 1.75 + 0.05));
const siltPale = (base) =>
    shifted(base, 0.018, 0.72, (l) => Math.min(0.72, l * 1.00 + 0.03));
const siltDamp = (base) =>
    shifted(base, -0.018, 1.18, (l) => Math.max(0.04, l * 0.82));

/**
 * Grow moss and lichen on the side that never dries — and only there.
 *
 * Moss sprayed evenly over a rock is green paint; what reads as moss is
 * that every rock, wall foot and trunk in the frame carries it on the
 * SAME side, thickest low down where water lingers and in the crevices
 * that hold it, and none at all on the face the sun bakes. Getting that
 * direction right is the whole effect, so `north` — the world direction
 * that stays shaded — is a uniform every material in a scene can share.
 *
 * FOUR TERMS DECIDE WHERE IT HOLDS. The SHADE is the cosine against
 * `north`. Level faces hold rain whatever they face, so the cosine
 * against `up` adds. CREVICES are the surface's own signed curvature:
 * the concave lee keeps what a convex edge sheds — and curvature reads
 * ZERO across a hard, unwelded edge (`BoxGeometry`'s corners, anything
 * flat-shaded), so there the two cosines carry it alone. And it thins
 * with height above the plane through the world origin, where an asset
 * rests: splash, ground damp and shade all live at the foot. That foot
 * is world-absolute, so a scene standing its ground at y = 40 gets the
 * thin end of the fade everywhere rather than a wrong answer.
 *
 * THE SUN GATE IS MULTIPLICATIVE. A face turned to the sun loses the
 * growth however generous the field is, keeping it only in the crevices
 * that shade themselves — which is what stops this becoming an even
 * green wash the moment `amount` is raised. Lichen rides the same gate
 * at a finer grain and without the foot fade, because it crusts the
 * exposed shaded stone that moss cannot hold.
 *
 * It MIXES over the albedo it is handed (and shades the material just
 * outside its edge, the only thickness a patch that cannot displace
 * geometry has), so a triplanar, a splat or a stain underneath survives
 * everywhere the moss is thin. Gloss here is per MATERIAL, not per pixel:
 * moss is the mattest thing on any rock, so its factor goes UP.
 *
 * @param {THREE.Material} material A built-in material, patched in
 *   place — a shared material from `materials.js` grows moss on every
 *   mesh wearing it, so clone it first (and clone BEFORE patching).
 * @param {object} [opts] `amount` how much of the shaded side is taken,
 *   0..1 (default 0.45; 0.15 is a trace in the crevices, 0.9 is a
 *   cushion); `color` THREE.Color or hex, the moss (default a deep damp
 *   green — the cushion's deep and crown tones and BOTH lichen tones
 *   are derived from it, so one option moves the whole family); `up`
 *   THREE.Vector3 or [x, y, z], the scene's up (default +Y); `north`
 *   THREE.Vector3 or
 *   [x, y, z], the world direction that stays SHADED, horizontal
 *   (default -Z); `seed` moves the growth (default 1).
 * @returns {THREE.Material} The same material, with its uniforms live
 *   on `material.userData.uniforms`, so `uMossNorth` can swing with the
 *   sun a scene was lit for.
 */
export function patchMoss(material, opts = {}) {
  const amount = opts.amount === undefined ? 0.45 : unit(opts.amount);
  const seed = opts.seed === undefined ? 1 : opts.seed;
  const up = upVector(opts.up);
  const color = toColor(opts.color, 0x40592a);
  // Moss is matte and it takes the gloss over only as far as it takes
  // the surface over.

  patchStandard(material, BASE);
  return patchStandard(material, {
    name: 'damp:moss',
    uniforms: {
      uMossAmt: { value: amount },
      uMossColor: { value: color },
      uMossDeep: { value: mossDeep(color) },
      uMossCrown: { value: mossCrown(color) },
      uMossLichen: { value: lichenTone(color) },
      uMossCrust: { value: crustTone(color) },
      uMossUp: { value: up },
      uMossNorth: { value: northVector(opts.north, up) },
      uMossSeed: { value: seedOffset(seed, 0.4) },
    },
    fragmentHead: [
      'uniform float uMossAmt;',
      'uniform vec3 uMossColor;',
      'uniform vec3 uMossDeep;',
      'uniform vec3 uMossCrown;',
      'uniform vec3 uMossLichen;',
      'uniform vec3 uMossCrust;',
      'uniform vec3 uMossUp;',
      'uniform vec3 uMossNorth;',
      'uniform vec3 uMossSeed;',
    ].join('\n'),
    roughnessBody: 'roughnessFactor = mix(roughnessFactor, 0.97, clamp((msK + msLiK) * msOn, 0.0, 1.0));',
    metalnessBody: 'metalnessFactor *= 1.0 - clamp((msK + msLiK) * msOn, 0.0, 1.0);',
    normalBody: coatingNormal('msBump',
      'msOn * (msK * (0.001 + 0.003 * msG) + msLiK * 0.0008)'
      + ' * (1.0 - smoothstep(0.03, 0.10, length(fwidth(vAstraWorld))))'),
    fragmentBody: [
      '  vec3 msN = normalize(vAstraWorldN);',
      '  vec3 msUp = normalize(uMossUp);',
      '  vec3 msNo = normalize(uMossNorth);',
      // The shaded side: full on the face that looks north, gone on the
      // one that looks at the sun. This is the whole effect.
      '  float msFace = dot(msN, msNo);',
      '  float msShade = smoothstep(-0.20, 0.55, msFace);',
      '  float msLie = smoothstep(0.05, 0.70, dot(msN, msUp));',
      // Crevices hold what an edge sheds. ~0.35 m features, the size of
      // the hollows a cushion of moss actually fills.
      '  float msCv = astraDampCurv(msN, vAstraWorld);',
      '  float msLee = smoothstep(1.2, 6.0, -msCv);',
      '  float msShed = smoothstep(1.8, 7.5, msCv);',
      // Water lingers at the FOOT — splash, ground damp, and the shade
      // of whatever is standing there — measured along `up` from the
      // plane through the world origin, where an asset rests.
      '  float msFoot = 1.0 - smoothstep(0.30, 2.60,',
      '                                  dot(vAstraWorld, msUp));',
      '  float msHold = clamp(msShade * 0.85 + msLie * 0.30',
      '                       + msLee * 0.70 - msShed * 0.55, 0.0, 1.6);',
      '  msHold *= mix(0.30, 1.0, msFoot);',
      // MULTIPLICATIVE, so no field can put moss on a baked face: it
      // survives there only in the crevices that shade themselves.
      '  float msSun = smoothstep(0.05, 0.75, -msFace)',
      '              * (1.0 - msLee * 0.65);',
      '  float msDry = 1.0 - clamp(msSun, 0.0, 1.0);',
      '  msHold *= msDry;',
      '  vec3 msW = astraDampAxes(msN);',
      '  vec3 msP = vAstraWorld * 3.2 + uMossSeed;',
      // Three octaves, not one: a cushion has clumps inside it and a
      // torn edge, and one octave is an airbrush.
      '  float msG = astraDampNoise(msP, msW) * 0.55',
      '            + astraDampNoise(msP * 2.7, msW) * 0.28',
      '            + astraDampNoise(msP * 7.9, msW) * 0.17;',
      '  float msD = msHold * (0.42 + 1.10 * msG);',
      // One threshold, torn by its own screen gradient: a clean edge is
      // paint, and moss has none anywhere.
      '  float msT = mix(1.05, 0.26, uMossAmt);',
      '  float msAA = clamp(fwidth(msD), 0.0, 0.35);',
      '  float msK = smoothstep(msT - 0.05 - msAA, msT + 0.14 + msAA, msD);',
      // Lichen: the same direction, a finer grain, and no foot fade —
      // it crusts the shaded stone above where moss can hold. TWO
      // octaves, because one gives round blobs at one size, which read
      // as mould spots rather than as a crust with a torn rim.
      '  float msLi = astraDampNoise(msP * 7.7 + 5.3, msW) * 0.68',
      '             + astraDampNoise(msP * 19.0 + 11.7, msW) * 0.32;',
      '  float msLiK = smoothstep(0.52, 0.78, msLi) * msShade * msDry',
      '              * (1.0 - msK) * max(msLie, 0.35);',
      // The material just OUTSIDE the edge stays damp under the growth.
      '  float msLip = (1.0 - msK)',
      '              * smoothstep(msT - 0.55, msT - 0.02, msD);',
      // `amount` 0 is OFF, which a threshold alone cannot say: a deep
      // enough crevice would clear any threshold. Declared here with
      // the rest of the MASK, so everything above this line is scalar
      // maths a probe can lift out and evaluate against a swept normal.
      '  float msOn = smoothstep(0.0, 0.05, uMossAmt);',
      '  msLip *= msOn;',
      '  diffuseColor.rgb *= 1.0 - 0.20 * msLip;',
      // THE CUSHION IS A HUE RANGE. Deep in the shade between the
      // clumps, body green where it is thick, a yellow-green crown on
      // the tips that see the sky — the field that tore the edge is the
      // same field that says which of the three a pixel is.
      '  vec3 msCol = mix(uMossDeep, uMossColor,',
      '                   smoothstep(0.14, 0.60, msG));',
      '  msCol = mix(msCol, uMossCrown,',
      '              0.80 * smoothstep(0.58, 0.98,',
      '                                msG * (0.78 + 0.44 * msLi)));',
      // Warm where the sun grazes it, cool where only the sky reaches —
      // projected on the north/up plane, so a boulder and a wall break
      // the same way and nothing needs a UV. Never on world Y.
      '  vec2 msQ = vec2(dot(msP, msNo), dot(msP, msUp));',
      '  msCol = astraHueBreak(msCol, msQ, 0.55, 0.20);',
      // The crust is two colours too: pale sage where it is thick,
      // ochre where it is thinning back to the stone.
      '  vec3 msCr = mix(uMossCrust, uMossLichen,',
      '                  smoothstep(0.50, 0.84, msLi));',
      // `amount` moved the THRESHOLD, so where the mask is full the
      // growth is nearly opaque; multiplying by it again is a wash.
      '  diffuseColor.rgb = mix(diffuseColor.rgb, msCol,',
      '                         clamp(msK * msOn',
      '                               * mix(0.60, 1.0, uMossAmt), 0.0, 1.0));',
      '  diffuseColor.rgb = mix(diffuseColor.rgb, msCr,',
      '                         clamp(msLiK * msOn',
      '                               * mix(0.30, 0.62, uMossAmt),',
      '                               0.0, 1.0));',
    ].join('\n'),
  });
}

/**
 * Darken and gloss the ground as it approaches water, out to `reach`.
 *
 * Ground within a few metres of a shoreline is visibly darker and
 * glossier than the ground beyond it — the pores are full — and without
 * that apron a lake sits on the land like a mirror dropped on a lawn.
 * The edge is irregular, because the damp follows where the ground
 * dips, never a contour.
 *
 * WHERE IT MEETS `patchShoreWet`. That patch owns the WATERLINE: the
 * decimetres either side of `level`, fully wet below it and fading out
 * over a `band` of ~0.25 m above. This owns the METRES beyond that, and
 * the two are meant to be worn together — set `reach` to several metres
 * and the apron hands over to the tide mark, which is darker again.
 * They compose MULTIPLICATIVELY (both scale the albedo they are handed,
 * neither assigns), so the overlap deepens rather than fighting; keep
 * `strength` under `patchShoreWet`'s `darken` or the apron reads as the
 * waterline and the real one disappears into it.
 *
 * REACH IS A HEIGHT, NOT A PLAN DISTANCE. A fragment sees its own
 * position and nothing else — it cannot find where the shoreline runs —
 * so the measure is world Y above `waterY`, exactly as
 * `patchShallowWater` measures depth. A water surface is level by
 * definition, so world Y is right here in a way it never is for a
 * deposit, and it needs a bank that RISES: on ground climbing 1 m over
 * 4 m, `reach: 1` darkens the first 4 m of it. Flat ground at the water's
 * own level is damp all over, which is what a salt flat looks like.
 *
 * It MULTIPLIES the albedo it is handed and deepens its colour, so a
 * triplanar, a splat or a wear pattern underneath comes through the
 * apron rather than being replaced by it. The same local moisture mask
 * lowers roughness; uncovered substrate retains its authored finish.
 *
 * @param {THREE.Material} material A built-in material, patched in
 *   place — a shared material damps every mesh wearing it.
 * @param {object} [opts] `waterY` world Y of the water surface (default
 *   0); `reach` metres of HEIGHT above it the damp climbs, zero beyond
 *   (default 1.5); `strength` how dark it goes at the water's edge,
 *   0..1 (default 0.45); `seed` moves the irregular edge (default 1).
 * @returns {THREE.Material} The same material, with its uniforms live
 *   on `material.userData.uniforms`, so `uMoistY` can follow a tide.
 */
export function patchMoisture(material, opts = {}) {
  const waterY = opts.waterY === undefined ? 0 : opts.waterY;
  const reach = opts.reach === undefined ? 1.5 : Math.max(1e-3, opts.reach);
  const strength = opts.strength === undefined ? 0.45 : unit(opts.strength);
  const seed = opts.seed === undefined ? 1 : opts.seed;
  // Damp ground is the glossiest thing on a shore that is not water —
  // but rough ground retains a broad wet-soil highlight. Already polished
  // substrate must never become rougher when the moisture mask increases.

  patchStandard(material, BASE);
  return patchStandard(material, {
    name: 'damp:moisture',
    uniforms: {
      uMoistY: { value: waterY },
      uMoistReach: { value: reach },
      uMoistAmt: { value: strength },
      uMoistSeed: { value: seedOffset(seed, 2.9) },
    },
    fragmentHead: [
      'uniform float uMoistY;',
      'uniform float uMoistReach;',
      'uniform float uMoistAmt;',
      'uniform vec3 uMoistSeed;',
    ].join('\n'),
    roughnessBody: 'roughnessFactor = mix(roughnessFactor, min(roughnessFactor, max(0.30, roughnessFactor * 0.68)), moA);',
    fragmentBody: [
      '  float moH = vAstraWorld.y - uMoistY;',
      '  float moT = clamp(moH / uMoistReach, 0.0, 1.0);',
      // The edge wanders: a level contour is a ruled line no bank has.
      '  float moN = astraFbm2(vAstraWorld.xz * 0.55',
      '                        + uMoistSeed.xz, 3) - 0.44;',
      // The wander dies at BOTH ends of the ramp — the waterline is
      // saturated whatever the noise says and `reach` is the reach — so
      // it can only ever move the edge inside the apron.
      '  float moW = clamp(moT + moN * 3.4 * moT * (1.0 - moT), 0.0, 1.0);',
      // A capillary margin has an EDGE, not a gradient across the whole
      // apron: the ramp is steep and the wander is what shapes it.
      '  float moK = 1.0 - smoothstep(0.10, 0.80, moW);',
      // Mottled, or the apron is a painted band; the field is centred
      // so it cannot push the damp past the ramp either.
      '  float moB = astraFbm2(vAstraWorld.xz * 1.9 + uMoistSeed.zx, 3);',
      '  float moA = clamp(moK * uMoistAmt * (0.70 + 0.66 * moB),',
      '                    0.0, 1.0);',
      // THE CAPILLARY RIM. Water climbing a bank carries the ground's
      // own dust with it and leaves it as a pale mineral bloom at the
      // exact line it stops — which is the only thing that gives the
      // apron an EDGE from twenty metres, where the darkening alone is
      // a gradient with nothing to read. It rides the same wander, so
      // the rim follows the damp rather than ruling its own contour,
      // and BOTH ends are gated so nothing survives at or past `reach`.
      '  float moR = smoothstep(0.55, 0.85, moW)',
      '            * (1.0 - smoothstep(0.85, 1.00, moW))',
      '            * (1.0 - smoothstep(0.80, 1.00, moT))',
      '            * uMoistAmt * (0.55 + 0.55 * moB);',
      // A third of a code value of screen dither: the ramp runs over
      // metres of near-flat ground and there is no post chain here to
      // break the steps for us. Gated by the apron's own strength, so
      // a surface this patch does not reach is left untouched.
      '  float moD = (astraHash21(gl_FragCoord.xy) - 0.5) * 0.008',
      '            * max(moA, moR);',
      // Wet ground is darker AND deeper in colour: pushing away from
      // its own luminance is the saturation half of that. One write,
      // folding in what it was handed — a patch that ASSIGNED here
      // would erase the triplanar it lands on, and the bloom is that
      // same albedo lifted and warmed rather than a colour of its own.
      '  float moL = dot(diffuseColor.rgb, vec3(0.2126, 0.7152, 0.0722));',
      '  diffuseColor.rgb = clamp(',
      '      mix(mix(vec3(moL), diffuseColor.rgb, 1.0 + 0.45 * moA)',
      '          * (1.0 - 0.55 * moA),',
      '          vec3(moL) * vec3(1.62, 1.54, 1.40), 0.42 * moR)',
      '      + moD, 0.0, 1.0);',
    ].join('\n'),
  });
}

/**
 * Crack a dried bed into curling polygons — cracks dark, plates pale.
 *
 * A lakebed, a paddy field in drought or the last puddle in a yard all
 * dry the same way: the surface shrinks, tears into Voronoi polygons a
 * hand's width across, and every plate curls up at its edges. A noise
 * field cannot fake it, because what the eye reads is the STRAIGHT
 * edges and sharp corners of the cells — so this measures the distance
 * to the nearest cell BORDER (not F2 - F1, which rounds every corner
 * off) and thresholds that into a crack.
 *
 * THE CURL IS THE SECOND HALF. A patch cannot displace geometry — a
 * vertex displacement would tear a hard-edged bed open at its rim — so
 * the lifted lip is a bright band just inside each crack and the crack
 * itself is a dark line, which is exactly what a curled plate does to
 * the light. `depth` widens the crack, darkens it and lifts the lip
 * together, because a bed that dried harder did all three.
 *
 * `wet` fills the cracks with water instead of shadow: the network goes
 * dark and cold rather than dusty, the plates keep their pale dry tops,
 * and the material's gloss goes the other way — a drying bed is matte,
 * a bed whose cracks still hold water is not.
 *
 * THE PLATES ARE READ FROM WORLD XZ, because a bed is level by
 * definition (unlike a deposit, which needs the scene's own `up`), and
 * the pattern thins from about 28 degrees off level and is gone by 57,
 * where that projection would smear into stripes. The cell field is
 * warped by a slow noise first, or every plate is the same size — and
 * the same warp opens the cracks in one patch and leaves hairlines in
 * the next, because a bed does not dry evenly.
 *
 * It MIXES over the albedo it is handed, weighted by how level the
 * surface is, so a triplanar or a splat underneath still carries the
 * bank while the flat of the bed cracks.
 *
 * @param {THREE.Material} material A built-in material, patched in
 *   place — a shared material cracks every mesh wearing it.
 * @param {object} [opts] `scale` metres across one plate (default 0.35
 *   — 0.1 is a sun-baked crust, 1.0 a lakebed); `depth` how far the
 *   drying has gone, 0..1: crack width, darkness and curl together
 *   (default 0.5); `color` THREE.Color or hex, the dry plate (default a
 *   pale silt); `wet` 0..1 how much water is still in the cracks
 *   (default 0); `seed` moves the cells (default 1).
 * @returns {THREE.Material} The same material, with its uniforms live
 *   on `material.userData.uniforms`, so `uMudWet` can dry over a shot.
 */
export function patchCrackedMud(material, opts = {}) {
  const scale = opts.scale === undefined ? 0.35 : Math.max(1e-3, opts.scale);
  const depth = opts.depth === undefined ? 0.5 : unit(opts.depth);
  const wet = opts.wet === undefined ? 0 : unit(opts.wet);
  const seed = opts.seed === undefined ? 1 : opts.seed;
  const mudColor = toColor(opts.color, 0x9c8a71);
  // Dry silt is matte; wet cracks carry narrower highlights. Both finishes
  // use the existing mud mask instead of changing the entire substrate.

  patchStandard(material, BASE);
  return patchStandard(material, {
    name: 'damp:mud',
    uniforms: {
      uMudScale: { value: scale },
      uMudDepth: { value: depth },
      uMudColor: { value: mudColor },
      uMudPale: { value: siltPale(mudColor) },
      uMudDamp: { value: siltDamp(mudColor) },
      uMudWet: { value: wet },
      uMudSeed: { value: seedOffset(seed, 5.1) },
    },
    fragmentHead: [
      'uniform float uMudScale;',
      'uniform float uMudDepth;',
      'uniform vec3 uMudColor;',
      'uniform vec3 uMudPale;',
      'uniform vec3 uMudDamp;',
      'uniform float uMudWet;',
      'uniform vec3 uMudSeed;',
    ].join('\n'),
    roughnessBody: 'roughnessFactor = mix(roughnessFactor, mix(0.95, 0.28, uMudWet * (0.32 + 0.68 * mdCrk)), mdAmt);',
    metalnessBody: 'metalnessFactor *= 1.0 - mdAmt;',
    normalBody: coatingNormal('mdBump',
      'mdAmt * uMudDepth * uMudScale * (mdLip * 0.025 - mdCrk * 0.010)'
      + ' * (1.0 - smoothstep(0.025, 0.12, length(fwidth(vAstraWorld))))'),
    fragmentBody: [
      '  vec3 mdN = normalize(vAstraWorldN);',
      // A bed is level: the cells are read from world XZ, and a face
      // past ~45 degrees would smear that projection into stripes.
      '  float mdLie = smoothstep(0.55, 0.88, mdN.y);',
      '  vec2 mdP = vAstraWorld.xz / uMudScale + uMudSeed.xz;',
      // Warped first, or every plate is the same size and the network
      // reads as a tiling rather than as a surface that tore.
      '  vec2 mdWp = vec2(astraFbm2(mdP * 0.31 + 7.1, 3),',
      '                   astraFbm2(mdP * 0.31 + 23.9, 3)) - 0.44;',
      '  vec2 mdC = astraDampCells(mdP + mdWp * 1.10);',
      // Crack width in CELLS, so one `depth` widens, darkens and lifts;
      // water standing in a crack spreads it wider still.
      '  float mdW = mix(0.004, 0.022, uMudDepth)',
      '            * mix(1.0, 1.6, uMudWet)',
      // A bed does not dry evenly: the same warp opens the cracks in
      // one patch of it and leaves hairlines in the next.
      '            * (1.0 + mdWp.x * 1.5);',
      '  float mdAA = clamp(fwidth(mdC.x), 0.0, 0.25);',
      '  float mdCrk = 1.0 - smoothstep(mdW, mdW * 2.6 + mdAA, mdC.x);',
      // The lip: a plate curls UP at its edge, so the band just inside
      // each crack catches the light the crack itself loses — and a
      // soaked lip has nothing to catch it with.
      '  float mdLip = smoothstep(mdW * 2.0, mdW * 5.5, mdC.x)',
      '              * (1.0 - smoothstep(mdW * 6.0, mdW * 15.0, mdC.x))',
      '              * (1.0 - 0.70 * uMudWet);',
      // PER-PLATE TONE, AND IT IS A HUE. The plates did not all dry at
      // the same hour: the ones that went first are pale and grey, the
      // ones that held their water are darker and warmer, and the CELL
      // hash is what makes them pieces rather than a pattern. A
      // brightness ramp alone (which is what this was) reads as dirt on
      // one flat chip of paint.
      '  float mdG = astraFbm2(mdP * 3.3 + uMudSeed.zx, 3);',
      '  vec3 mdPl = mix(uMudDamp, uMudPale,',
      '                  clamp(mdC.y * 1.16 - 0.08, 0.0, 1.0));',
      '  mdPl *= (0.92 + 0.18 * mdG) * mix(1.0, 0.78, uMudWet);',
      // Silt grain INSIDE the plate, at a finer scale than the plates:
      // warm where the sun bakes it, cool in the film of shade a curl
      // throws over itself.
      '  mdPl = astraHueBreak(mdPl, mdP * 1.7, 1.0, 0.18);',
      // A crack is a groove lit only by the sky, so its shadow is COOL
      // — the one place on a sun-baked bed that is not warm.
      '  vec3 mdDk = uMudColor * mix(0.34, 0.16, uMudDepth)',
      '            * vec3(0.86, 0.95, 1.16);',
      // Water in the crack, not shadow: far darker and cold, because a
      // filled crack is a window into it rather than a shaded groove.
      '  vec3 mdWc = uMudColor * 0.09 * vec3(0.75, 0.95, 1.30);',
      '  vec3 mdCol = mix(mdPl, mix(mdDk, mdWc, uMudWet), mdCrk);',
      // The lip catches the SUN, so it is warmer as well as brighter —
      // a lip that only brightens reads as a highlight on plastic.
      '  mdCol = mix(mdCol, mdCol * vec3(1.06, 1.02, 0.97),',
      '              clamp(mdLip * 1.30, 0.0, 1.0));',
      '  mdCol *= 1.0 + mdLip * mix(0.04, 0.12, uMudDepth);',
      '  float mdAmt = clamp(mdLie * mix(0.55, 1.0, uMudDepth), 0.0, 1.0);',
      '  diffuseColor.rgb = mix(diffuseColor.rgb, mdCol, mdAmt);',
    ].join('\n'),
  });
}

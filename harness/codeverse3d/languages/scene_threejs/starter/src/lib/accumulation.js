/**
 * Accumulation: what LIES on a scene, from one rule on every surface.
 *
 * A scene reads as WEATHER when the same stuff is on everything at
 * once — snow on the roof, the sill, the branch and the rock; sand
 * banked against the windward side of every wall. One white-topped
 * material is a prop, twenty materials sharing one deposit rule is a
 * storm, so both patches shade from world position and world normal
 * only: no UV, no per-object setup, and two objects sharing a scale
 * share one weather.
 *
 * Both go through `patchStandard`, so lighting, shadows, fog and the
 * depth chunks survive, and both CHAIN with `surface_wear`,
 * `terrain_shade`, `waterside` and `aging` on one material.
 *
 * THE CONSTRAINT IS THAT NEITHER HAS THICKNESS. `fragmentBody` edits
 * albedo and the lit micro-normal; vertex displacement would tear every
 * hard-edged BoxGeometry open at its rim, because the top face's
 * vertices carry a different normal from the side's and would walk
 * away from them. So a layer sells its depth by what it COVERS (steep
 * faces lose it, concave lees keep it), by a torn edge instead of a
 * ruled one, and by the shade it drops on the material just outside
 * that edge. Roughness and metalness follow the same local coverage as
 * the albedo; bare faces retain the authored substrate finish.
 */

import * as THREE from 'three';
import {
  patchStandard, glslAxes, glslCurv, glslTriNoise,
  seedVec3, toColor, unit, upVector, worldBase,
} from './shader.js';

// Both patches read these, so they are declared once, in the base.
// astraAcc*, not a neighbour's spelling: patchStandard THROWS when two
// chained patches give one function name two different bodies.
const ACC_HEAD = [
  glslAxes('astraAccAxes'),
  // Three world-plane projections blended by the normal: a deposit
  // must break up the same way on a wall, a roof and a boulder.
  glslTriNoise('astraAccNoise', 23.1, 57.7, 91.3),
  // NEGATIVE curvature is the concave lee that holds what a convex
  // edge sheds.
  glslCurv('astraAccCurv'),
].join('\n');

// One base for both, on the world varyings every library shares; the
// vertex locals are ac* because terrain_shade, waterside, surface_wear
// and aging own astraWp, wsP, wrP and agP.
const BASE = worldBase('acc:base', 'acP', 'acN', ACC_HEAD);

// The constants differ from surface_wear's and aging's, or one seed
// would drift this library's snow along that library's blotches.
const seedOffset = (seed, salt) =>
  seedVec3(seed + salt, 1.61, 8.09, 13.77, 56);

/**
 * Read `wind` as a direction it blows TOWARD, length 0..1 = strength.
 *
 * Length rather than a second option: zero wind must mean an even fall
 * with no drift axis at all, and normalizing a zero vector is NaN.
 */
function windVector(value, fallback) {
  const v = new THREE.Vector3();
  if (value) v.fromArray(value.toArray ? value.toArray() : value);
  else if (fallback) v.fromArray(fallback);
  if (v.lengthSq() > 1) v.normalize();
  return v;
}

/**
 * Lay snow on everything at once, by how each surface is turned.
 *
 * Snow is the deposit that most changes a scene, because it lands on
 * every upward face in it from one rule: a roof with a white cap over
 * a bare sill, a bare branch and a bare rock is a prop in a snow
 * scene, and the fix is not more painting but the SAME patch on every
 * material. What makes it snow rather than white paint is where it
 * stops — and it stops in four ways.
 *
 * DEPOSIT is the cosine against `up` (the scene's own gravity, not
 * world Y — a tilted asset or a listing hull snows on the faces that
 * really point up), thinning as a face steepens and reaching NOTHING
 * past about 58 degrees, the angle snow slides off at. That gate is
 * MULTIPLICATIVE, so no amount of noise can put snow on a wall.
 * SHELTER is the surface's own signed curvature: a convex edge sheds
 * what a concave lee keeps, and how small a feature the fall can bury
 * goes with how deep it lies. Curvature reads ZERO across a hard,
 * unwelded edge (`BoxGeometry`'s corners, anything flat-shaded), so
 * there the cosine carries it alone. WIND scours the face it meets and
 * leaves the lee packed. And the EDGE is a threshold on a two-octave
 * world field, torn by its own screen gradient, with a soft shade laid
 * on the material just outside it — the only thickness a patch that
 * cannot displace geometry has.
 *
 * `melt` gives the material back from the bottom up: the thaw line
 * climbs from the plane through the world origin, which is where an
 * asset's foot rests, so a wall's footing goes bare while its coping
 * stays white. Passed `{ amount, at, radius }` it also eats a hole
 * around one warm world point — a flue, a lamp, a vent.
 *
 * @param {THREE.Material} material A built-in material, patched in
 *   place — a shared material from `materials.js` snows every mesh
 *   wearing it, so clone it first (and clone BEFORE patching).
 * @param {object} [opts] `depth` metres of lying snow, which sets how
 *   far down a pitch it holds and how opaque it is (default 0.05; 0.01
 *   is a dusting, 0.15 buries); `color` THREE.Color or hex (default a
 *   neutral white below 0.78 linear — snow is the brightest albedo in a scene
 *   and still has to sit UNDER the tone curve's shoulder, or its own
 *   shading clips to one flat card); `up` THREE.Vector3 or [x, y, z], the scene's up
 *   (default +Y); `wind` THREE.Vector3 or [x, y, z] it blows TOWARD,
 *   its LENGTH 0..1 the strength (default none, an even fall);
 *   `melt` 0..1 how high the thaw has climbed, or
 *   `{ amount, at, radius }` to melt around a warm point as well
 *   (default 0); `seed` moves the drifts (default 1).
 * @returns {THREE.Material} The same material, with its uniforms live
 *   on `material.userData.uniforms`, so `uSnowMelt` can thaw over a
 *   shot and `uSnowDepth` can deepen through a storm.
 */
export function patchSnow(material, opts = {}) {
  const depth = opts.depth === undefined ? 0.05 : Math.max(0, opts.depth);
  const seed = opts.seed === undefined ? 1 : opts.seed;
  const melt = opts.melt;
  const meltAmt = unit(
      typeof melt === 'number' ? melt : (melt && melt.amount) || 0);
  const at = (melt && melt.at) || null;
  const warm = new THREE.Vector3();
  if (at) warm.fromArray(at.toArray ? at.toArray() : at);
  const warmR = at
      ? Math.max(1e-3, melt.radius === undefined ? 0.9 : melt.radius) : 0;
  // Snow is a diffuse cover with a faint sheen, and it takes the
  // material's gloss over only as far as it takes the surface over.

  patchStandard(material, BASE);
  return patchStandard(material, {
    name: 'acc:snow',
    uniforms: {
      uSnowDepth: { value: depth },
      uSnowColor: { value: toColor(opts.color, 0xe0e2e3) },
      uSnowUp: { value: upVector(opts.up) },
      uSnowWind: { value: windVector(opts.wind, null) },
      uSnowMelt: { value: meltAmt },
      uSnowWarm: { value: new THREE.Vector4(warm.x, warm.y, warm.z, warmR) },
      uSnowSeed: { value: seedOffset(seed, 0.7) },
    },
    fragmentHead: [
      'uniform float uSnowDepth;',
      'uniform vec3 uSnowColor;',
      'uniform vec3 uSnowUp;',
      'uniform vec3 uSnowWind;',
      'uniform float uSnowMelt;',
      'uniform vec4 uSnowWarm;',
      'uniform vec3 uSnowSeed;',
    ].join('\n'),
    roughnessBody: 'roughnessFactor = mix(roughnessFactor, 0.86, snAmt);',
    metalnessBody: 'metalnessFactor *= 1.0 - snAmt;',
    normalBody: [
      'vec3 snDx = dFdx(-vViewPosition);',
      'vec3 snDy = dFdy(-vViewPosition);',
      'vec3 snR1 = cross(snDy, normal);',
      'vec3 snR2 = cross(normal, snDx);',
      'float snDet = dot(snDx, snR1);',
      'float snHeight = snAmt * snF * snFad * 0.0009;',
      'vec3 snGradient = sign(snDet) *',
      '    (dFdx(snHeight) * snR1 + dFdy(snHeight) * snR2);',
      'if (abs(snDet) > 1e-12)',
      '  normal = normalize(abs(snDet) * normal - snGradient);',
    ].join('\n'),
    fragmentBody: [
      '  vec3 snN = normalize(vAstraWorldN);',
      '  vec3 snUp = normalize(uSnowUp);',
      // Depth in metres decides how much this fall can bury and how
      // little of the material is left showing through it.
      '  float snCov = clamp(uSnowDepth / 0.06, 0.0, 1.0);',
      '  vec3 snW = astraAccAxes(snN);',
      '  vec3 snP = vAstraWorld * mix(2.6, 1.3, snCov) + uSnowSeed;',
      '  float snG = astraAccNoise(snP, snW) * 0.62',
      '            + astraAccNoise(snP * 4.3, snW) * 0.38;',
      // Wind combs a drift OUT along itself: the same field with its
      // wind axis compressed makes every feature four times as long.
      '  float snWl = clamp(length(uSnowWind), 0.0, 1.0);',
      '  vec3 snWv = uSnowWind / max(length(uSnowWind), 1e-4);',
      '  vec3 snQ = vAstraWorld - snWv * dot(vAstraWorld, snWv) * 0.74;',
      '  snG = mix(snG, snG * 0.45',
      '            + astraAccNoise(snQ * 2.6 + uSnowSeed, snW) * 0.55, snWl);',
      // How far the surface faces up, gone by ~58 degrees — the angle
      // snow slides off — and thinner on everything steeper than flat.
      '  float snC = dot(snN, snUp);',
      '  float snLay = smoothstep(0.52, 0.80, snC)',
      '              * mix(0.55, 1.0, clamp(snC, 0.0, 1.0));',
      // A convex edge sheds what a concave lee holds, and a deeper fall
      // buries a bigger feature before it starts to shed.
      '  float snCv = astraAccCurv(snN, vAstraWorld);',
      '  float snR = 1.0 / max(uSnowDepth * 6.0, 0.05);',
      '  float snHold = 1.0 - 0.70 * smoothstep(snR * 0.6, snR * 3.0, snCv)',
      '               + 0.60 * smoothstep(snR * 0.4, snR * 2.2, -snCv);',
      // Wind scours the face it meets; its LENGTH is the strength, so
      // no wind is an even fall rather than a normalize() of zero.
      '  float snScour = 1.0 - 0.45 * snWl',
      '                * smoothstep(-0.10, 0.90, -dot(snN, snWv));',
      // MULTIPLICATIVE, so a steep face keeps nothing whatever the
      // field says: noise may tear an edge, never move a wall's.
      '  float snD = snLay * snHold * snScour * (0.55 + 0.90 * snG);',
      // The thaw climbs from the plane through the origin, where an
      // asset's foot rests, and its line is ragged from the same field.
      '  float snH = dot(vAstraWorld, snUp) + (snG - 0.5) * 0.8;',
      '  float snLine = mix(-0.8, 5.0, uSnowMelt);',
      '  float snThaw = 1.0 - smoothstep(snLine - 0.7, snLine + 0.5, snH);',
      '  float snWr = max(uSnowWarm.w, 1e-3);',
      '  float snWarm = (1.0 - smoothstep(snWr * 0.35, snWr,',
      '                  length(vAstraWorld - uSnowWarm.xyz)))',
      '               * step(1e-4, uSnowWarm.w);',
      '  snD -= snThaw * 1.10 + snWarm * 1.10;',
      // One threshold, antialiased by its own screen gradient: a hard
      // line is paint, and a torn one is something that landed.
      '  float snT = mix(0.92, 0.18, snCov);',
      '  float snAA = clamp(fwidth(snD), 0.0, 0.35);',
      '  float snK = smoothstep(snT - 0.05 - snAA, snT + 0.12 + snAA, snD);',
      // A SECOND, finer field, for the tone alone — kept out of snD so
      // the edge is untouched. The deposit's own field is most of a
      // metre across, and a metre of one value at arm's length is paint.
      // It fades out once a pixel spans it, or the grain aliases into
      // shimmer at the far end of a shot.
      '  float snFad = 1.0 - smoothstep(0.04, 0.10,',
      '                                 length(fwidth(vAstraWorld)));',
      '  float snF = astraAccNoise(snP * 6.0 + 17.3, snW);',
      '  float snTn = clamp(mix(snG, snG * 0.66 + snF * 0.34, snFad),',
      '                     0.0, 1.0);',
      // Fresh snow is nearly neutral. Wide blue/white albedo swings made
      // flat ground look marbled; the scene light supplies its shadow hue.
      '  vec3 snCol = mix(uSnowColor * 0.82, uSnowColor * 1.03,',
      '                   smoothstep(0.16, 0.86, snTn));',
      '  snCol = astraHueBreak(snCol, vAstraWorld.xz, 0.22, 0.025);',
      '  snCol = astraHueBreak(snCol, vAstraWorld.xz + 31.7, 1.45, 0.012);',
      // A third of a code value of screen dither: a near-white ramp
      // across a roof is the one place 8-bit banding is unmissable, and
      // there is no post chain here to dither it for us.
      '  snCol += (astraHash21(gl_FragCoord.xy) - 0.5) * 0.006;',
      // The shade just OUTSIDE the edge is the only thickness a patch
      // that cannot displace geometry has. The band is a FRACTION of the
      // threshold and never a fixed offset below it: past a dusting snT
      // drops under 0.55, a fixed 0.55 offset puts ZERO deposit inside
      // the ramp, and every bare wall and soffit in the scene picks up a
      // shade it has no snow to justify (measured: a 21% darkening on
      // every vertical face, with the melt hole printed through it).
      '  float snLip = (1.0 - snK) * smoothstep(snT * 0.40, snT - 0.02,',
      '                                         snD);',
      '  diffuseColor.rgb *= 1.0 - 0.30 * snLip;',
      // Thin snow is TRANSLUCENT — the substrate comes through it — so
      // the drifts read as deep and the scoured lanes as shallow.
      '  float snThin = mix(0.38, 1.0, smoothstep(0.55, 1.30, snD));',
      '  snThin = mix(snThin, 1.0, snCov * 0.88);',
      '  float snAmt = clamp(snK * snThin * mix(0.55, 1.0, snCov)',
      '                      * smoothstep(0.0, 0.004, uSnowDepth), 0.0, 1.0);',
      '  diffuseColor.rgb = mix(diffuseColor.rgb, snCol, snAmt);',
    ].join('\n'),
  });
}

/**
 * Bank sand against everything the wind runs into.
 *
 * Sand is not dust: dust is a film on what faces up, and sand is a
 * DRIFT — it has a windward side, a lee, a wedge profile that is
 * deepest at the foot of whatever stopped it, and ripples. A scene
 * reads as desert when every wall in it is banked on the same side,
 * which is one wind vector shared by every material.
 *
 * THE DRIFT IS A WEDGE. Deposit is banked on the face that meets the
 * wind and tapers to a thin tail in the lee, and it thins with height
 * above the plane through the world origin — so it climbs the foot of
 * a wall and lets its head go clear, which is the profile that says
 * drift rather than wash. `amount` raises the whole bank: how high it
 * climbs, how much it fills, how far the ripples space out.
 *
 * IT FILLS THE CONCAVITIES FIRST. The surface's own signed curvature
 * adds where it is concave and subtracts where it is convex, so at a
 * low `amount` sand shows only in the inside corners, along the joints
 * and in the hollows of a rock — the way a floor sands up before its
 * middle does. Curvature reads ZERO across a hard, unwelded edge, so
 * there the wedge carries it alone.
 *
 * RIPPLES RUN ACROSS THE WIND. Their phase advances ALONG the wind
 * axis, which puts every crest square to the flow the way aeolian
 * ripples lie; they wander on the same field so they are not ruled
 * lines, they light the crest and shade the trough, they hold a little
 * more sand than the troughs do, and they are dropped once a pixel
 * spans one — past that they are not ripples, they are static.
 *
 * @param {THREE.Material} material A built-in material, patched in
 *   place — a shared material from `materials.js` sands every mesh
 *   wearing it, so clone it first (and clone BEFORE patching).
 * @param {object} [opts] `amount` how much has drifted, 0..1 (default
 *   0.45; 0.1 is sand in the corners, 0.9 is a buried wall);
 *   `color` THREE.Color or hex (default a warm ochre at 0.42 linear, the
 *   luminance family a lit ground sits in — a paler one renders as cream
 *   under a physical sun and stops reading as sand at all); `wind`
 *   THREE.Vector3 or [x, y, z] it blows TOWARD, its LENGTH 0..1 the
 *   strength (default [1, 0, 0]); `up` THREE.Vector3 or [x, y, z], the
 *   scene's up (default +Y); `seed` moves the drifts (default 1).
 * @returns {THREE.Material} The same material, with its uniforms live
 *   on `material.userData.uniforms`, so `uSandWind` can swing with a
 *   storm and `uSandAmt` can bury a scene over a shot.
 */
export function patchSand(material, opts = {}) {
  const amount = opts.amount === undefined ? 0.45 : unit(opts.amount);
  const seed = opts.seed === undefined ? 1 : opts.seed;
  // Sand is the mattest thing in any scene it lands in.

  patchStandard(material, BASE);
  return patchStandard(material, {
    name: 'acc:sand',
    uniforms: {
      uSandAmt: { value: amount },
      uSandColor: { value: toColor(opts.color, 0xb08a55) },
      uSandWind: { value: windVector(opts.wind, [1, 0, 0]) },
      uSandUp: { value: upVector(opts.up) },
      uSandSeed: { value: seedOffset(seed, 3.9) },
    },
    fragmentHead: [
      'uniform float uSandAmt;',
      'uniform vec3 uSandColor;',
      'uniform vec3 uSandWind;',
      'uniform vec3 uSandUp;',
      'uniform vec3 uSandSeed;',
    ].join('\n'),
    roughnessBody: 'roughnessFactor = mix(roughnessFactor, 0.94, sdAmt);',
    metalnessBody: 'metalnessFactor *= 1.0 - sdAmt;',
    fragmentBody: [
      '  vec3 sdN = normalize(vAstraWorldN);',
      '  vec3 sdUp = normalize(uSandUp);',
      '  float sdWl = clamp(length(uSandWind), 0.0, 1.0);',
      '  vec3 sdWv = uSandWind / max(length(uSandWind), 1e-4);',
      '  vec3 sdW = astraAccAxes(sdN);',
      '  vec3 sdP = vAstraWorld * 1.4 + uSandSeed;',
      '  float sdG = astraAccNoise(sdP, sdW) * 0.62',
      '            + astraAccNoise(sdP * 3.9, sdW) * 0.38;',
      // The wedge: deepest at the foot of whatever stopped it, gone by
      // the height this much sand can climb.
      '  float sdTop = mix(0.35, 2.4, uSandAmt);',
      '  float sdRise = 1.0 - smoothstep(0.0, sdTop,',
      '                                  max(dot(vAstraWorld, sdUp), 0.0));',
      // Banked on the face that meets the wind, a thin tail in the lee;
      // with no wind at all there is no windward side to bank on.
      '  float sdFace = -dot(sdN, sdWv);',
      '  float sdBank = mix(0.45, mix(0.18, 1.0,',
      '                               smoothstep(-0.55, 0.75, sdFace)), sdWl);',
      '  float sdLie = smoothstep(-0.10, 0.55, dot(sdN, sdUp));',
      // Nothing banks against a soffit: a face turned under holds no
      // more sand than it holds water.
      '  sdBank *= smoothstep(-0.55, -0.05, dot(sdN, sdUp));',
      // Concave first: a corner sands up long before the flat beside it
      // does, and a convex edge is swept clean. The shed window starts
      // WELL past the fill's, because sand does not scour a stone the
      // way it scours a rib: at the old 0.5/2.5 window every cobble in a
      // drift came out bare and black while the sand ran on around it,
      // which is a deflation pavement and not the yard we asked for.
      '  float sdCv = astraAccCurv(sdN, vAstraWorld);',
      '  float sdR = 1.0 / max(uSandAmt * 0.6 + 0.05, 0.05);',
      '  float sdFill = smoothstep(sdR * 0.35, sdR * 2.0, -sdCv);',
      '  float sdShed = smoothstep(sdR * 0.9, sdR * 3.2, sdCv);',
      // The wedge gates EVERY term, tops included: wind carries sand
      // along the ground, so a roof four metres up gets none of it.
      '  float sdLay = clamp((max(sdLie * 0.90, sdBank)',
      '                       + sdFill * 0.55) * sdRise',
      '                      - sdShed * 0.42, 0.0, 1.6);',
      // Crests square to the flow: the phase runs ALONG the wind axis,
      // wandering on the field so they are not ruled lines. The skew
      // steepens the lee face of each crest the way a ripple's is.
      '  float sdLam = mix(0.12, 0.45, uSandAmt);',
      '  float sdPh = dot(vAstraWorld, sdWv) / sdLam + (sdG - 0.5) * 1.4;',
      '  sdPh = sdPh * 6.2831853;',
      // Gone once a pixel spans a ripple: past that it is not a ripple,
      // it is static, which is the one way this reads as an effect.
      '  float sdFade = 1.0 - smoothstep(0.16, 0.50,',
      '                                  length(fwidth(vAstraWorld)) / sdLam);',
      '  float sdRip = sin(sdPh + 0.55 * sin(sdPh)) * sdFade * sdWl',
      '              * mix(0.25, 1.0, sdLie);',
      // The crests hold a little more than the troughs, so the ripple
      // is in the COVERAGE and not only in the tone.
      '  float sdD = sdLay * (0.55 + 0.90 * sdG) + sdRip * 0.14;',
      '  float sdT = mix(1.10, 0.20, uSandAmt);',
      '  float sdAA = clamp(fwidth(sdD), 0.0, 0.35);',
      '  float sdK = smoothstep(sdT - 0.05 - sdAA, sdT + 0.14 + sdAA, sdD);',
      // A finer field for the TONE only, faded once a pixel spans it:
      // dune sand is graded, and a decimetre of one value is paint.
      '  float sdFad = 1.0 - smoothstep(0.04, 0.10,',
      '                                 length(fwidth(vAstraWorld)));',
      '  float sdF = astraAccNoise(sdP * 7.5 + 41.9, sdW);',
      '  float sdTn = clamp(mix(sdG, sdG * 0.66 + sdF * 0.34, sdFad),',
      '                     0.0, 1.0);',
      '  vec3 sdCol = mix(uSandColor * 0.68, uSandColor * 1.10,',
      '                   smoothstep(0.22, 0.84, sdTn));',
      // The crest is sun-bleached and warm, the trough holds the sky: a
      // ripple that is one brightness is a stripe painted on flat sand.
      '  sdCol *= vec3(1.0) + sdRip * vec3(0.20, 0.15, 0.06);',
      // Sand is a mineral mix, not a paint chip: quartz, iron and shell
      // put warm and cool metres apart, and the palette probe named a
      // whole dune field "one flat hue per surface". Wider than stone
      // because a dune has nothing else in it to carry variety, and at
      // TWO scales because the metre-wide one alone is a flat tint on
      // anything smaller than the dune it was written for.
      '  sdCol = astraHueBreak(sdCol, vAstraWorld.xz, 0.28, 0.36);',
      '  sdCol = astraHueBreak(sdCol, vAstraWorld.xz + 12.4, 1.70, 0.13);',
      '  sdCol += (astraHash21(gl_FragCoord.xy) - 0.5) * 0.006;',
      // A FRACTION of the threshold, never a fixed offset below it — see
      // snLip: past `amount` 0.5 sdT drops under 0.55 and a fixed offset
      // shades every wall the drift never reached.
      '  float sdLip = (1.0 - sdK) * smoothstep(sdT * 0.40, sdT - 0.02,',
      '                                         sdD);',
      '  diffuseColor.rgb *= 1.0 - 0.22 * sdLip;',
      '  float sdAmt = clamp(sdK * mix(0.70, 1.0, uSandAmt)',
      '                      * smoothstep(0.0, 0.05, uSandAmt), 0.0, 1.0);',
      '  diffuseColor.rgb = mix(diffuseColor.rgb, sdCol, sdAmt);',
    ].join('\n'),
  });
}

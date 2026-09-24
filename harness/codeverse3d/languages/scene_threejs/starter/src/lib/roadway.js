/**
 * Roadway: the made surface underfoot, and the mess where it stops.
 *
 * A road, a path or a yard is in nearly every scene here, it is the
 * surface a viewer walks on every day, and it arrives flat grey. These
 * three patches give it the marks that say TRAFFIC: the aggregate it
 * was mixed from, the two polished strips per lane where the wheels
 * run, the repairs cut into it, the damp gutter at the kerb, the gravel
 * and weeds where it meets the ground it was laid on, and the tracks
 * pressed into whatever is soft beside it.
 *
 * All three go through `patchStandard`, so lighting, shadows, fog and
 * the depth chunks survive; all three shade from world position and
 * world normal, so none needs a UV; and all three CHAIN with
 * `terrain_shade`, `surface_wear`, `aging`, `accumulation` and
 * `strata` on one material.
 *
 * THE ROAD FRAME is what makes this a roadway rather than more noise.
 * Every patch works in (ALONG, ACROSS) metres, taken from the mesh's
 * OWN axis: `dir` is an object-space direction carried through
 * `modelMatrix`, and across is the horizontal perpendicular measured
 * from the mesh's own origin. A rut is a line of constant ACROSS, so a
 * rut laid along a world axis would walk off the road the moment it
 * turned; this one turns with it, and a curve built from rotated
 * segments gets ruts that follow each one.
 *
 * ONE FRAME PER MATERIAL: `dir`, `halfWidth` and `center` belong to the
 * ROAD and all three patches share them — an option left out inherits
 * what an earlier call set, and the last call that gives one wins.
 * Tracks that must run somewhere else on the same material take
 * `offset` (metres across) rather than a second centreline.
 *
 * Gloss follows the same local masks as color: tire lanes polish,
 * gutters stay damp and collected grit remains matte at the verge.
 */

import * as THREE from 'three';
import {
  patchStandard, glslAxes, glslTriNoise,
  seedVec3, toColor, unit, WORLD_VARYINGS,
} from './shader.js';

// The world varyings terrain_shade, surface_wear, aging, accumulation and
// strata share, so a material wearing several libraries carries ONE
// world position; vAstraRoad is this library's own and no neighbour's.
const ROAD_VARYINGS = [WORLD_VARYINGS, 'varying vec4 vAstraRoad;'].join('\n');

// All three patches read these, so they are declared once, in the
// frame. astraRoad*, not a neighbour's spelling: patchStandard THROWS
// when two chained patches give one function name two different bodies.
const ROAD_HEAD = [
  glslAxes('astraRoadAxes'),
  // Three world-plane projections blended by the normal: the top of a
  // kerb and its face have to carry the same grit.
  glslTriNoise('astraRoadNoise', 31.7, 67.3, 13.9),
  // A fine field is TEXTURE up close and STATIC once a pixel spans a
  // cycle of it, so every grain here is faded out by the world size of
  // its own pixel. `cycle` is metres per repeat.
  'float astraRoadFade(vec3 p, float cycle) {',
  '  return 1.0 - smoothstep(0.30, 0.95,',
  '                          length(fwidth(p)) / max(cycle, 1e-4));',
  '}',
].join('\n');

// `transformed` is still object-space after <begin_vertex>, so the
// instance transform is folded in by hand or every copy takes its road
// from the mesh origin.
const FRAME_BODY = [
  '  vec4 rwP = vec4(transformed, 1.0);',
  '  vec3 rwN = normal;',
  '  vec4 rwD = vec4(uRoadDir, 0.0);',
  '  vec4 rwO = vec4(0.0, 0.0, 0.0, 1.0);',
  '#ifdef USE_INSTANCING',
  '  rwP = instanceMatrix * rwP;',
  '  rwN = astraNormalTransform(mat3(instanceMatrix), rwN);',
  // Each copy is its own segment UNLESS a centreline was given, which
  // says every copy belongs to ONE road.
  '  rwD = mix(instanceMatrix * rwD, rwD, uRoadCenter.w);',
  '  rwO = mix(instanceMatrix * rwO, rwO, uRoadCenter.w);',
  '#endif',
  '  vAstraWorld = (modelMatrix * rwP).xyz;',
  '  vAstraWorldN = astraNormalTransform(mat3(modelMatrix), rwN);',
  '  vec3 rwA = normalize((modelMatrix * rwD).xyz);',
  // The centreline is OBJECT-space like the direction, so a road built
  // inside a turned group needs no world arithmetic from its author.
  '  rwO = mix(rwO, vec4(uRoadCenter.xyz, 1.0), uRoadCenter.w);',
  '  vec3 rwC = (modelMatrix * rwO).xyz;',
  // Across is the HORIZONTAL perpendicular, so the pair is a road frame
  // and not the world axes; a road standing on end has no across.
  '  vec3 rwX = cross(vec3(0.0, 1.0, 0.0), rwA);',
  '  rwX = normalize(mix(vec3(1.0, 0.0, 0.0), rwX,',
  '                      step(1e-4, dot(rwX, rwX))));',
  '  vAstraRoad = vec4(rwA, dot(vAstraWorld - rwC, rwX));',
].join('\n');

const FRAME_HEAD = [
  'uniform vec3 uRoadDir;',
  'uniform vec4 uRoadCenter;',
  'uniform float uRoadHalf;',
].join('\n');

// The constants differ from surface_wear's, aging's and accumulation's,
// or one seed would lay this library's grit along that library's
// blotches.
const seedOffset = (seed, salt) =>
  seedVec3(seed + salt, 2.11, 6.37, 11.83, 40);

/** Read a direction option, never as a zero vector the GPU divides by. */
function dirVector(value, fallback) {
  const v = new THREE.Vector3();
  v.fromArray(value.toArray ? value.toArray() : value);
  if (!(v.lengthSq() > 1e-9)) return fallback.clone();
  return v.normalize();
}

/**
 * The road frame every patch here works in, applied once.
 *
 * Named, so a material wearing all three gets ONE copy of the base: two
 * would declare the same vertex locals and redefine the same helpers,
 * which is a compile error and a throw respectively. The frame belongs
 * to the ROAD, not to a patch, so an option left out INHERITS what an
 * earlier call set rather than resetting it to the default.
 *
 * @param {THREE.Material} material The material to patch, in place.
 * @param {object} opts `dir` object-space direction of travel (default
 *   +Z); `halfWidth` metres from the centreline to the kerb (default
 *   3.5); `center` THREE.Vector3 or [x, y, z], a point on the
 *   centreline in the MESH'S OWN space (default: the mesh's origin —
 *   and giving one also makes every instance share this one road).
 * @returns {THREE.Material} The same material.
 */
function withFrame(material, opts) {
  const live = material.userData.uniforms || {};
  const was = (name, fallback) => (live[name] ? live[name].value : fallback);
  const dir = opts.dir === undefined
      ? was('uRoadDir', new THREE.Vector3(0, 0, 1))
      : dirVector(opts.dir, new THREE.Vector3(0, 0, 1));
  const half = opts.halfWidth === undefined
      ? was('uRoadHalf', 3.5) : Math.max(0.05, opts.halfWidth);
  const at = new THREE.Vector3();
  if (opts.center) at.fromArray(
      opts.center.toArray ? opts.center.toArray() : opts.center);
  const center = opts.center === undefined
      ? was('uRoadCenter', new THREE.Vector4(0, 0, 0, 0))
      : new THREE.Vector4(at.x, at.y, at.z, 1);
  return patchStandard(material, {
    name: 'road:frame',
    uniforms: {
      uRoadDir: { value: dir.clone() },
      uRoadCenter: { value: center.clone() },
      uRoadHalf: { value: half },
    },
    vertexHead: [ROAD_VARYINGS, FRAME_HEAD].join('\n'),
    fragmentHead: [ROAD_VARYINGS, FRAME_HEAD, ROAD_HEAD].join('\n'),
    vertexBody: FRAME_BODY,
  });
}

/**
 * Asphalt or gravel: aggregate, wheel ruts, repairs and a damp gutter.
 *
 * A road is not a grey plane and it is not evenly worn — it is worn in
 * TWO STRIPS PER LANE, where the wheels run, with the drip line between
 * them and the silt at its edge. That asymmetry is the whole cue: it is
 * what says a surface is driven on rather than painted, and it is the
 * one thing a flat material can never suggest. Four layers, all in the
 * road's own (along, across) frame:
 *
 * AGGREGATE is the stone in the mix — a fine dark speckle at
 * `aggregate` 0 (a hot-rolled asphalt), loose pale chips at 1 (a gravel
 * track) — faded out once a pixel spans a stone, because past that it
 * is not texture, it is static. RUTS are the polished wheel lines, a
 * wandering pair 1.5 m apart in each lane — the road is divided into
 * WHOLE lanes, so a single-track lane keeps one pair and a two-lane
 * road gets four strips, and the lane centre between each pair keeps
 * the darker drip line. PATCHES are repairs: saw-cut rectangles in the
 * road's own frame, each its own batch of tar, sealed at the rim — a
 * soft-edged blob is an oil stain, not a repair. GUTTER is the
 * hand-span at the kerb that never dries: darker, greener and, through
 * a local roughness mask, glossier.
 *
 * ON A COLOUR THAT IS ALREADY THERE: this one RULES the hue — it is the
 * made surface, and `color` is what it was made of — but it carries
 * whatever is in `diffuseColor` through as a light/dark variation
 * rather than assigning over it, so a `patchTriplanar` or a
 * `patchMicroBreakup` under it still shows in the result.
 *
 * @param {THREE.Material} material A built-in material, patched in
 *   place — a shared material from `materials.js` surfaces every mesh
 *   wearing it, so clone it first (and clone BEFORE patching).
 * @param {object} [opts] `aggregate` fineness of the mix, 0 asphalt to
 *   1 loose gravel (default 0.25); `wear` how polished the wheel ruts
 *   are, 0..1 (default 0.5); `patches` how much of it has been dug up
 *   and made good, 0..1 (default 0.25); `gutter` how damp and silted
 *   the kerb line is, 0..1 (default 0.35); `color` THREE.Color or hex,
 *   the surface's own tone (default a worn bitumen grey); `lane` metres
 *   of one traffic lane, which sets where the ruts run (default 3.4);
 *   `dir`/`halfWidth`/`center` the road frame (see `withFrame`);
 *   `seed` moves the aggregate and the repairs (default 1).
 * @returns {THREE.Material} The same material, with its uniforms live
 *   on `material.userData.uniforms`, so `uRoadGutter` can wet down
 *   over a shot.
 */
export function patchRoadSurface(material, opts = {}) {
  const agg = opts.aggregate === undefined ? 0.25 : unit(opts.aggregate);
  const wear = opts.wear === undefined ? 0.5 : unit(opts.wear);
  const patches = opts.patches === undefined ? 0.25 : unit(opts.patches);
  const gutter = opts.gutter === undefined ? 0.35 : unit(opts.gutter);
  const lane = opts.lane === undefined ? 3.4 : Math.max(0.5, opts.lane);
  const seed = opts.seed === undefined ? 1 : opts.seed;
  withFrame(material, opts);
  return patchStandard(material, {
    name: 'road:surface',
    uniforms: {
      uRoadAgg: { value: agg },
      uRoadWear: { value: wear },
      uRoadPatch: { value: patches },
      uRoadGutter: { value: gutter },
      uRoadColor: { value: toColor(opts.color, 0x565658) },
      uRoadLane: { value: lane },
      uRoadSeed: { value: seedOffset(seed, 0.9) },
    },
    fragmentHead: [
      'uniform float uRoadAgg;',
      'uniform float uRoadWear;',
      'uniform float uRoadPatch;',
      'uniform float uRoadGutter;',
      'uniform vec3 uRoadColor;',
      'uniform float uRoadLane;',
      'uniform vec3 uRoadSeed;',
    ].join('\n'),
    fragmentBody: [
      '  float roU = dot(vAstraWorld, vAstraRoad.xyz);',
      '  float roA = abs(vAstraRoad.w);',
      // The road's tone rules, but it carries what is already in the
      // albedo as a variation: assigning outright erases a neighbour.
      '  float roKeep = dot(diffuseColor.rgb, vec3(0.3333));',
      '  vec3 roCol = uRoadColor',
      '             * mix(0.88, 1.12, clamp(roKeep * 1.7, 0.0, 1.0));',
      // TWO STRIPS PER LANE, and they come FIRST: a polished rut is
      // where the aggregate stopped showing, not a stripe painted on
      // top of it. The road is divided into WHOLE lanes, so every lane
      // centre lands inside it — a single-track lane keeps one pair.
      '  float roN = max(floor(2.0 * uRoadHalf / max(uRoadLane, 0.5)), 1.0);',
      '  float roLw = 2.0 * uRoadHalf / roN;',
      '  float roK = clamp(floor((vAstraRoad.w + uRoadHalf) / roLw),',
      '                    0.0, roN - 1.0);',
      '  float roCen = (roK + 0.5) * roLw - uRoadHalf;',
      // Nothing drives a ruled line: the wheel path wanders along the
      // road, which is what stops the pair reading as rails.
      '  float roWob = (astraNoise2(vec2(roU * 0.06, 3.7) + uRoadSeed.yz)',
      '                 - 0.5) * 0.30;',
      '  float roTr = min(0.75, roLw * 0.32);',
      '  float roRut = (1.0 - smoothstep(0.12, 0.38,',
      '                 abs(abs(vAstraRoad.w - roCen) - roTr + roWob)))',
      '               * uRoadWear;',
      // The stone in the mix, gone once a pixel spans one of them and
      // polished away under the wheels.
      '  float roGs = mix(0.018, 0.075, uRoadAgg);',
      '  vec2 roQ = vec2(roU, vAstraRoad.w) / roGs + uRoadSeed.xy;',
      '  float roFd = astraRoadFade(vAstraWorld, roGs) * (1.0 - roRut * 0.7);',
      '  float roG1 = astraNoise2(roQ);',
      '  float roG2 = astraNoise2(roQ * 2.7 + 19.3);',
      // A THIRD octave, on roFd twice so it is the first to go: two
      // octaves of value noise at one stone size read as soft blobs —
      // digital camouflage, not aggregate — and the break-up that makes
      // them stone is finer than the stone itself.
      '  float roG3 = astraNoise2(roQ * 5.9 + 41.7);',
      '  float roG2Fade = astraRoadFade(vAstraWorld, roGs / 2.7);',
      '  float roG3Fade = astraRoadFade(vAstraWorld, roGs / 5.9);',
      '  float roGrain = ((roG1 - 0.5) * 0.52 + (roG2 - 0.5) * 0.34 * roG2Fade',
      '                 + (roG3 - 0.5) * 0.30 * roG3Fade) * roFd;',
      '  roCol *= 1.0 + roGrain * mix(0.22, 0.60, uRoadAgg);',
      // A chip is a STONE, and a road is never mixed from one stone: the
      // exposed faces run warm (flint, limestone dust) to cool (granite,
      // basalt) on a field decorrelated from the one that placed them.
      '  float roChip = smoothstep(0.62, 0.74, roG1 * 0.45 + mix(0.5, roG3, roG3Fade) * 0.55)',
      '               * uRoadAgg * roFd;',
      '  vec3 roStone = roCol * 1.5 + vec3(0.035, 0.033, 0.030);',
      '  roStone *= mix(vec3(1.07, 1.00, 0.90),',
      '                 vec3(0.93, 0.99, 1.09), mix(0.5, roG2, roG2Fade));',
      '  roCol = mix(roCol, roStone, roChip * 0.7);',
      '  roCol = mix(roCol, uRoadColor * mix(1.06, 1.42, uRoadWear),',
      '              roRut * 0.85);',
      // The drip strip down the lane centre, between the wheels.
      '  float roOil = (1.0 - smoothstep(0.06, 0.34,',
      '                 abs(vAstraRoad.w - roCen))) * uRoadWear;',
      '  roCol *= 1.0 - roOil * 0.30;',
      // REPAIRS ARE SAW-CUT: a rectangle in the road's own frame with a
      // sealed rim, one per cell of a coarse grid. A soft blob is an
      // oil stain; only a straight edge reads as something dug up.
      '  vec2 roPq = vec2(roU / 5.6, vAstraRoad.w / 3.3) + uRoadSeed.zx;',
      '  vec2 roPi = floor(roPq);',
      // Jittered inside its cell and never quite filling it, or the
      // repairs line up on a lattice and read as paving slabs.
      '  vec2 roPj = vec2(astraHash21(roPi + 5.3),',
      '                   astraHash21(roPi + 17.1)) - 0.5;',
      '  vec2 roPf = abs(fract(roPq) - 0.5 + roPj * 0.22);',
      '  vec2 roPs = vec2(0.13 + 0.25 * astraHash21(roPi + 3.1),',
      '                   0.11 + 0.27 * astraHash21(roPi + 7.7));',
      '  vec2 roPaa = fwidth(roPq) * 0.7 + 0.0015;',
      '  vec2 roPe = 1.0 - smoothstep(roPs - roPaa, roPs + roPaa, roPf);',
      '  float roPk = roPe.x * roPe.y',
      '             * step(1.0 - uRoadPatch, astraHash21(roPi));',
      '  vec2 roPn = 1.0 - smoothstep(roPs - 0.05 - roPaa, roPs - 0.05 + roPaa,',
      '                               roPf);',
      '  float roRim = roPk * (1.0 - roPn.x * roPn.y);',
      // A repair is a different BATCH, not a black rectangle: some are
      // darker than the road it was cut into and some are paler.
      '  float roPt = mix(0.80, 1.12, astraHash21(roPi + 11.9));',
      '  roCol = mix(roCol, uRoadColor * roPt * (1.0 + roGrain * 1.2),',
      '              roPk * 0.8);',
      '  roCol *= 1.0 - roRim * 0.22;',
      // METRE-SCALE WEATHER, and the only structure on this surface that
      // survives to the horizon: every field above is finer than a pixel
      // within a few metres of the camera and fades out BY DESIGN, which
      // left the road one flat value past that — a grey card with a
      // repair grid on it. Two slow octaves in the road's own frame, and
      // a hue that walks with them from warm (dust, sun-bleached binder,
      // limestone fines) to cool (fresh bitumen, the damp side of a
      // patch), so the variance is in COLOUR and not only in level. The
      // mix is centred, so the road's mean tone is the one it was given.
      '  vec2 roMq = vec2(roU, vAstraRoad.w) + uRoadSeed.yx;',
      '  float roBlot = (astraNoise2(roMq * 0.115) - 0.5) * 0.55',
      '               + (astraNoise2(roMq * 0.034 + 4.7) - 0.5) * 0.45;',
      '  float roHue = clamp(0.5 + roBlot * 1.7, 0.0, 1.0);',
      '  roCol *= (1.0 + roBlot * 0.30)',
      '         * mix(vec3(0.950, 0.984, 1.062),',
      '               vec3(1.072, 1.012, 0.940), roHue);',
      // The gutter never dries and it silts up; its inner edge is
      // ragged, or it is a painted line. Two bands, not one: the damp
      // strip inside the kerb goes dark and COOL, and the silt washed to
      // the kerb line itself sits on top of it PALER and warm — a single
      // flat darkening reads as a stripe someone painted there.
      '  float roGw = mix(0.10, 0.60, uRoadGutter);',
      '  float roGj = (astraNoise2(vec2(roU * 0.35, 9.1) + uRoadSeed.xy)',
      '                - 0.5) * roGw * 0.7;',
      '  float roGut = smoothstep(uRoadHalf - roGw + roGj,',
      '                           uRoadHalf + roGj, roA) * uRoadGutter;',
      // Silt is washed along the kerb and dropped where the fall eases,
      // so how much of it there is walks ALONG the road: an even pale
      // line at the kerb is road marking, not dirt.
      '  float roSilt = smoothstep(uRoadHalf - roGw * 0.34 + roGj,',
      '                            uRoadHalf + roGj, roA) * uRoadGutter',
      '               * smoothstep(0.30, 0.72,',
      '                   astraNoise2(vec2(roU * 0.5, 2.3) + uRoadSeed.zx));',
      '  roCol = mix(roCol, roCol * vec3(0.44, 0.49, 0.56),',
      '              roGut * (1.0 - roSilt * 0.55));',
      '  roCol = mix(roCol, roCol * vec3(1.58, 1.44, 1.17), roSilt * 0.62);',
      // A metre-scale gradient across a near-neutral grey BANDS at eight
      // bits, and the aggregate that would have hidden it has faded out
      // by the distance the banding appears. Sub-LSB, per pixel, so it
      // costs one hash and never reads as noise.
      '  roCol *= 1.0 + (astraHash21(gl_FragCoord.xy) - 0.5) * 0.010;',
      '  diffuseColor.rgb = roCol;',
      // Metric surface-gradient relief preserves the base mesh silhouette.
      // Fine aggregate disappears before subpixel grain can shimmer.
      '  float roHeight = roGrain * mix(0.0005, 0.008, uRoadAgg)',
      '    * (1.0 - roRut * 0.7) + roChip * 0.002 - roRut * 0.004',
      '    - roRim * 0.0008 + roSilt * 0.0008;',
    ].join('\n'),
    normalBody: 'normal = astraBump(-vViewPosition, normal, roHeight);',
    roughnessBody: [
      'roughnessFactor = mix(roughnessFactor, max(roughnessFactor, 0.94), uRoadAgg * 0.18);',
      'roughnessFactor = mix(roughnessFactor, max(0.32, roughnessFactor * 0.78), roRut);',
      'roughnessFactor = mix(roughnessFactor, min(roughnessFactor, 0.32), roGut * (1.0 - roSilt));',
      'roughnessFactor = mix(roughnessFactor, max(roughnessFactor, 0.95), roSilt);',
    ].join('\n'),
  });
}

/**
 * The gravel, grit and weeds where a made surface meets the ground.
 *
 * Nothing in a photograph meets anything else with a clean edge, and
 * this is the paving version of that: tarmac does not stop at a ruled
 * line on grass, it frays into the spill that was swept off it, the
 * grit that washed off it, and the weeds that took the joint. A road
 * with a clean edge reads as a decal on the ground however good the
 * surface itself is, so this is the cheapest thing in the library.
 *
 * The band straddles the kerb line — |across| = `halfWidth` — and its
 * half-width WANDERS between about half of what it was given and all
 * of it, so the edge is ragged and still strictly inside `width`. Weeds
 * take the joint and the ground side of it and never the made surface;
 * the hairline of shadow in the joint itself is the only depth a patch
 * that cannot displace geometry has, and it is faded out by its own
 * pixel size before it can alias.
 *
 * ON A COLOUR THAT IS ALREADY THERE: it MIXES over it by the band mask
 * and leaves everything outside the band untouched — so it lands on the
 * road's material and on the verge's alike. On the verge, pass the
 * road's `center` and `halfWidth`: a fragment on the grass cannot know
 * where the tarmac ended.
 *
 * @param {THREE.Material} material A built-in material, patched in
 *   place — a shared material bands every mesh wearing it.
 * @param {object} [opts] `width` metres the whole band spans, kerb line
 *   in the middle (default 0.5); `color` THREE.Color or hex, the grit
 *   and gravel (default a pale grey-brown); `weeds` how much has taken
 *   root, 0..1 (default 0.4); `dir`/`halfWidth`/`center` the road frame
 *   (see `withFrame`); `seed` moves the grit and the tufts (default 1).
 * @returns {THREE.Material} The same material, with its uniforms live
 *   on `material.userData.uniforms` — `uSeamWeed` is the weed tone, so
 *   a dry season can yellow it.
 */
export function patchSeamBand(material, opts = {}) {
  const width = opts.width === undefined ? 0.5 : Math.max(1e-3, opts.width);
  const weeds = opts.weeds === undefined ? 0.4 : unit(opts.weeds);
  const seed = opts.seed === undefined ? 1 : opts.seed;
  withFrame(material, opts);
  return patchStandard(material, {
    name: 'road:seam',
    uniforms: {
      uSeamWidth: { value: width },
      uSeamColor: { value: toColor(opts.color, 0x847a6d) },
      uSeamWeeds: { value: weeds },
      uSeamWeed: { value: new THREE.Color(0x53602f) },
      uSeamSeed: { value: seedOffset(seed, 5.3) },
    },
    fragmentHead: [
      'uniform float uSeamWidth;',
      'uniform vec3 uSeamColor;',
      'uniform float uSeamWeeds;',
      'uniform vec3 uSeamWeed;',
      'uniform vec3 uSeamSeed;',
    ].join('\n'),
    fragmentBody: [
      '  float smU = dot(vAstraWorld, vAstraRoad.xyz);',
      // Signed metres past the kerb line, so the two sides can differ.
      '  float smD = abs(vAstraRoad.w) - uRoadHalf;',
      '  float smH = uSeamWidth * 0.5;',
      // The half-width wanders along the road and never exceeds what it
      // was given, so a ragged band is still a BOUNDED one.
      '  float smR = mix(0.45, 1.0, astraNoise2(vec2(smU * 0.8, 5.7)',
      '                                         + uSeamSeed.xy));',
      '  float smK = 1.0 - smoothstep(smR * smH * 0.45, smR * smH,',
      '                               abs(smD));',
      '  vec3 smW = astraRoadAxes(normalize(vAstraWorldN));',
      '  vec3 smP = vAstraWorld * 22.0 + uSeamSeed;',
      '  float smFd = astraRoadFade(vAstraWorld, 0.045);',
      '  float smFc = astraRoadFade(vAstraWorld, 0.20);',
      '  float smG = astraRoadNoise(smP, smW);',
      '  float smC = astraRoadNoise(smP * 0.22, smW);',
      // Grit is the fine half and gravel the coarse half. BOTH converge
      // to their average as the pixel grows past them: a band that keeps
      // its contrast into the distance is a dashed white line.
      '  vec3 smCol = uSeamColor * mix(1.0, mix(0.84, 1.16, smC), smFc)',
      '             * (1.0 + (smG - 0.5) * 0.40 * smFd);',
      // Gravel is SWEPT UP off a road, not quarried from one stone: a
      // slow field in the road's own frame (about a two-metre cycle, so
      // it is still there when the grain has faded) walks the band from
      // limestone-warm to granite-cool, and the same field splits the
      // weeds into dry stems and green leaf below.
      '  float smHue = astraNoise2(vec2(smU, vAstraRoad.w) * 0.55',
      '                            + uSeamSeed.zx);',
      '  smCol *= mix(vec3(1.07, 1.00, 0.91), vec3(0.93, 0.99, 1.08), smHue);',
      // WEEDS take the joint and the ground side of it: a tuft in the
      // middle of the tarmac is a decal.
      '  float smOut = smoothstep(-smH * 0.30, smH * 0.55, smD);',
      '  float smTf = astraNoise2(vec2(smU, vAstraRoad.w) * 6.5',
      '                           + uSeamSeed.yz);',
      '  float smWd = mix(0.26, smoothstep(0.60, 0.82, smTf), smFc)',
      '             * mix(0.20, 1.0, smOut) * uSeamWeeds;',
      '  vec3 smWc = uSeamWeed * mix(0.70, 1.20, smG)',
      '            * mix(vec3(1.18, 1.05, 0.72),',
      '                  vec3(0.84, 1.02, 0.94), smHue);',
      '  smCol = mix(smCol, smWc, smWd);',
      '  diffuseColor.rgb = mix(diffuseColor.rgb, smCol, smK * 0.88);',
      // The joint itself, faded out by its own pixel size: a hairline
      // that survives past the distance it can be resolved is moire.
      '  float smJt = (1.0 - smoothstep(0.0, smH * 0.22, abs(smD)))',
      '             * astraRoadFade(vAstraWorld, smH * 0.44);',
      '  diffuseColor.rgb *= 1.0 - smJt * 0.45;',
      '  float smHeight = smK * ((smG - 0.5) * 0.007 * smFd',
      '    + (smC - 0.5) * 0.012 * smFc) - smJt * 0.004;',
    ].join('\n'),
    roughnessBody: 'roughnessFactor = mix(roughnessFactor, max(roughnessFactor, 0.97), smK);',
    normalBody: 'normal = astraBump(-vViewPosition, normal, smHeight);',
  });
}

/**
 * Tyre tracks or footprints pressed into a soft surface.
 *
 * Mud, sand, snow and dust all record what crossed them, and a soft
 * surface with nothing written on it reads as untouched ground however
 * good its own texture is — which is why an otherwise finished yard,
 * verge or beach still looks like a render. `count` tracks run along
 * the road frame's direction, each wandering on its own field (a pair
 * of ruled lines is a railway, not a vehicle).
 *
 * Color, local gloss and filtered surface-gradient normals describe the
 * press. Geometry and silhouette stay unchanged: use displaced meshes for
 * foreground ruts that need to occlude a wheel or leave a visible edge.
 * The press is darker and damp; a paler raised rim carries displaced soil.
 *
 * `kind` is a UNIFORM, not two sources: 'tyre' lays a continuous band
 * with chevron tread (through `astraStroke`, which kills itself once a
 * pixel spans a rib), 'foot' lays discrete prints one stride apart,
 * each stepping to its own side of the line so a single trail reads as
 * a walk rather than as a ladder.
 *
 * ON A COLOUR THAT IS ALREADY THERE: it MIXES over it, and the colour
 * it mixes toward is that same albedo darkened — so the tracks are made
 * OF the surface they are pressed into, whatever a neighbouring patch
 * put there, and outside them nothing changes.
 *
 * @param {THREE.Material} material A built-in material, patched in
 *   place — a shared material tracks every mesh wearing it.
 * @param {object} [opts] `dir` object-space direction of travel
 *   (default +Z); `count` how many parallel tracks, 1..8 (default 2 —
 *   one vehicle, one walker); `depth` how deep they are pressed, 0..1
 *   (default 0.5); `kind` 'tyre' or 'foot' (default 'tyre'); `gauge`
 *   metres between tracks (default 1.6 for tyre, 0.3 for foot);
 *   `offset` metres ACROSS the frame that the group runs at, so a
 *   vehicle can drive down one lane and a seam band on the same
 *   material can still sit on the centreline it belongs to (default 0);
 *   `center`/`halfWidth` the road frame (see `withFrame`); `seed` moves
 *   the wander and the prints (default 1).
 * @returns {THREE.Material} The same material, with its uniforms live
 *   on `material.userData.uniforms`, so `uTrkDepth` can press in over
 *   a shot.
 */
export function patchTracks(material, opts = {}) {
  const count = Math.max(1, Math.min(8, Math.round(
      opts.count === undefined ? 2 : opts.count)));
  const depth = opts.depth === undefined ? 0.5 : unit(opts.depth);
  const foot = opts.kind === 'foot';
  const gauge = opts.gauge === undefined
      ? (foot ? 0.30 : 1.6) : Math.max(0.02, opts.gauge);
  const seed = opts.seed === undefined ? 1 : opts.seed;
  // Pressed ground is packed and damp only inside the track mask.
  withFrame(material, opts);
  return patchStandard(material, {
    name: 'road:tracks',
    uniforms: {
      uTrkCount: { value: count },
      uTrkDepth: { value: depth },
      uTrkKind: { value: foot ? 1 : 0 },
      uTrkGauge: { value: gauge },
      uTrkOffset: { value: opts.offset === undefined ? 0 : opts.offset },
      uTrkSeed: { value: seedOffset(seed, 8.7) },
    },
    fragmentHead: [
      'uniform float uTrkCount;',
      'uniform float uTrkDepth;',
      'uniform float uTrkKind;',
      'uniform float uTrkGauge;',
      'uniform float uTrkOffset;',
      'uniform vec3 uTrkSeed;',
    ].join('\n'),
    fragmentBody: [
      '  float tkU = dot(vAstraWorld, vAstraRoad.xyz);',
      '  float tkPress = 0.0;',
      '  float tkWide = 0.0;',
      '  float tkTread = 0.0;',
      // A churned tyre rut is ~27 cm across and a boot 11; the stride
      // and the rib pitch are the same constants for everyone.
      '  float tkHw = mix(0.135, 0.055, uTrkKind);',
      // Prints and ribs are gone before a pixel spans one of them, and
      // a line of prints converges to the worn TRAIL it really is
      // rather than to a row of sparkling dots.
      '  float tkFd = astraRoadFade(vAstraWorld, 0.30);',
      '  for (int i = 0; i < 8; i++) {',
      '    if (float(i) >= uTrkCount) break;',
      '    float tkO = (float(i) - (uTrkCount - 1.0) * 0.5) * uTrkGauge',
      '              + uTrkOffset;',
      '    float tkWob = (astraNoise2(vec2(tkU * 0.22, float(i) * 7.3)',
      '                   + uTrkSeed.xy) - 0.5) * 0.45;',
      '    float tkX = vAstraRoad.w - tkO - tkWob;',
      // TYRE: a continuous band with chevron ribs square across it.
      '    float tkB = 1.0 - smoothstep(tkHw * 0.68, tkHw, abs(tkX));',
      '    float tkBw = 1.0 - smoothstep(tkHw, tkHw * 1.7, abs(tkX));',
      // Half-width 0.06, not a fat rib: astraStroke clamps its own
      // antialias at 0.30 and self-kills between w * 1.5 and w * 5, so
      // only a thin stroke ever reaches the kill instead of greying out.
      '    float tkRb = astraStroke(tkU / 0.065 + abs(tkX) * 3.6,',
      '                             0.06);',
      // FOOT: one print per stride, each stepping to its own side.
      '    float tkC = floor(tkU / 0.72);',
      '    float tkAl = (tkU / 0.72 - tkC - 0.5) * 0.72 / 0.15;',
      '    float tkAc = (tkX - (mod(tkC, 2.0) - 0.5) * 0.11) / 0.055;',
      '    float tkE = length(vec2(tkAl, tkAc));',
      '    float tkTr = (1.0 - smoothstep(0.9, 1.7, abs(tkAc))) * 0.45;',
      '    float tkPr = mix(tkTr, 1.0 - smoothstep(0.74, 1.0, tkE), tkFd);',
      '    float tkPw = 1.0 - smoothstep(1.0, 1.45, tkE);',
      '    tkPress = max(tkPress, mix(tkB, tkPr, uTrkKind));',
      '    tkWide = max(tkWide, mix(tkBw, tkPw, uTrkKind));',
      '    tkTread = max(tkTread, mix(tkB * tkRb, tkPr * 0.5, uTrkKind));',
      '  }',
      '  float tkRim = clamp(tkWide - tkPress, 0.0, 1.0) * tkFd;',
      // Made OF the surface it is pressed into: turned over and damp,
      // so the same albedo, darker.
      '  vec3 tkCol = diffuseColor.rgb * mix(1.0, 0.55, uTrkDepth)',
      '             * (1.0 - tkTread * 0.17 * tkFd);',
      // Turned-over material is DAMP as well as dark, and damp is cool:
      // a press that only darkens reads as a shadow painted on the
      // ground rather than as ground that was moved.
      '  tkCol *= mix(vec3(1.0), vec3(0.94, 0.975, 1.045), uTrkDepth);',
      '  diffuseColor.rgb = mix(diffuseColor.rgb, tkCol,',
      '                         clamp(tkPress * uTrkDepth, 0.0, 1.0));',
      // The material it displaced has to go somewhere, and the pale rim
      // is the only thickness this hook can show — dry crumbs off the
      // top of the surface, so it lifts WARM against the cool press.
      '  diffuseColor.rgb *= 1.0 + tkRim * uTrkDepth',
      '                    * vec3(0.30, 0.25, 0.17);',
      '  float tkHeight = uTrkDepth * (-tkPress * 0.013',
      '    - tkTread * tkFd * 0.002 + tkRim * 0.006);',
    ].join('\n'),
    roughnessBody: 'roughnessFactor = mix(roughnessFactor, max(0.12, roughnessFactor * 0.58), clamp(tkPress * uTrkDepth, 0.0, 1.0));',
    normalBody: 'normal = astraBump(-vViewPosition, normal, tkHeight);',
  });
}

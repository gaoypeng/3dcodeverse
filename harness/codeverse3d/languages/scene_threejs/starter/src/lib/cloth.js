/**
 * Cloth and crop: the three things in a scene that show the wind
 * INSTEAD OF being shown by it.
 *
 * Each of the three fails the same way when it is written as "a sine
 * over the surface", and each fails differently:
 *
 *   `makeFlag` — the wave has to TRAVEL from the mast to the fly and
 *   GROW as it goes. Amplitude is pinned to zero along the attachment
 *   and largest at the free edge, so the eye reads a sheet held at one
 *   end. A sine over the whole sheet is a rippling billboard: it has
 *   no attachment, so it reads as a screen playing a video of cloth.
 *
 *   `makeBanner` — a cloth hung along its TOP edge does not flap, it
 *   SWINGS, at the pendulum rate its own drop sets (sqrt(g/drop), so a
 *   long curtain is slower than a short one), and it gathers into fold
 *   lines that run DOWN from the hanging edge. Flap it like a flag and
 *   a heavy curtain reads as a sheet of paper.
 *
 *   `makeWheatField` — the subject is the WAVE crossing the field, not
 *   the stalk. The gust field is shared by every stalk and travels
 *   downwind at a stated m/s, so neighbours move together with the lag
 *   between them; per-stalk motion is a tenth of it, the texture that
 *   makes the wave visible. Give every stalk its own phase — as grass
 *   does, correctly, at grass's scale — and a hectare of wheat boils
 *   instead of rippling.
 *
 * Which sibling do you want?
 *   `grass.js` — ankle-high ground cover, clumped, per-blade phase; the
 *     ground itself needs covering.
 *   `smalllife.js` makeReeds — waterline stand, rooted on a bed, the
 *     bend BREAKING at the surface.
 *   `flowers.js` — colour above the grass, nodding heads.
 *   here — a crop: chest-high, sown in DRILL ROWS, one shared wave.
 *
 * All three take grass.js's `wind` (its `windOf` is imported, never
 * re-read), so one scene's meadow, its wheat and its flags move as one
 * wind. All three are deterministic in `seed`, and all three need
 * driving from your `tick()` — `obj.userData.tick(t)` — or the wind
 * never blows.
 */

import * as THREE from 'three';

import { windOf } from './grass.js';
import { mulberry32 } from './noise.js';
import { patchStandard, shadowLike, tickShaders } from './shader.js';

const _TAU = Math.PI * 2;
const _G = 9.81;
// Rows up a stalk, and where its ear starts. The ear is a fifth of the
// stalk and takes nearly half the rows, because that is where the bend
// turns hardest; fatter or longer and the crop reads as pennants.
const _STALK_ROWS = 7;
const _STEM_ROWS = 4;
const _EAR_AT = 0.8;
// Tileable modes of the gust field. Integer frequencies only: the
// field scrolls downwind forever, so a seam would cross the crop.
const _WAVE_MODES = [
  [1, 0], [0, 1], [1, 1], [1, -1], [2, 1], [1, 2],
  [2, -1], [2, 2], [3, 1], [1, 3], [3, 2], [2, 3],
];

/** Clamp to 0..1 without importing MathUtils for a handful of calls. */
function clamp01(v) {
  return Math.max(0, Math.min(1, v));
}

/**
 * Rotations about Y that send local +X (or +Z) to a world XZ heading.
 *
 * Both sheets are authored with the wind along one local axis and
 * turned by this, so their GLSL never has to carry a wind vector: the
 * flag flies along its own +X, the banner swings along its own +Z.
 */
function yawToX(dir) {
  return Math.atan2(-dir.y, dir.x);
}

function yawToZ(dir) {
  return Math.atan2(dir.x, dir.y);
}

/**
 * A flag on a mast, whose wave travels out to the fly and grows.
 *
 * Buys the cue that says "held at one edge": the amplitude envelope is
 * a per-vertex attribute pinned to 0 along the hoist and rising to 1
 * at the fly, and the phase runs `k*u - rate*t`, so crests LEAVE the
 * mast. Chord length is conserved as the sheet ripples (the fly is
 * pulled back by the integral of its own slope), or a flapping flag
 * grows visibly longer than its own cloth.
 *
 * The constraint: the mast rests on y = 0 and the sheet hangs from its
 * top, so the group can be dropped straight onto ground; and the
 * stated bounding sphere covers the sheet's full excursion, because
 * the wave lives in the vertex shader and three culls on the sphere.
 *
 * @param {object} [opts]
 *   `width` chord of the sheet in metres, mast to fly (default 1.6);
 *   `height` hoist height of the sheet (default 1.0); `mast` mast
 *   height in metres (default 6, 0 for no mast at all — a flag already
 *   hung on someone else's pole); `wind` grass.js's option, a strength
 *   multiplier or `{dir, strength, speed}` with `dir` the direction it
 *   BLOWS TOWARD over world XZ — the flag flies that way; `color`
 *   cloth albedo (default a flag red); `seed` PRNG seed (default 11);
 *   `name` group name.
 * @returns {THREE.Group} Named `Flag`, resting on y = 0, holding
 *   `Mast` and a `FlagSheet` whose ripple is one patched material,
 *   with `userData.tick(t)` driving it.
 */
export function makeFlag(opts = {}) {
  const width = opts.width === undefined ? 1.6 : opts.width;
  const height = opts.height === undefined ? 1.0 : opts.height;
  const mast = opts.mast === undefined ? 6 : opts.mast;
  const seed = opts.seed === undefined ? 11 : opts.seed;
  const wind = windOf(opts.wind);
  const rand = mulberry32(seed);
  // Albedo, not a screen colour: a scene sun runs at 5-6, so a hex
  // that already looks like sunlit bunting tone-maps to pale card.
  // A dye, and a dye has a blue channel: at 0x8e2b28 the green and the
  // blue sit at 0.024 and 0.021 in linear, so every part of the sheet
  // the sun misses tone-maps to a black-red hole. This reads the same
  // red in the light and still has cloth in it in the shade.
  const color = new THREE.Color(
      opts.color === undefined ? 0x9b3630 : opts.color);

  const g = new THREE.Group();
  g.name = opts.name || 'Flag';
  const rad = Math.max(0.018, Math.min(0.05, height * 0.045));
  if (mast > 0) g.add(mastPole(mast, rad));
  const fly = new THREE.Group();
  fly.name = 'FlagFly';
  fly.position.y = Math.max(mast, height + 0.05);
  fly.rotation.y = yawToX(wind.dir);
  fly.add(flagSheet(width, height, rad, color, wind, rand));
  g.add(fly);
  g.userData.tick = (t) => tickShaders(g, t);
  return g;
}

/** The pole, tapered and capped so it is not a bare cylinder. */
function mastPole(mast, rad) {
  const geom = new THREE.CylinderGeometry(rad * 0.72, rad, mast, 10, 1);
  geom.translate(0, mast / 2, 0);
  const mat = new THREE.MeshStandardMaterial({
    color: 0xb8b3a6, roughness: 0.45, metalness: 0.65, name: 'FlagMast',
  });
  const pole = new THREE.Mesh(geom, mat);
  pole.name = 'Mast';
  pole.castShadow = true;
  pole.receiveShadow = true;
  const cap = new THREE.Mesh(
      new THREE.SphereGeometry(rad * 1.7, 10, 8), mat);
  cap.name = 'MastFinial';
  cap.position.y = mast;
  cap.castShadow = true;
  pole.add(cap);
  return pole;
}

/**
 * The sheet: one plane whose envelope is CPU data, not GLSL.
 *
 * `aFlag` carries (u along the chord, the amplitude envelope, its
 * derivative for the normal, the integral of its square for the chord
 * shortening). Keeping the envelope on the CPU is what lets a test
 * read "pinned at the mast, free at the fly" as a number instead of
 * trusting a claim about a shader.
 */
function flagSheet(width, height, rad, color, wind, rand) {
  const segX = Math.max(24, Math.min(96, Math.round(width * 40)));
  const segY = Math.max(8, Math.min(48, Math.round(height * 20)));
  const geom = new THREE.PlaneGeometry(width, height, segX, segY);
  geom.translate(width / 2 + rad, -height / 2, 0);
  const pos = geom.attributes.position;
  const env = new Float32Array(pos.count * 4);
  for (let i = 0; i < pos.count; i++) {
    const u = clamp01((pos.getX(i) - rad) / Math.max(width, 1e-6));
    // u^1.6, not u: a linear envelope reads as a sheet hinged at the
    // mast, and the growth toward the fly is the whole cue.
    env[i * 4] = u;
    env[i * 4 + 1] = Math.pow(u, 1.6);
    env[i * 4 + 2] = 1.6 * Math.pow(u, 0.6);
    env[i * 4 + 3] = Math.pow(u, 4.2) / 4.2;
  }
  geom.setAttribute('aFlag', new THREE.BufferAttribute(env, 4));

  const waves = Math.max(0.9, Math.min(3, 1.15 * width / Math.max(
      height, 1e-3)));
  const k = _TAU * waves;
  const amp = width * 0.22 * wind.amp;
  const rate = 11 * wind.speed;
  // A slack flag hangs; a taut one flies. Both are the same cloth, so
  // the droop is what `strength` buys back.
  const sag = height * 0.30 / (1 + 4.4 * wind.amp);
  const shrink = amp * amp * k * k / (4 * Math.max(width, 1e-6));
  const mat = new THREE.MeshStandardMaterial({
    color, roughness: 0.86, metalness: 0, side: THREE.DoubleSide,
    name: 'FlagCloth',
  });
  const head = [
    'uniform float uFlagAmp;',
    'uniform float uFlagK;',
    'uniform float uFlagRate;',
    'uniform float uFlagTilt;',
    'uniform float uFlagSag;',
    'uniform float uFlagShrink;',
    'uniform float uFlagMaxShrink;',
    'uniform float uFlagWidth;',
    'uniform float uFlagPhase;',
    'attribute vec4 aFlag;',
    'varying vec4 vFlag;',
  ].join('\n');
  patchStandard(mat, {
    name: 'cloth:flag',
    uniforms: {
      uFlagAmp: { value: amp },
      uFlagK: { value: k },
      uFlagRate: { value: rate },
      uFlagTilt: { value: 0.9 / Math.max(height, 1e-3) },
      uFlagSag: { value: sag },
      uFlagShrink: { value: shrink },
      uFlagMaxShrink: { value: width * 0.25 },
      uFlagWidth: { value: width },
      uFlagPhase: { value: rand() * _TAU },
      // Threads per SHEET, at 300 to the metre and 0.06 wide — thin
      // enough that astraStroke drops them once a pixel spans one,
      // which is every distance but a hand's breadth away.
      uFlagWeave: { value: new THREE.Vector2(width, height)
          .multiplyScalar(300) },
      // Cloth is fuzzy, so a sheet turning away from the eye catches
      // the sky along its own curve instead of ending in an ink edge.
      // The facing term is computed per vertex, where the wave's
      // normal already is; the fragment only spends it.
      uFlagSheen: { value: 0.85 },
      // Years on a pole: a flag fades from the FLY inward, because the
      // hoist spends its life rolled around the rope.
      uFlagFade: { value: 0.42 },
      // Radians of dye swing across the sheet, warm to cool. Dyed
      // cloth is never one value, and one value is what reads plastic.
      uFlagDye: { value: 0.6 },
    },
    vertexHead: head,
    vertexBody: FLAG_MOVE + '\n' + FLAG_NORMAL,
    fragmentHead: [
      'uniform vec2 uFlagWeave;',
      'uniform float uFlagSheen;',
      'uniform float uFlagFade;',
      'uniform float uFlagDye;',
      'varying vec4 vFlag;',
    ].join('\n'),
    fragmentBody: FLAG_FRAGMENT,
  });

  const mesh = new THREE.Mesh(geom, mat);
  mesh.name = 'FlagSheet';
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  shadowLike(mesh, 'cloth:flagDepth', head, FLAG_MOVE);
  // The wave lives in the vertex shader, so the derived bounds are the
  // FLAT sheet's and three would cull the flag on a box it leaves.
  const reach = amp * 1.42;
  geom.boundingBox = new THREE.Box3(
      new THREE.Vector3(rad - 0.02, -height - sag, -reach),
      new THREE.Vector3(rad + width + 0.02, 0.02, reach));
  geom.boundingSphere = geom.boundingBox.getBoundingSphere(
      new THREE.Sphere());
  return mesh;
}

const FLAG_MOVE = [
  '  float flU = aFlag.x;',
  // The phase runs k*u - rate*t, so a crest LEAVES the mast; the tilt
  // term keeps the crest off the vertical, as a real fly is.
  '  float flPh = uFlagK * flU - uFlagRate * uTime + uFlagPhase',
  '      + uFlagTilt * transformed.y;',
  '  float flS = sin(flPh) + 0.42 * sin(1.7 * flPh + 1.3);',
  '  float flD = cos(flPh) + 0.714 * cos(1.7 * flPh + 1.3);',
  '  float flZ = uFlagAmp * aFlag.y * flS;',
  // Cloth does not stretch: a rippled chord has to give back the
  // length its own slope spends, or the fly grows as the wind rises.
  '  float flBack = min(uFlagShrink * aFlag.w, uFlagMaxShrink);',
  '  transformed.x -= flBack;',
  '  transformed.y -= uFlagSag * aFlag.y;',
  '  transformed.z += flZ;',
  // .w is the facing term the sheen spends; the depth pass is
  // FLAT_SHADED and never fills it, so it starts at "square on".
  '  vFlag = vec4(flU, uv.y, flS, 1.0);',
].join('\n');

const FLAG_NORMAL = [
  '#ifndef FLAT_SHADED',
  '  float flZx = uFlagAmp * (aFlag.z * flS + aFlag.y * flD * uFlagK)',
  '      / max(uFlagWidth, 1e-4);',
  '  float flZy = uFlagAmp * aFlag.y * flD * uFlagTilt;',
  '  vNormal = normalize(normalMatrix * normalize(',
  '      vec3(-flZx, -flZy, 1.0)));',
  // Both in VIEW space, so a scaled or rotated flag needs no basis of
  // its own: 1 square on to the eye, 0 edge-on.
  '  vec4 flMv = modelViewMatrix * vec4(transformed, 1.0);',
  '  vFlag.w = astraFacing(vNormal, -flMv.xyz);',
  '#endif',
].join('\n');

const FLAG_FRAGMENT = [
  // Dyed cloth is never one hex. A slow warm/cool break across the
  // SHEET's own coordinates — so it travels with the cloth rather than
  // swimming through it — is the difference between bunting and a
  // painted panel, and it costs one fbm.
  '  diffuseColor.rgb = astraHueBreak(diffuseColor.rgb,',
  '      vec2(vFlag.x * 2.6, vFlag.y * 1.7), 1.0, uFlagDye);',
  // Warp and weft, not a wash — and gone before either can alias.
  '  float flW = astraStroke(vFlag.y * uFlagWeave.y, 0.06)',
  '      + astraStroke(vFlag.x * uFlagWeave.x, 0.06);',
  '  diffuseColor.rgb *= 0.94 + 0.10 * flW;',
  // The hoist is doubled cloth over a rope, so it is darker and
  // slightly duller than the field of the flag.
  '  diffuseColor.rgb *= 1.0 - 0.30 * (1.0 - smoothstep(0.0, 0.045,',
  '      vFlag.x));',
  // And the FLY is the end that has flown: sun and weather take the
  // dye out of it, which lightens and desaturates rather than fading
  // to grey. Without this a flag is uniformly new, and nothing in a
  // scene is uniformly new.
  '  float flOld = uFlagFade * smoothstep(0.30, 1.0, vFlag.x);',
  '  float flLum = dot(diffuseColor.rgb, vec3(0.2126, 0.7152, 0.0722));',
  '  diffuseColor.rgb = mix(diffuseColor.rgb,',
  '      mix(diffuseColor.rgb, vec3(flLum), 0.5) * 1.30, flOld);',
  // Crests catch the sky and troughs hold shade: this is what makes
  // the travelling wave read in flat light as well as in sun. The
  // swing carries HUE too — a crest is turned to a blue sky and a
  // trough sees only the warm bounce off the cloth beside it.
  '  float flC = clamp(vFlag.z * 0.5 + 0.5, 0.0, 1.0);',
  '  diffuseColor.rgb *= 0.84 + 0.26 * flC;',
  '  diffuseColor.rgb = astraHueShift(diffuseColor.rgb,',
  '      (flC - 0.5) * 0.17);',
  // Cloth fuzz: the pile stands off the weave, so a sheet turning away
  // lights along its own curve instead of ending in an ink edge. This
  // is the cue that separates cloth from sheet metal in one frame.
  '  float flGrz = 1.0 - clamp(vFlag.w, 0.0, 1.0);',
  '  diffuseColor.rgb *= 1.0 + uFlagSheen * flGrz * flGrz * flGrz;',
].join('\n');

/**
 * A cloth banner or curtain hung along its top edge.
 *
 * Buys the cue that says "hung, and heavy": the whole sheet swings as
 * a PENDULUM about its top edge at sqrt(g / drop) rad/s — a 4 m
 * curtain is visibly slower than a 1 m one — and it gathers into fold
 * lines that run straight DOWN from the hanging edge, pinched at the
 * rod and opening toward the hem. Nothing flaps: a hung cloth that
 * flaps like a flag reads as paper.
 *
 * The constraint: the fold profile is a function of x ALONE, held in a
 * per-vertex attribute, so the folds cannot drift into diagonal
 * ripples; and the swing is a rigid rotation about the top edge, so
 * the cloth swings without growing.
 *
 * @param {object} [opts]
 *   `width` metres across (default 2); `drop` metres from the hanging
 *   edge to the hem (default 3); `hang` height of the hanging edge
 *   above the group origin (default `drop`, which puts the hem on
 *   y = 0); `wind` grass.js's option — the banner leans and swings
 *   ALONG `dir`; `color` cloth albedo (default a banner crimson);
 *   `seed` PRNG seed (default 11); `rod` draw the rail it hangs from
 *   (default true); `folds` how many fold lines across the width
 *   (default 5); `name` group name.
 * @returns {THREE.Group} Named `Banner`, holding `BannerCloth` and
 *   (unless declined) `BannerRod`, with `userData.tick(t)` driving the
 *   swing.
 */
export function makeBanner(opts = {}) {
  const width = opts.width === undefined ? 2 : opts.width;
  const drop = opts.drop === undefined ? 3 : opts.drop;
  const hang = opts.hang === undefined ? drop : opts.hang;
  const seed = opts.seed === undefined ? 11 : opts.seed;
  const folds = Math.max(1, Math.round(
      opts.folds === undefined ? 5 : opts.folds));
  const wind = windOf(opts.wind);
  const rand = mulberry32(seed);
  // A curtain is the biggest flat area this library ships, and it
  // usually hangs in its own shade: 0x7a2733 puts its green and blue
  // at 0.019 and 0.032 linear, which is a black hole with a red rim.
  // Same crimson, with cloth still in it where the sun is not.
  const color = new THREE.Color(
      opts.color === undefined ? 0x963a36 : opts.color);

  const g = new THREE.Group();
  g.name = opts.name || 'Banner';
  const swing = new THREE.Group();
  swing.name = 'BannerHang';
  swing.position.y = hang;
  // Local +Z is downwind, so the pendulum swings in the ZY plane and
  // the fold lines stay square to the rod.
  swing.rotation.y = yawToZ(wind.dir);
  swing.add(bannerCloth(width, drop, folds, color, wind, rand));
  if (opts.rod !== false) swing.add(bannerRod(width, drop));
  g.add(swing);
  g.userData.tick = (t) => tickShaders(g, t);
  return g;
}

/** The rail: what makes "fixed along the top edge" visible. */
function bannerRod(width, drop) {
  const r = Math.max(0.012, Math.min(0.045, drop * 0.012));
  const geom = new THREE.CylinderGeometry(r, r, width * 1.08, 10, 1);
  geom.rotateZ(Math.PI / 2);
  const mesh = new THREE.Mesh(geom, new THREE.MeshStandardMaterial({
    color: 0x6a5a44, roughness: 0.6, metalness: 0.3, name: 'BannerRod',
  }));
  mesh.name = 'BannerRod';
  mesh.position.y = r * 0.4;
  mesh.castShadow = true;
  return mesh;
}

/**
 * The cloth: folds are CPU data indexed by x, swing is one angle.
 *
 * `aBan` carries (depth fraction from the rod, the fold profile at
 * this x, its slope, the width the fold gathers). Every one of them is
 * a function of x or of y ALONE, which is exactly the claim "the folds
 * run down" — and a test can read it off the attribute.
 */
function bannerCloth(width, drop, folds, color, wind, rand) {
  const segX = Math.max(24, Math.min(120, Math.round(width * 26)));
  const segY = Math.max(10, Math.min(60, Math.round(drop * 12)));
  const geom = new THREE.PlaneGeometry(width, drop, segX, segY);
  geom.translate(0, -drop / 2, 0);
  const prof = foldProfile(folds, rand);
  const pos = geom.attributes.position;
  const data = new Float32Array(pos.count * 4);
  for (let i = 0; i < pos.count; i++) {
    const x = pos.getX(i) / Math.max(width, 1e-6) + 0.5;
    const f = prof(x);
    data[i * 4] = clamp01(-pos.getY(i) / Math.max(drop, 1e-6));
    data[i * 4 + 1] = f.v;
    data[i * 4 + 2] = f.d / Math.max(width, 1e-6);
    data[i * 4 + 3] = f.s;
  }
  geom.setAttribute('aBan', new THREE.BufferAttribute(data, 4));

  // A fold is deep in proportion to its OWN spacing, never to the
  // width: at a fifth of the spacing the cloth is already gathering
  // back a tenth of its span, and past that it is a concertina.
  const fold = Math.min(0.16 * width / folds, drop * 0.06);
  // The pendulum a cloth of this drop actually is. Doubling the drop
  // slows the swing by 1.41, which is the whole "heavy" cue.
  const rate = Math.sqrt(_G / Math.max(drop, 0.05));
  const lean = 0.55 * wind.amp;
  const sway = 0.18 * wind.amp + 0.02;
  const mat = new THREE.MeshStandardMaterial({
    color, roughness: 0.9, metalness: 0, side: THREE.DoubleSide,
    name: 'BannerClothMat',
  });
  patchStandard(mat, {
    name: 'cloth:banner',
    uniforms: {
      uBanDrop: { value: drop },
      uBanFold: { value: fold },
      uBanShrink: { value: fold * fold / Math.max(width, 1e-6) },
      uBanShrinkMax: { value: width * 0.15 },
      uBanRate: { value: rate },
      uBanLean: { value: lean },
      uBanSway: { value: sway },
      // The hem arrives after the rod: a cloth with no lag swings as
      // one board, which is the paper look again.
      uBanLag: { value: 0.85 },
      uBanPhase: { value: rand() * _TAU },
      // Threads across the SHEET at 300 to the metre: thin enough that
      // astraStroke drops them before they can alias into moire.
      uBanWeave: { value: width * 300 },
      // Same three as the flag: the fuzz that lights a turning edge,
      // the dye that is never one value, and the dust a hem stands in.
      uBanSheen: { value: 0.75 },
      uBanDye: { value: 0.38 },
      uBanDust: { value: 0.40 },
    },
    vertexHead: BANNER_HEAD,
    vertexBody: BANNER_MOVE + '\n' + BANNER_NORMAL,
    fragmentHead: [
      'uniform float uBanWeave;',
      'uniform float uBanSheen;',
      'uniform float uBanDye;',
      'uniform float uBanDust;',
      'varying vec4 vBan;',
    ].join('\n'),
    fragmentBody: BANNER_FRAGMENT,
  });

  const mesh = new THREE.Mesh(geom, mat);
  mesh.name = 'BannerCloth';
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  shadowLike(mesh, 'cloth:bannerDepth', BANNER_HEAD, BANNER_MOVE);
  // The hem can only RISE as the cloth swings (y = -drop * cos), so
  // the box's floor is the drop itself and the asset still rests where
  // the caller hung it.
  const reach = drop * Math.sin(Math.min(lean + sway, 1.3)) + fold + 0.02;
  geom.boundingBox = new THREE.Box3(
      new THREE.Vector3(-width / 2 - fold, -drop, -fold - 0.02),
      new THREE.Vector3(width / 2 + fold, 0.02, reach));
  geom.boundingSphere = geom.boundingBox.getBoundingSphere(
      new THREE.Sphere());
  return mesh;
}

/**
 * The fold profile across the width: value, slope and gathered width.
 *
 * All three are functions of x ALONE, which is the whole claim "the
 * fold lines run down". Seeded sines of integer frequency, so the
 * profile is smooth and the count of fold lines is what `folds` says;
 * the gathered width is the slope-squared integral, in units of the
 * NORMALISED x the shader scales back to metres.
 */
function foldProfile(folds, rand) {
  const modes = [];
  let peak = 1e-6;
  for (let i = 0; i < 3; i++) {
    modes.push([folds + i, rand() * _TAU, 1 / (1 + i * 1.4)]);
  }
  const raw = (x) => {
    let v = 0;
    for (const [f, p, a] of modes) v += a * Math.sin(_TAU * f * x + p);
    return v;
  };
  for (let i = 0; i <= 256; i++) peak = Math.max(peak, Math.abs(raw(i / 256)));
  const slope = (x) => {
    let d = 0;
    for (const [f, p, a] of modes) {
      d += a * _TAU * f * Math.cos(_TAU * f * x + p);
    }
    return d / peak;
  };
  // One pass of the slope integral, sampled once and reused: cheaper
  // than integrating per vertex and identical for equal x.
  const N = 512;
  const cum = new Float32Array(N + 1);
  for (let i = 1; i <= N; i++) {
    const s = slope((i - 0.5) / N);
    cum[i] = cum[i - 1] + 0.5 * s * s / N;
  }
  const mid = cum[N] / 2;
  return (x) => {
    const t = clamp01(x) * N;
    const i = Math.min(N - 1, Math.floor(t));
    const f = t - i;
    return {
      v: raw(clamp01(x)) / peak,
      d: slope(clamp01(x)),
      s: cum[i] + (cum[i + 1] - cum[i]) * f - mid,
    };
  };
}

const BANNER_HEAD = [
  'uniform float uBanDrop;',
  'uniform float uBanFold;',
  'uniform float uBanShrink;',
  'uniform float uBanShrinkMax;',
  'uniform float uBanRate;',
  'uniform float uBanLean;',
  'uniform float uBanSway;',
  'uniform float uBanLag;',
  'uniform float uBanPhase;',
  'attribute vec4 aBan;',
  'varying vec4 vBan;',
].join('\n');

const BANNER_MOVE = [
  '  float bnS = aBan.x;',
  // Folds open downward from the rod, where the cloth is nailed flat.
  '  float bnE = bnS * (2.0 - bnS);',
  '  float bnFold = uBanFold * aBan.y * bnE;',
  // ONE angle for the whole cloth, at the drop\'s own pendulum rate,
  // with the hem arriving after the rod.
  '  float bnA = uBanLean + uBanSway * sin(uBanRate * uTime + uBanPhase',
  '      - uBanLag * bnS);',
  '  float bnC = cos(bnA), bnSi = sin(bnA);',
  '  float bnD = uBanDrop * bnS;',
  // Cloth pulled into a fold is cloth taken out of the span, so the
  // banner narrows as its folds deepen instead of growing.
  '  transformed.x -= clamp(uBanShrink * aBan.w * bnE * bnE,',
  '      -uBanShrinkMax, uBanShrinkMax);',
  '  transformed.y = -bnD * bnC;',
  '  transformed.z = bnD * bnSi + bnFold;',
  '  vBan = vec4(bnS, aBan.y, uv.x, 1.0);',
].join('\n');

const BANNER_NORMAL = [
  '#ifndef FLAT_SHADED',
  '  float bnZx = uBanFold * aBan.z * bnE;',
  '  float bnZd = uBanFold * aBan.y * (2.0 - 2.0 * bnS)',
  '      / max(uBanDrop, 1e-4);',
  '  vNormal = normalize(normalMatrix * normalize(',
  '      vec3(-bnZx * bnC, bnSi + bnZd, bnC)));',
  // View space, as the flag's: 1 square on to the eye, 0 edge-on.
  '  vec4 bnMv = modelViewMatrix * vec4(transformed, 1.0);',
  '  vBan.w = astraFacing(vNormal, -bnMv.xyz);',
  '#endif',
].join('\n');

const BANNER_FRAGMENT = [
  // A hung cloth is the largest flat area this library draws, so it is
  // the one that most needs its dye broken up: a slow warm/cool field
  // across the width and down the drop, in the cloth's own frame.
  '  diffuseColor.rgb = astraHueBreak(diffuseColor.rgb,',
  '      vec2(vBan.z * 2.2, vBan.x * 1.6), 1.0, uBanDye);',
  '  float bnW = astraStroke(vBan.z * uBanWeave, 0.06);',
  '  diffuseColor.rgb *= 0.95 + 0.09 * bnW;',
  // A fold holds shade in its trough and the light on its ridge, and
  // the gradient DEEPENS downward as the fold opens. Hue follows the
  // value: a trough sees only the sky, a ridge sees the sun.
  '  float bnF = vBan.y * smoothstep(0.0, 0.6, vBan.x);',
  '  diffuseColor.rgb *= 1.0 + 0.26 * bnF;',
  '  diffuseColor.rgb = astraHueShift(diffuseColor.rgb, 0.13 * bnF);',
  // A hem stands in dust, and dust GREYS cloth. Darkening alone leaves
  // a dark dye reading as soot; this keeps the hem lighter than the
  // shadow it sits in and still visibly dirtier than the rod.
  '  diffuseColor.rgb = mix(diffuseColor.rgb, vec3(0.20, 0.185, 0.16),',
  '      uBanDust * smoothstep(0.42, 1.0, vBan.x));',
  '  diffuseColor.rgb *= 1.03 - 0.10 * vBan.x;',
  // Cloth fuzz at the turn of a fold — the same term the flag spends,
  // and here it is what makes the folds read at all in flat light.
  '  float bnGrz = 1.0 - clamp(vBan.w, 0.0, 1.0);',
  '  diffuseColor.rgb *= 1.0 + uBanSheen * bnGrz * bnGrz * bnGrz;',
].join('\n');

/**
 * A field of tall crop whose gusts cross it as visible waves.
 *
 * Buys the shot a meadow cannot give: a hectare of wheat with the wind
 * running over it in long streaks, at a stated metres per second, so
 * the field reads as weather rather than as animated scenery. Every
 * stalk samples ONE gust field — a seeded, seamlessly tiling texture
 * scrolled downwind at `uWheatMps` — so neighbours move together with
 * the travel lag between them, and per-stalk wobble is a tenth of it.
 *
 * The constraint: this is a CROP, not tall grass. It is sown in drill
 * rows at `rowGap`, so the field carries lines a meadow never has; the
 * stalk is a bare stem with a heavy EAR that bends further than the
 * stem does; and the wave is made visible by the pale flank a laid-
 * over ear turns to the light, not by the bend alone. For ankle-high
 * ground cover use `grass.js`; at a waterline use `makeReeds`.
 *
 * @param {object} [opts]
 *   `extent` metres of the square field's side (default 30, centred on
 *   the group origin); `density` stalks per square metre (default 70;
 *   `maxStalks` caps it by widening the DRILL spacing, never by
 *   dropping a corner); `height` stalk height in metres (default 1.1);
 *   `wind` grass.js's option — `speed` scales the wave's travel, which
 *   is `waveMps` metres per second; `color` straw albedo; `earColor`
 *   the ripe head; `heightAt` (x, z) => y, the caller's ground;
 *   `seed` PRNG seed (default 11); `rowGap` drill spacing in metres
 *   (default 0.18); `waveMps` crest travel at `wind.speed` 1 (default
 *   7, about what a crop wave really runs at); `maxStalks` hard cap
 *   (default 70000); `shadows` cast stalk shadows through a displaced
 *   depth pass (default false — the same cost call grass.js makes);
 *   `name` group name.
 * @returns {THREE.Group} Named `Wheat`, resting on y = 0 (or on
 *   `heightAt`), holding `Stalks` — ONE draw call — with
 *   `userData.tick(t)` driving the wave.
 */
export function makeWheatField(opts = {}) {
  const extent = opts.extent === undefined ? 30 : opts.extent;
  const density = opts.density === undefined ? 70 : opts.density;
  const height = opts.height === undefined ? 1.1 : opts.height;
  const seed = opts.seed === undefined ? 11 : opts.seed;
  const rowGap = opts.rowGap === undefined ? 0.18 : opts.rowGap;
  const maxStalks = opts.maxStalks === undefined ? 70000 : opts.maxStalks;
  const waveMps = opts.waveMps === undefined ? 7 : opts.waveMps;
  const ground = typeof opts.heightAt === 'function' ? opts.heightAt : null;
  const wind = windOf(opts.wind);
  const straw = new THREE.Color(
      opts.color === undefined ? 0xa8863a : opts.color);
  const ear = new THREE.Color(
      opts.earColor === undefined ? 0xd2ad55 : opts.earColor);

  const g = new THREE.Group();
  g.name = opts.name || 'Wheat';
  const field = sowField(
      extent, density, rowGap, maxStalks, height, ground, seed);
  g.add(stalkMesh(field, extent, straw, ear, wind, waveMps, seed,
                  opts.shadows === true));
  g.userData.tick = (t) => tickShaders(g, t);
  return g;
}

/**
 * Sow the field in DRILL ROWS — the cue no meadow has.
 *
 * A crop is planted by a machine: straight rows at a fixed gap, dense
 * along the row, bare between. Scattering it like grass.js's clumps
 * would make this library a taller copy of that one, and would lose
 * the lines that tell a viewer the ground was worked.
 */
function sowField(extent, density, rowGap, maxStalks, height, ground,
                  seed) {
  const rand = mulberry32(seed);
  // The drill ran at some angle to the world; the wind does not know
  // about it, which is what keeps the wave off the rows.
  const ang = rand() * Math.PI;
  const ux = Math.cos(ang), uz = Math.sin(ang);
  const want = Math.max(1, Math.round(density * extent * extent));
  // A cap widens the DRILL spacing, so a capped field is thinner and
  // never smaller than the extent it advertises.
  const scale = Math.sqrt(Math.max(1, want / maxStalks));
  const gap = rowGap * scale;
  const step = scale / Math.max(density * rowGap, 1e-3);
  const span = extent * 0.71 + gap;
  const rows = Math.floor(span / Math.max(gap, 1e-3));
  const along = Math.floor(span / Math.max(step, 1e-3));
  const half = extent / 2;
  const pos = [], crop = [], vary = [];
  let n = 0, maxH = 0, low = Infinity, high = -Infinity;
  for (let r = -rows; r <= rows && n < maxStalks; r++) {
    for (let s = -along; s <= along && n < maxStalks; s++) {
      const a = s * step + (rand() - 0.5) * step * 0.8;
      const b = r * gap + (rand() - 0.5) * gap * 0.45;
      const x = ux * a - uz * b;
      const z = uz * a + ux * b;
      if (x < -half || x > half || z < -half || z > half) continue;
      const y = ground ? ground(x, z) : 0;
      const h = height * (0.82 + 0.30 * rand());
      pos.push(x, y, z);
      // A taller stalk is a longer lever on the same stem, so it lays
      // over further in the same gust.
      crop.push(h, h * (0.0032 + 0.0016 * rand()), _EAR_AT,
                (0.72 + 0.34 * rand()) * (0.7 + 0.5 * h / height));
      vary.push(rand(), (rand() - 0.5) * 0.5, (rand() - 0.5) * 0.36);
      maxH = Math.max(maxH, h);
      low = Math.min(low, y);
      high = Math.max(high, y + h);
      n++;
    }
  }
  return { n, pos, crop, vary, maxH,
           low: n ? low : 0, high: n ? high : 0 };
}

/**
 * The gust field, baked once on the CPU so both sides agree on it.
 *
 * grass.js builds its gusts from GLSL_UTIL's noise, which takes no
 * seed — every grass field in every scene gusts identically, and no
 * test can read what the shader will see. A tiling texture fixes both:
 * it is seeded, and the exact numbers the vertex shader samples are
 * readable from `uniforms.uWheatWave.value.image.data`.
 *
 * @param {number} seed PRNG seed.
 * @param {number} [size] Texels per side (default 128).
 * @returns {THREE.DataTexture} Repeat-wrapped, linear, no mipmaps
 *   (a vertex fetch has no derivatives to choose one with).
 */
function gustTexture(seed, size = 128) {
  const rand = mulberry32(seed);
  const modes = _WAVE_MODES.map(
      ([p, q]) => [p, q, rand() * _TAU, 1 / Math.pow(p * p + q * q, 0.62)]);
  const raw = new Float32Array(size * size);
  let peak = 1e-6;
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      let v = 0;
      for (const [p, q, ph, a] of modes) {
        v += a * Math.sin(_TAU * (p * x / size + q * y / size) + ph);
      }
      raw[y * size + x] = v;
      peak = Math.max(peak, Math.abs(v));
    }
  }
  const data = new Uint8Array(size * size * 4);
  for (let i = 0; i < raw.length; i++) {
    const v = Math.round(255 * clamp01(0.5 + 0.5 * raw[i] / peak));
    data[i * 4] = data[i * 4 + 1] = data[i * 4 + 2] = v;
    data[i * 4 + 3] = 255;
  }
  const tex = new THREE.DataTexture(data, size, size, THREE.RGBAFormat);
  tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
  tex.minFilter = tex.magFilter = THREE.LinearFilter;
  tex.generateMipmaps = false;
  tex.needsUpdate = true;
  return tex;
}

/**
 * The stalk lattice: ONE instanced strip with `position` pinned at 0.
 *
 * GTAOPass redraws with an override material that ignores this vertex
 * shader, so a real stalk left in `position` would stack every copy at
 * the world origin and burn a black slab there.
 */
function stalkLattice(count) {
  const base = new THREE.PlaneGeometry(1, 1, 1, _STALK_ROWS);
  base.translate(0, 0.5, 0);
  // Spend the rows where the curvature is: an evenly spaced strip
  // draws the nodding ear with one segment and it reads as an elbow.
  const p = base.attributes.position;
  for (let i = 0; i < p.count; i++) {
    const a = p.getY(i) * _STALK_ROWS;
    p.setY(i, a <= _STEM_ROWS
        ? _EAR_AT * a / _STEM_ROWS
        : _EAR_AT + (1 - _EAR_AT) * (a - _STEM_ROWS)
            / (_STALK_ROWS - _STEM_ROWS));
  }
  const g = new THREE.InstancedBufferGeometry();
  g.index = base.index;
  g.setAttribute('position', new THREE.BufferAttribute(
      new Float32Array(base.attributes.position.count * 3), 3));
  g.setAttribute('aCorner', base.attributes.position);
  g.setAttribute('normal', base.attributes.normal);
  g.setAttribute('uv', base.attributes.uv);
  g.instanceCount = count;
  return g;
}

/** One mesh, one draw call: every stalk lives in the vertex shader. */
function stalkMesh(field, extent, straw, ear, wind, waveMps, seed,
                   shadows) {
  const geom = stalkLattice(field.n);
  const inst = (name, arr, size) => geom.setAttribute(
      name, new THREE.InstancedBufferAttribute(new Float32Array(arr), size));
  inst('iPos', field.pos, 3);
  inst('iCrop', field.crop, 4);
  inst('iVar', field.vary, 3);
  // position is zero, so the derived bounds would be a point at the
  // origin. A stalk bends along an arc of its OWN length, so its tip
  // never leaves a circle of that radius about its root.
  const reach = extent / 2 + field.maxH;
  geom.boundingBox = new THREE.Box3(
      new THREE.Vector3(-reach, field.low, -reach),
      new THREE.Vector3(reach, field.high, reach));
  geom.boundingSphere = geom.boundingBox.getBoundingSphere(
      new THREE.Sphere());

  const mat = new THREE.MeshStandardMaterial({
    color: 0xffffff, roughness: 0.74, metalness: 0,
    side: THREE.DoubleSide, name: 'WheatStalk',
  });
  patchStandard(mat, {
    name: 'cloth:wheat',
    uniforms: {
      uWheatStraw: { value: straw.clone() },
      uWheatEar: { value: ear.clone() },
      uWheatWave: { value: gustTexture(seed) },
      uWheatWind: { value: wind.dir.clone() },
      // Metres the crest travels per second, and the metres one tile
      // of the gust field covers ALONG and ACROSS the wind. The 3.8:1
      // is grass.js's streak ratio, at the scale a crop wave needs.
      uWheatMps: { value: waveMps * wind.speed },
      uWheatTile: { value: new THREE.Vector2(1 / 46, 1 / 12) },
      // The shared wave is what moves this field; the per-stalk terms
      // are texture on top of it and must stay a fraction of it.
      uWheatBase: { value: 0.10 },
      uWheatGust: { value: 0.62 * wind.amp * 2 },
      uWheatJitter: { value: 0.05 * wind.amp * 2 },
      uWheatNod: { value: 0.9 },
      uWheatRate: { value: 2.1 * wind.speed },
      uWheatHead: { value: 3.2 },
      uWheatDroop: { value: 1.8 },
      uWheatSheen: { value: 0.7 },
      // A crop does not ripen all at once: it goes first where the
      // ground is light and last in the wet corner, so a field carries
      // green-gold PATCHES tens of metres across with stalk-to-stalk
      // scatter inside them. One flat straw hex over a hectare is the
      // single loudest tell that a field was painted, not sown — and it
      // costs one fbm of the root position to fix.
      uWheatPatch: { value: 0.055 },
      uWheatRipen: { value: 1 },
      // Straw is a shiny tube: the flank turning away from the eye is
      // where a stalk catches the sky, and it is why a crop glitters.
      uWheatEdge: { value: 0.26 },
    },
    vertexHead: STALK_HEAD,
    vertexBody: STALK_VERTEX,
    fragmentHead: [
      'uniform vec3 uWheatStraw;',
      'uniform vec3 uWheatEar;',
      'uniform float uWheatSheen;',
      'uniform float uWheatEdge;',
      'varying vec4 vWheat;',
      'varying float vWheatTone;',
    ].join('\n'),
    fragmentBody: STALK_FRAGMENT,
  });

  const mesh = new THREE.Mesh(geom, mat);
  mesh.name = 'Stalks';
  mesh.frustumCulled = false;
  mesh.receiveShadow = true;
  // Without the displaced depth pass a shadow pass draws only the
  // degenerate zero-position quads, so casting stays off by default:
  // tens of thousands of stalks in the shadow map are a real
  // SwiftShader cost the caller opts into.
  mesh.castShadow = false;
  if (shadows) {
    shadowLike(mesh, 'cloth:stalkDepth', STALK_HEAD, STALK_VERTEX);
  }
  return mesh;
}

const STALK_HEAD = [
  'uniform sampler2D uWheatWave;',
  'uniform vec2 uWheatWind;',
  'uniform vec2 uWheatTile;',
  'uniform float uWheatMps;',
  'uniform float uWheatBase;',
  'uniform float uWheatGust;',
  'uniform float uWheatJitter;',
  'uniform float uWheatNod;',
  'uniform float uWheatRate;',
  'uniform float uWheatHead;',
  'uniform float uWheatDroop;',
  'uniform float uWheatPatch;',
  'uniform float uWheatRipen;',
  'attribute vec3 aCorner;',
  'attribute vec3 iPos;',
  'attribute vec4 iCrop;',
  'attribute vec3 iVar;',
  'varying vec4 vWheat;',
  'varying float vWheatTone;',
  // A world direction as the LOCAL offset that moves this surface
  // one metre along it, valid while the basis stays orthogonal.
  'vec3 clothLocalDir(vec3 w) {',
  '  mat3 m = mat3(modelMatrix);',
  '  return vec3(dot(w, m[0]) / max(dot(m[0], m[0]), 1e-6),',
  '              dot(w, m[1]) / max(dot(m[1], m[1]), 1e-6),',
  '              dot(w, m[2]) / max(dot(m[2], m[2]), 1e-6));',
  '}',
].join('\n');

const STALK_VERTEX = [
  '  vec3 whRoot = iPos;',
  '  float whV = aCorner.y;',
  '  float whH = max(iCrop.x, 1e-3);',
  // Ripeness: a field-wide patch field plus this stalk's own scatter,
  // read ONCE per vertex from the root, so a stalk is one tone from
  // its foot to its ear and its neighbours are near it, not random.
  '  float whPatch = astraFbm2(whRoot.xz * uWheatPatch, 2) - 0.375;',
  '  vWheatTone = clamp((whPatch * 2.2 + iVar.y * 1.4) * uWheatRipen,',
  '      -1.0, 1.0);',
  // ONE gust field for the whole crop, scrolled downwind at a stated
  // m/s: two stalks a metre apart see the same wave, a lag apart.
  '  vec2 whW = uWheatWind;',
  '  vec2 whP = vec2(dot(whRoot.xz, whW),',
  '                  dot(whRoot.xz, vec2(-whW.y, whW.x)));',
  '  vec2 whUv = vec2((whP.x - uWheatMps * uTime) * uWheatTile.x,',
  '                   whP.y * uWheatTile.y);',
  '  float whWave = texture2D(uWheatWave, whUv).r;',
  '  float whGust = smoothstep(0.34, 0.98, whWave);',
  // Per-stalk motion is texture, not the subject: a twentieth of the
  // gust, or a hectare of wheat boils instead of rippling.
  '  float whJit = uWheatJitter * sin(uTime * uWheatRate',
  '      + astraStagger(iVar.x));',
  '  float whK1 = max(iCrop.w * (uWheatBase + uWheatGust * whGust',
  '      + whJit), 1e-3);',
  // A ripe ear is the heavy end of a light stem: it hangs even in
  // still air, keeps bending after the stem has stopped, and nods.
  '  float whK2 = whK1 * uWheatHead + uWheatDroop',
  '      + uWheatNod * (0.5 + 0.5 * whGust)',
  '      * sin(uTime * uWheatRate * 1.6 + astraStagger(iVar.x) * 1.3);',
  '  whK2 = max(whK2, 1e-3);',
  '  vec2 whF = normalize(whW + vec2(-whW.y, whW.x) * iVar.z);',
  '  float whE0 = iCrop.z;',
  // Two circular arcs of the stalk\'s own length: a translated tip
  // would stretch a stem that must instead lie over.
  '  float whA1 = whK1 * min(whV, whE0);',
  '  float whU = whH * (1.0 - cos(whA1)) / whK1;',
  '  float whY = whH * sin(whA1) / whK1;',
  '  float whA2 = whA1 + whK2 * max(whV - whE0, 0.0);',
  '  whU += whH * (cos(whA1) - cos(whA2)) / whK2;',
  '  whY += whH * (sin(whA2) - sin(whA1)) / whK2;',
  '  vec3 whSpine = whRoot + vec3(whF.x * whU, whY, whF.y * whU);',
  '  vec3 whTan = vec3(whF.x * sin(whA2), cos(whA2), whF.y * sin(whA2));',
  // The ear: a spindle on the top of the stem, and the only fat part
  // of a stalk that is otherwise 4 mm of straw.
  '  float whE = clamp((whV - whE0) / max(1.0 - whE0, 1e-3), 0.0, 1.0);',
  '  float whEar = whV > whE0 ? sqrt(max(sin(3.14159 * whE), 0.0)) : 0.0;',
  '  float whWid = iCrop.y * (1.0 - 0.35 * whV) + whEar * whH * 0.011;',
  // A stem is round, so its strip faces the eye from every angle
  // instead of collapsing to a line like a flat blade.
  '  vec3 whWp = (modelMatrix * vec4(whSpine, 1.0)).xyz;',
  '  vec3 whView = normalize(clothLocalDir(cameraPosition - whWp));',
  '  vec3 whC = cross(whTan, whView);',
  '  float whCl = length(whC);',
  '  vec3 whSide = whCl > 1e-4 ? whC / whCl : vec3(1.0, 0.0, 0.0);',
  '  float whX = aCorner.x * 2.0;',
  '  transformed = whSpine + whSide * (whX * whWid);',
  '  vWheat = vec4(whV, whEar, whGust, whX);',
  '#ifndef FLAT_SHADED',
  '  vec3 whFace = normalize(cross(whSide, whTan));',
  '  vNormal = normalize(normalMatrix * normalize(',
  '      whFace * sqrt(max(1.0 - whX * whX, 0.04)) + whSide * whX));',
  '#endif',
].join('\n');

const STALK_FRAGMENT = [
  // Green where the crop is still coming, gold where it has come. The
  // hue swing is 0.30 rad — under a fifth of that and a mixed field
  // reads as one straw with a lighting error in it.
  '  float whRipe = vWheatTone * 0.5 + 0.5;',
  '  vec3 whCol = astraHueShift(uWheatStraw,',
  '      vWheat.w * 0.04 - vWheatTone * 0.30);',
  '  whCol *= 0.80 + 0.32 * whRipe;',
  // Ripe from the ear DOWN, not only on the ear: from above, a crop
  // is the colour of its heads and the hand's breadth under them.
  '  float whGold = clamp(0.6 * smoothstep(0.5, 0.95, vWheat.x)',
  '      + 0.6 * smoothstep(0.05, 0.5, vWheat.y), 0.0, 1.0);',
  '  whCol = mix(whCol, astraHueShift(uWheatEar, -vWheatTone * 0.16),',
  '      whGold * (0.62 + 0.38 * whRipe));',
  // A crop is DEEP: the foot of a stalk sits in the shade of the row,
  // and that gradient is most of what reads as a standing field.
  '  whCol *= 0.34 + 0.66 * smoothstep(0.0, 0.55, vWheat.x);',
  // Spikelets: the grain sits in ranks up the ear, and this is the
  // texture that says wheat rather than tall grass.
  '  whCol *= 1.0 - 0.35 * vWheat.y',
  '      * astraStroke(vWheat.x * 220.0, 0.08);',
  // THE WAVE, made visible: a laid-over crop turns its pale flanks to
  // the sky, so a crest is a band of light crossing the field and a
  // trough is a band of shade.
  '  whCol *= 0.84 + uWheatSheen * vWheat.z * (0.6 + 0.4 * vWheat.y);',
  // A stem is a polished tube: its turning flank is where it catches
  // the sky, and a field of those flanks is what glitters. The strip
  // already carries its across-width coordinate, so this is free.
  '  float whFl = abs(vWheat.w);',
  '  whCol *= 1.0 + uWheatEdge * whFl * whFl * whFl;',
  '  diffuseColor.rgb *= whCol;',
].join('\n');

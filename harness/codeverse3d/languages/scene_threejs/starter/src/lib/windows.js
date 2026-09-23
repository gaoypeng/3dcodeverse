/**
 * Night windows that are ROOMS, not glowing rectangles.
 *
 * A lit window is the strongest habitation cue a night city has, and an
 * emissive rectangle is the flattest way to draw one: it has no inside,
 * so a whole facade reads as a decal. This shades the INTERIOR instead —
 * from the surface UV and the view direction it intersects a shallow box
 * behind the glass and shades the wall the ray lands on, so every window
 * has a back wall, side walls, a floor and a ceiling that slide with the
 * camera. No extra geometry and no second draw call: it is a
 * `patchStandard` on the facade material the mesh already has.
 */

import * as THREE from 'three';
import { clonePatchedMaterial, patchStandard, toColor } from './shader.js';
import { attachDisposal } from './lifecycle.js';

const WIN_VARYINGS = [
  'varying vec2 vWinUv;',
  'varying vec3 vWinW;',
  'varying vec3 vWinN;',
  'varying vec3 vWinKey;',
].join('\n');

// `transformed` is still object-space at <begin_vertex>, so an instanced
// facade needs instanceMatrix folded in by hand.
const WIN_VERTEX = [
  '  vec4 winP = vec4(transformed, 1.0);',
  '  vec3 winNo = normal;',
  '  vec4 winO = vec4(0.0, 0.0, 0.0, 1.0);',
  '#ifdef USE_INSTANCING',
  '  winP = instanceMatrix * winP;',
  '  winNo = astraNormalTransform(mat3(instanceMatrix), winNo);',
  '  winO = instanceMatrix * winO;',
  '#endif',
  '  vWinUv = uv;',
  '  vWinW = (modelMatrix * winP).xyz;',
  '  vWinN = astraNormalTransform(mat3(modelMatrix), winNo);',
  // The draw's own origin, constant across the mesh: it keys the lit
  // pattern per BUILDING even where a whole city shares one material.
  '  vWinKey = (modelMatrix * winO).xyz;',
].join('\n');

const WIN_HEAD = [
  'uniform vec3 uWinWarm;',
  'uniform vec3 uWinCool;',
  'uniform vec2 uWinGrid;',
  'uniform float uWinDepth;',
  'uniform float uWinLit;',
  'uniform float uWinSeed;',
  'uniform float uWinEmissive;',
  // The pane inside its cell; the rest of the cell stays facade, which
  // is what makes a grid of glass read as openings in a wall.
  'const vec2 ASTRA_WIN_EDGE = vec2(0.18, 0.22);',
  // A facade is any box face at any orientation and carries no tangent
  // attribute, so the frame comes from the screen-space derivatives.
  'mat3 astraWinFrame(vec3 p, vec2 st, vec3 n, out vec2 mpu) {',
  '  vec3 dp1 = dFdx(p), dp2 = dFdy(p);',
  '  vec2 du1 = dFdx(st), du2 = dFdy(st);',
  '  vec3 a = cross(dp2, n), b = cross(n, dp1);',
  '  vec3 t = a * du1.x + b * du2.x;',
  '  vec3 c = a * du1.y + b * du2.y;',
  '  float k = inversesqrt(max(max(dot(t, t), dot(c, c)), 1e-12));',
  // Metres per unit of UV: the only way a shader can tell a wall panel
  // from a mullion box carrying its own full 0..1 UV.
  '  float det = max(abs(du1.x * du2.y - du2.x * du1.y), 1e-12);',
  '  mpu = vec2(length(dp1 * du2.y - dp2 * du1.y),',
  '             length(dp2 * du1.x - dp1 * du2.x)) / det;',
  '  return mat3(t * k, c * k, n);',
  '}',
  // Interior mapping: the nearest wall of a room one cell wide whose
  // back sits `depth` behind the glass. z runs 0 (glass) to -depth, and
  // `face` comes back as the weight of the axis that was hit.
  'vec3 astraWinRoom(vec3 o, vec3 d, float depth, out vec3 face) {',
  '  vec3 ds = max(abs(d), vec3(1e-4)) * sign(d + 1e-9);',
  '  vec3 tHi = (vec3(1.0, 1.0, 0.0) - o) / ds;',
  '  vec3 tLo = (vec3(0.0, 0.0, -depth) - o) / ds;',
  '  vec3 tm = max(tLo, tHi);',
  '  float t = min(min(tm.x, tm.y), tm.z);',
  '  face = step(tm, vec3(t));',
  '  face /= max(face.x + face.y + face.z, 1.0);',
  '  return o + d * t;',
  '}',
].join('\n');

const WIN_BODY = [
  '  vec2 winG = vWinUv * uWinGrid;',
  '  vec2 winF = fract(winG);',
  '  vec2 winAa = fwidth(winG) + 1e-4;',
  // Derivatives are undefined under divergent control flow, so the pane
  // is a mask mixed in at the end, never an early return.
  '  vec2 winE = smoothstep(ASTRA_WIN_EDGE - winAa,',
  '                         ASTRA_WIN_EDGE + winAa, winF)',
  '      * (1.0 - smoothstep(1.0 - ASTRA_WIN_EDGE - winAa,',
  '                          1.0 - ASTRA_WIN_EDGE + winAa, winF));',
  '  float winPane = winE.x * winE.y;',
  '  vec3 winNn = normalize(vWinN);',
  '  vec3 winV = normalize(cameraPosition - vWinW);',
  '  vec2 winMpu;',
  '  mat3 winTbn = astraWinFrame(vWinW, vWinUv, winNn, winMpu);',
  // A cell under a metre across is a mullion or a spandrel band wearing
  // its own 0..1 UV, never a window; that member keeps its own surface.
  '  winPane *= smoothstep(0.55, 1.0, min(winMpu.x / uWinGrid.x,',
  '                                       winMpu.y / uWinGrid.y));',
  // And once a pixel spans a third of a cell the room can only alias
  // into sparkle, so the facade is handed back instead.
  '  winPane *= 1.0 - smoothstep(0.3, 0.8, max(winAa.x, winAa.y));',
  '  vec3 winD = -vec3(dot(winV, winTbn[0]), dot(winV, winTbn[1]),',
  '                    dot(winV, winTbn[2]));',
  // Edge-on and back-facing fragments would otherwise divide by ~0 and
  // smear one room across the whole wall.
  '  winD = normalize(vec3(winD.xy, min(winD.z, -0.02)));',
  // Quantised normal + draw origin: one key per face per building, so
  // four facades of one box are not the same lit pattern four times.
  //
  // EVERY term here is rounded to a whole number, and that is the whole
  // reason the pattern holds still. `vWinKey` is one constant handed to
  // every vertex, but a perspective-correct varying is (sum w/q)/(sum 1/q)
  // and comes back a few ULPs apart per pixel; astraHash21 amplifies an
  // input wobble by about a thousand (measured: 1 ULP at coordinate 120
  // moves it 0.008), so hashing the raw key and then hashing THAT into
  // the cell id chains two amplifications and turns `winOn` into a
  // per-pixel coin flip — a facade of static, which is what this
  // rendered before the floor()s went in.  On an integer lattice inside
  // +-700 the same hash is bit-exact per cell and still well spread
  // (mean 0.504, sigma 0.291, adjacent |dh| 0.333).
  '  vec3 winKq = floor(vWinKey * 16.0 + 0.5);',
  '  float winFace = dot(floor(winNn * 4.0 + 0.5), vec3(11.0, 29.0, 53.0));',
  '  float winB = floor(astraHash21(winKq.xz + winKq.y) * 29.0) + winFace;',
  '  vec2 winId = floor(winG)',
  '      + vec2(uWinSeed + winB, floor(uWinSeed * 1.7) - winB);',
  '  float winOn = step(astraHash21(winId), uWinLit);',
  // Three independent draws per room. A facade whose only variable is
  // on/off reads as a stencil; what a night city actually has is a wide
  // spread of BRIGHTNESS and a wide spread of HUE, and the fittings.
  '  float winHt = astraHash21(winId + 3.3);',
  '  float winHf = astraHash21(winId + 19.7);',
  '  float winHl = astraHash21(winId + 41.1);',
  '  vec2 winQ = clamp((winF - ASTRA_WIN_EDGE)',
  '      / max(1.0 - 2.0 * ASTRA_WIN_EDGE, vec2(1e-3)), 0.0, 1.0);',
  '  vec3 winHitFace;',
  '  vec3 winHit = astraWinRoom(vec3(winQ, 0.0), winD,',
  '                             max(uWinDepth, 0.02), winHitFace);',
  // The back wall carries the room, the ceiling catches the fitting and
  // the floor stays dark; side walls fall off with how deep they are hit.
  '  float winShade = winHitFace.z + winHitFace.x * 0.45',
  '      + winHitFace.y * mix(0.22, 0.8, step(0.5, winHit.y));',
  '  winShade *= mix(1.0, 0.5,',
  '                  clamp(-winHit.z / max(uWinDepth, 0.02), 0.0, 1.0));',
  // The fitting is on the CEILING, so a room falls off downward: without
  // this the back wall is one flat card and the box reads as a printed
  // panel however well the ray traced it.
  '  winShade *= mix(0.58, 1.14, smoothstep(0.0, 1.0, winHit.y));',
  // A room is never an empty box: furniture against the back wall. The
  // noise lattice is entered through the cell hash, not through winId
  // itself, so the coordinate stays small enough to interpolate.
  '  vec2 winNz = winHit.xy * 3.0 + vec2(winHt, winHl) * 37.0;',
  '  winShade *= mix(0.55, 1.0, astraNoise2(winNz));',
  // A blind pulled down over the top of a slice of the windows. The old
  // hard step() cut a razor line across the pane at exactly one height on
  // every floor of the building, which is the tell of a decal; this is a
  // soft hem, and it slides with the room because it rides winQ.
  '  winShade *= mix(1.0, 0.24, step(0.74, winHf)',
  '      * smoothstep(0.46, 0.64, winQ.y));',
  // And a curtain drawn across another slice: cloth SCATTERS, so that
  // room stops being a box and becomes an even glowing panel, brightest
  // where the fitting sits behind it. A facade of nothing but open rooms
  // is as uniform as a facade of nothing but rectangles.
  '  float winCurt = step(winHf, 0.30) * 0.85;',
  '  winShade = mix(winShade, 0.52 + 0.34 * (1.0 - winQ.y), winCurt);',
  // COLOUR. A two-tone warm/cool lerp gives a facade exactly two bulbs;
  // the rotation and the saturation draw spread those into a hundred,
  // which is the difference between a lit block and a printed one. The
  // rotation is small (+-5 degrees) because a night window that leaves
  // the tungsten-to-daylight axis stops reading as a window.
  '  vec3 winTint = mix(uWinWarm, uWinCool, smoothstep(0.45, 0.75, winHt));',
  '  winTint = astraHueShift(winTint, (winHl - 0.5) * 0.18);',
  '  float winGry = dot(winTint, vec3(0.2126, 0.7152, 0.0722));',
  '  winTint = max(mix(vec3(winGry), winTint,',
  '                    mix(0.72, 1.24, astraHash21(winId + 7.9))), 0.0);',
  // Cloth washes the bulb toward its own pale, never toward white: a
  // curtain over a tungsten room is still a warm curtain.
  '  winTint = mix(winTint, mix(winTint, vec3(winGry * 1.25), 0.3), winCurt);',
  // Brightness spread. Squared, so most rooms sit low and a few carry
  // the facade — the shape a photograph of a lit block actually has.
  '  float winLevel = mix(0.30, 1.15, winHl * winHl * (3.0 - 2.0 * winHl));',
  '  vec3 winRoom = winTint * (winShade * winLevel);',
  // Unlit cells keep the same room at a trickle and add the sky the
  // glass reflects: a black rectangle reads as a hole in the wall. The
  // sky it reflects is the SCENE's, not a constant — fog carries the
  // horizon's own hue, so the same facade goes pale blue at noon and
  // deep blue at midnight with no per-scene tuning. uWinCool stays in as
  // the floor, so a face-on pane in a scene with no fog is dark glass
  // rather than a hole.
  '  vec3 winSky = uWinCool;',
  '#ifdef USE_FOG',
  '  winSky = mix(uWinCool, fogColor, 0.7);',
  '#endif',
  // Glass mirrors the GROUND as well as the sky, and the line between
  // the two sweeping across a facade is most of what a curtain wall
  // looks like from the street. The split rides the reflected ray, so it
  // moves with the camera and bends round a corner instead of sitting on
  // the surface; the per-pane term is the batch spread real glazing has.
  '  vec3 winRefl = reflect(-winV, winNn);',
  '  winSky *= mix(0.34, 1.0, smoothstep(-0.30, 0.30, winRefl.y))',
  '      * mix(0.8, 1.25, winHt);',
  '  float winFr = astraFresnel(winNn, winV, 3.0);',
  '  vec3 winDark = winRoom * 0.06 + uWinCool * 0.015',
  '      + winSky * (0.05 + 0.6 * winFr);',
  // Every gradient above is a shallow ramp over a dark room, which is
  // where 8-bit banding lives. One LSB of hash grain kills the contours
  // and is under the noise floor of every display it will be seen on.
  '  vec3 winGrain = vec3((astraHash21(gl_FragCoord.xy) - 0.5) * 0.004);',
  '  diffuseColor.rgb = mix(diffuseColor.rgb,',
  '      max(mix(winDark, winRoom * 0.35, winOn) + winGrain * 0.25, 0.0),',
  '      winPane);',
  // diffuseColor is ALBEDO: without lifting the emissive term as well, a
  // lit room can only ever be a pale wall.
  '  totalEmissiveRadiance += max(winRoom + winGrain, 0.0)',
  '      * (uWinEmissive * winOn * winPane);',
  // And the SAME argument decides the unlit pane, which is the half of
  // this effect that a night scene actually shows most of. A reflection
  // is radiance, not albedo: written to diffuseColor alone it is
  // multiplied by the light landing on the wall, and at night that is
  // nothing — so every dark pane collapses to the black hole in the
  // facade that `winDark` exists to prevent. A whisper of the room
  // behind it rides along, so an unlit window still parallaxes.
  '  totalEmissiveRadiance += (winSky * (0.06 + 0.9 * winFr)',
  '      + max(winRoom + winGrain, 0.0) * 0.05)',
  '      * ((1.0 - winOn) * winPane);',
].join('\n');

/**
 * A seed becomes a far-apart WHOLE-NUMBER lattice offset.
 *
 * Seeds 7 and 8 offsetting the hash lattice by 1 would merely TRANSLATE
 * the pattern by one window, which reads as the same building twice —
 * hence the multiplier. It is an integer, and stays under a few hundred,
 * because the shader hashes `uWinSeed + <integers>` as a lattice
 * coordinate: a fractional offset would put every cell id between two
 * lattice points, where the hash is a thousand times more sensitive to
 * the last bit of the inputs than it is to the cell.
 */
function seedOffset(seed) {
  const s = Math.abs(Math.round(seed === undefined ? 1 : seed)) % 9973;
  return (s * 16807) % 257;
}

/**
 * Shade the ROOM behind each window instead of the window itself.
 *
 * From the surface UV and the view direction this intersects a shallow
 * box behind every cell of the facade and shades the wall the ray hits,
 * so a lit window slides and parallaxes like an opening rather than
 * sitting flat like a sticker — at ONE draw call for the whole facade,
 * with no added geometry. Unlit cells keep the same interior at a
 * trickle plus a fresnel sky reflection, because a black rectangle reads
 * as a hole punched in the wall.
 *
 * No two rooms are the same room. `warm`/`cool` set the ends of the
 * tungsten-to-daylight axis, and each cell then draws its own small hue
 * rotation, its own saturation and its own BRIGHTNESS off that axis, so
 * a facade carries a spread of bulbs rather than two. About a third of
 * them are dressed: a blind hung over the top of the pane, or a curtain
 * drawn right across, which stops being a box and becomes an even
 * glowing panel. The sky an unlit pane mirrors is the SCENE's — it is
 * read from the fog colour where the scene has fog, split at the
 * reflected horizon so glass takes the ground below and the sky above —
 * and it is added as RADIANCE, not albedo, so a dark pane still reads as
 * glass in a night scene where nothing is lighting the wall.
 *
 * Two constraints it encodes. The grid is measured in UV, and three's
 * BoxGeometry gives EVERY box face a full 0..1 UV whatever its size, so
 * a facade merged from many boxes (`building.js` `block()`) would repeat
 * the whole grid on every mullion; cells that work out under a metre
 * across therefore fade out, and such a member keeps its own surface.
 * Best on a wall whose UV spans it — a shell, a slab, a plane. And the
 * lift is applied to `totalEmissiveRadiance` as well as `diffuseColor`,
 * which needs a lit material — MeshStandard/MeshPhysical, not Basic.
 *
 * @param {THREE.Material} material A built-in material, patched in place.
 * @param {object} [opts] `rows`/`cols` window cells per unit of UV,
 *   rounded so the grid closes at the seam (default 8/6); `depth` how far
 *   the back wall sits behind the glass, in cell widths (default 0.6);
 *   `lit` fraction of windows lit (default 0.35); `warm`/`cool`
 *   THREE.Color or hex room tints, mostly warm with the odd cool room —
 *   `cool` also floors the sky an unlit pane reflects where the scene
 *   carries no fog; `seed` the lit pattern (default 1); `emissive` the
 *   radiance of a lit room, which peaks around 1.3x this (default 1.6,
 *   so ~2.1 — keep it in 1.5..4 to bloom instead of clipping white);
 *   `name` the program cache key (default 'windows:interior' —
 *   every option here is a UNIFORM, so one name is correct and two
 *   differently tuned facades still share one compiled program).
 * @returns {THREE.Material} The same material, its uniforms live on
 *   `material.userData.uniforms` for a scene that dims the city at dawn.
 */
export function patchWindowInteriors(material, opts = {}) {
  const rows = opts.rows === undefined ? 8 : opts.rows;
  const cols = opts.cols === undefined ? 6 : opts.cols;
  const depth = opts.depth === undefined ? 0.6 : opts.depth;
  const lit = opts.lit === undefined ? 0.35 : opts.lit;
  const emissive = opts.emissive === undefined ? 1.6 : opts.emissive;
  return patchStandard(material, {
    name: opts.name || 'windows:interior',
    uniforms: {
      uWinWarm: { value: toColor(opts.warm, 0xffc98a) },
      uWinCool: { value: toColor(opts.cool, 0x9fc0e8) },
      uWinGrid: { value: new THREE.Vector2(
          Math.max(1, Math.round(cols)), Math.max(1, Math.round(rows))) },
      uWinDepth: { value: Math.max(0.02, depth) },
      uWinLit: { value: Math.min(1, Math.max(0, lit)) },
      uWinSeed: { value: seedOffset(opts.seed) },
      uWinEmissive: { value: Math.max(0, emissive) },
    },
    vertexHead: WIN_VARYINGS,
    vertexBody: WIN_VERTEX,
    fragmentHead: [WIN_VARYINGS, WIN_HEAD].join('\n'),
    fragmentBody: WIN_BODY,
  });
}

// What a facade is called across this library: block() names its wall
// mesh 'Walls', and a curtain wall or a shell is the same surface.
const FACADE_RE = /facade|wall|shell|curtain/i;

/** Bounding-box area: the facade is the biggest surface a building has. */
function boxArea(geometry) {
  if (!geometry) return 0;
  if (!geometry.boundingBox) geometry.computeBoundingBox();
  const b = geometry.boundingBox;
  if (!b) return 0;
  const x = b.max.x - b.min.x;
  const y = b.max.y - b.min.y;
  const z = b.max.z - b.min.z;
  return x * y + y * z + z * x;
}

/** The meshes carrying the facade: by name where there is one, else size. */
function facadeMeshes(root) {
  const all = [];
  root.traverse((o) => { if (o.isMesh && o.material) all.push(o); });
  const named = all.filter(
      (o) => FACADE_RE.test(o.name)
          || [].concat(o.material).some((m) => m && FACADE_RE.test(m.name)));
  if (named.length) return named;
  if (all.length < 2) return all;
  return [all.reduce(
      (a, b) => (boxArea(b.geometry) > boxArea(a.geometry) ? b : a))];
}

/**
 * Give a building night windows without knowing how it is built.
 *
 * `block()` and `tower()` return a GROUP of merged meshes — walls, trim,
 * glazing, reveals — and only one of them is the facade, so a caller
 * that reached for `mesh.material` would window the cornice or miss
 * entirely. This finds the facade slot (by name, falling back to the
 * largest surface), patches every slot it found once, and hands the
 * object back. It also CLONES a material `materials.js` marked shared:
 * that library returns one instance to every caller that asked for the
 * same look, so patching in place would put windows on the whole city.
 * On a `block()` that facade is merged mullions and spandrel bands, so
 * the size gate stands the patch down and `block({ lit })` — which
 * models those openings — still owns the look. It earns its keep on the
 * plain shells and slabs a skyline is mostly made of.
 *
 * @param {THREE.Object3D} mesh A building mesh or group (a `block()`, a
 *   `tower()`, a whole `cityFabric()` group, or one InstancedMesh).
 * @param {object} [opts] Passed straight to `patchWindowInteriors`.
 * @returns {THREE.Object3D} The same object, so it can be added inline.
 */
export function makeNightWindows(mesh, opts = {}) {
  if (!mesh || !mesh.traverse) return mesh;
  const done = new Map();
  const owned = new Set();
  for (const target of facadeMeshes(mesh)) {
    const slots = [].concat(target.material);
    const next = slots.map((m) => {
      if (!m) return m;
      if (done.has(m.uuid)) return done.get(m.uuid);
      const out = (m.userData && m.userData.shared) ? clonePatchedMaterial(m) : m;
      if (out !== m) owned.add(out);
      patchWindowInteriors(out, opts);
      done.set(m.uuid, out);
      return out;
    });
    target.material = Array.isArray(target.material) ? next : next[0];
  }
  if (owned.size) {
    const previous = mesh.userData.dispose;
    if (previous) owned.add({ dispose: previous });
    attachDisposal(mesh, owned);
  }
  return mesh;
}

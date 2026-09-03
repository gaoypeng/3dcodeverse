/**
 * Interiors: the light a room bounces, and the marks hands leave on it.
 *
 * Two things separate a room that is LIT from a room that is FILLED with
 * ambient. The first is colour bleed — every surface indoors is also a
 * light, so a red wall throws red on the ceiling beside it and a green
 * lawn throws green up under an eave. Nothing here did that:
 * `neon.js`'s `patchNeonSpill` throws light from an EMITTER, a point
 * that is itself the brightest thing in frame, which is the outdoor
 * night case. A bouncer emits nothing; it returns what already landed on
 * it, it is usually a PLANE (a wall, a floor, a table top) rather than a
 * point, and its colour is its own albedo times how lit it is.
 *
 * The second is that the surfaces people touch are never clean.
 * `patchFingerprints` is grease, not paint: it moves GLOSS, so it is
 * invisible head-on and obvious against a lamp — the other grazing-angle
 * effect, `finish.js`'s `patchIridescence`, shifts HUE with angle where
 * this one shifts only how the surface catches a highlight.
 *
 * `makeCrowdImposters` is the third: distant people on cards, one draw
 * call, so a square or a platform can be populated at all.
 *
 * Neither patch writes `diffuseColor.rgb`. Both ADD to
 * `totalEmissiveRadiance`, blended with the albedo that is already
 * there, for the reason `windows.js` and `caustics.js` state: the only
 * fragment hook runs at `<color_fragment>`, so a patch may tint albedo —
 * capped at the light already landing — or add light. Bounce and a
 * smudge-lit haze are both brighter than the surface's own front
 * lighting, so neither can be albedo.
 */

import * as THREE from 'three';

import { mulberry32 } from './noise.js';
import {
  composeRoughness, instancedQuad, keepOutOfDepthPasses, makeShaderMaterial,
  patchStandard, tickShaders,
} from './shader.js';

// How many bounce sources one material can carry. A room needs a wall,
// a floor and a window's worth; past that the loop costs more than the
// cue is worth.
const BOUNCE_MAX = 6;

// Named as surface_wear, finish, caustics and waterside name them, so a
// material wearing several libraries declares ONE pair; the vertex
// locals are idr* because those libraries already own wrP, fnP and cauWp.
const WORLD_VARYINGS = [
  'varying vec3 vAstraWorld;',
  'varying vec3 vAstraWorldN;',
].join('\n');

// `transformed` is still object-space after <begin_vertex>, so an
// instanced railing takes the origin's position unless this is folded in.
const BASE = {
  name: 'indoor:base',
  vertexHead: WORLD_VARYINGS,
  vertexBody: [
    '  vec4 idrP = vec4(transformed, 1.0);',
    '  vec3 idrN = normal;',
    '#ifdef USE_INSTANCING',
    '  idrP = instanceMatrix * idrP;',
    '  idrN = mat3(instanceMatrix) * idrN;',
    '#endif',
    '  vAstraWorld = (modelMatrix * idrP).xyz;',
    '  vAstraWorldN = normalize((modelMatrix * vec4(idrN, 0.0)).xyz);',
  ].join('\n'),
  fragmentHead: WORLD_VARYINGS,
};

/** Take a THREE.Color, a hex or nothing, never sharing the instance. */
function toColor(value, fallback) {
  return new THREE.Color(
      value === undefined || value === null ? fallback : value);
}

/** Read a Vector3, an array or an {x,y,z} into a Vector3. */
function toVec(p, fx, fy, fz) {
  if (!p) return new THREE.Vector3(fx, fy, fz);
  if (Array.isArray(p)) return new THREE.Vector3(p[0], p[1], p[2]);
  return new THREE.Vector3(p.x, p.y, p.z);
}

/** Clamp to 0..1 without importing MathUtils for three calls. */
function unit(v) {
  return Math.max(0, Math.min(1, v));
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
 * Turn a seed into a noise-space offset, so two panes differ.
 *
 * The offset is a UNIFORM: a seed baked into the GLSL would be fixed for
 * every other material sharing this patch's cache key, because the first
 * one to compile a key decides the source for all of them.
 */
function seedOffset(seed) {
  return new THREE.Vector3(
      hash11(seed + 0.41), hash11(seed + 4.13), hash11(seed + 8.77))
      .multiplyScalar(52);
}

const BOUNCE_HEAD = [
  'uniform vec3 uBncPos[' + BOUNCE_MAX + '];',
  'uniform vec3 uBncNrm[' + BOUNCE_MAX + '];',
  'uniform vec3 uBncCol[' + BOUNCE_MAX + '];',
  // x metres of reach, y 1 for a plane and 0 for a point.
  'uniform vec2 uBncSpan[' + BOUNCE_MAX + '];',
  'uniform float uBncCount;',
  'uniform float uBncGain;',
  // Windowed: 1 at the source and EXACTLY 0 at the reach. A bare
  // inverse square never reaches zero, so every surface keeps a wash of
  // every bouncer — ambient again. A plane's knee is the softer one.
  'float astraBncFall(float d, float r, float plane) {',
  '  float x = clamp(d / max(r, 1e-4), 0.0, 1.0);',
  '  float w = 1.0 - x * x;',
  '  return w * w / (1.0 + mix(12.0, 2.5, plane) * x * x);',
  '}',
].join('\n');

const BOUNCE_BODY = [
  '  vec3 bncSum = vec3(0.0);',
  '  vec3 bncN = normalize(vAstraWorldN);',
  '  for (int i = 0; i < ' + BOUNCE_MAX + '; i++) {',
  '    if (float(i) >= uBncCount) break;',
  '    vec3 bncD = vAstraWorld - uBncPos[i];',
  '    float bncPl = uBncSpan[i].y;',
  '    float bncSide = dot(bncD, uBncNrm[i]);',
  // A bouncer has a front: a lawn does not light the cellar under it,
  // and a wall throws nothing through itself.
  '    float bncFront = step(1e-4, bncSide);',
  '    float bncR = length(bncD);',
  // A plane is measured PERPENDICULAR to itself, so a long wall reddens
  // a BAND of ceiling along its whole length; a point falls off
  // radially, so a rug or a lit patch of lawn stays local.
  '    float bncDist = mix(bncR, bncSide, bncPl);',
  '    vec3 bncL = mix(-bncD / max(bncR, 1e-4), -uBncNrm[i], bncPl);',
  // Half lambert: a bouncer is an AREA seen over most of the receiver's
  // hemisphere, so a hard terminator would cut the ceiling off in a line
  // above a wall that in truth lights all of it.
  '    float bncLam = clamp(dot(bncN, bncL) * 0.5 + 0.5, 0.0, 1.0);',
  // A PLANE takes it UN-squared, because un-squared is the exact form
  // factor at both limits of a Lambertian half-space: a ceiling facing a
  // lit floor sees it over its whole hemisphere (lam 1, factor 1), and a
  // WALL over exactly half of it (lam 0.5, factor 0.5). Squaring lands
  // the wall at 0.25 — half the bleed, on the one geometry every room
  // has. A POINT keeps the square: a small source really is directional.
  '    float bncWrap = mix(bncLam * bncLam, bncLam, bncPl);',
  '    float bncX = clamp(bncDist / max(uBncSpan[i].x, 1e-4), 0.0, 1.0);',
  // Light that has come further has bounced off more than one thing on
  // the way, so the wash loses the bouncer's hue as it spreads. Without
  // this the far edge of a red bleed is as red as the skirting under the
  // wall, which reads as a coloured LAMP rather than as a red wall.
  '    vec3 bncTint = mix(uBncCol[i],',
  '        vec3(dot(uBncCol[i], vec3(0.2126, 0.7152, 0.0722))), 0.32 * bncX);',
  '    bncSum += bncTint * (bncFront * bncWrap',
  '        * astraBncFall(bncDist, uBncSpan[i].x, bncPl));',
  '  }',
  // Bounce is the smoothest gradient in a room and plaster is the widest
  // flat it lands on: without a dither the wash climbs the 8-bit ladder
  // in visible bands. Half a code, hashed per pixel, only where there is
  // bleed to band.
  '  float bncLvl = max(max(bncSum.r, bncSum.g), bncSum.b);',
  '  bncSum += (astraHash11(gl_FragCoord.x * 3.13 + gl_FragCoord.y * 71.7)',
  '      - 0.5) * 0.005 * step(1e-4, bncLvl);',
  // Arriving light is REFLECTED, so it takes the receiver's own colour
  // — but never all of it, or a dark ceiling swallows the room.
  '  totalEmissiveRadiance += bncSum * uBncGain',
  '      * mix(vec3(0.35), diffuseColor.rgb, 0.65);',
].join('\n');

/**
 * Read one source into the four parallel uniform slots.
 *
 * A source that states a `normal` is a PLANE (a wall, a floor, a table
 * top); one that does not is a POINT, and `up` is the side it throws
 * toward.
 */
function readSource(s, up, slot) {
  const raw = (s && (s.position || s.point)) || s;
  slot.pos.push(toVec(raw, 0, 0, 0));
  const plane = !!(s && s.normal);
  const n = plane ? toVec(s.normal, 0, 1, 0) : up.clone();
  slot.nrm.push(n.lengthSq() < 1e-9 ? up.clone() : n.normalize());
  slot.col.push(toColor(s && s.color, 0x808080));
  const reach = s && s.reach !== undefined ? s.reach : 3;
  slot.span.push(new THREE.Vector2(Math.max(1e-3, reach), plane ? 1 : 0));
}

/**
 * Colour bleed: light that has already hit something else on its way in.
 *
 * This is the cue that says a room is LIT rather than filled with a flat
 * ambient term, and it is the one an author cannot fake with a lamp: a
 * white ceiling beside a red wall goes pink, the underside of an eave
 * over a lawn goes green, a wooden floor warms every face turned down
 * toward it. Each source is a surface that already caught the light and
 * is handing it on, so its `color` is its own ALBEDO times how lit it
 * is, not a lamp colour.
 *
 * A source with a `normal` is a PLANE and is measured PERPENDICULAR to
 * itself, which is what makes a long wall bleed onto a band of ceiling
 * along its whole length instead of near one point. A source without one
 * is a POINT and falls off radially, which is right for a rug, a lit
 * patch of lawn or a poster. Both are exactly ZERO past `reach` and
 * neither lights anything behind its own face, so a lawn cannot light
 * the cellar under it.
 *
 * A plane's strength is its FORM FACTOR, which is the half-lambert
 * un-squared: a ceiling facing a lit floor sees it over its whole
 * hemisphere (1.0) and a wall standing on one sees it over exactly half
 * (0.5). A point keeps the squared, tighter response.
 *
 * Measured on THIS renderer (exposure 1.0, no post chain) in a loggia
 * with white plaster walls, a sunlit terracotta floor as a plane source
 * (reach 3.6 m) and a red rug and teal table as points (3.2 / 2.8 m),
 * against a control render with `strength: 0` — mean absolute difference
 * over each region, of 255: the wall's skirting 5.7, the wall above the
 * rug 7.6, a free-standing pillar 6.2, the soffit underside 2.1, and the
 * same wall at 2.5-3.1 m up — past what a 3.6 m floor reach carries —
 * 0.7, which is the effect ending where it says it ends. In hue: the
 * skirting's R-B goes -14.5 to -7.7, the wall over the rug -2.1 to +6.9,
 * the pillar -4.5 to +3.0.
 *
 * WHICH PATCH: `patchNeonSpill` is the outdoor cousin and the two are
 * not interchangeable. Its sources EMIT — a tube that is itself the
 * brightest thing in the frame — so it is always a point, always an
 * inverse square, and its gain is cut back as the surroundings brighten
 * because emissive added to a sunlit wall clips. This one's sources
 * REFLECT, at a fraction of what fell on them, so it holds up in
 * daylight and its geometry is a plane as often as a point. A neon sign
 * on an interior wall wants both; they chain.
 *
 * It ADDS to `totalEmissiveRadiance` through the receiver's own albedo
 * and never touches `diffuseColor`: bounce is LIGHT, and an albedo lift
 * is capped at the light already landing, so the shaded soffit that
 * needs this most would take none of it. That needs a LIT material —
 * MeshStandard / MeshPhysical, not Basic, which has no emissive term.
 *
 * The constraint it cannot solve, the same one `patchNeonSpill` states:
 * nothing is OCCLUDED. A plane is infinite in its own plane, so it
 * bleeds through a partition as readily as across the room. Keep `reach`
 * to what the bounce really carries — a metre or three indoors — and put
 * the source on the face that is lit.
 *
 * @param {THREE.Material} material A lit built-in material, patched in
 *   place. A shared `materials.js` instance patches every mesh wearing
 *   it, so clone it first (and clone BEFORE patching).
 * @param {object} [opts] `sources` up to 6 bouncers, each a Vector3, an
 *   `[x,y,z]`, or `{ position, normal, color, reach }` — stating
 *   `normal` makes it a plane through `position` throwing that way,
 *   `color` is the bouncer's albedo times how lit it is (default a mid
 *   grey), `reach` is metres, zero beyond (default 3); `up` the side a
 *   source with no normal of its own throws toward (default +Y — a
 *   floor, a lawn and a rug all bounce upward); `strength` how hard the
 *   bleed reads (default 1); `name` the program cache key (default
 *   'indoor:bounce' — every option here is a UNIFORM, so one name is
 *   correct and two differently lit rooms still share one program).
 * @returns {THREE.Material} The same material, its uniforms live on
 *   `material.userData.uniforms` so a scene can dim the bleed at dusk
 *   or move a source with the surface that casts it.
 */
export function patchBounceLight(material, opts = {}) {
  const up = toVec(opts.up, 0, 1, 0);
  if (up.lengthSq() < 1e-9) up.set(0, 1, 0);
  up.normalize();
  const list = (Array.isArray(opts.sources) ? opts.sources
      : (opts.sources ? [opts.sources] : [])).slice(0, BOUNCE_MAX);
  const slot = { pos: [], nrm: [], col: [], span: [] };
  for (let i = 0; i < BOUNCE_MAX; i++) {
    if (i < list.length) readSource(list[i], up, slot);
    else {
      slot.pos.push(new THREE.Vector3());
      slot.nrm.push(up.clone());
      slot.col.push(new THREE.Color(0, 0, 0));
      slot.span.push(new THREE.Vector2(1, 0));
    }
  }
  if (material && material.emissive === undefined) {
    console.warn(
        'patchBounceLight: ' + (material.name || material.type) + ' has '
        + 'no emissive term — bounce is LIGHT and lands on '
        + 'totalEmissiveRadiance, which a MeshBasicMaterial and a raw '
        + 'ShaderMaterial do not have. Patch a lit material instead.');
  }
  patchStandard(material, BASE);
  return patchStandard(material, {
    name: opts.name || 'indoor:bounce',
    uniforms: {
      uBncPos: { value: slot.pos },
      uBncNrm: { value: slot.nrm },
      uBncCol: { value: slot.col },
      uBncSpan: { value: slot.span },
      uBncCount: { value: list.length },
      uBncGain: { value: Math.max(0, opts.strength === undefined
          ? 1 : opts.strength) },
    },
    vertexHead: WORLD_VARYINGS,
    fragmentHead: [WORLD_VARYINGS, BOUNCE_HEAD].join('\n'),
    fragmentBody: BOUNCE_BODY,
  });
}

const FPR_HEAD = [
  'uniform float uFprAmt;',
  'uniform float uFprSmear;',
  'uniform vec3 uFprSeed;',
  // Metres across one unit of the field. A greasy arc comes out about
  // 2 cm across and a scratch about 2 mm, which is what each is.
  'const float ASTRA_FPR_SPAN = 0.42;',
  // Grease sits in ARCS because a hand sweeps, and the contours of a
  // warped field are curved bands where a straight streak reads as a
  // wiper blade.
  'float astraFprField(vec2 p) {',
  '  return astraFbm2(p * 0.8, 3) * 6.0',
  '       + astraFbm2(p * 2.6 + 5.3, 2) * 2.0;',
  '}',
  // One soft band per period of v, squared so it has no edge of its
  // own: grease has no boundary and a top-hat reads as paint. It drops
  // once a pixel spans a band, so the pane cleans itself at distance.
  'float astraFprArc(float v, float w) {',
  '  float g = abs(fract(v) - 0.5);',
  '  float aa = clamp(fwidth(v), 0.0008, 0.5);',
  '  float m = 1.0 - smoothstep(0.0, max(w, aa), g);',
  '  return m * m * (1.0 - smoothstep(w * 0.8, w * 3.0, aa));',
  '}',
  // The two world axes least aligned with the normal, because a pane, a
  // screen and a rail all carry grease and none carries a usable UV. It
  // steps at 45 degrees: for a flat surface, not for a sphere.
  'vec2 astraFprPlane(vec3 p, vec3 n) {',
  '  vec3 a = abs(n);',
  '  if (a.y > max(a.x, a.z)) return p.xz;',
  '  return a.x > a.z ? p.zy : p.xy;',
  '}',
  // A smudge does not reflect an image, it SCATTERS one: a tight core
  // where the clean surface puts its highlight, inside the halo that is
  // why a fingerprint shows against a lamp and nowhere else.
  'float astraFprLobe(vec3 n, vec3 v, vec3 l) {',
  '  float h = max(dot(n, normalize(l + v)), 0.0);',
  '  return pow(h, 30.0) * 0.55 + pow(h, 8.0) * 0.45;',
  '}',
].join('\n');

const FPR_BODY = [
  '  vec3 fprV = normalize(cameraPosition - vAstraWorld);',
  '  vec3 fprN = normalize(vAstraWorldN);',
  '  fprN *= sign(dot(fprN, fprV) + 1e-6);',
  '  vec2 fprP = astraFprPlane(vAstraWorld, fprN) / ASTRA_FPR_SPAN',
  '      + uFprSeed.xy;',
  // A drag stretches the grease along the way the hand went; without it
  // `smear` could only make the arcs fatter, which reads as fog.
  '  vec2 fprQ = vec2(fprP.x / (1.0 + 3.0 * uFprSmear), fprP.y);',
  // Hands land in patches, so a slow blotch decides WHERE there is
  // grease at all and `amount` is how much of the surface that covers.
  '  float fprWhere = smoothstep(0.72 - 0.5 * uFprAmt,',
  '      0.94 - 0.5 * uFprAmt, astraFbm2(fprQ * 0.55 + uFprSeed.yz, 3));',
  // A hand leaves a broken arc, never a ribbon, so the bands are cut by
  // a second field at their own scale.
  '  float fprArcs = astraFprArc(astraFprField(fprQ) * 2.2,',
  '      0.10 + 0.10 * uFprSmear) * fprWhere',
  '      * smoothstep(0.30, 0.72, astraNoise2(fprQ * 1.3 + uFprSeed.xy));',
  // Finer, straighter, broken: the scratch field a polished rail or a
  // shop window carries, which only ever shows at grazing.
  '  float fprScr = astraFprArc(dot(fprP, vec2(0.86, 0.51)) * 9.0',
  '      + astraFbm2(fprP * 0.5 + 3.1, 2) * 4.0, 0.060)',
  '      * step(0.52, astraNoise2(fprP * 3.1 + uFprSeed.zx));',
  // A dielectric returns a few percent head-on and everything at
  // grazing, so the same weight that makes a clean pane a window at
  // your feet and a mirror at the horizon hides the grease head-on.
  '  float fprGrz = astraFresnel(fprN, fprV, 4.0);',
  '  float fprW = mix(0.06, 1.0, fprGrz);',
  // The FILM between the arcs. Grease is a continuous layer with ridges
  // in it, not a set of isolated bands: with the arcs alone a touched
  // pane is a few bright stripes on clean glass, and the eye reads
  // stripes as paint. The film is where the blotch says a hand has been,
  // at a third of the arcs' weight and with its own slow variation, so
  // the pane clouds where it is handled and stays clean where it is not.
  '  float fprFilm = fprWhere * (0.34 + 0.46',
  '      * astraNoise2(fprQ * 0.85 + uFprSeed.zx));',
  '  float fprMask = clamp(fprArcs * 0.85 + fprScr * fprGrz',
  '      + fprFilm * 0.52, 0.0, 1.0);',
  '  vec3 fprHaze = vec3(0.0);',
  '#if NUM_DIR_LIGHTS > 0',
  '  for (int i = 0; i < NUM_DIR_LIGHTS; i++) {',
  // three keeps light vectors in VIEW space; v * mat3(viewMatrix) is
  // that matrix's transpose applied, which is its inverse rotation.
  '    vec3 fprL = directionalLights[i].direction * mat3(viewMatrix);',
  '    fprHaze += directionalLights[i].color',
  '        * astraFprLobe(fprN, fprV, fprL);',
  '  }',
  '#endif',
  '#if NUM_POINT_LIGHTS > 0',
  '  for (int i = 0; i < NUM_POINT_LIGHTS; i++) {',
  '    vec3 fprPp = pointLights[i].position * mat3(viewMatrix)',
  '        + cameraPosition;',
  '    vec3 fprPd = fprPp - vAstraWorld;',
  '    float fprPr = length(fprPd);',
  '    fprHaze += pointLights[i].color * getDistanceAttenuation(',
  '        fprPr, pointLights[i].distance, pointLights[i].decay)',
  '        * astraFprLobe(fprN, fprV, fprPd / max(fprPr, 1e-4));',
  '  }',
  '#endif',
  '#if NUM_HEMI_LIGHTS > 0',
  // The sky is an AREA source, so there is no lobe to aim — only the
  // direction the pane reflects. Without it a vertical shopfront under
  // a high sun shows nothing: its mirror direction points at the ground.
  '  vec3 fprR = reflect(-fprV, fprN);',
  '  for (int i = 0; i < NUM_HEMI_LIGHTS; i++) {',
  '    vec3 fprHd = hemisphereLights[i].direction * mat3(viewMatrix);',
  '    fprHaze += mix(hemisphereLights[i].groundColor,',
  '        hemisphereLights[i].skyColor,',
  '        dot(fprR, fprHd) * 0.5 + 0.5) * 0.35;',
  '  }',
  '#endif',
  // The haze takes the LIGHT's colour, never a colour of its own:
  // grease is only a change in how the surface catches a highlight.
  '  totalEmissiveRadiance += fprHaze',
  '      * (fprMask * fprW * uFprAmt * 0.55);',
].join('\n');

/**
 * The smudges on glass, a screen, a polished rail: gloss, not paint.
 *
 * A pane nobody has touched is the tell that a room was assembled rather
 * than lived in, and the cue is not a stain — it is that the grease
 * SCATTERS a highlight the clean surface reflects sharply. So the pane
 * is clean until it is REFLECTING something: greasy arcs bloom out of a
 * lamp's own reflection, and a fine scratch field comes up as the
 * surface turns toward grazing. Three layers carry it: broken ARCS where
 * a hand has swept, a fine SCRATCH field that only shows at grazing, and
 * the FILM between them — grease is a continuous layer with ridges in it,
 * and with the arcs alone a touched pane is a few bright bands on clean
 * glass, which the eye reads as paint rather than as use.
 *
 * Measured on THIS renderer (exposure 1.0, no post chain) on a dark
 * polished counter top seen ~12 degrees off grazing with the sun in its
 * mirror, at `amount` 0.62, against the same material unpatched: mean
 * absolute difference 3.0/255 with 55% of the surface moved by more than
 * 1/255 and peaks near 12 — the halo of arcs inside the cloud of film. A
 * tilted vitrine lid three metres away in the same frame, whose blotch
 * field says few hands have been on it, comes back at 1.3/255 over 17%:
 * that is the effect saying the pane is clean, and at `amount` 0.95 the
 * same lid carries the full set of arcs. The scratches are millimetres
 * wide and are a close-range detail; they drop out on their own once a
 * pixel spans one, which is why the pane does not crawl at distance.
 *
 * It never touches `diffuseColor` — a smudge that changes the COLOUR of
 * glass is a stain, and the surface would read as dirty rather than
 * used. Two things carry it instead. Per pixel it ADDS the scattered
 * light to `totalEmissiveRadiance`, summed over the scene's own
 * `directionalLights` and `pointLights` as a lobe, plus the
 * `hemisphereLights` the pane happens to be reflecting — the sky is an
 * AREA and has no lobe to aim, and without it a vertical shopfront
 * under a high sun shows nothing, its mirror direction pointing at the
 * ground. A scene lit ONLY by an AmbientLight shows nothing either:
 * that light has no direction, so there is nothing to reflect. And per
 * MATERIAL it composes a roughness factor through
 * `composeRoughness`: `<color_fragment>` runs before
 * `<roughnessmap_fragment>`, so per-pixel gloss is unreachable and the
 * reachable half is that a smudged surface is a little less glossy
 * overall (see `surface_wear.js`, which states the same limit).
 *
 * WHICH PATCH: `finish.js`'s `patchIridescence` is the other
 * grazing-angle effect and answers a different question — it shifts HUE
 * with the angle, because a thin film's optical path shortens toward
 * grazing. This shifts no hue at all; it only changes how the surface
 * catches a light. An oiled rail can carry both.
 *
 * The field is projected on the two world axes least aligned with the
 * normal, so no UV is needed — and it steps where that choice flips, at
 * 45 degrees. That makes it right for a pane, a screen, a table top or a
 * flat rail, and wrong for a sphere.
 *
 * @param {THREE.Material} material A lit built-in material (it needs
 *   `totalEmissiveRadiance`), patched in place — a shared material
 *   smudges every mesh wearing it.
 * @param {object} [opts] `amount` how much of the surface hands have
 *   reached and how hard the grease catches the light, 0..1 (default
 *   0.5 — a door that gets used; 0.15 is a wiped shopfront, 0.9 a bus
 *   shelter); `smear` 0 crisp arcs to 1 dragged and stretched, as a
 *   cloth or a sleeve leaves it (default 0.35); `seed` moves the field,
 *   so two panes are not one pane twice (default 1); `name` the program
 *   cache key (default 'indoor:fingerprints' — every option is a
 *   UNIFORM, so one name is correct for every smudged surface).
 * @returns {THREE.Material} The same material, its uniforms live on
 *   `material.userData.uniforms`.
 */
export function patchFingerprints(material, opts = {}) {
  const amount = unit(opts.amount === undefined ? 0.5 : opts.amount);
  const smear = unit(opts.smear === undefined ? 0.35 : opts.smear);
  const seed = opts.seed === undefined ? 1 : opts.seed;
  if (material && material.emissive === undefined) {
    console.warn(
        'patchFingerprints: ' + (material.name || material.type) + ' has '
        + 'no emissive term — a smudge is light SCATTERED off grease and '
        + 'lands on totalEmissiveRadiance. Patch a lit material instead.');
  }
  // Grease scatters, so the surface loses gloss where it is not
  // reflecting a light — the half of the effect that is per material.
  composeRoughness(material, 'indoor:fingerprints',
                   1 + 0.5 * amount * (0.6 + 0.4 * smear));
  patchStandard(material, BASE);
  return patchStandard(material, {
    name: opts.name || 'indoor:fingerprints',
    uniforms: {
      uFprAmt: { value: amount },
      uFprSmear: { value: smear },
      uFprSeed: { value: seedOffset(seed) },
    },
    vertexHead: WORLD_VARYINGS,
    fragmentHead: [WORLD_VARYINGS, FPR_HEAD].join('\n'),
    fragmentBody: FPR_BODY,
  });
}

/**
 * Seat `count` people on a jittered grid, knotted toward gathering
 * points.
 *
 * The grid is shuffled before it is cut to `count`, or a crowd that does
 * not fill its last row loses a rectangular corner of itself. People
 * then stand in knots rather than on a lattice, which is most of what
 * separates a crowd from a car park.
 */
function gather(count, extent, height, seed, ground) {
  const cells = Math.max(1, Math.ceil(Math.sqrt(count)));
  const step = extent / cells;
  const rand = mulberry32(seed);
  const order = [];
  for (let i = 0; i < cells * cells; i++) order.push(i);
  for (let i = order.length - 1; i > 0; i--) {
    const j = Math.floor(rand() * (i + 1));
    const t = order[i]; order[i] = order[j]; order[j] = t;
  }
  const knots = [];
  for (let i = 0; i < 5; i++) {
    knots.push([(rand() * 2 - 1) * extent * 0.36,
                (rand() * 2 - 1) * extent * 0.36]);
  }
  const pos = new Float32Array(count * 3);
  const card = new Float32Array(count * 4);
  let low = Infinity, high = -Infinity, tall = 0;
  for (let i = 0; i < count; i++) {
    const c = order[i % order.length];
    let x = -extent / 2 + ((c % cells) + rand()) * step;
    let z = -extent / 2 + (Math.floor(c / cells) + rand()) * step;
    const k = knots[Math.floor(rand() * knots.length)];
    x += (k[0] - x) * 0.35;
    z += (k[1] - z) * 0.35;
    const y = ground ? ground(x, z) : 0;
    pos[i * 3] = x; pos[i * 3 + 1] = y; pos[i * 3 + 2] = z;
    // A crowd is adults, children and the odd tall one — one height is
    // a rank of soldiers seen from behind.
    const a = rand(), b = rand();
    const s = a < 0.14 ? 0.60 + 0.24 * b : 0.90 + 0.17 * b;
    card[i * 4] = rand();
    card[i * 4 + 1] = s;
    card[i * 4 + 2] = (rand() * 2 - 1) * 0.55;
    card[i * 4 + 3] = rand();
    low = Math.min(low, y); high = Math.max(high, y);
    tall = Math.max(tall, s);
  }
  return { pos, card, low, top: high + height * tall, tall };
}

/** Person, silhouette and light — the whole crowd in one program. */
function crowdMaterial(cfg) {
  return makeShaderMaterial({
    name: 'CrowdImposters',
    uniforms: {
      uCrwSize: { value: new THREE.Vector2(cfg.width, cfg.height) },
      uCrwSway: { value: cfg.sway },
      uCrwWarm: { value: cfg.warm },
      uCrwCool: { value: cfg.cool },
      uCrwLeg: { value: cfg.leg },
      uCrwSkin: { value: cfg.skin },
      uCrwSun: { value: cfg.sun },
      uCrwSunColor: { value: cfg.sunColor },
      uCrwAmbient: { value: cfg.ambient },
    },
    varyings: 'varying vec2 vCrwUv; varying vec3 vCrwVar;'
        + ' varying vec3 vCrwN; varying vec2 vCrwRim;',
    vertexHead: [
      'attribute vec3 aCorner;',
      'attribute vec3 aPos;',
      'attribute vec4 aCrowd;',
      'uniform vec2 uCrwSize;',
      'uniform float uCrwSway;',
      'uniform vec3 uCrwSun;',
    ].join('\n'),
    vertexMain: [
      '  vCrwUv = uv;',
      // Camera axes in LOCAL space (rows of modelViewMatrix), so a moved
      // or rotated parent cannot tear the crowd off the camera.
      '  vec3 camR = vec3(modelViewMatrix[0][0], modelViewMatrix[1][0],',
      '                   modelViewMatrix[2][0]);',
      '  vec3 up = vec3(0.0, 1.0, 0.0);',
      // A person yaws to face the camera and never tips: a card that
      // also pitches lifts the whole crowd off the ground together.
      '  vec3 right = normalize(camR - up * dot(camR, up)',
      '                         + vec3(1e-5, 0.0, 0.0));',
      '  float hf = aCorner.y + 0.5;',
      '  vec3 p = aPos + right * (aCorner.x * uCrwSize.x * aCrowd.y)',
      '         + up * (hf * uCrwSize.y * aCrowd.y);',
      // Nobody stands still: a slow weight shift, decorrelated by more
      // than a full turn so the crowd does not sway as one body.
      '  float sw = uTime * 0.8 + astraStagger(aPos.x + aPos.z * 0.31);',
      '  p += right * (uCrwSway * hf * hf * sin(sw));',
      '  transformed = p;',
      // A body is round and the card is not: the fake normal bows away
      // from the centre line, which is what puts a lit and a shaded side
      // on a figure instead of one flat tone.
      '  vec3 fwd = cross(right, up);',
      '  vCrwN = right * (aCorner.x * 1.35) + up * ((hf - 0.55) * 0.20',
      '        + 0.10) + fwd * 0.90;',
      '  vCrwVar = vec3(aCrowd.x, aCrowd.z, aCrowd.w);',
      // Which way the light lies ACROSS the card, and how far behind it:
      // a backlit figure is a silhouette with a lit edge, and the edge is
      // the whole reason a distant crowd separates from the ground it
      // stands on. The card has no depth for a normal to find this in.
      '  vCrwRim = vec2(dot(right, uCrwSun), clamp(-dot(fwd, uCrwSun),',
      '                 0.0, 1.0));',
    ].join('\n'),
    fragmentHead: [
      'uniform vec3 uCrwWarm;',
      'uniform vec3 uCrwCool;',
      'uniform vec3 uCrwLeg;',
      'uniform vec3 uCrwSkin;',
      'uniform vec3 uCrwSun;',
      'uniform vec3 uCrwSunColor;',
      'uniform vec3 uCrwAmbient;',
    ].join('\n'),
    fragmentMain: [
      // iq.x spans HALF the card and iq.y the whole height, so a round
      // head is an ellipse here.
      '  vec2 iq = vec2(vCrwUv.x * 2.0 - 1.0, vCrwUv.y);',
      '  float cPh = vCrwVar.z * 37.0;',
      // Weight on one leg: a rank of perfectly upright figures is a
      // fence, and the lean costs one add.
      '  iq.x += (fract(cPh * 0.61) - 0.5) * 0.14 * iq.y;',
      // Ankles, hips, shoulders, neck: four widths off the standard
      // 7.5-head figure, because a person is not a rectangle and the
      // eye knows every one of these numbers by heart.
      '  float cW = 0.30 + 0.12 * smoothstep(0.05, 0.50, iq.y)',
      '           + 0.16 * smoothstep(0.50, 0.72, iq.y)',
      '           - 0.45 * smoothstep(0.80, 0.87, iq.y);',
      '  float cBody = min(min(cW - abs(iq.x), 0.90 - iq.y), iq.y);',
      // The gap between the legs, which is where the eye decides person
      // or bollard; its width is the figure's stance.
      '  float cGap = 0.06 + 0.06 * fract(cPh * 0.37);',
      '  cBody = min(cBody, max(abs(iq.x) - cGap, iq.y - 0.50));',
      '  float cHead = (0.175 + 0.02 * fract(cPh * 0.13))',
      '      - length(vec2(iq.x, (iq.y - 0.935) * 2.6));',
      '  float iS = max(cHead, cBody);',
      // A card is antialiased by its own gradient or a distant crowd
      // stipples in and out of the frame as the camera turns.
      '  float iAA = fwidth(iS) + 1e-4;',
      '  float iA = clamp(iS / iAA + 0.5, 0.0, 1.0);',
      // Dropped whole, never faded: a card that still writes depth at
      // 5% alpha punches a sky-coloured hole through the one behind it.
      '  if (iA < 0.02) discard;',
      // Clothing is the whole variety a crowd has at this size: a ramp
      // between two tones, then a hue swing per person on top.
      '  vec3 iAlb = astraHueShift(mix(uCrwWarm, uCrwCool, vCrwVar.x),',
      '                            vCrwVar.y);',
      // Hue alone is not a crowd: a street is pale coats and dark ones,
      // and value is what the eye counts heads by at 40 m.
      '  iAlb *= 0.72 + 0.62 * fract(cPh * 0.29);',
      '  iAlb = mix(uCrwLeg, iAlb, smoothstep(0.50, 0.55, iq.y));',
      '  iAlb = mix(iAlb, uCrwSkin, smoothstep(0.86, 0.89, iq.y));',
      // Hair over the crown, and shoes: two dark caps that stop a figure
      // reading as a mannequin. Not BLACK caps — an albedo under about
      // 0.02 is a hole in the frame that no light can open again.
      '  iAlb = mix(iAlb, uCrwLeg * 0.72,',
      '             smoothstep(0.945, 0.975, iq.y));',
      '  iAlb = mix(uCrwLeg * 0.68, iAlb, smoothstep(0.0, 0.035, iq.y));',
      // WRAPPED, not clamped: a body is round and this normal is a coarse
      // bow across a flat card, so a hard terminator prints the whole
      // backlit half of the crowd as one flat ambient tone.
      '  float iNdL = clamp((dot(normalize(vCrwN), uCrwSun) + 0.30)',
      '      / 1.30, 0.0, 1.0);',
      // Irradiance over PI, the arithmetic a built-in Lambert does, so a
      // card sits at the brightness its geometry would. Ankles see less
      // sky than heads, which is what seats a figure ON the ground.
      '  vec3 iIrr = (uCrwAmbient + uCrwSunColor * iNdL)',
      '            * mix(0.55, 1.0, smoothstep(0.0, 0.55, iq.y));',
      // The rim: the sun-side edge of a BACKLIT figure, in the light's own
      // colour. `cW` is the body half-width, so the ramp rides the real
      // silhouette rather than the card's rectangle, and the head — where
      // cW is narrow — takes the whole of it, which is where a rim reads.
      '  float iRim = smoothstep(0.66, 1.04, abs(iq.x) / max(cW, 0.02))',
      // The 0.25 floor is the sun straight BEHIND a figure, where both
      // edges light and neither side wins.
      '      * clamp(vCrwRim.x * sign(iq.x) * 2.0 + 0.25, 0.0, 1.0)',
      '      * vCrwRim.y;',
      '  gl_FragColor = vec4(iAlb * iIrr / PI',
      '      + uCrwSunColor * (iRim * 0.075), iA);',
    ].join('\n'),
    transparent: true,
    depthWrite: true,
    side: THREE.DoubleSide,
  });
}

/**
 * A crowd on cards: distant people for one draw call.
 *
 * DO NOT USE THESE INSIDE 20 m. Measured against the same person built
 * as real geometry (`figure.js`; one figure, 512 px, fov 50, daylight,
 * mean absolute pixel difference over the frame): 6 m 0.87/255 and
 * plainly a cut-out — no arms, no thickness, one flat gradient where a
 * body has a lit and a shaded side; 12 m 0.21/255, still readable as
 * flat if you look for it; 20 m 0.07/255, where a still frame stops
 * separating them; 40 m 0.02/255, indistinguishable. Across that range
 * the card's silhouette runs about a fifth smaller than the geometry's
 * (it has no arms) and its mean brightness sits within 8/255 of it,
 * which is what keeps the join invisible where the two meet. A person
 * is small and the eye is a specialist at people, so the crossover sits
 * far nearer than the ~35 m `woodland.js` measures for a tree — but the
 * rule is the same: real figures in the near field, cards for everyone
 * behind them, and the same clothing tones on both.
 *
 * Every card is seated on `heightAt` with its FEET at the ground, faces
 * the camera in yaw only (never pitch, or the crowd lifts off the
 * ground together), and carries its own height, stance, lean, clothing
 * hue AND clothing VALUE — a field of identical figures is wallpaper at
 * any distance, and hue alone is not a crowd: a street is pale coats and
 * dark ones. One in seven is a child.
 *
 * A card has no depth, so the two things that stop a distant crowd
 * printing as flat stumps are built here. Its diffuse is WRAPPED, because
 * a hard terminator on a coarse bowed normal has no gradient to show; and
 * a BACKLIT figure gets a RIM in the light's own colour along the
 * sun-side edge of its silhouette, which is the cue the eye separates a
 * person from the ground by at 40 m. Measured on this renderer, 90 cards
 * at ~35 m with the sun behind them: the darkest crowd pixels went from
 * 15.9 to 24.7 of 255 and the crowd's distinct colours from 4337 to 4726.
 * The rim is gated on the sun being behind — a front-lit crowd grows no
 * halo — so it costs nothing in the frames that do not want it.
 *
 * The whole crowd is ONE `InstancedBufferGeometry`: `position` stays at
 * zero (the GTAO reason `instancedQuad` documents) and the cards are
 * built in the vertex shader, so the stated bounding SPHERE — computed
 * here from the real placements, about the group origin — is the only
 * thing three can cull by; the BOX is left stating the real ground and
 * the real heads, because that is what measures the asset.
 * The mesh is guarded by `keepOutOfDepthPasses`, because an override
 * material draws a transparent card as a solid wall; that also means
 * these cards cast no shadow, and their contact with the ground is the
 * darkened foot in the silhouette itself.
 *
 * @param {object} [opts] `count` people (default 60 — one draw call at
 *   any count); `extent` metres of the square the crowd fills, centred
 *   on the group's origin (default 40); `height` metres of the average
 *   adult (default 1.72 — every figure is scaled off it); `heightAt`
 *   (x, z) => y, the ground the crowd stands on (default y = 0);
 *   `seed` PRNG seed (default 11); `warm`/`cool` the two ends of the
 *   clothing ramp, `legColor` trousers, hair and shoes (default a dark
 *   navy, not a black: hair and shoes are taken to about 0.7 of it, and
 *   an albedo under ~0.02 linear is a hole in the frame that no light
 *   reopens), `skin` faces
 *   and hands; `sunDir`/`sunColor`/`ambient` the scene's own light, so
 *   the cards land at the brightness real geometry does — pass the
 *   values you lit the scene with; `sway` metres of idle weight shift
 *   (default 0.03; 0 stands the crowd still); `name` group name.
 * @returns {THREE.Group} Named `Crowd`, resting on the ground, holding
 *   ONE mesh, with `userData.tick(t)` driving the idle. Add it at the
 *   scene ROOT: the cards billboard against WORLD axes.
 */
export function makeCrowdImposters(opts = {}) {
  const count = Math.max(1, Math.round(
      opts.count === undefined ? 60 : opts.count));
  const extent = Math.max(1, opts.extent === undefined ? 40 : opts.extent);
  const height = Math.max(0.05,
                          opts.height === undefined ? 1.72 : opts.height);
  const ground = typeof opts.heightAt === 'function' ? opts.heightAt : null;
  const seed = opts.seed === undefined ? 11 : opts.seed;
  const width = height * 0.52;

  const field = gather(count, extent, height, seed, ground);
  const half = 0.5 * width * field.tall;
  const box = new THREE.Box3(
      new THREE.Vector3(-extent / 2 - half, field.low, -extent / 2 - half),
      new THREE.Vector3(extent / 2 + half, field.top, extent / 2 + half));
  // The REAL radius, from the group ORIGIN because that is where
  // `instancedQuad` centres the sphere. `position` is all zeros, so it
  // is all three can cull by, and the 10 km default frames empty air.
  const far = new THREE.Vector3(
      Math.max(Math.abs(box.min.x), Math.abs(box.max.x)),
      Math.max(Math.abs(box.min.y), Math.abs(box.max.y)),
      Math.max(Math.abs(box.min.z), Math.abs(box.max.z)));
  const geom = instancedQuad(count, 1, 1, far.length());
  geom.setAttribute('aPos',
                    new THREE.InstancedBufferAttribute(field.pos, 3));
  geom.setAttribute('aCrowd',
                    new THREE.InstancedBufferAttribute(field.card, 4));
  // The BOX is what measures the asset, so it states the real ground
  // and the real heads; the sphere is only what three culls by.
  geom.boundingBox = box;

  const mesh = new THREE.Mesh(geom, crowdMaterial({
    width, height,
    sway: Math.max(0, opts.sway === undefined ? 0.03 : opts.sway),
    warm: toColor(opts.warm, 0xb8a893),
    cool: toColor(opts.cool, 0x4b5566),
    leg: toColor(opts.legColor, 0x39404a),
    skin: toColor(opts.skin, 0xb08668),
    sun: toVec(opts.sunDir, 0.45, 0.78, 0.35).normalize(),
    // Irradiance, not a screen colour: the defaults are the daylight rig
    // the asset renderer lights an outdoor scene with.
    sunColor: opts.sunColor === undefined
        ? new THREE.Color(0xfff0d6).multiplyScalar(3.0)
        : toColor(opts.sunColor, 0xffffff),
    ambient: opts.ambient === undefined
        ? new THREE.Color(0x9fb2c4).multiplyScalar(0.85)
        : toColor(opts.ambient, 0xffffff),
  }));
  mesh.name = 'CrowdCards';
  const g = new THREE.Group();
  g.name = opts.name || 'Crowd';
  g.add(keepOutOfDepthPasses(mesh));
  g.userData.tick = (t) => tickShaders(g, t);
  return g;
}

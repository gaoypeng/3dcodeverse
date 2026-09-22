/**
 * Neon: the light a sign MAKES, not the sign itself.
 *
 * A night city assembled rather than lit fails in one specific way —
 * bright signs on unlit walls. These three break that: a bent tube with
 * a core hot enough to clip white and a halo that pools where the glass
 * bends back past itself; the light that tube throws onto the brick
 * behind it and the pavement below; and the head- and tail-light
 * streaks along a road, the only motion a still frame can show.
 *
 * All three ADD light, because added light survives a DARK frame and
 * clips over a bright one — and a night city is exactly the dark frame
 * where adding is correct. Each factory still takes an `ambient` escape
 * (0 an unlit street, 1 open daylight): from 0.35 up it stops adding
 * and paints a pale, normal-blended tube instead, so the 0.5 a composer
 * picks when handed "0 to 1" is the mode that cannot clip.
 *
 * Composes with `windows.js` (the rooms behind the facade this spills
 * onto).
 */

import * as THREE from 'three';

import {
    keepOutOfDepthPasses, makeShaderMaterial, patchStandard, sweepProfile,
    tickShaders, toColor } from './shader.js';

const _UP = new THREE.Vector3(0, 1, 0);

// How many spill sources one program carries. The array length is baked
// into the GLSL and `patchStandard` gives every spill patch ONE cache
// key, so it is a constant here and the live count is a uniform.
const SPILL_MAX = 8;

// A failing tube does not blink on a clock: it holds, drops out for a
// frame or two, then buzzes at the mains while it strikes again.
// Shared verbatim by the tube and the spill so one seed stutters both.
const FLICK_GLSL = [
    'float astraNeonFlick(float t, float key, float amt) {',
    '  if (amt <= 0.0) return 1.0;',
    '  float s = astraHash11(floor(t * 14.0) * 1.13 + key);',
    '  float dip = step(s, amt * 0.30);',
    '  float buzz = 0.86 + 0.14 * sin(t * 96.0 + key);',
    '  return mix(1.0, mix(buzz, 0.08 + 0.22 * s, dip), amt);',
    '}',
].join('\n');

// HOW WHITE THE MIDDLE OF THE GLASS GOES, and it is not very white.
// This renderer tone maps in the FRAGMENT TAIL with no post chain, so
// additive layers sum in DISPLAY space: the near and far walls of a
// double-sided shell plus the halo twice stack four deep on the middle
// of a tube, and every channel a colour carries is multiplied by that
// stack. Whitening the colour BEFORE it threw the gas away — measured,
// a cyan sign rendered as a white strip light with a cyan edge. A tube
// IS meant to clip, toward its OWN hue, which the stack does by itself
// once the colour stays saturated. So: a fifth of the way to white dead
// centre, gone two thirds out — a hot line in coloured glass.
const FILAMENT_GLSL = [
    'float astraNeonFilament(float face, float hot) {',
    '  return pow(face, 14.0) * hot * 0.22;',
    '}',
].join('\n');

// One vehicle's colour temperature, as a signed offset off the lane's
// own colour. Spent two ways at the call site because a lane's colour
// can be anything: a hue rotation moves a saturated tail-light, a
// warm/cool tilt is the only thing that moves a near-white head-light.
const TEMP_GLSL = [
    'float astraTrailTemp(float id, float vary) {',
    '  return (astraHash11(id * 5.71 + 1.9) - 0.5) * vary;',
    '}',
].join('\n');

/** A seed becomes a far-apart hash offset, not a one-cell shift. */
function seedKey(seed) {
    const s = Math.abs(Math.round(seed === undefined ? 1 : seed)) % 9973;
    return ((s * 16807) % 9973) * 0.011;
}

/** A point in any accepted spelling as its own Vector3. */
function toVec(p) {
    if (p && p.isVector3) return p.clone();
    if (Array.isArray(p)) return new THREE.Vector3(p[0], p[1], p[2]);
    return new THREE.Vector3(p.x || 0, p.y || 0, p.z || 0);
}

/**
 * One polyline or a set of them, always as an array of strokes.
 *
 * A sign is lettering as often as it is a single bent tube, and a
 * caller holding one stroke per character should not have to wrap it.
 */
function toStrokes(path) {
    if (!Array.isArray(path) || !path.length) return [];
    const head = path[0];
    const nested = Array.isArray(head)
        && (Array.isArray(head[0]) || (head[0] && head[0].isVector3)
            || (head[0] && typeof head[0] === 'object'));
    const raw = nested ? path : [path];
    return raw.map((s) => {
        const out = [];
        for (const p of s) {
            const v = toVec(p);
            // A repeated point is a zero-length segment, and a
            // zero-length segment has no tangent to build a frame on.
            if (!out.length || out[out.length - 1].distanceTo(v) > 1e-6) {
                out.push(v);
            }
        }
        return out;
    }).filter((s) => s.length >= 2);
}

/**
 * Round every corner: glass bends on a radius.
 *
 * A mitred corner is a thing no glassblower makes, and the sweep would
 * pinch there. Each corner becomes a quadratic through points `bend`
 * back along both legs, which keeps the tube within `bend / 4` of the
 * polyline it was given.
 */
function filletPath(pts, bend) {
    if (pts.length < 3 || bend <= 0) return pts.map((p) => p.clone());
    const out = [pts[0].clone()];
    for (let i = 1; i < pts.length - 1; i++) {
        const a = pts[i - 1], b = pts[i], c = pts[i + 1];
        const u = new THREE.Vector3().subVectors(a, b);
        const w = new THREE.Vector3().subVectors(c, b);
        const la = u.length(), lc = w.length();
        const k = Math.min(bend, la * 0.45, lc * 0.45);
        if (k < 1e-5) { out.push(b.clone()); continue; }
        u.multiplyScalar(k / la);
        w.multiplyScalar(k / lc);
        for (let j = 0; j <= 6; j++) {
            const t = j / 6, s = 1 - t;
            out.push(new THREE.Vector3(
                s * s * (b.x + u.x) + 2 * s * t * b.x
                    + t * t * (b.x + w.x),
                s * s * (b.y + u.y) + 2 * s * t * b.y
                    + t * t * (b.y + w.y),
                s * s * (b.z + u.z) + 2 * s * t * b.z
                    + t * t * (b.z + w.z)));
        }
    }
    out.push(pts[pts.length - 1].clone());
    return out;
}

/** Stations spaced by ARC LENGTH, so v is metres and pulses ride it. */
function resample(pts, n) {
    const acc = [0];
    for (let i = 1; i < pts.length; i++) {
        acc.push(acc[i - 1] + pts[i].distanceTo(pts[i - 1]));
    }
    const total = acc[acc.length - 1];
    const out = [];
    let j = 0;
    for (let i = 0; i <= n; i++) {
        const d = total * (i / n);
        while (j < acc.length - 2 && acc[j + 1] < d) j++;
        const seg = Math.max(acc[j + 1] - acc[j], 1e-9);
        const f = Math.min(Math.max((d - acc[j]) / seg, 0), 1);
        out.push(pts[j].clone().lerp(pts[j + 1], f));
    }
    return { pts: out, length: total };
}

/**
 * A rotation-minimising frame at every station.
 *
 * Rebuilding the section from a fixed up-vector spins the profile
 * through every bend and shears the tube; transporting the previous
 * normal keeps it steady.
 */
function frames(pts) {
    const n = pts.length;
    const tan = [];
    for (let i = 0; i < n; i++) {
        const a = pts[Math.max(i - 1, 0)];
        const b = pts[Math.min(i + 1, n - 1)];
        const t = new THREE.Vector3().subVectors(b, a);
        if (t.lengthSq() < 1e-12) t.copy(tan[i - 1] || _UP);
        tan.push(t.normalize());
    }
    let nrm = Math.abs(tan[0].y) > 0.9
        ? new THREE.Vector3(1, 0, 0) : _UP.clone();
    const out = [];
    for (let i = 0; i < n; i++) {
        nrm = nrm.clone().addScaledVector(tan[i], -nrm.dot(tan[i]));
        if (nrm.lengthSq() < 1e-10) {
            nrm.set(tan[i].y, -tan[i].x, 0);
            if (nrm.lengthSq() < 1e-10) nrm.set(1, 0, 0);
        }
        nrm.normalize();
        out.push({
            p: pts[i], t: tan[i].clone(), n: nrm.clone(),
            b: new THREE.Vector3().crossVectors(tan[i], nrm).normalize(),
        });
    }
    return out;
}

/** Concatenate position / normal / uv geometries into one draw. */
function mergeGeoms(list) {
    let nv = 0, ni = 0;
    for (const g of list) {
        nv += g.attributes.position.count;
        ni += g.index.count;
    }
    const pos = new Float32Array(nv * 3), nor = new Float32Array(nv * 3);
    const uv = new Float32Array(nv * 2), idx = new Uint32Array(ni);
    let v = 0, k = 0;
    for (const g of list) {
        const a = g.attributes;
        pos.set(a.position.array, v * 3);
        nor.set(a.normal.array, v * 3);
        uv.set(a.uv.array, v * 2);
        const src = g.index.array;
        for (let i = 0; i < src.length; i++) idx[k + i] = src[i] + v;
        v += a.position.count;
        k += src.length;
    }
    const out = new THREE.BufferGeometry();
    out.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    out.setAttribute('normal', new THREE.BufferAttribute(nor, 3));
    out.setAttribute('uv', new THREE.BufferAttribute(uv, 2));
    out.setIndex(new THREE.BufferAttribute(idx, 1));
    return out;
}

/** One stroke as a swept circular section around its own frame. */
function tubeGeometry(st, radius, nu) {
    const nv = st.length - 1;
    const at = (t) => st[Math.min(nv, Math.max(0, Math.round(t * nv)))];
    return sweepProfile(
        (t) => at(t).p,
        (a, t) => {
            const s = at(t);
            const c = Math.cos(a) * radius, d = Math.sin(a) * radius;
            return {
                x: s.n.x * c + s.b.x * d,
                y: s.n.y * c + s.b.y * d,
                z: s.n.z * c + s.b.z * d,
            };
        },
        { nu, nv });
}

/** Stations for every stroke of a path, filleted and arc-length even. */
function strokeStations(strokes, bend, step) {
    return strokes.map((s) => {
        const smooth = filletPath(s, bend);
        const rough = resample(smooth, 4).length;
        const n = Math.min(400, Math.max(8, Math.round(rough / step)));
        const r = resample(smooth, n);
        return { frames: frames(r.pts), length: r.length };
    });
}

/**
 * The glowing gas, as a chord integral through the shell.
 *
 * `astraFacing` is exactly the chord the eye cuts through a tube:
 * longest down the axis, zero at the silhouette. Skip it and the front
 * and back faces of the shell stack at the rim into a bright OUTLINE,
 * which is the solid-plastic-rod look no colour tuning removes.
 *
 * `toe` is the second half of that: the chord is exact for a CYLINDER
 * and the mesh is an n-gon, so at the outermost facet the normal is
 * still up to 180/n degrees off perpendicular and the shell ends on a
 * live alpha — a crease that draws the halo's own polygon around the
 * letter. Fading the chord out over `toe` puts that under the discard.
 */
function tubeMaterial(name, color, gain, core, hot, flicker, key, hazy,
                      toe, vary) {
    // Pale, not white: over a bright wall a core that clips to white is
    // a tube you cannot read, which is the day failure mirrored.
    const tint = hazy
        ? color.clone().lerp(new THREE.Color(1, 1, 1), 0.20) : color.clone();
    const hotv = hazy ? hot * 0.25 : hot;
    return makeShaderMaterial({
        name,
        additive: !hazy,
        uniforms: {
            uColor: { value: tint },
            uGain: { value: gain },
            uCore: { value: core },
            uHot: { value: hotv },
            uFlicker: { value: flicker },
            uKey: { value: key },
            uToe: { value: toe },
            uVary: { value: vary },
        },
        varyings:
            'varying vec3 vNeonN; varying vec3 vNeonW; varying vec2 vNeonUv;',
        vertexMain: [
            '  vNeonN = normalize(mat3(modelMatrix) * normal);',
            '  vNeonW = (modelMatrix * vec4(transformed, 1.0)).xyz;',
            '  vNeonUv = uv;',
        ].join('\n'),
        fragmentHead: [
            'uniform vec3 uColor; uniform float uGain; uniform float uCore;',
            'uniform float uHot; uniform float uFlicker;',
            'uniform float uKey; uniform float uToe; uniform float uVary;',
            FLICK_GLSL,
            FILAMENT_GLSL,
        ].join('\n'),
        fragmentMain: [
            '  vec3 eye = normalize(cameraPosition - vNeonW);',
            '  float face = astraFacing(vNeonN, eye);',
            '  float body = pow(face, uCore) * smoothstep(0.0, uToe, face);',
            '  float a = uGain * body',
            '      * astraNeonFlick(uTime, uKey, uFlicker);',
            // Rolled off, not clamped: a tube that saturates draws a
            // flat band with a hard rim, which is the rod again.
            '  a = 1.0 - exp(-a * 1.6);',
            // One tube is not one colour: electrodes run hotter, the
            // phosphor is not evenly laid, the glass is not evenly
            // thick. The drift rides each stroke's own v, so a word
            // bent one stroke per letter drifts per LETTER — which is
            // what stops four letters reading as one printed decal.
            '  float drift = astraNoise2(vec2(vNeonUv.y * 9.0, uKey))',
            '      - 0.5;',
            '  vec3 gas = astraHueShift(uColor, drift * uVary);',
            '  vec3 c = mix(gas, vec3(1.0),',
            '      astraNeonFilament(face, uHot));',
            // A halo is a wide, shallow ramp: in 8-bit output that is
            // exactly the gradient that bands into rings.
            '  a += (astraHash21(gl_FragCoord.xy + uKey) - 0.5) * 0.005;',
            '  if (a < 0.003) discard;',
            '  gl_FragColor = vec4(c, clamp(a, 0.0, 1.0));',
        ].join('\n'),
        transparent: true,
        blending: hazy ? THREE.NormalBlending : THREE.AdditiveBlending,
        side: THREE.DoubleSide,
        depthWrite: false,
    });
}

/**
 * A bent glass tube that GLOWS — core, halo and its own colour bleed.
 *
 * Two shells swept along the same path: a thin one carrying the hot
 * core and a fat one carrying the halo, both weighted by the chord the
 * eye cuts through them, both ADDITIVE. Additive is what makes the
 * colour bleed onto itself where the tube bends back past its own halo,
 * and additive is right here because added light survives a DARK frame
 * and clips over a bright one — a night street is that dark frame. Over
 * anything brighter, say so with `ambient` and the tube switches to a
 * pale normal-blended glass instead of clipping the sky out.
 *
 * @param {object} [opts]
 *   `path` a polyline (`[[x,y,z], ...]` or Vector3s) OR an array of
 *   those — one stroke per character of a word (default a 2 m bar);
 *   `radius` tube radius in metres (default 0.05: real glass is ~0.008,
 *   but a tube thinner than a pixel renders as a dashed line);
 *   `color` the gas colour, as its MEAN — the hue drifts a few degrees
 *   along each stroke, so a word bent one stroke per letter is four
 *   related colours rather than one flat fill (default 0xff2e6a);
 *   `intensity` overall gain
 *   (default 1); `halo` how many tube radii the glow reaches (default
 *   3.6); `bend` corner radius in metres (default 6 * radius, capped at
 *   45% of the shorter leg — the tube stays within bend/4 of the
 *   polyline); `flicker` 0..1 failing-tube stutter (default 0, OFF —
 *   a whole sign stuttering is a story, and most signs work);
 *   `ambient` how bright the surroundings are, 0 an unlit street to 1
 *   open daylight (default 0.15, this being a night library; from 0.35
 *   up the tube stops adding light and turns pale and normal-blended,
 *   so a composer who is unsure and picks 0.5 gets the mode that cannot
 *   clip); `seed` the flicker phase (default 3).
 * @returns {THREE.Group} Named `NeonTube`, guarded by
 *   `keepOutOfDepthPasses` (a glow card is a solid wall to the GTAO
 *   override pass, and a light source must cast no shadow), with
 *   `userData.tick(t)` and `userData.spillSources()` — the world points
 *   to hand straight to `patchNeonSpill`.
 */
export function makeNeonTube(opts = {}) {
    const radius = Math.max(1e-3,
        opts.radius === undefined ? 0.05 : opts.radius);
    const strokes = toStrokes(opts.path && opts.path.length ? opts.path
        : [[-1, 0, 0], [1, 0, 0]]);
    const color = toColor(opts.color, 0xff2e6a);
    const intensity = opts.intensity === undefined ? 1 : opts.intensity;
    const halo = opts.halo === undefined ? 3.6 : opts.halo;
    const bend = opts.bend === undefined ? radius * 6 : opts.bend;
    const flicker = Math.min(Math.max(
        opts.flicker === undefined ? 0 : opts.flicker, 0), 1);
    const ambient = Math.min(Math.max(
        opts.ambient === undefined ? 0.15 : opts.ambient, 0), 1);
    const key = seedKey(opts.seed === undefined ? 3 : opts.seed);
    const hazy = ambient >= 0.35;

    const g = new THREE.Group();
    g.name = 'NeonTube';
    if (!strokes.length) return g;
    const st = strokeStations(strokes, bend,
        Math.max(0.02, Math.min(radius * 2.2, bend * 0.25)));
    const gain = Math.max(0, intensity) * (hazy ? 1.9 : 1.05);
    // THE ADDITIVE BUDGET (see FILAMENT_GLSL for why display space is
    // what stacks). The old gains laid four layers of ~0.8 and ~0.44 on
    // the middle of the glass — a sum near 2.5, where every channel of
    // every colour clips and the sign renders white whatever gas it
    // holds. Budgeted to ~1.4 instead: the strong channel still clips,
    // as a tube must, and the weak ones keep the hue.
    // 18 sides, not 10: the halo is `halo` times the radius of the
    // glass, so its silhouette is the widest polygon here and a 10-gon
    // draws that polygon around the letter (measured: a 2% alpha step
    // at the outermost facet). The core's facets fit inside a pixel.
    g.add(shell(st, radius * Math.max(1.2, halo), 18, tubeMaterial(
        'NeonHalo', color, gain * (hazy ? 0.12 : 0.148), 2.6, 0.10,
        flicker, key, hazy, 0.45, 0.20), 'NeonHalo', 2));
    g.add(shell(st, radius, 14, tubeMaterial(
        'NeonCore', color, gain * (hazy ? 1.0 : 0.41), 0.85, 0.9,
        flicker, key, hazy, 0.10, 0.13), 'NeonCore', 3));
    g.userData.tick = (t) => tickShaders(g, t);
    // Sources for `patchNeonSpill`, read through the group's CURRENT
    // world matrix, so placing the sign moves the light it throws.
    g.userData.spillSources = (n) => sampleSources(g, st, color, n);
    return keepOutOfDepthPasses(g);
}

/** One merged mesh for every stroke at one radius. */
function shell(st, radius, nu, material, name, order) {
    const mesh = new THREE.Mesh(
        mergeGeoms(st.map((s) => tubeGeometry(s.frames, radius, nu))),
        material);
    mesh.name = name;
    mesh.renderOrder = order;
    return mesh;
}

/** Evenly spaced points along the whole sign, in world space. */
function sampleSources(group, st, color, count) {
    const n = Math.min(SPILL_MAX, Math.max(1,
        count === undefined ? 4 : Math.round(count)));
    const all = [];
    for (const s of st) for (const f of s.frames) all.push(f.p);
    group.updateWorldMatrix(true, false);
    const out = [];
    for (let i = 0; i < n; i++) {
        const k = Math.round((i + 0.5) / n * (all.length - 1));
        out.push({
            position: all[k].clone().applyMatrix4(group.matrixWorld),
            color: color.clone(),
        });
    }
    return out;
}

const SPILL_VARYINGS =
    'varying vec3 vNeonSW;\nvarying vec3 vNeonSN;';

// `transformed` is still object-space at <begin_vertex>, so an
// instanced wall needs instanceMatrix folded in by hand.
const SPILL_VERTEX = [
    '  vec4 nsP = vec4(transformed, 1.0);',
    '  vec3 nsN = normal;',
    '#ifdef USE_INSTANCING',
    '  nsP = instanceMatrix * nsP;',
    '  nsN = mat3(instanceMatrix) * nsN;',
    '#endif',
    '  vNeonSW = (modelMatrix * nsP).xyz;',
    '  vNeonSN = normalize((modelMatrix * vec4(nsN, 0.0)).xyz);',
].join('\n');

const SPILL_HEAD = [
    'uniform vec3 uNeonPos[' + SPILL_MAX + '];',
    'uniform vec3 uNeonCol[' + SPILL_MAX + '];',
    'uniform float uNeonCount;',
    'uniform float uNeonRadius;',
    'uniform float uNeonGain;',
    'uniform float uNeonFlicker;',
    'uniform float uNeonKey;',
    // Windowed inverse square: 1 at the source, EXACTLY 0 at radius and
    // beyond. A bare 1/d^2 never reaches zero, so every wall in the
    // scene keeps a wash of every sign in it and the night goes flat.
    'float astraNeonFall(float d, float r) {',
    '  float x = clamp(d / max(r, 1e-4), 0.0, 1.0);',
    '  float w = 1.0 - x * x;',
    '  return w * w / (1.0 + 12.0 * x * x);',
    '}',
    FLICK_GLSL,
].join('\n');

const SPILL_BODY = [
    '  vec3 nsSum = vec3(0.0);',
    '  vec3 nsN = normalize(vNeonSN);',
    '  for (int i = 0; i < ' + SPILL_MAX + '; i++) {',
    '    if (float(i) >= uNeonCount) break;',
    '    vec3 dv = uNeonPos[i] - vNeonSW;',
    '    float d = length(dv);',
    // A sign is a broad source, not a point: a hard lambert terminator
    // would cut the pavement off right under the tube that lights it.
    '    float lam = dot(nsN, dv / max(d, 1e-4)) * 0.5 + 0.5;',
    '    lam = clamp(lam, 0.0, 1.0);',
    '    nsSum += uNeonCol[i] * (astraNeonFall(d, uNeonRadius) * lam * lam);',
    '  }',
    '  nsSum *= uNeonGain * astraNeonFlick(uTime, uNeonKey, uNeonFlicker);',
    // A pool is a metres-wide ramp on a flat wall — the widest, softest
    // gradient in the library and the one that rings in 8-bit output.
    // Proportional, so an unlit wall stays unlit instead of gaining a
    // floor of noise.
    '  nsSum *= 1.0 + (astraHash21(gl_FragCoord.xy + uNeonKey) - 0.5)',
    '      * 0.03;',
    // Arriving light is REFLECTED, so it takes the wall's own colour —
    // but never all of it, or a dark wall swallows the sign entirely.
    '  totalEmissiveRadiance +=',
    '      nsSum * mix(vec3(0.30), diffuseColor.rgb, 0.70);',
].join('\n');

/**
 * The light a sign throws onto the wall behind and the road below.
 *
 * Without this a neon sign is a bright decal on an unlit wall, which is
 * the single tell that a night scene was assembled rather than lit. Each
 * source adds a windowed inverse square, wrapped by how the surface
 * faces it and reflected through the surface's own albedo, straight
 * into `totalEmissiveRadiance` — so it ADDS light, correct over the
 * dark frame a night city is and clipping over a bright one, which is
 * what `ambient` is for. It is a `patchStandard`, so it needs a LIT
 * material (MeshStandard/MeshPhysical, not Basic), and it chains
 * happily with `patchWindowInteriors` and `patchMicroBreakup`.
 *
 * The one constraint it cannot solve: nothing is occluded. Light
 * reaches the far side of a wall as readily as the near one, so keep
 * `radius` to what the sign really reaches (a few metres) and put the
 * sources on the face they light.
 *
 * @param {THREE.Material} material A built-in material, patched in
 *   place. Clone a shared `materials.js` instance first.
 * @param {object} [opts]
 *   `sources` up to 8 world points with colours — a Vector3, an
 *   `[x,y,z]`, or `{ position, color }`; `makeNeonTube().userData
 *   .spillSources()` hands back exactly this; `radius` metres the
 *   light reaches, ZERO beyond it (default 4); `strength` how bright
 *   the pool reads (default 1); `ambient` how bright the surroundings
 *   are, 0 an unlit street to 1 open daylight (default 0.15 — from
 *   0.35 up the spill is cut back hard, because emissive added to an
 *   already-lit wall is the same clip); `flicker`/`seed` pass the SAME
 *   pair as the tube and the wall stutters with it (default 0, off);
 *   `name` the program cache key (default 'neon:spill' — every option
 *   here is a UNIFORM, so one name is correct and two differently lit
 *   walls still share one compiled program).
 * @returns {THREE.Material} The same material; its uniforms live on
 *   `material.userData.uniforms`, so a scene can move or dim the
 *   sources per frame.
 */
export function patchNeonSpill(material, opts = {}) {
    const radius = Math.max(1e-3,
        opts.radius === undefined ? 4 : opts.radius);
    const strength = Math.max(0,
        opts.strength === undefined ? 1 : opts.strength);
    const ambient = Math.min(Math.max(
        opts.ambient === undefined ? 0.15 : opts.ambient, 0), 1);
    const flicker = Math.min(Math.max(
        opts.flicker === undefined ? 0 : opts.flicker, 0), 1);
    const list = (Array.isArray(opts.sources) ? opts.sources : [])
        .slice(0, SPILL_MAX);
    const pos = [], col = [];
    for (let i = 0; i < SPILL_MAX; i++) {
        const s = list[i];
        const p = s && (s.position || s.point || s);
        pos.push(s ? toVec(p) : new THREE.Vector3());
        col.push(s ? toColor(s.color, 0xff2e6a) : new THREE.Color(0, 0, 0));
    }
    return patchStandard(material, {
        name: opts.name || 'neon:spill',
        uniforms: {
            uNeonPos: { value: pos },
            uNeonCol: { value: col },
            uNeonCount: { value: list.length },
            uNeonRadius: { value: radius },
            // Emissive added to a wall the sun already lit clips the
            // same way an additive pass does, so the escape is a gain.
            uNeonGain: { value: strength * (1 - 0.85 * ambient) },
            uNeonFlicker: { value: flicker },
            uNeonKey: { value: seedKey(opts.seed === undefined ? 3
                                                              : opts.seed) },
        },
        vertexHead: SPILL_VARYINGS,
        vertexBody: SPILL_VERTEX,
        fragmentHead: [SPILL_VARYINGS, SPILL_HEAD].join('\n'),
        fragmentBody: SPILL_BODY,
    });
}

/** A flat ribbon riding the road, u across and v along the arc. */
function ribbonGeometry(fr, offset, halfW, lift) {
    const n = fr.length;
    const pos = new Float32Array(n * 2 * 3);
    const nor = new Float32Array(n * 2 * 3);
    const uv = new Float32Array(n * 2 * 2);
    const idx = new Uint32Array((n - 1) * 6);
    const lat = new THREE.Vector3();
    for (let i = 0; i < n; i++) {
        lat.crossVectors(fr[i].t, _UP);
        if (lat.lengthSq() < 1e-9) lat.set(1, 0, 0);
        lat.normalize();
        const v = i / (n - 1);
        for (let s = 0; s < 2; s++) {
            const k = (i * 2 + s);
            const w = offset + (s === 0 ? -halfW : halfW);
            pos[k * 3] = fr[i].p.x + lat.x * w;
            pos[k * 3 + 1] = fr[i].p.y + lift;
            pos[k * 3 + 2] = fr[i].p.z + lat.z * w;
            nor[k * 3 + 1] = 1;
            uv[k * 2] = s;
            uv[k * 2 + 1] = v;
        }
        if (i < n - 1) {
            const a = i * 2;
            idx.set([a, a + 1, a + 2, a + 1, a + 3, a + 2], i * 6);
        }
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    g.setAttribute('normal', new THREE.BufferAttribute(nor, 3));
    g.setAttribute('uv', new THREE.BufferAttribute(uv, 2));
    g.setIndex(new THREE.BufferAttribute(idx, 1));
    return g;
}

/**
 * Comet-shaped pulses riding v, one lane per material.
 *
 * The head is a hot point and everything behind it is the smear a long
 * exposure leaves; `uDir` flips which way along the path that is, so
 * the two lanes run against each other from one shared source.
 */
function trailMaterial(name, color, dir, count, rate, gain, hot, fore,
                       key, hazy, vary) {
    const tint = hazy
        ? color.clone().lerp(new THREE.Color(1, 1, 1), 0.20) : color.clone();
    const hotv = hazy ? hot * 0.25 : hot;
    return makeShaderMaterial({
        name,
        additive: !hazy,
        uniforms: {
            uColor: { value: tint },
            uDir: { value: dir },
            uCount: { value: count },
            uRate: { value: rate },
            uGain: { value: gain },
            uHot: { value: hotv },
            uFore: { value: fore },
            uKey: { value: key },
            uVary: { value: vary },
        },
        varyings: 'varying vec2 vTrailUv;',
        vertexMain: '  vTrailUv = uv;',
        fragmentHead: [
            'uniform vec3 uColor; uniform float uDir; uniform float uCount;',
            'uniform float uRate; uniform float uGain; uniform float uHot;',
            'uniform float uFore; uniform float uKey; uniform float uVary;',
            // The phase one vehicle rides. Written as its own function
            // so the direction it travels is a property of the source,
            // not something only a rendered frame could tell you.
            'float astraTrailPhase(float v, float dir, float t,',
            '                      float rate, float count) {',
            '  return fract(dir * v * count - t * rate);',
            '}',
            TEMP_GLSL,
        ].join('\n'),
        fragmentMain: [
            '  float u = vTrailUv.x * 2.0 - 1.0;',
            '  float s = uDir * vTrailUv.y * uCount - uTime * uRate;',
            '  float f = fract(s);',
            '  float id = floor(s) + uKey;',
            // A lane is not one vehicle: each sits at its own place
            // across the lane and carries its own brightness.
            '  float du = u - (astraHash11(id * 1.37) - 0.5) * 0.55;',
            '  float amp = 0.45 + 1.0 * astraHash11(id * 2.13 + 3.1);',
            '  float head = pow(f, 26.0);',
            '  float tail = pow(f, 3.2);',
            // A lamp lights the road IN FRONT of itself. Without this
            // the smear stops dead at the head and the cut reads as a
            // straight-edged slab laid across the lane.
            '  float fore = exp(-f * 12.0) * uFore;',
            '  float core = exp(-du * du * 26.0);',
            // The road catching the same lamp: wider, dimmer, and the
            // reason a streak is not a floating line. It must reach
            // ZERO at the ribbon edge or the road wears a hard slab.
            '  float rim = 1.0 - u * u;',
            '  float wash = exp(-du * du * 2.2) * 0.42 * rim * rim;',
            '  float ends = smoothstep(0.0, 0.05, vTrailUv.y)',
            '      * (1.0 - smoothstep(0.95, 1.0, vTrailUv.y));',
            '  float a = uGain * amp * ends',
            '      * ((head + (tail + fore) * 0.42) * core',
            '         + (tail + fore) * wash);',
            '  a = 1.0 - exp(-a * 1.6);',
            // No two vehicles carry the same lamp: halogen against cold
            // LED one way, a braked tail-light against a coasting one
            // the other.
            '  float w = astraTrailTemp(id, uVary);',
            '  vec3 lamp = astraHueShift(uColor, w)',
            '      * vec3(1.0 + w, 1.0 + w * 0.15, 1.0 - w);',
            '  vec3 c = mix(lamp, vec3(1.0), head * uHot);',
            // The wash is a metre-wide ramp across tarmac: the one
            // gradient in this effect wide enough to band.
            '  a += (astraHash21(gl_FragCoord.xy + uKey) - 0.5) * 0.005;',
            '  if (a < 0.003) discard;',
            '  gl_FragColor = vec4(c, clamp(a, 0.0, 1.0));',
        ].join('\n'),
        transparent: true,
        blending: hazy ? THREE.NormalBlending : THREE.AdditiveBlending,
        side: THREE.DoubleSide,
        depthWrite: false,
    });
}

/**
 * Head- and tail-light streaks along a road: motion in a still frame.
 *
 * Two ribbons laid on the road, one per lane, each carrying comet
 * pulses that ride the path's own arc length — so the streaks BEND with
 * the road instead of crossing it. Warm white runs one way and red the
 * other, which is what tells a reader the traffic has two directions.
 * The pulses ADD light, correct over the dark frame a night road is and
 * wrong over a bright one; `ambient` is the escape.
 *
 * @param {object} [opts]
 *   `path` the road centreline as a polyline (`[[x,y,z], ...]` or
 *   Vector3s; default a 40 m straight); `count` streaks per lane
 *   (default 5); `speed` metres per second along the path (default 14);
 *   `warm` the lane running FORWARD along the path, head-lights
 *   (default 0xffeec8); `cool` the lane running back, tail-lights
 *   (default 0xff1e14 — `cool` names the lane, not the hue). Each is
 *   the lane's MEAN: every vehicle rotates a few degrees off it, so a
 *   lane reads as traffic rather than as eight copies of one lamp;
 *   `width`
 *   streak width in metres (default 0.5; the ribbon carrying its wash
 *   is six times that, so the road wants `2 * lane + 6 * width` of
 *   tarmac); `lane` metres from the centreline to each lane (default
 *   width * 4); `lift` metres above
 *   the road (default 0.03, enough to clear z-fighting); `ambient`
 *   0 an unlit street to 1 open daylight (default 0.15 — from 0.35 up
 *   the streaks turn pale and normal-blended instead of clipping);
 *   `seed` per-vehicle jitter (default 7).
 * @returns {THREE.Group} Named `LightTrails`, holding `TrailsWarm` and
 *   `TrailsCool`, guarded by `keepOutOfDepthPasses` (an additive card
 *   is an opaque wall to the GTAO override pass, and a moving light
 *   must cast no shadow), with `userData.tick(t)`.
 */
export function makeLightTrails(opts = {}) {
    const strokes = toStrokes(opts.path && opts.path.length ? opts.path
        : [[0, 0, -20], [0, 0, 20]]);
    const count = Math.max(1, Math.round(
        opts.count === undefined ? 5 : opts.count));
    const speed = opts.speed === undefined ? 14 : opts.speed;
    const width = Math.max(0.02,
        opts.width === undefined ? 0.5 : opts.width);
    const lane = opts.lane === undefined ? width * 4 : opts.lane;
    const lift = opts.lift === undefined ? 0.03 : opts.lift;
    const ambient = Math.min(Math.max(
        opts.ambient === undefined ? 0.15 : opts.ambient, 0), 1);
    const key = seedKey(opts.seed === undefined ? 7 : opts.seed);
    const hazy = ambient >= 0.35;

    const g = new THREE.Group();
    g.name = 'LightTrails';
    if (!strokes.length) return g;
    const line = strokes[0];
    const smooth = filletPath(line, Math.max(lane, width) * 2);
    const rough = resample(smooth, 4).length;
    const r = resample(smooth, Math.min(600, Math.max(24,
        Math.round(rough * 1.5))));
    const fr = frames(r.pts);
    // Pulses per second along the path: `speed` is metres, and v is
    // metres because the stations were spaced by arc length.
    const rate = speed * count / Math.max(r.length, 1e-3);
    const gain = hazy ? 0.55 : 1.15;
    const half = width * 3;
    // 0.5, not 0.7, on the head-lights: a lamp that whitens hard has
    // no colour temperature left to vary, and with no bloom pass here
    // the white it clips to is the last thing the frame records.
    g.add(laneMesh(fr, -lane, half, lift, trailMaterial(
        'NeonTrailWarm', toColor(opts.warm, 0xffeec8), 1, count, rate,
        gain, 0.5, 0.60, key, hazy, 0.40), 'TrailsWarm'));
    g.add(laneMesh(fr, lane, half, lift, trailMaterial(
        'NeonTrailCool', toColor(opts.cool, 0xff1e14), -1, count, rate,
        gain, 0.15, 0.22, key + 5.3, hazy, 0.16), 'TrailsCool'));
    g.userData.tick = (t) => tickShaders(g, t);
    return keepOutOfDepthPasses(g);
}

/** One lane's ribbon, named so a test and a composer can find it. */
function laneMesh(fr, offset, halfW, lift, material, name) {
    const mesh = new THREE.Mesh(
        ribbonGeometry(fr, offset, halfW, lift), material);
    mesh.name = name;
    mesh.renderOrder = 2;
    return mesh;
}

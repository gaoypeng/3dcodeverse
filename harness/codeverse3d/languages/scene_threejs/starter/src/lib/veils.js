/**
 * Weather seen AT DISTANCE: the veils, not the drops.
 *
 * `rain.js` gives the drops near the camera — a wrap box a few metres
 * wide, streaks measured in centimetres. A rainy valley needs the other
 * thing entirely: the pale sheets of rain crossing the far hillside,
 * tens of metres tall, slanted with the wind, that tell you the weather
 * is out THERE. Same for snow, and for the dust and pollen that say
 * this air has something in it. None of the three is a bigger copy of
 * the near-field version, and the differences are the point:
 *
 *   `makeRainVeil` is a handful of huge slanted curtains, not thousands
 *   of streaks. A curtain is legible only if it never shows an edge and
 *   never reads as a rectangle, so each one billboards about its OWN
 *   fall axis and carries filaments that travel down its face.
 *
 *   `makeSnowfall` is not rain that is white. A snowflake falls at
 *   under a metre a second and WANDERS — its horizontal path is a curve
 *   with no preferred direction — and that wander is the whole
 *   difference. Flakes near the camera are drawn bigger and softer,
 *   because a real lens focussed on the valley cannot hold them.
 *
 *   `makeMotes` is the air itself: specks that hang, barely drift, and
 *   flash as they turn their faces to the light.
 *
 * BLENDING — all three blend NORMALLY, none of them additively.
 * `godrays.js` measures what added light does to a bright frame: 12.8%
 * of it past 0.97, and 3.6x less gain still left 11.3%, because 0.94
 * plus anything visible clips. Every effect here is pale and every one
 * of them can appear in daylight, so additive blending would turn each
 * into a solid white slab at any strength worth having. Rain and snow
 * SCATTER — they lower the contrast of what is behind them, which is
 * the opposite of adding — and a mote is a speck of matter, not a lamp;
 * its glint is carried to near-white through ALPHA, which is the
 * brightest an element that does not add is allowed to be.
 *
 * LIGHT — none of the three is a lit material, and all three read the
 * scene anyway: each field probes the lights around it every few frames
 * and multiplies its colour by that irradiance, normalised so the
 * library's own day rig is 1.0. Daylight is therefore unchanged and a
 * moonlit valley no longer carries a glowing white curtain. A lamp is
 * included with its falloff at the field's own origin, which is what
 * puts a mote volume standing in a lamp's pool into that pool.
 *
 * Camera vectors are converted to local coordinates, so translated,
 * rotated and scaled parents retain the authored local field.
 */

import * as THREE from 'three';
import { attachDisposal, snapshotResources } from './lifecycle.js';

import { lehmer } from './noise.js';
import {
    glslWob, instancedQuad, keepOutOfDepthPasses, makeShaderMaterial,
    tickShaders, wobX, wobZ } from './shader.js';

// Terminal fall of medium rain. Written once because the slant, the
// sheet length and the streak rate must all read the same number.
const _RAIN_FALL = 9.0;

/** Horizontal velocity in xz from a number, an array or a vector. */
function toWind(v, dx, dz) {
    if (typeof v === 'number') return new THREE.Vector2(v, 0);
    if (Array.isArray(v)) {
        return new THREE.Vector2(v[0] || 0,
            (v.length > 2 ? v[2] : v[1]) || 0);
    }
    if (v && v.isVector3) return new THREE.Vector2(v.x, v.z);
    if (v && v.isVector2) return new THREE.Vector2(v.x, v.y);
    return new THREE.Vector2(dx, dz);
}

// The swing: `wobX`/`wobZ` on the CPU mirror, the same pair in GLSL.
const _WOB_GLSL = glslWob('astraVeilWob');

/** Wrap v into [-half, half), the CPU mirror of GLSL mod(). */
function _wrap(v, extent, half) {
    return v - Math.floor((v + half) / extent) * extent;
}

/*
 * LIGHT. None of the three materials is lit — they are billboards with
 * a colour and an alpha — and an unlit pale card is the brightest thing
 * in any frame that is not daylight. Measured on the moonlit rig
 * (elevation -20): the rain curtain came back at 0.62 luminance over a
 * 0.12 scene, a glowing sheet hanging in front of a night sky, and the
 * motes stayed white inside a shadow. Nothing here can be lit properly
 * — a veil is a volume, not a surface — but what it SCATTERS is the
 * irradiance where it hangs, and that is one vec3 the scene can be
 * asked for. So each field probes its own scene once per render frame
 * and multiplies its colour by the answer, normalised so the library's
 * own day rig lands on 1.0 and every veil in daylight looks exactly as
 * it did. `uLight` becomes zero when all contributing lights are removed.
 * Effect previews should provide an ambient light explicitly.
 */

// Weights per light type: a veil sees the whole sky and only a slice of
// a directional beam (forward scatter), so the hemisphere dominates.
const _W_HEMI_SKY = 0.62;
const _W_HEMI_GROUND = 0.18;
const _W_DIR = 0.12;
// Irradiance the library's `sunRig()` day preset puts on a veil, so the
// probe is a no-op at noon and a real dimming everywhere else.
const _LIGHT_REF = 1.02;

const _PROBE = new WeakMap();
const _TMP_AT = new THREE.Vector3();
const _TMP_LP = new THREE.Vector3();

/** The distant (position-free) part of a scene's light, cached per frame. */
function _probeScene(scene, frame) {
    const hit = _PROBE.get(scene);
    if (hit && frame === hit.frame) return hit;
    const distant = { r: 0, g: 0, b: 0 };
    const punctual = [];
    let found = 0;
    scene.traverseVisible((o) => {
        if (!o.isLight || o.visible === false || !(o.intensity > 0)) return;
        found++;
        const c = o.color;
        const i = o.intensity;
        if (o.isAmbientLight) {
            distant.r += c.r * i; distant.g += c.g * i; distant.b += c.b * i;
        } else if (o.isHemisphereLight) {
            const g = o.groundColor;
            distant.r += (c.r * _W_HEMI_SKY + g.r * _W_HEMI_GROUND) * i;
            distant.g += (c.g * _W_HEMI_SKY + g.g * _W_HEMI_GROUND) * i;
            distant.b += (c.b * _W_HEMI_SKY + g.b * _W_HEMI_GROUND) * i;
        } else if (o.isDirectionalLight) {
            distant.r += c.r * i * _W_DIR;
            distant.g += c.g * i * _W_DIR;
            distant.b += c.b * i * _W_DIR;
        } else if (o.isPointLight || o.isSpotLight) {
            punctual.push(o);
        }
    });
    const out = { frame, distant, punctual, found, fog: scene.fog || null };
    _PROBE.set(scene, out);
    return out;
}

/**
 * Drive `uLight` (and, when the caller left the colour alone, the hue of
 * `uColor`) from the scene the mesh is being drawn in.
 *
 * A lamp is included with its own inverse-square falloff at the FIELD's
 * origin, which is what gives a mote volume standing in a lamp's pool a
 * warm lift and leaves the 70 m rain field untouched by it.
 *
 * @param {THREE.Mesh} mesh Carries a `makeShaderMaterial` with `uLight`.
 * @param {THREE.Color} [autoTint] The default colour to pull into the
 *   scene's fog hue — omitted when the caller passed a colour of its own.
 */
function _litByScene(mesh, autoTint) {
    const prev = mesh.onBeforeRender;
    const uni = mesh.material.uniforms;
    const base = autoTint ? autoTint.clone() : null;
    const hsl = { h: 0, s: 0, l: 0 };
    const own = { h: 0, s: 0, l: 0 };
    const inverse = new THREE.Matrix4();
    uni.uCameraLocal = { value: new THREE.Vector3() };
    uni.uCameraRight = { value: new THREE.Vector3(1, 0, 0) };
    uni.uCameraUp = { value: new THREE.Vector3(0, 1, 0) };
    mesh.material.vertexShader = 'uniform vec3 uCameraLocal; uniform vec3 uCameraRight; uniform vec3 uCameraUp;\n' + mesh.material.vertexShader;
    mesh.material.fragmentShader = 'uniform vec3 uCameraLocal;\n' + mesh.material.fragmentShader;
    mesh.onBeforeRender = (r, scene, cam, geo, mat, grp) => {
        if (prev) prev.call(mesh, r, scene, cam, geo, mat, grp);
        inverse.copy(mesh.matrixWorld).invert();
        cam.getWorldPosition(uni.uCameraLocal.value).applyMatrix4(inverse);
        uni.uCameraRight.value.setFromMatrixColumn(cam.matrixWorld, 0).transformDirection(inverse);
        uni.uCameraUp.value.setFromMatrixColumn(cam.matrixWorld, 1).transformDirection(inverse);
        const p = _probeScene(scene, r.info.render.frame);
        if (!p.found) {
            uni.uLight.value.setRGB(0, 0, 0);
            if (base) uni.uColor.value.copy(base);
            return;
        }
        let { r: er, g: eg, b: eb } = p.distant;
        if (p.punctual.length) {
            mesh.getWorldPosition(_TMP_AT);
            for (const l of p.punctual) {
                l.getWorldPosition(_TMP_LP);
                const d2 = Math.max(0.25, _TMP_LP.distanceToSquared(_TMP_AT));
                if (l.distance > 0 && d2 > l.distance * l.distance) continue;
                const a = (l.intensity / d2) * _W_DIR;
                er += l.color.r * a; eg += l.color.g * a; eb += l.color.b * a;
            }
        }
        // The floor keeps a veil legible in a scene lit by one dim lamp;
        // the ceiling stops a lamp two metres away turning it into a
        // white slab, which is the failure ordinary blending exists to
        // avoid in the first place.
        // ADAPTATION, not physics. A straight ratio is right for the
        // hue and too steep for the value: a moonlit curtain at 0.15 of
        // daylight is a curtain nobody can see, while the eye watching
        // that valley has opened up. The 0.82 exponent is the whole of
        // the fudge — it is 1.0 at daylight, so noon is untouched, and
        // it lifts the moonlit field to 0.21 of it.
        const k = 1 / _LIGHT_REF;
        const adapt = (e) => Math.min(1.7,
            Math.max(0.05, Math.pow(Math.max(0, e * k), 0.82)));
        uni.uLight.value.setRGB(adapt(er), adapt(eg), adapt(eb));
        // FOG HUE. A veil the scene's own haze does not agree with reads
        // as a card in front of the weather rather than part of it — so
        // an untouched default takes the fog's HUE and keeps its own
        // lightness. Taking the fog's value as well would send the veil
        // to black in a night scene the light probe has already dimmed.
        if (base && p.fog) {
            p.fog.color.getHSL(hsl);
            base.getHSL(own);
            uni.uColor.value.setHSL(hsl.h,
                Math.min(0.30, hsl.s * 0.75 + own.s * 0.25), own.l);
        } else if (base) uni.uColor.value.copy(base);
    };
}

/**
 * Sheets of rain crossing the middle and far distance.
 *
 * A few very large curtains, not a cloud of streaks: at 80 m a drop is
 * far under a pixel, so what the eye is given is the SHEET — leaning
 * with the wind, thickest at its foot, ragged at its edges, with
 * filaments running down its face. Two constraints keep it from reading
 * as flat cards, which is the one way this effect fails. Each sheet
 * billboards about its own fall axis, so it turns its face to every
 * camera and never shows an edge or a rectangle; and the per-sheet
 * alpha is solved from how many sheets a ray across the field crosses,
 * so `density` means the same veil however many of them there are and
 * an overlap never stacks into a slab.
 *
 * BLENDING: ORDINARY. Rain scatters — it lowers the contrast of the
 * ridge behind it — and a sheet this size added to a daylight sky
 * would clip to a white slab at any strength worth having.
 *
 * @param {object} [opts]
 *   `extent` metres square the sheets are spread over (default 120 —
 *   this is landscape weather, so think valley, not garden);
 *   `height` metres from the cloud base to the ground (default 60 —
 *   a veil no taller than the ridge it crosses reads as ground haze,
 *   so give it half again the height of what it falls past);
 *   `direction` wind in m/s as a number (along +x), [dx, dz] or a
 *   THREE.Vector2/3 (default [3.5, 0]) — it sets the SLANT (against a
 *   9 m/s fall) and the speed the sheets travel across the field;
 *   `density` how much the veil hides, 0 to 1 (default 0.6; 0.9 is a
 *   downpour that greys out the far ridge);
 *   `color` THREE.Color or hex — the veil's ALBEDO, which the scene's
 *   own light then multiplies (default a cool grey that takes the
 *   scene fog's HUE at its own lightness, so the sheets never stand in
 *   front of the weather; pass a colour to keep that from happening);
 *   `seed` PRNG seed (default 7).
 * @returns {THREE.Group} Named `RainVeil`, centred on its own origin
 *   with the sheets' feet at y ~ 0, with `userData.tick(t)`,
 *   `userData.sample(i, t)` giving sheet i's foot and `userData.axis`
 *   the shared fall direction. Move the group to place the field.
 */
export function makeRainVeil(opts = {}) {
    const extent = Math.max(6,
        opts.extent === undefined ? 120 : opts.extent);
    const height = Math.max(3,
        opts.height === undefined ? 60 : opts.height);
    const wind = toWind(opts.direction, 3.5, 0);
    const density = Math.min(0.97, Math.max(0.02,
        opts.density === undefined ? 0.6 : opts.density));
    const color = new THREE.Color(
        opts.color === undefined ? 0xb9c6d4 : opts.color);
    const autoTint = opts.color === undefined ? color : null;
    const rnd = lehmer(opts.seed === undefined ? 7 : opts.seed);

    const axis = new THREE.Vector3(wind.x, -_RAIN_FALL, wind.y).normalize();
    // Length ALONG the slanted axis that spans `height` vertically, so
    // the cloud base stays level however hard the wind blows.
    const len = height / Math.max(0.2, -axis.y);
    const half = extent * 0.5;
    const sink = height * 0.05;

    const count = Math.round(Math.min(9, Math.max(4, extent / 25)));
    const sheet = new Float32Array(count * 4);
    const veil = new Float32Array(count * 2);
    const footR = Math.hypot(half * Math.SQRT2, sink);
    const topR = Math.hypot(half * Math.SQRT2 + len * Math.hypot(axis.x,
        axis.z), len * -axis.y - sink);
    let widths = 0;
    let radius = 0;
    for (let i = 0; i < count; i++) {
        // Stratified around a ring, never a uniform draw: a uniform one
        // puts two sheets on top of each other and leaves a third of
        // the valley with no weather in it at all.
        const a = ((i + rnd()) / count) * Math.PI * 2;
        const r = half * (0.22 + 0.74 * rnd());
        const hw = extent * (0.09 + 0.09 * rnd());
        sheet[i * 4] = Math.cos(a) * r;
        sheet[i * 4 + 1] = Math.sin(a) * r;
        sheet[i * 4 + 2] = hw;
        sheet[i * 4 + 3] = rnd() * 40;
        veil[i * 2] = 0.62 + 0.58 * rnd();
        veil[i * 2 + 1] = rnd() * 9;
        widths += hw * 2;
        radius = Math.max(radius, Math.max(footR, topR) + hw);
    }
    // Sheets a ray across the field crosses. Solving the per-sheet
    // alpha from it keeps `density` the depth of the VEIL at any count,
    // and the 2.6 pays back what the profile and the holes take.
    const hits = Math.max(0.8, widths / extent);
    const alpha = Math.min(0.70, Math.max(0.01,
        2.6 * (1 - Math.pow(1 - density, 1 / hits))));

    const geom = instancedQuad(count, 1, 1, radius * 1.02);
    geom.setAttribute('aSheet', new THREE.InstancedBufferAttribute(sheet, 4));
    geom.setAttribute('aVeil', new THREE.InstancedBufferAttribute(veil, 2));

    const mesh = new THREE.Mesh(geom, veilMaterial({
        color, alpha, axis, wind, extent, half, len, sink,
        wisp: Math.max(1.2, extent * 0.05),
        near: Math.max(4, extent * 0.08),
    }));
    mesh.name = 'RainSheets';
    mesh.renderOrder = 3;
    const g = new THREE.Group();
    g.name = 'RainVeil';
    g.add(mesh);
    // A curtain of rain is not a wall to the ambient-occlusion pass and
    // does not cast a shadow; every other veil in this library says so.
    keepOutOfDepthPasses(g);
    _litByScene(mesh, autoTint);
    g.userData.update = g.userData.tick = (t) => tickShaders(g, t);
    g.userData.axis = axis.clone();
    g.userData.length = len;
    g.userData.sample = (i, t) => {
        const k = Math.min(count - 1, Math.max(0, i | 0));
        return new THREE.Vector3(
            _wrap(sheet[k * 4] + wind.x * t, extent, half), -sink,
            _wrap(sheet[k * 4 + 1] + wind.y * t, extent, half));
    };
    return attachDisposal(g, snapshotResources(g));
}

/** One curtain per instance, rolled about its own fall axis. */
function veilMaterial(cfg) {
    return makeShaderMaterial({
        name: 'RainVeilSheets',
        uniforms: {
            uTime: { value: 0 },
            uColor: { value: cfg.color },
            uLight: { value: new THREE.Color(1, 1, 1) },
            uAlpha: { value: cfg.alpha },
            uAxis: { value: cfg.axis },
            uWind: { value: cfg.wind },
            uExtent: { value: cfg.extent },
            uHalf: { value: cfg.half },
            uLen: { value: cfg.len },
            uSink: { value: cfg.sink },
            uWisp: { value: cfg.wisp },
            uNear: { value: cfg.near },
            uFall: { value: _RAIN_FALL },
            // Metres between filaments across the sheet and between
            // their heads down it.
            uSpacing: { value: 0.55 },
            uSeg: { value: 6.0 },
        },
        varyings: 'varying vec2 vUv; varying vec3 vP;'
            + ' varying float vBright; varying float vSeed;'
            + ' varying float vHalfW; varying float vFade;',
        // `uniform float uTime;` rides on a long line on purpose: the
        // GLSL audit only reads quoted strings long enough to look like
        // shader source, so alone it is skipped and this file reads as
        // using uTime without ever declaring it (shader.js injects the
        // declaration, and the duplicate here is deduped away).
        vertexHead: 'attribute vec3 aCorner; attribute vec4 aSheet;'
            + ' uniform float uTime;'
            + ' attribute vec2 aVeil; uniform vec3 uAxis;'
            + ' uniform vec2 uWind; uniform float uExtent;'
            + ' uniform float uHalf; uniform float uLen;'
            + ' uniform float uSink;',
        vertexMain: [
            '  vUv = uv;',
            '  vBright = aVeil.x;',
            '  vSeed = aVeil.y + aSheet.w;',
            '  vHalfW = aSheet.z;',
            // The whole curtain travels with the wind and wraps in the
            // field; the edge fade below hides the wrap.
            '  vec2 fxz = mod(aSheet.xy + uWind * uTime + uHalf, uExtent)',
            '      - uHalf;',
            '  vec3 foot = vec3(fxz.x, -uSink, fxz.y);',
            '  vec3 up = -uAxis;',
            '  vec3 mid = foot + up * (uLen * 0.5);',
            // Rolled about its OWN fall axis: the slant stays true from
            // every camera and the sheet never turns edge-on, which is
            // the moment a curtain would betray itself as a card.
            '  vec3 side = cross(uAxis, mid - uCameraLocal);',
            '  float sl = length(side);',
            '  side = sl > 1e-4 ? side / sl : vec3(1.0, 0.0, 0.0);',
            '  transformed = foot + up * ((aCorner.y + 0.5) * uLen)',
            '      + side * (aCorner.x * 2.0 * aSheet.z);',
            '  vP = transformed;',
            '  vFade = 1.0 - smoothstep(0.85, 1.20,',
            '      length(fxz) / uHalf);',
        ].join('\n'),
        fragmentHead: 'uniform vec3 uColor; uniform vec3 uLight;'
            + ' uniform float uAlpha;'
            + ' uniform float uLen; uniform float uWisp;'
            + ' uniform float uNear; uniform float uFall;'
            + ' uniform float uSpacing; uniform float uSeg;',
        fragmentMain: [
            '  vec2 e = abs(vUv * 2.0 - 1.0);',
            // Denser at the foot and gone into the cloud at the top —
            // and soft on both flanks, because a rim IS the card.
            '  float prof = mix(1.0, 0.45, vUv.y)',
            '      * smoothstep(0.0, 0.16, vUv.y)',
            '      * (1.0 - smoothstep(0.76, 1.0, vUv.y))',
            '      * (1.0 - smoothstep(0.30, 1.0, e.x));',
            '  float near = smoothstep(uNear * 0.3, uNear,',
            '      length(vP - uCameraLocal));',
            '  float cap = uAlpha * vBright * vFade * prof * near;',
            '  if (cap < 0.002) discard;',
            '  float wx = (vUv.x - 0.5) * 2.0 * vHalfW;',
            '  float wy = vUv.y * uLen + uTime * uFall;',
            // Stretched 8x along the fall. Round blotches are what
            // makes a pale mass read as steam; rain is drawn out into
            // bands that run the whole way down the shaft.
            '  vec2 q = vec2(wx / uWisp, wy / (uWisp * 8.0)) + vSeed;',
            '  float band = astraFbm2(q, 3) * 0.72',
            '      + astraNoise2(q * 3.1 + vec2(0.0, 4.0)) * 0.28;',
            // GUSTS, metres across, under the centimetre bands: a real
            // curtain is thick in patches and thin between them, and
            // without that the sheet is flat everywhere the filaments
            // are not, which is what makes the filaments read as
            // scratches on the ridge instead of rain in front of it.
            // Centred on 1.0, so `density` still means what it says.
            '  float gust = astraFbm2(q * 0.21 + vec2(5.3, 1.7), 2);',
            '  float body = mix(0.42, 1.0, smoothstep(0.20, 0.78, band))',
            '      * mix(0.68, 1.28, smoothstep(0.14, 0.64, gust));',
            // Filaments travelling down the face: without them a sheet
            // this size is a painted gradient and reads as a backdrop.
            // Thin (0.10 of the spacing, not 0.16) so `astraStroke`'s
            // own kill can still retire them at distance, never fully
            // off between heads, and coupled to `body` — a filament in
            // a thin part of the curtain has nothing to be made of.
            '  float ux = wx / uSpacing + 0.65 * astraNoise2(vec2(wx * .11, wy * .045) + vSeed);',
            '  float d = fract(wy / uSeg + astraStagger(floor(ux) + vSeed));',
            '  float fil = astraStroke(ux, 0.12)',
            '      * (0.12 + 0.38 * exp(-d * 4.5)) * body;',
            '  float a = cap * body * (1.0 + 0.66 * fil);',
            // Hue variance inside one curtain: broad warm/cool patches
            // across its face, plus a per-sheet offset, so a stack of
            // them is weather rather than one flat grey wash.
            '  vec3 c = astraHueBreak(uColor, q, 0.34, 0.32);',
            '  float hue = astraHash11(vSeed * 0.37) * 2.0 - 1.0;',
            '  c *= 1.0 + hue * vec3(0.06, 0.012, -0.065);',
            // Dither: a curtain is a wide, shallow alpha ramp over a
            // smooth sky, which is exactly where 8-bit output bands.
            '  a += (astraHash21(gl_FragCoord.xy) - 0.5) * 0.006;',
            '  if (a < 0.003) discard;',
            '  gl_FragColor = vec4(c * uLight * (0.96 + 0.11 * fil),',
            '      clamp(a, 0.0, 1.0));',
        ].join('\n'),
        transparent: true,
        depthWrite: false,
        side: THREE.DoubleSide,
    });
}

/**
 * Snow: flakes that fall slowly and WANDER, not white rain.
 *
 * A raindrop falls at 9 m/s down a straight line; a flake falls at well
 * under one and its horizontal path is a curve that goes nowhere in
 * particular. That wander is the entire difference between snow and
 * white rain, so it is not decoration here — every flake carries its
 * own two swing frequencies and phases, and the CPU mirror runs the
 * same curve the vertex shader does. The second half is the lens:
 * flakes nearer than the focus distance are drawn BIGGER and softer,
 * with their alpha divided by the same factor, because a camera
 * focussed on the valley cannot hold a flake at arm's length and a
 * field of uniformly crisp dots reads as a particle system.
 *
 * BLENDING: ORDINARY, for the same reason as the rain — snow scatters,
 * and white added to a bright winter frame has nowhere to go.
 *
 * @param {object} [opts]
 *   `extent` metres square the field covers (default 40); `height`
 *   metres it stands (default 18); `rate` flakes reaching the ground
 *   per second across the whole field (default 260 — the count is
 *   solved from it and the fall time, so there is no particle budget to
 *   guess); `size` flake diameter in metres (default 0.035, which also
 *   sets how fast they fall and how far they swing); `drift` wind in
 *   m/s, a number (along +x), [dx, dz] or a THREE.Vector2/3 (default
 *   [0.35, 0] — snow drifts, it does not race); `color` THREE.Color or
 *   hex, the flake albedo the scene's light multiplies (default a cold
 *   white that takes the fog's hue); `seed` PRNG seed (default 9).
 * @returns {THREE.Group} Named `Snowfall`, its foot on y = 0, with
 *   `userData.tick(t)` and `userData.sample(i, t)` returning flake i's
 *   group-local position — the CPU mirror of the vertex shader.
 */
export function makeSnowfall(opts = {}) {
    const extent = Math.max(2, opts.extent === undefined ? 40 : opts.extent);
    const height = Math.max(1, opts.height === undefined ? 18 : opts.height);
    const rate = Math.max(1, opts.rate === undefined ? 260 : opts.rate);
    const size = Math.max(0.002,
        opts.size === undefined ? 0.035 : opts.size);
    const drift = toWind(opts.drift, 0.35, 0);
    const color = new THREE.Color(
        opts.color === undefined ? 0xf2f7ff : opts.color);
    const rnd = lehmer(opts.seed === undefined ? 9 : opts.seed);
    const autoTint = opts.color === undefined ? color : null;

    // A bigger flake carries more mass per unit drag, so it falls
    // faster and swings wider; both are read off `size`.
    const fall = 0.45 + 6 * size;
    const wob = 0.22 + 4.5 * size;
    const half = extent * 0.5;
    const count = Math.round(Math.min(9000,
        Math.max(120, rate * (height / fall))));

    const pos = new Float32Array(count * 3);
    const flake = new Float32Array(count * 4);
    const swing = new Float32Array(count * 4);
    let scale = 0;
    for (let i = 0; i < count; i++) {
        pos[i * 3] = (rnd() - 0.5) * extent;
        pos[i * 3 + 1] = rnd() * height;
        pos[i * 3 + 2] = (rnd() - 0.5) * extent;
        flake[i * 4] = 0.6 + 0.9 * rnd();
        flake[i * 4 + 1] = 0.65 + 0.8 * rnd();
        flake[i * 4 + 2] = 0.55 + 0.6 * rnd();
        flake[i * 4 + 3] = rnd();
        swing[i * 4] = 0.35 + 0.95 * rnd();
        swing[i * 4 + 1] = rnd() * 6.283;
        swing[i * 4 + 2] = 0.35 + 0.95 * rnd();
        swing[i * 4 + 3] = rnd() * 6.283;
        scale = Math.max(scale, flake[i * 4]);
    }
    const focus = Math.max(2, extent * 0.10);
    const bokeh = 2.4;
    // The wrap keeps every flake inside the box, so the only thing the
    // sphere has to add is the widest a quad can be drawn.
    const radius = Math.hypot(half * Math.SQRT2, height)
        + size * scale * (1 + bokeh) * Math.SQRT1_2;

    const geom = instancedQuad(count, 1, 1, radius * 1.02);
    geom.setAttribute('aPos', new THREE.InstancedBufferAttribute(pos, 3));
    geom.setAttribute('aFlake', new THREE.InstancedBufferAttribute(flake, 4));
    geom.setAttribute('aSwing', new THREE.InstancedBufferAttribute(swing, 4));

    const mesh = new THREE.Mesh(geom, snowMaterial({
        color, size, fall, wob, drift, extent, half, height, focus, bokeh,
    }));
    mesh.name = 'Snowflakes';
    mesh.renderOrder = 3;
    const g = new THREE.Group();
    g.name = 'Snowfall';
    g.add(mesh);
    keepOutOfDepthPasses(g);
    _litByScene(mesh, autoTint);
    g.userData.update = g.userData.tick = (t) => tickShaders(g, t);
    g.userData.sample = (i, t) => {
        const k = Math.min(count - 1, Math.max(0, i | 0));
        const y = pos[k * 3 + 1] - fall * flake[k * 4 + 1] * t;
        return new THREE.Vector3(
            _wrap(pos[k * 3] + drift.x * t
                + wob * wobX(swing[k * 4], swing[k * 4 + 1], t),
                  extent, half),
            y - Math.floor(y / height) * height,
            _wrap(pos[k * 3 + 2] + drift.y * t
                + wob * wobZ(swing[k * 4 + 2], swing[k * 4 + 3], t),
                  extent, half));
    };
    return attachDisposal(g, snapshotResources(g));
}

/** Slow fall, wandering path, and a lens that cannot hold the near ones. */
function snowMaterial(cfg) {
    return makeShaderMaterial({
        name: 'SnowflakeField',
        uniforms: {
            uTime: { value: 0 },
            uColor: { value: cfg.color },
            uLight: { value: new THREE.Color(1, 1, 1) },
            uAlpha: { value: 0.85 },
            uSize: { value: cfg.size },
            uFall: { value: cfg.fall },
            uWob: { value: cfg.wob },
            uDrift: { value: cfg.drift },
            uExtent: { value: cfg.extent },
            uHalf: { value: cfg.half },
            uHeight: { value: cfg.height },
            uFocus: { value: cfg.focus },
            uBokeh: { value: cfg.bokeh },
        },
        varyings: 'varying vec2 vUv; varying float vBright;'
            + ' varying float vBlur; varying float vFade;'
            + ' varying float vHue; varying float vTumble;',
        vertexHead: 'attribute vec3 aCorner; attribute vec3 aPos;'
            + ' uniform float uTime;'
            + ' attribute vec4 aFlake; attribute vec4 aSwing;'
            + ' uniform float uSize; uniform float uFall;'
            + ' uniform float uWob; uniform vec2 uDrift;'
            + ' uniform float uExtent; uniform float uHalf;'
            + ' uniform float uHeight; uniform float uFocus;'
            + ' uniform float uBokeh;\n' + _WOB_GLSL,
        vertexMain: [
            '  vUv = uv;',
            '  vBright = aFlake.z;',
            // aFlake.w was spare. A field of flakes at ONE white is the
            // give-away that they came out of a loop: real ones catch
            // the sun or only the sky, and they TUMBLE — face, edge,
            // face — which is a slow breath of brightness, not a
            // sparkle. Both ride this one per-flake number.
            '  vHue = aFlake.w * 2.0 - 1.0;',
            '  vTumble = 0.74 + 0.26 * abs(sin(uTime * (0.7 + aFlake.w)',
            '      + aFlake.w * 6.283));',
            // Its own fall speed, so the field never descends as a
            // sheet, and its own two swings, which is the wander.
            '  float y = mod(aPos.y - uFall * aFlake.y * uTime, uHeight);',
            '  vec2 w = vec2(astraVeilWobX(aSwing.x, aSwing.y, uTime),',
            '      astraVeilWobZ(aSwing.z, aSwing.w, uTime));',
            '  vec2 xz = mod(aPos.xz + uDrift * uTime + uWob * w + uHalf,',
            '      uExtent) - uHalf;',
            '  vec3 p = vec3(xz.x, y, xz.y);',
            // Nearer than the focus distance: bigger and blurrier, the
            // way a lens focussed on the valley renders arm's length.
            '  vBlur = 1.0 - smoothstep(0.0, uFocus,',
            '      length(p - uCameraLocal));',
            '  float s = uSize * aFlake.x * (1.0 + uBokeh * vBlur);',
            '  vec3 camR = uCameraRight;',
            '  vec3 camU = uCameraUp;',
            '  transformed = p + camR * (aCorner.x * s)',
            '      + camU * (aCorner.y * s);',
            '  float vy = y / uHeight;',
            '  vFade = smoothstep(0.0, 0.05, vy)',
            '      * (1.0 - smoothstep(0.86, 1.0, vy))',
            '      * (1.0 - smoothstep(0.74, 1.02, length(xz) / uHalf));',
        ].join('\n'),
        fragmentHead: 'uniform vec3 uColor; uniform vec3 uLight;'
            + ' uniform float uAlpha;',
        fragmentMain: [
            '  float d = length(vUv - 0.5) * 2.0;',
            '  if (d > 1.0) discard;',
            // In focus it is a disc with a soft rim; out of focus it is
            // a smear, and the same factor takes its alpha away.
            '  float core = 1.0 - smoothstep(mix(0.42, 0.0, vBlur),',
            '      1.0, d);',
            '  float a = uAlpha * vBright * vFade * core * vTumble',
            '      / (1.0 + 2.4 * vBlur);',
            '  a += (astraHash21(gl_FragCoord.xy) - 0.5) * 0.006;',
            '  if (a < 0.004) discard;',
            // Snow is white the way paper is white: the ones the sun
            // reaches are warm, the ones only the sky reaches are blue.
            '  vec3 c = uColor * (1.0 + vHue * vec3(0.055, 0.008, -0.06));',
            '  gl_FragColor = vec4(c * uLight, clamp(a, 0.0, 1.0));',
        ].join('\n'),
        transparent: true,
        depthWrite: false,
        side: THREE.DoubleSide,
    });
}

/**
 * Dust or pollen hanging in a shaft of light.
 *
 * What this buys is the statement that the air is not empty — the thing
 * a light shaft, a barn interior or a summer meadow has and a rendered
 * one never does. The constraint is that motes must not read as
 * particles: they hang almost still (a few centimetres a second of
 * settle, a bounded wander that never leaves the stated volume) and
 * they TWINKLE, because a speck of matter is a flake with faces, and
 * what the eye actually catches is the flash as one comes round to the
 * light. A speck at constant brightness reads as a dot on the lens.
 *
 * BLENDING: ORDINARY, though a mote is the one of the three that is
 * arguably light. It is a speck of MATTER, and additive specks over a
 * sunlit wall clip; the glint rides alpha up to a near-white instead.
 *
 * @param {object} [opts]
 *   `extent` metres square the volume covers (default 6); `height`
 *   metres it stands (default 4); `count` specks (default 320 — a
 *   shaft of light holds surprisingly few, and a fog of them reads as
 *   noise); `size` speck diameter in metres (default 0.024 — it is the
 *   glint that has to be visible, not the grain); `color` THREE.Color
 *   or hex (default a warm near-white — the dust's own colour, spread
 *   ochre to grey per speck and then multiplied by the light actually
 *   reaching the volume, lamps included); `seed` PRNG seed
 *   (default 13).
 * @returns {THREE.Group} Named `Motes`, its foot on y = 0, with
 *   `userData.tick(t)`, `userData.sample(i, t)` giving speck i's
 *   group-local position and `userData.volume` the box it stays in.
 */
export function makeMotes(opts = {}) {
    const extent = Math.max(0.5, opts.extent === undefined ? 6 : opts.extent);
    const height = Math.max(0.3, opts.height === undefined ? 4 : opts.height);
    const count = Math.round(Math.min(4000,
        Math.max(8, opts.count === undefined ? 320 : opts.count)));
    const size = Math.max(0.001,
        opts.size === undefined ? 0.024 : opts.size);
    const color = new THREE.Color(
        opts.color === undefined ? 0xfff2d8 : opts.color);
    const rnd = lehmer(opts.seed === undefined ? 13 : opts.seed);

    const half = extent * 0.5;
    // Dust does not fall so much as fail to stay up.
    const settle = 0.035;
    const wob = Math.min(0.35, extent * 0.12);
    // Start inside the volume by the whole swing, so the wander can
    // never carry a speck out of the box the caller was promised.
    const inner = Math.max(0, half - wob);

    const pos = new Float32Array(count * 3);
    const mote = new Float32Array(count * 4);
    const swing = new Float32Array(count * 4);
    let scale = 0;
    for (let i = 0; i < count; i++) {
        pos[i * 3] = (rnd() * 2 - 1) * inner;
        pos[i * 3 + 1] = rnd() * height;
        pos[i * 3 + 2] = (rnd() * 2 - 1) * inner;
        mote[i * 4] = 0.5 + 1.5 * rnd();
        mote[i * 4 + 1] = 0.5 + 1.3 * rnd();
        mote[i * 4 + 2] = rnd() * 6.283;
        mote[i * 4 + 3] = 0.55 + 0.6 * rnd();
        swing[i * 4] = 0.12 + 0.34 * rnd();
        swing[i * 4 + 1] = rnd() * 6.283;
        swing[i * 4 + 2] = 0.12 + 0.34 * rnd();
        swing[i * 4 + 3] = rnd() * 6.283;
        scale = Math.max(scale, mote[i * 4]);
    }
    const radius = Math.hypot(half * Math.SQRT2, height)
        + size * scale * 1.2 * Math.SQRT1_2;

    const geom = instancedQuad(count, 1, 1, radius * 1.02);
    geom.setAttribute('aPos', new THREE.InstancedBufferAttribute(pos, 3));
    geom.setAttribute('aMote', new THREE.InstancedBufferAttribute(mote, 4));
    geom.setAttribute('aSwing', new THREE.InstancedBufferAttribute(swing, 4));

    const mesh = new THREE.Mesh(geom, moteMaterial({
        color, size, settle, wob, height,
    }));
    mesh.name = 'DustMotes';
    mesh.renderOrder = 3;
    const g = new THREE.Group();
    g.name = 'Motes';
    g.add(mesh);
    keepOutOfDepthPasses(g);
    // No fog tint for dust: motes are LIT matter close to the camera,
    // not haze at distance, and the colour is the caller's statement
    // about the light they hang in.
    _litByScene(mesh, null);
    g.userData.update = g.userData.tick = (t) => tickShaders(g, t);
    g.userData.volume = { extent, height };
    g.userData.sample = (i, t) => {
        const k = Math.min(count - 1, Math.max(0, i | 0));
        const y = pos[k * 3 + 1] - settle * t;
        return new THREE.Vector3(
            pos[k * 3] + wob * wobX(swing[k * 4], swing[k * 4 + 1], t),
            y - Math.floor(y / height) * height,
            pos[k * 3 + 2] + wob * wobZ(swing[k * 4 + 2],
                swing[k * 4 + 3], t));
    };
    return attachDisposal(g, snapshotResources(g));
}

/** Near-still specks that flash as their faces come round. */
function moteMaterial(cfg) {
    return makeShaderMaterial({
        name: 'DustMotes',
        uniforms: {
            uTime: { value: 0 },
            uColor: { value: cfg.color },
            uLight: { value: new THREE.Color(1, 1, 1) },
            uAlpha: { value: 0.95 },
            uSize: { value: cfg.size },
            uSettle: { value: cfg.settle },
            uWob: { value: cfg.wob },
            uHeight: { value: cfg.height },
            uSpin: { value: 1.6 },
        },
        varyings: 'varying vec2 vUv; varying float vBright;'
            + ' varying float vTw; varying float vFade;'
            + ' varying float vHue;',
        vertexHead: 'attribute vec3 aCorner; attribute vec3 aPos;'
            + ' uniform float uTime;'
            + ' attribute vec4 aMote; attribute vec4 aSwing;'
            + ' uniform float uSize; uniform float uSettle;'
            + ' uniform float uWob; uniform float uHeight;'
            + ' uniform float uSpin;\n' + _WOB_GLSL,
        vertexMain: [
            '  vUv = uv;',
            '  vBright = aMote.w;',
            // Dust is not white. A shaft holds grain, pollen, ash and
            // fibre at once, and their spread runs ochre to cool grey —
            // one flat tint is the tell that this is a particle system.
            '  vHue = astraHash11(aMote.z * 4.7 + aMote.y) * 2.0 - 1.0;',
            '  float y = mod(aPos.y - uSettle * uTime, uHeight);',
            '  vec3 p = vec3(',
            '      aPos.x + uWob * astraVeilWobX(aSwing.x, aSwing.y, uTime),',
            '      y,',
            '      aPos.z + uWob * astraVeilWobZ(aSwing.z, aSwing.w,',
            '          uTime));',
            // A mote is a flake with faces: it flashes when one comes
            // round to the light and all but vanishes edge-on.
            '  vTw = pow(abs(sin(uTime * uSpin * aMote.y + aMote.z)), 5.0);',
            '  float s = uSize * aMote.x * (0.45 + 0.75 * vTw);',
            '  vec3 camR = uCameraRight;',
            '  vec3 camU = uCameraUp;',
            '  transformed = p + camR * (aCorner.x * s)',
            '      + camU * (aCorner.y * s);',
            '  float vy = y / uHeight;',
            '  vFade = smoothstep(0.0, 0.06, vy)',
            '      * (1.0 - smoothstep(0.90, 1.0, vy));',
        ].join('\n'),
        fragmentHead: 'uniform vec3 uColor; uniform vec3 uLight;'
            + ' uniform float uAlpha;',
        fragmentMain: [
            '  float d = length(vUv - 0.5) * 2.0;',
            '  if (d > 1.0) discard;',
            // A bright centre inside a wide soft skirt, not a disc with
            // an edge: a speck at this size is a point spread, and a
            // hard rim is what makes a field of them read as a dot
            // screen printed on the lens.
            '  float core = (1.0 - smoothstep(0.16, 1.0, d))',
            '      * (0.55 + 0.45 * (1.0 - smoothstep(0.0, 0.42, d)));',
            '  float a = uAlpha * vBright * vFade * core',
            '      * (0.18 + 0.82 * vTw);',
            '  a += (astraHash21(gl_FragCoord.xy) - 0.5) * 0.006;',
            '  if (a < 0.004) discard;',
            '  vec3 c = uColor * (1.0 + vHue * vec3(0.10, 0.015, -0.14));',
            '  gl_FragColor = vec4(c * uLight * (0.78 + 0.44 * vTw),',
            '      clamp(a, 0.0, 1.0));',
        ].join('\n'),
        transparent: true,
        depthWrite: false,
        side: THREE.DoubleSide,
    });
}

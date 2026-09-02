/**
 * A river whose surface flows along its OWN course.
 *
 * Scroll a UV on a curved ribbon and the whole sheet translates one
 * way: round a bend the water keeps going the way it went before the
 * bend, which is the single tell that turns a river back into a moving
 * texture. So the ribbon carries a flow field of its own — the
 * centreline tangent times the local speed, per vertex — and the
 * fragment samples its noise in a frame ADVECTED along that field,
 * cross-fading two phases half a period apart so the shear of a
 * curving flow never stretches the pattern without bound.
 *
 * The channel is read from the same field: pale where the water runs
 * thin over a bank or a rock, deep mid-channel, broken white where it
 * narrows (the same water through a smaller gap runs faster) or where
 * something stands proud of the bed. Depth and rocks are measured on
 * the CPU from `heightAt` and handed over as attributes — GLSL_UTIL's
 * value noise is not the Perlin field the terrain is built from, so
 * the shader could never find those rocks itself.
 *
 * NOT a second reflective surface: `water.js` owns the scene's one RTT
 * plane, and this ribbon lies 12 mm above the water it joins.
 */

import * as THREE from 'three';

import { makeShaderMaterial, tickShaders } from './shader.js';

// Above the surface it lies on: clears z-fighting with a coplanar
// water plane, and stays inside the 2 cm an asset's base is allowed.
const LIFT = 0.012;

// Metres of course per row, and columns across the channel.
const ROW_M = 0.55;
const COLS = 15;

// Metres a broken patch keeps foaming downstream of what broke it.
const WAKE_M = 3.0;

// Metres of sideways drift per unit curvature per metre travelled:
// surface water at a bend crowds the outer bank (helical secondary
// flow), which is what stops the advection being one rigid scroll.
const DRIFT = 0.8;

// The day rig's sun (azimuth 35, elevation 48), same as water.js, so a
// river that was not told where the sun is still agrees with it.
const _AZ = 35 * Math.PI / 180;
const _EL = 48 * Math.PI / 180;
const DAY_SUN = new THREE.Vector3(
    Math.cos(_EL) * Math.cos(_AZ), Math.sin(_EL),
    Math.cos(_EL) * Math.sin(_AZ)).normalize();

// The day rig's own intensities, used only to normalise what is read
// off a scene: a key light at 5.4 and a fill at 1.4 are "full daylight",
// and a 2.2 moon has to arrive as 0.41 of it or the night river glows.
const DAY_KEY = 5.4;
const DAY_FILL = 1.4;

/**
 * Take the light from the scene the ribbon was added to.
 *
 * The surface is UNLIT — it writes its own light out — so every colour
 * in it is a decision, and three daylight constants baked into a
 * factory are three ways for a river to disagree with the scene around
 * it. Measured on this harness's night rig: with the defaults the
 * channel came back at luminance 0.40/0.54 over a frame whose mean was
 * 0.21/0.31 — a strip of white plastic lying in the dark. So the
 * material reads the key light (direction, colour, strength), the sky
 * half of the hemisphere fill, and the fog, at the first render.
 * Anything the CALLER passed wins and is never overwritten.
 */
function adoptSceneLight(mesh, u, given) {
    let done = false;
    mesh.onBeforeRender = (renderer, scene) => {
        if (done || !scene || !scene.isScene) return;
        done = true;
        let key = null, hemi = null;
        scene.traverse((o) => {
            if (o.isDirectionalLight && o.visible && o.intensity > 0
                && (!key || o.intensity > key.intensity)) key = o;
            if (o.isHemisphereLight && o.visible && o.intensity > 0
                && (!hemi || o.intensity > hemi.intensity)) hemi = o;
        });
        if (key) {
            if (!given.sunDir) {
                const a = key.getWorldPosition(new THREE.Vector3());
                const b = key.target
                    ? key.target.getWorldPosition(new THREE.Vector3())
                    : new THREE.Vector3();
                const d = a.sub(b);
                if (d.lengthSq() > 1e-9) u.uSun.value.copy(d.normalize());
            }
            if (!given.sunColor) {
                u.uSunCol.value.copy(key.color).multiplyScalar(
                    Math.min(1.35, Math.max(0.12, key.intensity / DAY_KEY)));
            }
        }
        // What the water reflects is the SKY, not the haze: the
        // hemisphere's sky half is that colour in one number, and the
        // fog is the fallback for a scene lit some other way.
        const skyScale = hemi
            ? Math.min(1.4, Math.max(0.3, hemi.intensity / DAY_FILL)) : 1;
        if (!given.sky && (hemi || scene.fog)) {
            u.uSky.value.copy(hemi ? hemi.color : scene.fog.color)
                .multiplyScalar(skyScale);
        }
        if (!given.ambient && hemi) {
            u.uAmb.value.copy(hemi.color).lerp(hemi.groundColor, 0.35)
                .multiplyScalar(skyScale);
        } else if (!given.ambient && scene.fog) {
            u.uAmb.value.copy(scene.fog.color);
        }
    };
}

const DEFAULT_COURSE = [
    [-16, 0, -13], [-7, 0, -6], [0, 0, 1], [-2, 0, 9], [3, 0, 16],
];

/** Deterministic 0..1 stream — no Math.random in a shipped factory. */
function rng(seed) {
    let s = (seed >>> 0) || 1;
    return () => ((s = (s * 16807) % 2147483647) / 2147483647);
}

/** GLSL's smoothstep, for the parts measured on the CPU. */
function smooth01(e0, e1, x) {
    const t = Math.max(0, Math.min(1, (x - e0) / (e1 - e0 || 1e-6)));
    return t * t * (3 - 2 * t);
}

/** Accept [x, y, z], [x, z] or Vector3 waypoints, all as Vector3. */
function toCourse(points) {
    const ok = Array.isArray(points) && points.length >= 2;
    if (points !== undefined && !ok) {
        console.warn('makeRiver: `points` needs two or more waypoints '
            + '— falling back to the default course.');
    }
    const src = ok ? points : DEFAULT_COURSE;
    return src.map((p) => (p && p.isVector3 ? p.clone()
        : new THREE.Vector3(p[0], p.length > 2 ? p[1] : 0,
                            p.length > 2 ? p[2] : p[1])));
}

/**
 * The ribbon: positions, the flow field, and the channel it measured.
 *
 * `aFlow` is the world flow VELOCITY (unit centreline tangent times
 * the local speed) and `aChan` is (metres across, water depth, how
 * broken, sideways drift) — everything the fragment needs that only
 * the CPU can know.
 */
function channelGeometry(course, o) {
    const curve =
        new THREE.CatmullRomCurve3(course, false, 'catmullrom', 0.5);
    const len = Math.max(curve.getLength(), 0.5);
    const rows = Math.max(8, Math.min(400, Math.round(len / ROW_M)));
    const ds = len / rows;
    const p1 = o.rand() * 6.2832;
    const p2 = o.rand() * 6.2832;
    const widthAt =
        typeof o.width === 'function' ? o.width : () => o.width;

    const cp = [], ct = [], cw = [], cs = [], cd = [];
    let wSum = 0;
    for (let j = 0; j <= rows; j++) {
        const u = j / rows;
        const t = curve.getTangentAt(u);
        t.y = 0;
        if (t.lengthSq() < 1e-9) t.set(1, 0, 0);
        cp.push(curve.getPointAt(u));
        ct.push(t.normalize());
        // The banks are not ruled lines; two seeded tones wander them.
        cw.push(Math.max(0.25, widthAt(u)
            * (1 + 0.055 * Math.sin(u * 7.3 + p1)
                 + 0.035 * Math.sin(u * 15.9 + p2))));
        wSum += cw[j];
    }
    const wRef = wSum / (rows + 1);
    for (let j = 0; j <= rows; j++) {
        // Flux: the same water through a smaller gap runs faster,
        // which is what makes a narrows both race and break.
        cs.push(o.flow * Math.max(0.5, Math.min(2.4, wRef / cw[j])));
    }
    for (let j = 0; j <= rows; j++) {
        const a = ct[Math.max(0, j - 1)];
        const b = ct[Math.min(rows, j + 1)];
        const dth = Math.atan2(b.z, b.x) - Math.atan2(a.z, a.x);
        const span = ds * (Math.min(rows, j + 1) - Math.max(0, j - 1));
        // +s is right of travel and a positive turn is to the right,
        // so the outer bank of that turn is -s.
        cd.push(-Math.atan2(Math.sin(dth), Math.cos(dth)) / span
            * cs[j] * DRIFT);
    }

    const nV = (rows + 1) * COLS;
    const pos = new Float32Array(nV * 3);
    const uv = new Float32Array(nV * 2);
    const flow = new Float32Array(nV * 3);
    const chan = new Float32Array(nV * 4);
    const bed = o.heightAt;
    const row = new Float32Array(COLS);
    for (let j = 0; j <= rows; j++) {
        const p = cp[j], t = ct[j], w = cw[j];
        const sx = -t.z, sz = t.x;
        const narrow = smooth01(1.10, 1.55, cs[j] / o.flow);
        // The bed this row is measured against is its own deepest
        // point: a boulder ON the centreline must not raise the datum
        // it is a boulder above.
        let bedC = 0;
        for (let i = 0; bed && i < COLS; i++) {
            const s = ((i / (COLS - 1)) * 2 - 1) * w * 0.5;
            row[i] = bed(p.x + sx * s, p.z + sz * s);
            bedC = i ? Math.min(bedC, row[i]) : row[i];
        }
        for (let i = 0; i < COLS; i++) {
            const q = (i / (COLS - 1)) * 2 - 1;
            const s = q * w * 0.5;
            const x = p.x + sx * s, z = p.z + sz * s;
            const k = j * COLS + i;
            pos[k * 3] = x;
            pos[k * 3 + 1] = p.y + LIFT;
            pos[k * 3 + 2] = z;
            uv[k * 2] = i / (COLS - 1);
            uv[k * 2 + 1] = j / rows;
            flow[k * 3] = t.x * cs[j];
            flow[k * 3 + 2] = t.z * cs[j];
            // Whatever stands proud of that bed is a rock: the water
            // over it runs thin, then breaks.
            const prot = bed ? Math.max(0, row[i] - bedC) : 0;
            const dp = o.depth * (1 - q * q);
            chan[k * 4] = s;
            chan[k * 4 + 1] = Math.max(0, dp - prot);
            chan[k * 4 + 2] = Math.max(narrow,
                smooth01(0.45 * o.depth, 0.95 * o.depth, prot));
            chan[k * 4 + 3] = cd[j];
        }
    }
    // Foam does not stop at the rock that made it — it streams away
    // downstream and dies over a few metres.
    const decay = Math.exp(-ds / WAKE_M);
    for (let j = 1; j <= rows; j++) {
        for (let i = 0; i < COLS; i++) {
            const k = (j * COLS + i) * 4 + 2;
            chan[k] = Math.max(chan[k], chan[k - COLS * 4] * decay);
        }
    }

    const idx = [];
    for (let j = 0; j < rows; j++) {
        for (let i = 0; i < COLS - 1; i++) {
            const a = j * COLS + i, b = a + 1, c = a + COLS;
            idx.push(a, b, c, b, c + 1, c);
        }
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    g.setAttribute('uv', new THREE.BufferAttribute(uv, 2));
    g.setAttribute('aFlow', new THREE.BufferAttribute(flow, 3));
    g.setAttribute('aChan', new THREE.BufferAttribute(chan, 4));
    g.setIndex(idx);
    g.computeVertexNormals();
    return { geometry: g, length: len, width: wRef };
}

/**
 * Build a river ribbon that follows `points` and flows along it.
 *
 * @param {object} [opts]
 *   `points` the course, [[x, y, z], ...] (or [[x, z], ...], or
 *   Vector3s) — y is the WATER SURFACE there, so a river that descends
 *   is just a polyline that descends; `width` channel width in metres,
 *   a number or f(u in 0..1) for a reach that narrows (default 6);
 *   `depth` mid-channel depth in metres, which is what the deep colour
 *   MEANS — this ribbon is a surface, not a solid (default 1.2);
 *   `flow` surface speed in m/s at the nominal width, driving both the
 *   advection and the foam (default 1.2); `heightAt` (x, z) => bed
 *   height, sampled per vertex to find what stands proud of the bed
 *   under the channel; `deep` / `shallow` / `foam` THREE.Color or
 *   hex; `sky` what the surface reflects at grazing angles; `sunDir`
 *   normalized Vector3 toward the sun — pass the key light's, or the
 *   glitter disagrees with the shadows (default: the day rig's);
 *   `sunColor` / `ambient` THREE.Color or hex overriding what is read
 *   off the scene; `seed` PRNG seed (default 5).
 *
 *   Everything about the LIGHT is optional because the ribbon reads it
 *   from the scene it is added to at the first render — key light
 *   direction, colour and strength, the sky half of the hemisphere
 *   fill, the fog — so a river dropped into a night scene goes dark
 *   with it. Pass any of them to override that scene reading.
 * @returns {THREE.Group} Named `River`, resting on the water surface
 *   (12 mm above the course's own y), with `userData.tick(t)` driving
 *   the flow. One ordinary transparent mesh: it must NOT be a second
 *   reflective Water, which would re-render the whole scene again.
 */
export function makeRiver(opts = {}) {
    const o = {
        width: opts.width === undefined ? 6 : opts.width,
        depth: opts.depth === undefined ? 1.2 : opts.depth,
        flow: opts.flow === undefined ? 1.2 : opts.flow,
        heightAt: typeof opts.heightAt === 'function'
            ? opts.heightAt : null,
        rand: rng(opts.seed === undefined ? 5 : opts.seed),
    };
    o.depth = Math.max(o.depth, 0.05);
    o.flow = Math.max(Math.abs(o.flow), 0.02);
    const built = channelGeometry(toCourse(opts.points), o);

    const g = new THREE.Group();
    g.name = 'River';
    const mat = riverMaterial(o, built, opts);
    const mesh = new THREE.Mesh(built.geometry, mat);
    mesh.name = 'Channel';
    mesh.renderOrder = 1;
    adoptSceneLight(mesh, mat.uniforms, opts);
    g.add(mesh);
    g.userData.tick = (t) => tickShaders(g, t);
    return g;
}

/** The surface: two-phase flow-map advection, depth, foam, glitter. */
function riverMaterial(o, built, opts) {
    // Cycles per metre. Across is tied to the channel so a brook and a
    // river both get ~6 streaks bank to bank; along is a sixth of it,
    // because structure runs ACROSS a flow, never ranked down it.
    const across = Math.max(0.35, Math.min(2.5, 6 / built.width));
    return makeShaderMaterial({
        name: 'RiverSurface',
        uniforms: {
            uDeep: { value: new THREE.Color(
                opts.deep === undefined ? 0x0e3f5c : opts.deep) },
            uShallow: { value: new THREE.Color(
                opts.shallow === undefined ? 0xa8c49a : opts.shallow) },
            uFoam: { value: new THREE.Color(
                opts.foam === undefined ? 0xeef4f5 : opts.foam) },
            uSky: { value: new THREE.Color(
                opts.sky === undefined ? 0x9cc3e2 : opts.sky) },
            // The key light's colour and the sky's own ambient, both
            // replaced by the scene's at the first render unless the
            // caller pinned them (see `adoptSceneLight`).
            uSunCol: { value: new THREE.Color(
                opts.sunColor === undefined ? 0xfff0d8 : opts.sunColor) },
            uAmb: { value: new THREE.Color(
                opts.ambient === undefined ? 0x9db8e8 : opts.ambient) },
            uSun: { value: (opts.sunDir ? opts.sunDir.clone()
                : DAY_SUN.clone()).normalize() },
            uFreq: { value: new THREE.Vector2(across, across * 0.16) },
            // Where this river's noise sits, and where its cycle
            // starts: two rivers in one scene must not beat together.
            uPhase: { value: new THREE.Vector2(
                o.rand() * 37, o.rand() * 37) },
            uLength: { value: built.length },
            // Metres of water it takes to swallow the bed. Absolute, so
            // a brook stays pale and a channel goes deep; normalising
            // by the river's own depth divides that difference out.
            // 1.1, not 2.4: a real river is 1-2 m deep, and at 2.4 the
            // whole of one rendered as its shallow colour — pale sage
            // bank to bank, with the ground washing through it.
            uOpaqueM: { value: 1.1 },
            // Seconds before the advected frame is reset. Long enough
            // to read as travel, short enough that the shear of a bend
            // has not yet smeared the pattern.
            uPeriod: { value: 2.6 },
            // 0.62, not 0.85: at 0.85 the finite-difference normals
            // swung ~37 degrees off vertical everywhere, so the tight
            // specular lobe fired over the WHOLE sheet and the channel
            // came back as crumpled foil (measured: 19-23% of the
            // channel below saturation 0.05). The crests are still
            // there; they are now facets, not a mirror.
            uBump: { value: 0.62 },
        },
        varyings: [
            'varying vec2 vUv; varying vec3 vN; varying vec3 vW;',
            'varying vec3 vFlow; varying vec4 vChan;',
        ].join('\n'),
        vertexHead: 'attribute vec3 aFlow;\nattribute vec4 aChan;',
        vertexMain: [
            '  vUv = uv;',
            '  vFlow = aFlow;',
            '  vChan = aChan;',
            // World, not view: the sun and the camera are both given
            // in world space below.
            '  vN = normalize(mat3(modelMatrix) * normal);',
            '  vW = (modelMatrix * vec4(transformed, 1.0)).xyz;',
        ].join('\n'),
        fragmentHead: [
            'uniform vec3 uDeep; uniform vec3 uShallow;',
            'uniform vec3 uFoam; uniform vec3 uSky; uniform vec3 uSun;',
            'uniform vec3 uSunCol; uniform vec3 uAmb;',
            'uniform vec2 uFreq; uniform vec2 uPhase;',
            'uniform float uLength; uniform float uOpaqueM;',
            'uniform float uPeriod; uniform float uBump;',
            // One sample of the surface, in the ribbon frame (metres
            // across, metres along). Anisotropic on purpose: the grain
            // of a current is lengthwise streaks.
            'float rvField(vec2 q) {',
            '  vec2 p = q * uFreq + uPhase;',
            '  float streak = astraFbm2(p, 3);',
            '  float chop = astraNoise2(p * vec2(2.9, 6.0) + uPhase.yx);',
            '  return streak + 0.42 * chop;',
            '}',
            // The flow map: the frame is carried along the flow and
            // reset every period, and two copies half a period apart
            // cross-fade, so a curving flow never stretches it without
            // bound. ph is (offset A, offset B, blend).
            'float rvWave(vec2 q, vec2 f, vec3 ph) {',
            '  return mix(rvField(q - f * ph.x),',
            '             rvField(q - f * ph.y), ph.z);',
            '}',
        ].join('\n'),
        fragmentMain: [
            '  vec2 T = vFlow.xz;',
            '  float sp = length(T);',
            '  T = sp > 1e-4 ? T / sp : vec2(1.0, 0.0);',
            '  vec2 S = vec2(-T.y, T.x);',
            // The ribbon's own frame: metres across, metres along.
            '  vec2 Q = vec2(vChan.x, vUv.y * uLength);',
            '  vec2 F = vec2(vChan.w, sp);',
            '  float k0 = fract(uTime / uPeriod + uPhase.x);',
            '  vec3 ph = vec3((k0 - 0.5) * uPeriod,',
            '      (fract(k0 + 0.5) - 0.5) * uPeriod,',
            '      abs(2.0 * k0 - 1.0));',
            '  float h = rvWave(Q, F, ph);',
            // Finite differences in the same frame: the crests are
            // built from the flow, so they run across it.
            '  float E = 0.06;',
            '  vec2 gr = vec2(rvWave(Q + vec2(E, 0.0), F, ph) - h,',
            '                 rvWave(Q + vec2(0.0, E), F, ph) - h) / E;',
            '  vec3 wg = vec3(S.x, 0.0, S.y) * gr.x',
            '          + vec3(T.x, 0.0, T.y) * gr.y;',
            '  vec3 n = normalize(vN - wg * uBump);',
            '  vec3 V = normalize(cameraPosition - vW);',
            '  float hn = clamp((h - 0.30) / 0.75, 0.0, 1.0);',
            '  float dep = clamp(vChan.y / uOpaqueM, 0.0, 1.0);',
            // The pale margin belongs to the shelving bank, not to half
            // the channel — but it is a RAMP, not a rim: at 0.34 the
            // colour saturated the moment the bed dropped away and the
            // whole channel was one value.
            '  vec3 c = mix(uShallow, uDeep, smoothstep(0.03, 0.62, dep));',
            // Broken colour, in the ribbon's OWN frame so the patches
            // travel with the reach: silt, weed and depth never leave a
            // channel one flat hue, and two scales keep it reading at
            // 20 m and at 2 m.
            '  c = astraHueBreak(c, Q, 0.16, 0.30);',
            '  c = astraHueBreak(c, Q, 0.75, 0.13);',
            // Unlit, so the light is written out — and it is written
            // out ONCE, as sky ambient plus key, so the body of the
            // water darkens with the scene instead of glowing at
            // daylight values under a moon. (The old `0.70 + 0.80*ndl`
            // was a pure geometry term: measured on this harness's
            // night rig, the channel came back at luminance 0.40/0.54
            // over a frame whose mean was 0.21/0.31.) The ambient half
            // never reaches zero, so no crest has a black shadow side.
            '  float ndl = max(dot(n, uSun), 0.0);',
            '  vec3 lit = uAmb * (0.62 + 0.34 * max(n.y, 0.0))',
            '           + uSunCol * 0.95 * ndl;',
            '  c *= lit;',
            // The sky comes back by REFLECTION, not by washing the
            // albedo: Schlick with water's own F0 (2%), so looking into
            // a river you see the river, and only a grazing view turns
            // it into a mirror. The old flat 0.22 + 0.50*fres put a
            // fifth of the sky over even the deepest pixel.
            '  float f5 = pow(1.0 - clamp(dot(n, V), 0.0, 1.0), 5.0);',
            '  float fr = clamp(0.02 + 0.98 * f5, 0.0, 0.90);',
            // What comes back is a sky GRADIENT sampled by the mirror
            // direction, not one number: a flat sky colour turned every
            // grazing view into a smooth wash with no wave left in it,
            // and the whole point of a reflection is that the crests
            // and the troughs look at different parts of the dome.
            '  vec3 R = reflect(-V, n);',
            '  float ry = clamp(R.y * 0.5 + 0.5, 0.0, 1.0);',
            '  vec3 skyC = mix(uAmb * 0.95, uSky * 1.14,',
            '      smoothstep(0.34, 0.78, ry));',
            '  c = mix(c, skyC * (0.90 + 0.22 * hn), fr);',
            '  vec3 H = normalize(uSun + V);',
            '  float nh = max(dot(n, H), 0.0);',
            // Glitter is POINTS on the water, not a sheen over all of
            // it: the tight lobe is gated by a coarse mask riding the
            // same advected frame, and both lobes are scaled by the
            // same Fresnel the reflection uses, so nothing specular
            // survives a look straight down into the channel.
            '  float glint = smoothstep(0.52, 0.90,',
            '      rvWave(Q * 5.0, F * 5.0, ph));',
            '  c += uSunCol * (0.05 + 0.95 * f5)',
            '     * (pow(nh, 60.0) * 0.07',
            '      + pow(nh, 420.0) * (0.15 + 2.60 * glint));',
            '  float brk = clamp(vChan.z, 0.0, 1.0);',
            '  float thin = 1.0 - smoothstep(0.02, 0.22, dep);',
            // Foam is torn, never a wash — and it travels, so the tear
            // rides the same advected frame three times finer.
            '  float tear = clamp((rvWave(Q * 3.0, F * 3.0, ph) - 0.30)',
            '      / 0.75, 0.0, 1.0);',
            // Capped at 0.9, and carried almost entirely by the TEAR:
            // a broken reach is white threads on water, not a lid over
            // it. The old floor of 0.25 meant every metre a wake
            // reached went a quarter white whatever the surface was
            // doing, and three boulders' wakes chained into one opaque
            // sheet that hid the whole lower river (measured: the
            // channel's saturation fell to 0.12 and a fifth of it was
            // colourless). The bank term is halved for the same reason.
            '  float foamK = clamp(brk * (0.16 + 1.20 * tear)',
            '      + thin * hn * 0.26, 0.0, 0.90);',
            // Aerated water is white the way snow is — LIT by the same
            // ambient and key as the water it came out of, not a lamp
            // buried in the river. Under a moon this is what keeps the
            // whitewater a soft grey-blue instead of blazing.
            '  vec3 foamC = uFoam * (0.84 + 0.26 * tear)',
            '      * (uAmb * 0.75 + uSunCol * 0.85 * ndl);',
            '  foamC = mix(foamC, uSky * 0.85, 0.18);',
            '  c = mix(c, foamC, foamK);',
            // Hands over to the bank and to whatever is upstream of it
            // instead of ending at a cut edge. The shallows are the
            // THINNEST water there is: letting the bed show through
            // them is where a channel gets its gravel colour.
            '  float edge = 1.0 - abs(2.0 * vUv.x - 1.0);',
            '  float a = mix(0.16, 0.94, smoothstep(0.0, 0.55, dep));',
            '  a = max(a, foamK * 0.95);',
            '  a *= smoothstep(0.0, 0.07, edge);',
            '  a *= smoothstep(0.0, 0.03, vUv.y)',
            '     * (1.0 - smoothstep(0.97, 1.0, vUv.y));',
            // A depth ramp that crosses tens of metres of channel is
            // exactly the gradient an 8-bit target bands.
            '  c += (astraHash21(gl_FragCoord.xy) - 0.5) * (1.6 / 255.0);',
            '  gl_FragColor = vec4(c, clamp(a, 0.0, 1.0));',
        ].join('\n'),
        transparent: true,
        depthWrite: false,
    });
}

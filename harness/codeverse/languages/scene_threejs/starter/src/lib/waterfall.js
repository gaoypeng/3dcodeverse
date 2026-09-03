/**
 * A waterfall that reads as one from every angle.
 *
 * Four things decide that, and none of them is the noise function.
 * The water leaves a lip as a PROJECTILE, so the sheet is a parabola
 * bulging downstream, not a plane — a plane collapses to a line the
 * moment the eye moves off its face. It has a CROSS-SECTION, so the
 * sheet is a closed shell swept along that parabola rather than stacked
 * cards. It ACCELERATES, so features ride a sqrt-of-drop age instead of
 * a linear scroll. And it is threads, not a wash: structure varies
 * hard ACROSS the sheet and barely along it, with neighbouring threads
 * decorrelated by several full turns or their fronts weave a
 * herringbone.
 *
 * A fifth thing decides whether it looks like WATER: how much air is in
 * it. At the lip the flow is coherent — a thin glass sheet you see the
 * cliff through, blue where it is thick and sky-coloured at grazing
 * angles. By the foot it is torn into white spray. Everything here that
 * is not geometry rides that one aeration ramp: colour, opacity, how
 * mirror-like the surface is, how much sun scatters THROUGH it. The
 * three materials are unlit, so all of that light is written out by
 * hand — and it is read off the scene the fall was added to
 * (`adoptSceneLight`), never baked in, or a waterfall glows at noon
 * values under a moon.
 *
 * Everything else — foam where it lands, mist off the impact, the
 * plunge pool — exists so the fall does not simply END at a seam.
 */

import * as THREE from 'three';

import {
    instancedQuad, makeShaderMaterial, sweepProfile, tickShaders, keepOutOfDepthPasses } from './shader.js';

// The rig this library ships (`environment.js`): a day key at 5.4 and a
// hemisphere fill at 1.4. Both are DIVISORS below, so what the material
// adopts is a light's strength RELATIVE to daylight, not its raw watts.
const DAY_KEY = 5.4;
const DAY_FILL = 1.4;
// Fallback key direction for a scene with no directional light at all.
const DAY_SUN = new THREE.Vector3(0.42, 0.72, 0.55).normalize();

/**
 * Build a waterfall, its plunge pool and its mist.
 *
 * @param {object} [opts]
 *   `height` drop in metres (default 8); `width` sheet width at the lip
 *   (default 3.2); `throw_` how far downstream it lands, defaults to a
 *   fifth of the height (water leaves a lip with the river's speed);
 *   `bow` curvature across the sheet in metres (default width/6, 0 for
 *   flat); `color` deep water colour; `foam` colour of the broken
 *   water; `mist` billboard count (default 55, 0 to omit); `pool` draw
 *   the plunge pool (default true); `seed` PRNG seed (default 7).
 *   The light is taken from the scene unless pinned here: `sunDir`
 *   (THREE.Vector3 toward the key), `sunColor`, `sky` what the water
 *   reflects at grazing angles, `ambient` the sky's own fill.
 * @returns {THREE.Group} Named `Waterfall`, resting on y = 0, with
 *   `userData.tick(t)` driving every shader in it.
 */
export function makeWaterfall(opts = {}) {
    const h = opts.height === undefined ? 8 : opts.height;
    const w = opts.width === undefined ? 3.2 : opts.width;
    const thr = opts.throw_ === undefined ? h * 0.2 : opts.throw_;
    const bow = opts.bow === undefined ? w / 6 : opts.bow;
    // Teal, not navy: the water at a lip is a metre of it seen edge-on,
    // and a metre of water is green-blue. 0x0d3550 multiplied by a sky
    // ambient came out as a black band under the foam.
    const deep = opts.color || new THREE.Color(0x11566b);
    const foam = opts.foam || new THREE.Color(0xdfeaf1);
    const nMist = opts.mist === undefined ? 55 : opts.mist;
    const seed = opts.seed === undefined ? 7 : opts.seed;

    // ONE light bundle, shared by the sheet, the pool and the mist:
    // three materials that disagree about where the sun is are three
    // separate objects standing in the same place.
    const light = {
        uSun: { value: (opts.sunDir ? opts.sunDir.clone()
            : DAY_SUN.clone()).normalize() },
        uSunCol: { value: new THREE.Color(
            opts.sunColor === undefined ? 0xfff0d8 : opts.sunColor) },
        uSky: { value: new THREE.Color(
            opts.sky === undefined ? 0x9cc3e2 : opts.sky) },
        uAmb: { value: new THREE.Color(
            opts.ambient === undefined ? 0x9db8e8 : opts.ambient) },
    };

    const g = new THREE.Group();
    g.name = 'Waterfall';

    // The sheet. t runs 0 at the lip to 1 at the pool; the fall is
    // t-squared in y and linear in z, which is what a projectile does.
    const sheet = new THREE.Mesh(
        sweepProfile(
            (t) => ({ x: 0, y: h - h * t * t, z: thr * t }),
            (a, t) => {
                const u = Math.cos(a);          // -1..1 across the sheet
                // Closes into the pool instead of ending as an open
                // tube, and the bow is a symmetric arc across the
                // width — an asymmetric one grew a beak at the lip.
                const k = Math.min(1, (1 - t) / 0.10);
                return {
                    x: u * w * 0.5,
                    y: 0,
                    z: Math.sin(a) * (0.18 + 1.5 * t) * (0.12 + 0.88 * k)
                       + bow * (1 - u * u),
                };
            },
            // 56 around, not 40: the silhouette of a closed shell is
            // the outline of its SECTION, and at 40 the near edge of
            // the sheet showed its facets as a 6 px staircase from 5 m.
            { nu: 56, nv: 64 }),
        waterMaterial(deep, foam, light));
    sheet.name = 'Sheet';
    sheet.renderOrder = 2;
    g.add(sheet);

    if (opts.pool !== false) {
        const disc = new THREE.Mesh(
            new THREE.CircleGeometry(w * 1.15, 48),
            poolMaterial(deep, foam, light));
        disc.rotation.x = -Math.PI / 2;
        disc.position.set(0, 0.02, thr);
        disc.name = 'PlungePool';
        disc.renderOrder = 1;
        g.add(disc);
    }

    if (nMist > 0) {
        g.add(mistCloud(nMist, w, thr, seed, light));
    }

    g.userData.tick = (t) => tickShaders(g, t);
    const built = keepOutOfDepthPasses(g);
    adoptSceneLight(sheet, light, {
        sunDir: opts.sunDir !== undefined,
        sunColor: opts.sunColor !== undefined,
        sky: opts.sky !== undefined,
        ambient: opts.ambient !== undefined,
    });
    return built;
}

/**
 * Take the light from the scene the fall was added to.
 *
 * All three materials here are UNLIT — every photon in them is written
 * out by hand — so a daylight constant baked into the factory is a way
 * for the water to disagree with the scene around it: on this
 * harness's night rig the old sheet came back at luminance 0.70 over a
 * frame whose mean was 0.12, a sheet of white plastic hanging in the
 * dark. The key light's direction, colour and strength, the sky half of
 * the hemisphere fill and the fog are read at the FIRST render, and
 * anything the caller pinned is never overwritten.
 *
 * `keepOutOfDepthPasses` has already claimed `onBeforeRender` on every
 * mesh to guard the override passes, so this CHAINS onto it rather than
 * replacing it — replacing it puts the sheet back into ambient
 * occlusion as a solid wall.
 */
function adoptSceneLight(mesh, u, given) {
    const prev = mesh.onBeforeRender;
    let done = false;
    mesh.onBeforeRender = (renderer, scene, camera, geometry, material,
        group) => {
        if (prev) prev(renderer, scene, camera, geometry, material, group);
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
        // Water reflects the SKY, not the haze between it and the
        // camera: the hemisphere's sky half is that colour in one
        // number, and the fog is the fallback for a scene lit some
        // other way.
        const skyScale = hemi
            ? Math.min(1.4, Math.max(0.3, hemi.intensity / DAY_FILL)) : 1;
        if (!given.sky && (hemi || scene.fog)) {
            u.uSky.value.copy(hemi ? hemi.color : scene.fog.color)
                .multiplyScalar(skyScale);
        }
        if (!given.ambient && hemi) {
            u.uAmb.value.copy(hemi.color).lerp(hemi.groundColor, 0.30)
                .multiplyScalar(skyScale);
        } else if (!given.ambient && scene.fog) {
            u.uAmb.value.copy(scene.fog.color);
        }
    };
}

/** The falling sheet: threads, acceleration, aeration, foam at the foot. */
function waterMaterial(deep, foam, light) {
    return makeShaderMaterial({
        name: 'WaterfallSheet',
        uniforms: Object.assign({
            uDeep: { value: deep.clone() },
            uFoam: { value: foam.clone() },
        }, light),
        varyings: 'varying vec2 vUv; varying vec3 vN; varying vec3 vW;',
        vertexMain: [
            '  vUv = uv;',
            // World, not view: both consumers below take a world-space
            // view vector, and normalMatrix is the modelVIEW inverse
            // transpose — mixing them keys the rim to the camera's yaw.
            '  vN = normalize(mat3(modelMatrix) * normal);',
            '  vW = (modelMatrix * vec4(transformed, 1.0)).xyz;',
        ].join('\n'),
        fragmentHead: [
            'uniform vec3 uDeep; uniform vec3 uFoam;',
            'uniform vec3 uSun; uniform vec3 uSunCol;',
            'uniform vec3 uSky; uniform vec3 uAmb;',
        ].join('\n'),
        fragmentMain: [
            // v runs 0 at the lip to 1 at the pool, so drop is v.
            '  float drop = clamp(vUv.y, 0.0, 1.0);',
            '  float age = astraFallAge(drop);',
            '  float stag = astraStagger(vUv.x);',
            // Threads: high frequency ACROSS the sheet, almost none
            // along it, or the strands read as ranks marching at you.
            '  float fil = astraStroke(vUv.x * 26.0 + stag * 0.08, 0.34);',
            '  float grain = 0.62 + 0.38 * astraStroke(vUv.x * 61.0',
            '      + stag * 0.4, 0.42);',
            // Travel: the thread pattern itself moves down, or only the
            // packet brightness changes and nothing reads as flowing.
            '  float travel = 0.42 + 0.58 * astraStroke(',
            '      drop * 6.5 - uTime * 2.4 + stag * 0.02, 0.38);',
            // Packets ride a constant rate in AGE, so they speed up.
            '  float pk = pow(0.5 + 0.5 * sin((age * 7.0 - uTime * 3.1)',
            '      * 6.2831 + stag), 3.0);',
            '  float body = 0.30 + 0.52 * fil * grain * travel',
            '      + 0.24 * pk;',
            // WHERE ON THE SECTION this fragment sits. u runs once round
            // the closed shell, so |sin| is 1 down the middle of either
            // face and 0 at the two side edges — and a fall frays at its
            // side edges long before its middle goes white.
            '  float faceMid = abs(sin(vUv.x * 6.2831));',
            '  float edge = 1.0 - faceMid;',
            // AERATION is the one ramp everything below rides. A lip is
            // a coherent glass sheet (blue, see-through, mirror-like at
            // grazing); a foot is white spray (opaque, matte, lit by
            // scatter). Making the whole sheet the second thing is what
            // turned this into a white curtain: measured on the close
            // camera, mean saturation 0.099 with 24.2% of the sheet's
            // pixels above 0.85 luminance — one flat value. With the
            // ramp: 0.118 and 0.14%.
            '  float aer = clamp(smoothstep(0.16, 0.94, drop)',
            '      * (0.45 + 0.55 * body) + 0.10 * pk',
            '      + 0.40 * edge * edge, 0.0, 1.0);',
            '  float foamK = smoothstep(0.86, 1.0, drop);',
            '  vec3 V = normalize(cameraPosition - vW);',
            // A closed shell shows its back face half the time, and a
            // normal pointing away from the eye dots to zero against
            // everything: face the one we can actually see.
            '  float far = dot(vN, V) < 0.0 ? 1.0 : 0.0;',
            '  vec3 n = normalize(vN) * (far > 0.5 ? -1.0 : 1.0);',
            '  float ndl = max(dot(n, uSun), 0.0);',
            // BROKEN COLOUR, across the threads rather than along them:
            // river water is never one hue, and neither is its foam —
            // silt warms it where it is thick, sky cools it where it is
            // thin. Two scales so it reads at 20 m and at 2 m.
            '  vec3 deepC = astraHueBreak(uDeep,',
            '      vec2(vUv.x * 22.0 + stag * 0.02, drop * 3.0), 1.0, 0.75);',
            '  deepC = astraHueBreak(deepC, vec2(vUv.x * 5.0, drop), 1.0,',
            '      0.30);',
            '  vec3 airC = astraHueBreak(uFoam,',
            '      vec2(vUv.x * 9.0, drop * 2.2 - uTime * 0.15), 1.0, 0.30);',
            '  vec3 c = mix(deepC, airC, aer);',
            '  c = mix(c, airC, foamK * 0.7);',
            // Which way a thread is turned decides its colour as much as
            // how much air is in it: the half of the shell turned toward
            // the key takes its warmth even in shade, the half turned
            // away takes the sky. Small (12%), and it is the difference
            // between a plume of one grey and a plume with a lit side.
            '  float turn = 0.5 + 0.5 * dot(n, uSun);',
            '  c *= mix(normalize(uSky + 1e-4) * 1.732,',
            '           normalize(uSunCol + 1e-4) * 1.732, turn) * 0.12',
            '     + 0.88;',
            // The far wall of the shell is seen THROUGH the near one, so
            // it must not arrive at the same brightness: two coats of
            // white at 0.5 composite to 0.75 and the whole fall reads as
            // one opaque lid (this is what the close camera was showing).
            '  c *= mix(1.0, 0.78, far);',
            // Unlit, so the light is written out ONCE: sky ambient
            // everywhere, key where the sheet turns into it. Spray takes
            // more of both than deep water does, because light does not
            // stop at a cloud's first droplet — the extra 0.40 of
            // ambient is that multiple scattering, and it is what makes
            // broken water the brightest thing in a shaded gorge.
            '  vec3 lit = uAmb * (0.55 + 0.45 * clamp(n.y * 0.5 + 0.5,',
            '      0.0, 1.0) + 0.40 * aer)',
            '      + uSunCol * ndl * (0.35 + 0.75 * aer);',
            '  c *= lit;',
            // The emerald in the lip. Sky enters the top of a coherent
            // sheet and leaves through its face a metre later, having
            // lost its red on the way — this is the ONE thing that
            // makes the upper third of a fall read as water rather than
            // as dark glass, and it dies as the water breaks up.
            '  c += uAmb * vec3(0.06, 0.26, 0.22) * (1.0 - aer)',
            '     * (0.45 + 0.55 * body);',
            // TRANSMISSION. The best-looking waterfall in the world is
            // a backlit one: the sun behind the sheet scatters forward
            // through the air in it and the whole fall lights up. The
            // lobe is on the VIEW ray, so it appears exactly when the
            // camera is looking through the water at the sun.
            '  float fwd = max(dot(-V, uSun), 0.0);',
            // What comes through COHERENT water is green — that is the
            // colour of a metre of it — and what comes through spray is
            // white, because spray is a cloud. One mix, not two terms.
            '  c += uSunCol * mix(vec3(0.34, 0.86, 0.74), vec3(1.0), aer)',
            '     * pow(fwd, 2.6) * (0.12 + 0.42 * aer)',
            '     * (0.35 + 0.65 * body);',
            // The sky arrives by REFLECTION with water's own F0 (2%),
            // so only a grazing view turns the sheet into a mirror —
            // and torn white water is not a mirror at all.
            '  float f5 = pow(1.0 - clamp(dot(n, V), 0.0, 1.0), 5.0);',
            '  float fr = clamp(0.02 + 0.98 * f5, 0.0, 0.86)',
            '      * (1.0 - 0.65 * aer);',
            '  vec3 R = reflect(-V, n);',
            '  vec3 skyC = mix(uAmb * 0.95, uSky * 1.12,',
            '      smoothstep(0.34, 0.78, clamp(R.y * 0.5 + 0.5, 0.0, 1.0)));',
            '  c = mix(c, skyC, fr);',
            // Threads have GAPS: alpha carries the strands, or the
            // sheet is a solid lid with a pattern painted on it.
            '  float thread = clamp(0.16 + 0.92 * fil * grain * travel',
            '      + 0.22 * pk, 0.0, 1.0);',
            '  float veil = smoothstep(0.0, 0.10, drop);',
            // A closed shell piles front and back into one pixel at the
            // silhouette; without this it stacks into a bright rib.
            '  float face = astraFacing(n, cameraPosition - vW);',
            // Coherent water is not gauze: a lip is 0.55 opaque and it
            // is the THREADS that let the cliff through, not a global
            // fade. At 0.30 the upper sheet read as a net curtain.
            '  float a = mix(0.55, 0.92, aer) * mix(0.42, 1.0, thread)',
            '      * veil * smoothstep(0.04, 0.34, face)',
            // Thin where it frays, and the far wall arrives through the
            // near one.
            '      * mix(0.45, 1.0, smoothstep(0.0, 0.5, faceMid))',
            '      * mix(1.0, 0.55, far);',
            '  a = mix(a, 0.90, foamK * 0.5);',
            // The sweep ends on a straight cut across the pool. Water
            // does not: the last fifth of the drop is torn away by its
            // own turbulence so the sheet dies INTO the basin rather
            // than at a seam the eye finds in one frame.
            '  float tear = astraFbm2(vec2(vUv.x * 34.0,',
            '      drop * 18.0 - uTime * 2.6), 3);',
            '  a *= mix(1.0, clamp(tear * 2.1, 0.0, 1.0),',
            '      smoothstep(0.82, 1.0, drop));',
            // A sheet is a long smooth ramp in both colour and alpha,
            // which is exactly what an 8-bit buffer bands. One LSB of
            // ordered-free noise costs nothing and removes the rings.
            '  c += (astraHash21(gl_FragCoord.xy) - 0.5) * 0.006;',
            '  gl_FragColor = vec4(c, clamp(a, 0.0, 1.0));',
        ].join('\n'),
        transparent: true,
        side: THREE.DoubleSide,
        depthWrite: false,
    });
}

/** Where it lands: churn at the impact, rings leaving it, sky on top. */
function poolMaterial(deep, foam, light) {
    return makeShaderMaterial({
        name: 'WaterfallPool',
        uniforms: Object.assign({
            uDeep: { value: deep.clone() },
            uFoam: { value: foam.clone() },
        }, light),
        varyings: 'varying vec2 vUv; varying vec3 vW;',
        vertexMain: [
            '  vUv = uv;',
            '  vW = (modelMatrix * vec4(transformed, 1.0)).xyz;',
        ].join('\n'),
        fragmentHead: [
            'uniform vec3 uDeep; uniform vec3 uFoam;',
            'uniform vec3 uSun; uniform vec3 uSunCol;',
            'uniform vec3 uSky; uniform vec3 uAmb;',
        ].join('\n'),
        fragmentMain: [
            '  float d = length(vUv * 2.0 - 1.0);',
            '  if (d > 1.0) discard;',
            '  float turb = astraFbm2(vUv * 9.0 + vec2(0.0, uTime * 0.45), 4);',
            '  float fine = astraFbm2(vUv * 26.0 - vec2(uTime * 0.7, 0.0), 3);',
            '  float rings = 0.5 + 0.5 * sin(d * 22.0 - uTime * 3.0);',
            // Churn belongs where the sheet HITS, and it is torn, not a
            // wash: the old material mixed 85% of the noise straight to
            // foam over the whole disc, which is a lid of grey milk
            // sitting on the dirt (measured: saturation 0.17, and the
            // deep colour never appeared at all).
            '  float impact = 1.0 - smoothstep(0.04, 0.88, d);',
            '  float tear = clamp((turb - 0.34) / 0.40, 0.0, 1.0);',
            // Foam LEAVES a boil: streaks radiate, so the pattern lives
            // in (angle, radius - time) and travels outward. Without
            // them the basin is a static stain and the only thing that
            // moves in it is a ripple nobody reads at 8 m.
            '  vec2 q = vUv * 2.0 - 1.0;',
            // The +1e-5 is not decoration: CircleGeometry carries a
            // centre vertex at uv (0.5, 0.5), so q is exactly (0, 0)
            // there and atan(0, 0) is undefined — one NaN fragment in
            // the middle of the boil.
            '  float streak = astraFbm2(vec2(atan(q.y, q.x + 1e-5) * 3.6,',
            '      d * 5.0 - uTime * 0.55), 3);',
            '  float churn = clamp(impact * (0.22 + 0.95 * tear)',
            '      + 0.42 * impact * smoothstep(0.34, 0.72, streak)',
            '      + 0.24 * tear * rings * (1.0 - smoothstep(0.25, 0.95, d)),',
            '      0.0, 1.0);',
            '  vec3 deepC = astraHueBreak(uDeep, vUv * 6.0, 1.0, 0.42);',
            '  vec3 foamC = astraHueBreak(uFoam, vUv * 14.0, 1.0, 0.14);',
            '  vec3 c = mix(deepC, foamC, churn);',
            // Flat water tilted by its own turbulence, so the sun does
            // not land as one even sheen over the whole disc.
            '  vec3 n = normalize(vec3((fine - 0.375) * 0.9, 1.0,',
            '      (turb - 0.375) * 0.9));',
            '  vec3 V = normalize(cameraPosition - vW);',
            '  float ndl = max(dot(n, uSun), 0.0);',
            // Churn is AIR in water: it scatters the sky back out
            // instead of swallowing it, so the same ambient buys a lot
            // more light there than it does over the deep water.
            '  c *= uAmb * (0.80 + 0.95 * churn)',
            '     + uSunCol * ndl * (0.30 + 0.70 * churn);',
            '  float f5 = pow(1.0 - clamp(dot(n, V), 0.0, 1.0), 5.0);',
            '  float fr = clamp(0.02 + 0.98 * f5, 0.0, 0.80)',
            '      * (1.0 - 0.6 * churn);',
            '  vec3 R = reflect(-V, n);',
            '  vec3 skyC = mix(uAmb * 0.95, uSky * 1.12,',
            '      smoothstep(0.34, 0.78, clamp(R.y * 0.5 + 0.5, 0.0, 1.0)));',
            '  c = mix(c, skyC, fr);',
            '  vec3 H = normalize(uSun + V);',
            '  c += uSunCol * pow(max(dot(n, H), 0.0), 90.0) * 0.35 * f5;',
            // Feathered to nothing at the rim, or the disc draws its own
            // circle on the ground.
            '  float a = smoothstep(1.0, 0.15, d)',
            '      * (0.40 + 0.50 * churn + 0.10 * tear);',
            '  c += (astraHash21(gl_FragCoord.xy) - 0.5) * 0.006;',
            '  gl_FragColor = vec4(c, clamp(a, 0.0, 1.0));',
        ].join('\n'),
        transparent: true,
        depthWrite: false,
    });
}

/** Mist rising off the impact, as GTAO-safe instanced billboards. */
function mistCloud(n, w, thr, seed, light) {
    const geom = instancedQuad(n, 1, 1, w * 1.5 + thr);
    const s0 = { v: (seed >>> 0) || 1 };
    const rnd = () => ((s0.v = (s0.v * 16807) % 2147483647) / 2147483647);
    const data = new Float32Array(n * 4);
    for (let i = 0; i < n; i++) {
        data[i * 4] = (rnd() * 2 - 1) * w * 0.75;
        data[i * 4 + 1] = thr + (rnd() * 2 - 1) * w * 0.55;
        data[i * 4 + 2] = rnd();
        data[i * 4 + 3] = 0.6 + rnd() * 1.7;
    }
    geom.setAttribute('iSeed', new THREE.InstancedBufferAttribute(data, 4));
    // A billboard needs its own gl_Position in view space, which the
    // assembled vertex cannot express — so this one is raw, and carries
    // the depth and fog chunks itself.
    const mat = makeShaderMaterial({
        name: 'WaterfallMist',
        uniforms: Object.assign({}, light),
        vertexShader: [
            '#include <common>',
            '#include <logdepthbuf_pars_vertex>',
            '#include <fog_pars_vertex>',
            'attribute vec3 aCorner;',
            'attribute vec4 iSeed;',
            'uniform float uTime;',
            'varying vec2 vUv;',
            'varying float vLife;',
            'varying float vSeed;',
            'varying vec3 vW;',
            'void main() {',
            '  vUv = uv;',
            '  float k = fract(iSeed.z + uTime * 0.15);',
            '  vLife = k;',
            '  vSeed = iSeed.z;',
            // Spray off an impact does not rise in a column: it lifts,
            // spreads and drifts off downstream as it goes.
            '  vec3 c = vec3(iSeed.x + (iSeed.z - 0.5) * k * 1.6,',
            '                0.10 + k * 2.6,',
            '                iSeed.y + k * 0.9);',
            '  vec4 mvPosition = modelViewMatrix * vec4(c, 1.0);',
            '  mvPosition.xy += aCorner.xy * iSeed.w * (0.7 + k * 1.7);',
            '  vW = (modelMatrix * vec4(c, 1.0)).xyz;',
            '  gl_Position = projectionMatrix * mvPosition;',
            '#include <logdepthbuf_vertex>',
            '#include <fog_vertex>',
            '}',
        ].join('\n'),
        fragmentShader: [
            '#include <common>',
            '#include <logdepthbuf_pars_fragment>',
            '#include <fog_pars_fragment>',
            'uniform vec3 uSun; uniform vec3 uSunCol;',
            'uniform vec3 uSky; uniform vec3 uAmb;',
            'varying vec2 vUv;',
            'varying float vLife;',
            'varying float vSeed;',
            'varying vec3 vW;',
            // The raw route gets no GLSL_UTIL — that is injected only
            // into shaders shader.js assembles — so the two lines of
            // value noise this needs live here.
            'float wfHash(vec2 p) {',
            '  vec3 q = fract(vec3(p.xyx) * 0.1031);',
            '  q += dot(q, q.yzx + 33.33);',
            '  return fract((q.x + q.y) * q.z);',
            '}',
            'float wfNoise(vec2 p) {',
            '  vec2 i = floor(p), f = fract(p);',
            '  f = f * f * (3.0 - 2.0 * f);',
            '  return mix(mix(wfHash(i), wfHash(i + vec2(1.0, 0.0)), f.x),',
            '             mix(wfHash(i + vec2(0.0, 1.0)),',
            '                 wfHash(i + vec2(1.0, 1.0)), f.x), f.y);',
            '}',
            'void main() {',
            '#include <logdepthbuf_fragment>',
            '  float d = length(vUv - 0.5) * 2.0;',
            '  float soft = smoothstep(1.0, 0.05, d);',
            // A perfect airbrushed disc is a smudge, not spray: break
            // the puff with its own noise, coarse enough to survive
            // being 40 px wide and different for every instance.
            '  float lump = wfNoise(vUv * 3.4 + vSeed * 31.0)',
            '      * 0.65 + wfNoise(vUv * 8.0 - vSeed * 17.0) * 0.35;',
            '  soft *= 0.45 + 1.05 * lump;',
            // Fade IN as well as out: at 0.17 * (1 - life) every puff
            // was born at full opacity and popped on the frame it
            // appeared, which is the one artefact a still frame shows.
            '  float life = smoothstep(0.0, 0.16, vLife)',
            '      * (1.0 - smoothstep(0.34, 1.0, vLife));',
            '  float a = soft * soft * 0.26 * life;',
            '  if (a < 0.004) discard;',
            // A cloud of droplets is lit by the SKY from above and lights
            // up when the sun is behind it — the same forward lobe the
            // sheet uses, so plume and sheet agree about where the sun
            // is. Per-puff warmth keeps the plume from being one grey.
            '  vec3 V = normalize(vW - cameraPosition);',
            '  float fwd = max(dot(V, uSun), 0.0);',
            '  vec3 c = uAmb * (1.05 + 0.25 * vSeed)',
            '      + uSunCol * (0.18 + 0.85 * pow(fwd, 3.0));',
            '  gl_FragColor = vec4(c, a);',
            '#include <fog_fragment>',
            '}',
        ].join('\n'),
        transparent: true,
        depthWrite: false,
    });
    const mist = new THREE.Mesh(geom, mat);
    mist.name = 'Mist';
    mist.frustumCulled = false;
    mist.renderOrder = 3;
    return mist;
}

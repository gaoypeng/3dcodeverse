/**
 * The air over moving water, and the droplets moving water throws.
 *
 * `makeWaterMist` is the veil that hangs over a river, a weir or a
 * wake: thickest in the first hand's breadth above the surface, gone by
 * a couple of metres, streaked ALONG the flow and scrolling downstream
 * with it. `atmosphere.js` `makeHeightFog` is the same height profile
 * in STILL air — evenly blotched, barely moving — and the two must not
 * read alike, so everything here that costs anything goes into the
 * motion: the streaks lie down the current, and the top of the bank
 * runs ahead of its foot.
 *
 * `makeSpray` is the other half a wake or a waterfall foot needs and
 * neither has today: the droplets thrown UP where water strikes, each
 * on its own ballistic arc, stretched along its own motion so it reads
 * as fast, and fading as it falls back.
 *
 * BLENDING — both are pale, both can appear in daylight, so both use
 * ORDINARY blending. `makeShaderMaterial({ additive: true })` is for
 * light that ADDS, and `godrays.js` measures what added light does to a
 * bright frame: 12.8% of it blown past 0.97, and cutting the gain 3.6x
 * still left 11.3%, because 0.94 plus anything visible clips. Mist does
 * the opposite of adding — it SCATTERS, lowering the contrast behind
 * it — and a droplet is an object, not a light.
 *
 * The mist is a field of soft cards, and the whole difficulty is that
 * no card may be visible. Three things do that, and dropping any one
 * of them puts the cards back in the frame. The structure a fragment
 * shows comes from ONE noise field read at the fragment's own
 * position, so overlapping cards agree and their sum along a view ray
 * is a crude volume integral. Every card faces the camera squarely and
 * is no wider than the bank is deep, so a ray crossing the bank meets
 * a dozen of them rather than one — one card per pixel paints its own
 * noise raw, and that ribbon IS the card. And their heights are packed
 * toward the water, so a view from above integrates the profile
 * instead of a single slice of it.
 *
 * `waterfall.js` builds a mist inline (billboards on a fixed rise, one
 * plume, no drift). To use these instead: `makeWaterfall({ mist: 0 })`,
 * then `makeWaterMist({ extent: width * 4, height: 2.5, drift: [0,
 * 0.6], density: 0.6 })` moved to the plunge pool, plus
 * `makeSpray({ origin: [0, 0, throw_], radius: width * 0.55 })` for the
 * droplets the inline mist never had.
 *
 * Both billboard against the WORLD camera axes, so add them at the
 * scene root or under a translated parent — a rotated or scaled parent
 * tilts the cards.
 */

import * as THREE from 'three';

import { instancedQuad, makeShaderMaterial, tickShaders, keepOutOfDepthPasses } from './shader.js';

// Free fall. It is written twice on purpose — in the vertex shader and
// in the CPU mirror `userData.sample` — so both read this one number.
const _G = 9.81;

/** 16807 LCG: same seed, same field, on every machine. */
function prng(seed) {
    let s = (seed >>> 0) || 1;
    return () => ((s = (s * 16807) % 2147483647) / 2147483647);
}

/** A THREE.Vector3 from a vector, an [x,y,z] array or nothing. */
function toVec3(v) {
    if (Array.isArray(v)) {
        return new THREE.Vector3(v[0] || 0, v[1] || 0, v[2] || 0);
    }
    if (v && v.isVector3) return v.clone();
    return new THREE.Vector3();
}

/**
 * The key light as a direction plus how much of it there is.
 *
 * Mist and spray are LIT things, not coloured ones: what they show is
 * an albedo times what reaches them. The sun below the horizon delivers
 * nothing, so the gain goes to zero there and the skylight term (the
 * scene's own fog colour) is all that is left — which is what turns
 * both of these dark at night without the caller saying so.
 *
 * @param {*} v A THREE.Vector3, an [x,y,z], or nothing.
 * @returns {{dir: THREE.Vector3, gain: number}} Unit direction TOWARD
 *   the sun (default straight up, the neutral top-lit read for a
 *   caller with no rig to hand) and 0..1 for how far it has risen.
 */
function toSun(v) {
    const d = toVec3(v);
    if (d.lengthSq() < 1e-8) return { dir: new THREE.Vector3(0, 1, 0), gain: 1 };
    d.normalize();
    // 0 at the horizon, full by ten degrees up: the last of the sun
    // reddens and dims the scene's own lights long before this matters.
    const gain = Math.min(1, Math.max(0, (d.y - 0.005) / 0.175));
    return { dir: d, gain };
}

/** Downstream velocity as a unit direction in xz plus a speed. */
function toFlow(v) {
    let x = 0.45;
    let z = 0;
    if (typeof v === 'number') {
        x = v;
    } else if (Array.isArray(v)) {
        x = v[0] || 0;
        z = (v.length > 2 ? v[2] : v[1]) || 0;
    } else if (v && (v.isVector3 || v.isVector2)) {
        x = v.x;
        z = v.isVector3 ? v.z : v.y;
    }
    const speed = Math.hypot(x, z);
    if (speed < 1e-5) return { dir: new THREE.Vector2(1, 0), speed: 0 };
    return { dir: new THREE.Vector2(x / speed, z / speed), speed };
}

/**
 * The mist that hangs over moving water — air, not a stack of cards.
 *
 * Density falls with height ABOVE THE WATER, not with camera distance,
 * so a hull sits in it and a bank above it does not. Every card is
 * faint; the per-card alpha is solved from how many of them a
 * horizontal ray crosses, so `density` means the same thickness
 * whatever `extent` and `height` make of the card count.
 *
 * @param {object} [opts]
 *   `extent` metres square the bank covers (default 14; full strength
 *   right across it and gone by its corners, so it draws no boundary
 *   of its own); `height` metres at which the mist is gone (default 2
 *   — mist over water is a low thing, and a tall one reads as
 *   weather); `density` how thick the bank is, 0 to 1 (default 0.5 a
 *   clear haze; 0.9 hides the far bank);
 *   `color` the medium's ALBEDO, THREE.Color or hex (default a pale
 *   blue-grey) — it is multiplied by the light that reaches the bank,
 *   never emitted, so keep it near white and let the scene colour it;
 *   `sunDir` THREE.Vector3 or [x,y,z] TOWARD the key light, from
 *   `sunRig().sunDir` (default +Y, top lit) — the bank scatters it
 *   FORWARD, so it burns looking into the sun and greys looking away,
 *   and it delivers nothing once the sun is down;
 *   `sunColor` THREE.Color or hex for that light (default a warm
 *   white); `drift` downstream velocity in m/s, a number (along +x), [dx, dz],
 *   [dx, dy, dz] or a THREE.Vector2/3 (default [0.45, 0]) — it sets
 *   both the direction the streaks lie in and how fast they travel;
 *   `heightAt` (x, z) => y water surface height, so the bank follows a
 *   sloping reach; `seed` PRNG seed (default 5).
 * @returns {THREE.Group} Named `WaterMist`, its foot on y = 0 (or on
 *   `heightAt`), with `userData.tick(t)` driving the drift. Move the
 *   group to move the bank.
 */
export function makeWaterMist(opts = {}) {
    const extent = Math.max(1, opts.extent === undefined ? 14 : opts.extent);
    const top = Math.max(0.2, opts.height === undefined ? 2 : opts.height);
    const density = Math.min(0.97, Math.max(0,
        opts.density === undefined ? 0.5 : opts.density));
    const color = new THREE.Color(
        opts.color === undefined ? 0xdfeaf2 : opts.color);
    const sun = toSun(opts.sunDir);
    const sunColor = new THREE.Color(
        opts.sunColor === undefined ? 0xfff2e0 : opts.sunColor);
    const flow = toFlow(opts.drift);
    const heightAt = opts.heightAt || null;
    const rnd = prng(opts.seed === undefined ? 5 : opts.seed);

    // Cards no wider than the bank is deep. A ray that crosses the
    // bank must meet SEVERAL of them or the one it meets paints its
    // own noise raw, and a single card's pattern is a visible ribbon.
    const cardW = Math.min(extent * 0.25,
        Math.max(1, top * 1.1, extent / 22));
    const cardH = top * 1.1;
    const count = Math.round(Math.min(1200, Math.max(24,
        14 * (extent * extent) / (cardW * cardW))));
    // Cards a horizontal ray crosses. Solving the per-card alpha from
    // it keeps `density` the thickness of the BANK at any card count,
    // and the constant pays back what the height profile, the wisps and
    // the card envelope take. It rose from 9 to 16 when the card
    // envelope was softened to kill the striping — a softer envelope
    // spends more of every card, and `density` has to go on meaning the
    // same thickness. Measured on the weir showcase against a
    // mist-free control: the bank's own contribution over the wall came
    // back to 5.3/255 where the hard-edged 9 gave 5.9.
    const hits = Math.max(1, (count * cardW) / extent);
    const alpha = Math.min(0.60, Math.max(0.004,
        16.0 * (1 - Math.pow(1 - density, 1 / hits))));
    // Wisps scale with the bank's own height, not with the reach: a
    // pattern stretched to the extent reads as one printed backdrop.
    const wisp = Math.min(Math.max(top * 1.2, 0.8), extent * 0.28);

    const puff = new Float32Array(count * 4);
    const card = new Float32Array(count * 2);
    const cols = Math.ceil(Math.sqrt(count));
    let radius = 0;
    for (let i = 0; i < count; i++) {
        // A jittered grid, never a uniform draw: the clumps and holes a
        // uniform draw leaves read as separate clouds, not one bank.
        const gx = ((i % cols) + 0.5) / cols - 0.5;
        const gz = (Math.floor(i / cols) + 0.5) / cols - 0.5;
        const x = (gx + (rnd() - 0.5) / cols) * extent;
        const z = (gz + (rnd() - 0.5) / cols) * extent;
        const base = heightAt ? heightAt(x, z) : 0;
        // Heights PACKED toward the water, because that is where the
        // mist is: cards all at one height leave a view from above
        // integrating a single slice of the profile.
        const mid = base + top * 1.05 * Math.pow(rnd(), 1.6);
        const s = 0.70 + 0.75 * rnd();
        puff[i * 4] = x;
        puff[i * 4 + 1] = z;
        puff[i * 4 + 2] = base;
        puff[i * 4 + 3] = mid;
        card[i * 2] = s;
        card[i * 2 + 1] = 0.80 + 0.5 * rnd();
        radius = Math.max(radius, Math.hypot(x, mid, z)
            + 0.5 * Math.hypot(cardW * s, cardH * card[i * 2 + 1]));
    }
    // position stays at zero for the AO pass, so the sphere is the only
    // thing three can cull the field by; the margin covers the float32
    // the attributes are read back at.
    const geom = instancedQuad(count, 1, 1, radius * 1.02);
    geom.setAttribute('aPuff', new THREE.InstancedBufferAttribute(puff, 4));
    geom.setAttribute('aCard', new THREE.InstancedBufferAttribute(card, 2));

    const mesh = new THREE.Mesh(geom, mistMaterial({
        color, alpha, top, extent, flow, cardW, cardH, wisp,
        sun, sunColor, hits,
        offset: new THREE.Vector2(rnd() * 96, rnd() * 96),
    }));
    mesh.name = 'MistCards';
    mesh.renderOrder = 2;
    const g = new THREE.Group();
    g.name = 'WaterMist';
    g.add(mesh);
    g.userData.tick = (t) => tickShaders(g, t);
    return keepOutOfDepthPasses(g);
}

/** Camera-facing cards, all reading one scrolling field of air. */
function mistMaterial(cfg) {
    return makeShaderMaterial({
        name: 'WaterMistCards',
        uniforms: {
            // Declared and bound HERE as well as by shader.js, because the
            // static GLSL audit reads one FILE at a time: the dither line
            // below carries `gl_FragCoord`, which is what makes the audit
            // collect the string at all, and it then sees a file that reads
            // uTime without ever declaring or binding it.  godrays.js
            // carries the same pair for the same reason.
            uTime: { value: 0 },
            uColor: { value: cfg.color },
            uAlpha: { value: cfg.alpha },
            uTop: { value: cfg.top },
            // Under half the height is the e-fold, so the first hand's
            // breadth over the water carries most of the extinction.
            uScale: { value: cfg.top * 0.45 },
            uFreq: { value: 1 / cfg.wisp },
            uFlow: { value: cfg.flow.dir },
            uSpeed: { value: cfg.flow.speed },
            uSize: { value: new THREE.Vector2(cfg.cardW, cfg.cardH) },
            uRadius: { value: cfg.extent * 0.5 },
            // Metres downstream per metre of height.
            uShear: { value: cfg.top * 0.9 },
            uOffset: { value: cfg.offset },
            uSunDir: { value: cfg.sun.dir },
            uSunColor: { value: cfg.sunColor },
            // Skylight is the scene's OWN fog colour, lifted: the fog
            // is the sky seen through a kilometre of it and the bank is
            // a metre away, so it arrives brighter than the horizon.
            uSkyGain: { value: 1.28 },
            // Forward-scattered sun at its peak, held UNDER the sky
            // term: a bank that out-runs the sky reads as a lamp.
            uSunGain: { value: 0.75 * cfg.sun.gain },
            // Dither, PER CARD. A dozen independent draws of it sum as
            // a random walk, so the amplitude has to be divided by the
            // root of the stack depth or the veil comes out visibly
            // grainy — measured: 0.004 flat put a coarse speckle right
            // across the bank at 85 cards deep.
            uDither: { value: 0.010 / Math.sqrt(Math.max(1, cfg.hits)) },
        },
        varyings: 'varying vec2 vUv; varying vec3 vP;'
            + ' varying float vBase;',
        vertexHead: 'attribute vec3 aCorner; attribute vec4 aPuff;'
            + ' attribute vec2 aCard; uniform vec2 uSize;',
        vertexMain: [
            '  vUv = uv;',
            '  vBase = aPuff.z;',
            // Square on to the camera from every elevation. An upright
            // card seen from above shows the bank's thin foot as a
            // ribbon; this one shows it spread across its face.
            '  vec3 camR = vec3(viewMatrix[0][0], viewMatrix[1][0],',
            '      viewMatrix[2][0]);',
            '  vec3 camU = vec3(viewMatrix[0][1], viewMatrix[1][1],',
            '      viewMatrix[2][1]);',
            '  vec3 mid = vec3(aPuff.x, aPuff.w, aPuff.y);',
            '  transformed = mid + camR * (aCorner.x * uSize.x * aCard.x)',
            '      + camU * (aCorner.y * uSize.y * aCard.y);',
            '  vP = transformed;',
        ].join('\n'),
        fragmentHead: 'uniform vec3 uColor; uniform float uAlpha;'
            + ' uniform float uTop; uniform float uScale;'
            + ' uniform float uFreq; uniform vec2 uFlow;'
            + ' uniform float uSpeed; uniform float uRadius;'
            + ' uniform vec2 uOffset; uniform float uShear;'
            + ' uniform vec3 uSunDir; uniform vec3 uSunColor;'
            + ' uniform float uSkyGain; uniform float uSunGain;'
            + ' uniform float uDither; uniform float uTime;',
        fragmentMain: [
            // Height above THIS card's water, so a sloping reach keeps
            // its own surface, and zero before either card edge.
            '  float h = vP.y - vBase;',
            // Both axes: the height profile cannot fade a card the
            // camera is looking down on, and its rim would be an edge.
            // The VERTICAL fade starts almost at the middle: a camera
            // near the top of the bank sees the cards' upper rims
            // near edge-on and a late fade lands them all in one screen
            // band, which is the horizontal striping a night frame
            // showed across the whole veil.
            '  vec2 e = abs(vUv * 2.0 - 1.0);',
            '  float env = (1.0 - smoothstep(0.30, 1.0, e.x))',
            '      * (1.0 - smoothstep(0.16, 1.0, e.y));',
            // The profile at its thickest (lift = 1) bounds this
            // fragment, so everything outside the bank leaves before
            // paying for three noise fields — and the cards are stacked
            // a dozen deep, which is most of what this effect costs.
            '  float cap = uAlpha * env * exp(-h / uScale)',
            '      * (1.0 - smoothstep(uTop * 0.35, uTop, h))',
            '      * smoothstep(-uTop * 0.22, -uTop * 0.02, h);',
            '  if (cap < 0.002) discard;',
            '  float along = dot(vP.xz, uFlow) - uTime * uSpeed;',
            '  float across = dot(vP.xz, vec2(-uFlow.y, uFlow.x));',
            // Compressed along the current, so the wisps lie DOWN it
            // instead of blotching like still air.
            '  vec2 q0 = vec2(along * 0.70, across) * uFreq + uOffset;',
            // How deep the bank is HERE, read at the surface: a
            // constant top is a lid, and a lid is what reads as a slab.
            // TWO scales, and the coarse one carries most of the swing.
            // A ray crosses a dozen cards spread over metres of xz, so
            // the integral averages any pattern shorter than the ray
            // away to its mean — the wisp scale survives inside one
            // card and vanishes from the sum, which is exactly how the
            // first render came out with a flat top and no tongues.
            // Only structure metres across lives through the integral.
            '  float coarse = astraFbm2(q0 * 0.30 + vec2(11.3, 4.7), 2);',
            '  float lift = 0.20 + 0.42 * astraFbm2(q0, 3) + 0.62 * coarse;',
            '  float prof = exp(-h / (uScale * lift))',
            '      * (1.0 - smoothstep(uTop * lift * 0.35, uTop * lift, h))',
            '      * smoothstep(-uTop * 0.22, -uTop * 0.02, h);',
            // Higher air has been carried further downstream. Without
            // this shear a card paints one column of the xz field over
            // its whole height, and the field reads as flat ribbons.
            '  vec2 q = q0 + vec2(h * uShear * uFreq, 0.0);',
            '  float n = astraFbm2(q, 4);',
            '  float sh = -uTime * uSpeed * uFreq * 0.55;',
            '  float n2 = astraFbm2(q * 2.6 + vec2(sh, h * 0.5), 3);',
            '  float wisp = smoothstep(0.12, 0.80, mix(n, n2, 0.32));',
            // Full strength across the whole square and gone by its
            // corners: an inscribed fade leaves mist on a third of the
            // reach the caller asked for.
            '  float edge = 1.0 - smoothstep(1.0, 1.42,',
            '      length(vP.xz) / uRadius);',
            '  float a = uAlpha * prof * wisp * env * edge;',
            // A ramp this smooth across this many pixels bands in 8
            // bits, and a dozen cards stack the SAME ramp, so the steps
            // land on top of each other instead of dissolving. Under
            // half a code value, per pixel, moving.
            '  a += (astraHash21(gl_FragCoord.xy + vec2(uTime * 60.0))',
            '      - 0.5) * uDither;',
            '  if (a < 0.002) discard;',
            // LIGHT, not paint. What a scattering medium shows is its
            // albedo times what reaches it, so `uColor` is multiplied
            // by the scene's own light and never emitted: `fogColor` IS
            // the skylight at ground level, and reading it is what
            // makes this bank pale at noon and a dim blue-grey at
            // night with nothing said by the caller. (It exists only
            // when the scene has fog — hence the guard, and the white
            // fallback that leaves a fogless scene looking as before.)
            '  vec3 sky = vec3(1.0);',
            '  #ifdef USE_FOG',
            '  sky = fogColor;',
            '  #endif',
            // Forward scattering, as the Henyey-Greenstein lobe rather
            // than a power of the cosine. Water droplets throw light ON
            // rather than back, so a bank between the camera and the
            // sun burns and the same bank with the sun behind is grey —
            // the one read that separates mist from a grey card. The
            // lobe matters because it is BROAD: `pow(mu, 3)` only fires
            // within about 30 degrees of the sun, and the flip test
            // below moved the veil 2% between mu = +0.64 and mu = -0.85,
            // which is no coupling at all at the angles a camera
            // actually stands at. Normalised so side-scatter (mu = 0)
            // reads 0.20, the value the power form had as its floor.
            '  vec3 V = normalize(vP - cameraPosition);',
            '  float mu = dot(V, uSunDir);',
            '  float den = 1.3025 - 1.10 * mu;',
            '  float hg = clamp(0.2929 / max(pow(den, 1.5), 1e-3),',
            '      0.14, 2.0);',
            // The foot of a bank stands in the shade of the bank above
            // it, so the sun reaches the top of the profile first.
            '  float open = mix(0.40, 1.0,',
            '      smoothstep(0.0, uTop * 0.75, h));',
            '  vec3 col = uColor * (sky * uSkyGain + uColor * 0.032',
            '      + uSunColor * (uSunGain * hg * open));',
            // Never one flat value: cool in the thick of it where only
            // sky gets in, warm where the sun does, plus a slow break
            // across the reach so no two tongues are the same grey.
            '  float warm = clamp(hg * open, 0.0, 1.0);',
            '  col *= mix(vec3(0.94, 0.98, 1.07), vec3(1.05, 1.00, 0.94),',
            '      warm);',
            '  col = astraHueBreak(col, vP.xz, uFreq * 0.5, 0.16);',
            // One flat colour per fragment keeps the over-blend order
            // independent, so overlapping cards need no sorting.
            '  gl_FragColor = vec4(col, clamp(a, 0.0, 1.0));',
        ].join('\n'),
        transparent: true,
        depthWrite: false,
        side: THREE.DoubleSide,
    });
}

/**
 * Droplets thrown UP where water strikes — real arcs, not a puff.
 *
 * Every droplet is launched from the impact ring with its own speed and
 * its own flight time, rises, slows, and falls back under gravity; the
 * count is solved from `rate` times the longest flight, so the field is
 * continuous without anyone guessing a particle count. They are
 * stretched along their own motion in the view plane, because a round
 * dot at this size reads as snow and the streak is what reads as water.
 *
 * @param {object} [opts]
 *   `origin` impact point, THREE.Vector3 or [x,y,z] (default 0,0,0 —
 *   the group is MOVED there, so the droplets stay local);
 *   `radius` metres the impacts are spread over (default 0.7);
 *   `rate` droplets launched per second (default 120);
 *   `speed` launch speed in m/s (default 4.5 — it decides the height:
 *   4.5 m/s tops out near a metre); `size` droplet diameter in metres
 *   (default 0.055); `color` the droplet's ALBEDO, THREE.Color or hex
 *   (default pale water) — multiplied by the light that reaches it,
 *   never emitted; `sunDir` THREE.Vector3 or [x,y,z] TOWARD the key
 *   light, from `sunRig().sunDir` (default +Y) — a droplet is a lens,
 *   so the sun comes back off it as a hard glint and nothing at all
 *   once the sun is down; `sunColor` for that light (default a warm
 *   white); `seed` PRNG seed (default 3).
 * @returns {THREE.Group} Named `Spray`, positioned at `origin`, with
 *   `userData.tick(t)` driving it and `userData.sample(i, t)` returning
 *   droplet i's group-local position — the CPU mirror of the ballistics
 *   the vertex shader runs.
 */
export function makeSpray(opts = {}) {
    const origin = toVec3(opts.origin);
    const radius = Math.max(0.01,
        opts.radius === undefined ? 0.7 : opts.radius);
    const rate = Math.max(1, opts.rate === undefined ? 120 : opts.rate);
    const speed = Math.max(0.4, opts.speed === undefined ? 4.5 : opts.speed);
    const size = Math.max(0.004,
        opts.size === undefined ? 0.055 : opts.size);
    const color = new THREE.Color(
        opts.color === undefined ? 0xeaf4ff : opts.color);
    const sun = toSun(opts.sunDir);
    const sunColor = new THREE.Color(
        opts.sunColor === undefined ? 0xfff2e0 : opts.sunColor);
    const rnd = prng(opts.seed === undefined ? 3 : opts.seed);

    // A droplet lives one whole arc, so the field is exactly the launch
    // rate times the longest flight — no particle count to guess.
    const count = Math.round(Math.min(900,
        Math.max(8, (rate * 2 * speed) / _G)));
    const org = new Float32Array(count * 3);
    const vel = new Float32Array(count * 3);
    const ph = new Float32Array(count * 2);
    let reach = 0;
    for (let i = 0; i < count; i++) {
        const a = rnd() * Math.PI * 2;
        const r = radius * Math.sqrt(rnd());
        const vy = speed * (0.52 + 0.48 * rnd());
        // Thrown OUTWARD from where it struck, and far slower sideways
        // than up: a symmetric cone reads as a fountain, not an impact.
        const ang = a + (rnd() - 0.5) * 1.1;
        const vh = speed * (0.10 + 0.38 * rnd());
        org[i * 3] = Math.cos(a) * r;
        org[i * 3 + 1] = rnd() * 0.05;
        org[i * 3 + 2] = Math.sin(a) * r;
        vel[i * 3] = Math.cos(ang) * vh;
        vel[i * 3 + 1] = vy;
        vel[i * 3 + 2] = Math.sin(ang) * vh;
        ph[i * 2] = rnd();
        ph[i * 2 + 1] = 0.55 + 0.95 * rnd();
        const t = (2 * vy) / _G;
        const far = Math.hypot(org[i * 3] + vel[i * 3] * t,
                               org[i * 3 + 2] + vel[i * 3 + 2] * t);
        const apex = org[i * 3 + 1] + (vy * vy) / (2 * _G);
        reach = Math.max(reach,
            Math.hypot(far, apex) + size * ph[i * 2 + 1] * 1.5);
    }
    const geom = instancedQuad(count, 1, 1, reach * 1.02);
    geom.setAttribute('aOrg', new THREE.InstancedBufferAttribute(org, 3));
    geom.setAttribute('aVel', new THREE.InstancedBufferAttribute(vel, 3));
    geom.setAttribute('aPhase', new THREE.InstancedBufferAttribute(ph, 2));

    const mesh = new THREE.Mesh(geom,
        sprayMaterial(color, size, sun, sunColor));
    mesh.name = 'SprayDroplets';
    mesh.renderOrder = 3;
    const g = new THREE.Group();
    g.name = 'Spray';
    g.position.copy(origin);
    g.add(mesh);
    g.userData.tick = (t) => tickShaders(g, t);
    g.userData.sample = (i, t) => {
        const k = Math.min(count - 1, Math.max(0, i | 0));
        const vy = vel[k * 3 + 1];
        const flight = (2 * vy) / _G;
        const age = ((((ph[k * 2] + t / flight) % 1) + 1) % 1) * flight;
        return new THREE.Vector3(
            org[k * 3] + vel[k * 3] * age,
            org[k * 3 + 1] + vy * age - 0.5 * _G * age * age,
            org[k * 3 + 2] + vel[k * 3 + 2] * age);
    };
    return keepOutOfDepthPasses(g);
}

/** One arc per droplet, stretched along the way it is going. */
function sprayMaterial(color, size, sun, sunColor) {
    return makeShaderMaterial({
        name: 'SprayDroplets',
        uniforms: {
            uColor: { value: color },
            uAlpha: { value: 0.85 },
            uSize: { value: size },
            uG: { value: _G },
            // Seconds of travel drawn into the streak: a droplet at
            // launch runs ~2.5x its width, one at the apex is round.
            uStreak: { value: 0.33 },
            uSunDir: { value: sun.dir },
            uSunColor: { value: sunColor },
            // A droplet is close and sees the whole sky, so it takes
            // more skylight than the mist does.
            uSkyGain: { value: 1.5 },
            // The glint. It peaks near 1.8 in linear — bright enough to
            // survive a bloom threshold, nowhere near the 20 that turns
            // a highlight into a white hole.
            uSunGain: { value: 0.95 * sun.gain },
        },
        varyings: 'varying vec2 vUv; varying float vLife;'
            + ' varying float vGlint; varying float vJit;',
        vertexHead: 'attribute vec3 aCorner; attribute vec3 aOrg;'
            + ' attribute vec3 aVel; attribute vec2 aPhase;'
            + ' uniform float uSize; uniform float uG;'
            + ' uniform float uStreak; uniform vec3 uSunDir;',
        vertexMain: [
            '  vUv = uv;',
            // Its own flight time, so every droplet relaunches on its
            // own beat and the field never pulses together.
            '  float flight = 2.0 * aVel.y / uG;',
            '  float age = fract(aPhase.x + uTime / flight) * flight;',
            '  vLife = age / flight;',
            '  vec3 c = aOrg + vec3(aVel.x, 0.0, aVel.z) * age;',
            '  c.y += aVel.y * age - 0.5 * uG * age * age;',
            '  vec3 v = vec3(aVel.x, aVel.y - uG * age, aVel.z);',
            '  vec3 f = normalize(cameraPosition - c);',
            '  vec3 d = v - f * dot(v, f);',
            '  float dl = length(d);',
            '  vec3 dir = dl > 1e-4 ? d / dl',
            '      : vec3(viewMatrix[0][1], viewMatrix[1][1],',
            '             viewMatrix[2][1]);',
            '  vec3 sid = normalize(cross(f, dir));',
            '  float w = uSize * aPhase.y;',
            '  float st = 1.0 + uStreak * length(v);',
            '  transformed = c + dir * (aCorner.y * w * st)',
            '      + sid * (aCorner.x * w);',
            // Per droplet, not per pixel: a sphere this small is one
            // highlight, and the whole lighting is a function of where
            // it sits between the camera and the sun.
            '  vGlint = pow(max(dot(-f, uSunDir), 0.0), 2.0);',
            // No two the same. aPhase.y already varies the SIZE; this
            // decorrelates the brightness from it so the big ones are
            // not also the bright ones.
            '  vJit = fract(aPhase.y * 7.3 + aPhase.x * 3.1);',
        ].join('\n'),
        fragmentHead: 'uniform vec3 uColor; uniform float uAlpha;'
            + ' uniform vec3 uSunColor; uniform float uSkyGain;'
            + ' uniform float uSunGain;',
        fragmentMain: [
            '  float d = length(vUv - 0.5) * 2.0;',
            '  if (d > 1.0) discard;',
            // A solid core with a one-pixel rim: a droplet is water,
            // not a glow, and a soft blob at this size reads as dust.
            '  float core = 1.0 - smoothstep(0.45, 1.0, d);',
            // Thrown clear, then spent: it thins on the way back down
            // instead of winking out at the end of its arc.
            '  float rise = smoothstep(0.0, 0.05, vLife);',
            '  float fall = 1.0 - smoothstep(0.40, 1.0, vLife);',
            '  float a = uAlpha * core * rise * fall;',
            '  if (a < 0.004) discard;',
            // Same law as the mist: albedo times what arrives. The
            // scene's fog colour is the skylight a droplet swims in, so
            // the field goes from white in the sun to a dim blue-grey
            // at night with nothing said by the caller.
            '  vec3 sky = vec3(1.0);',
            '  #ifdef USE_FOG',
            '  sky = fogColor;',
            '  #endif',
            // A droplet is a LENS, so the sun leaves it as a hard
            // glint pointed at the camera rather than a diffuse wash —
            // and there is always a little skylight in the shade of it.
            '  vec3 col = uColor * (sky * uSkyGain + uColor * 0.05',
            '      + uSunColor * uSunGain * (0.28 + 0.72 * vGlint));',
            '  col *= 0.80 + 0.40 * vJit;',
            // The rim of a sphere refracts, so it out-runs the body:
            // this is what keeps it a droplet and not a flat disc.
            '  col *= 0.88 + 0.30 * core;',
            '  gl_FragColor = vec4(col, clamp(a, 0.0, 1.0));',
        ].join('\n'),
        transparent: true,
        depthWrite: false,
        side: THREE.DoubleSide,
    });
}

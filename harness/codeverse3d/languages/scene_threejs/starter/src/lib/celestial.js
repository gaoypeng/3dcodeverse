/**
 * The sky above the scene, and the air that bends what is behind it.
 *
 * Two different jobs share this file because both live OUTSIDE the
 * lit world and both are additive-or-nothing:
 *
 * A SHIMMER IS NOT A THING YOU CAN SEE. Heat haze has no colour, no
 * silhouette and no shape — it is the image behind it, moved. So this
 * one never emits a pixel of its own: it grabs the frame drawn so far
 * into a texture and resamples it at displaced coordinates. That grab
 * is a framebuffer COPY, not a second scene render (a `Reflector`
 * re-renders the WHOLE scene at +48 ms/frame on SwiftShader; a copy of
 * a frame already drawn is a blit). What it costs and what it cannot
 * do is written out on `makeHeatShimmer`.
 *
 * A NIGHT SKY IS THE DARK FRAME ADDED LIGHT SURVIVES. Stars and
 * aurora both ADD light, which is right only while the frame behind
 * them is dark — godrays.js measured 12.8% of a pale frame blown past
 * 0.97 by exactly this. So both take the same `ambient` escape it has.
 * The DEFAULT is the night build, because a star field belongs to
 * exactly one frame and that frame is dark; `ambient: 0.9` still buys
 * the blend that cannot clip, and it is still measurably harmless over
 * a daylight sky (blown_frac 0.0000 on this renderer).
 *
 * Nothing here is tone-map-exempt except the shimmer, so all of it is
 * graded for ACES at exposure 1.0 with no post chain: added light that
 * reaches alpha 1 comes back out of the curve WHITE, so the aurora
 * saturates toward a ceiling below 1 and the band's gain is spent on
 * structure rather than on level.
 *
 * Neither sky field writes depth, casts a shadow or takes part in fog:
 * a star is at infinity and `FogExp2` at the night density erases
 * anything past ~400 m, which is why `worldShell()`'s own dome runs
 * `fog: false` too.
 */

import * as THREE from 'three';
import { attachDisposal, snapshotResources } from './lifecycle.js';

import { lehmer } from './noise.js';
import {
    instancedQuad, keepOutOfDepthPasses, makeShaderMaterial,
    tickShaders } from './shader.js';

// Backdrop paint order among TRANSPARENT meshes. three renders every
// opaque object before any transparent one whatever renderOrder says,
// so these only sort the sky against itself; depth testing is what
// keeps the scene in front of it.
const ORDER_MILKYWAY = -1.75;
const ORDER_STARS = -1.70;
const ORDER_AURORA = -1.60;
const ORDER_SHIMMER = 20;

const _SIZE = new THREE.Vector2();

/**
 * Copy the frame drawn so far into a texture the shimmer can sample.
 *
 * `renderer.copyFramebufferToTexture` cannot do this job: measured on
 * ANGLE/D3D12, `copyTexSubImage2D` is INVALID_OPERATION out of the
 * MULTISAMPLED buffer every renderer here draws into (the composer
 * runs 4x MSAA, the canvas `antialias: true`), and again into an sRGB
 * destination. A resolve BLIT survives both, on one condition: the
 * destination's internal format must equal the source's exactly — so
 * the type is taken from the bound target (HalfFloat under the post
 * chain, byte straight to canvas) and the colour space left off, or
 * three allocates SRGB8_ALPHA8 and the copy is refused again.
 *
 * This is a resolve of a frame already drawn, not a second pass over
 * the scene: it does not render the scene again.
 */
function frameGrabber(mat) {
    let tex = null;
    let fbo = null;
    let context = null;
    let disposed = false;
    let warned = false;
    const release = () => {
        if (tex) tex.dispose();
        if (fbo && context) context.deleteFramebuffer(fbo);
        tex = null;
        fbo = null;
        mat.uniforms.uScene.value = null;
    };
    const grab = (renderer) => {
        if (disposed) return;
        const gl = renderer.getContext();
        if (context && context !== gl) release();
        context = gl;
        if (typeof gl.blitFramebuffer !== 'function') {
            if (!warned) {
                warned = true;
                console.warn(
                    'makeHeatShimmer: no WebGL2 blitFramebuffer — the ' +
                    'card cannot read the frame and draws nothing.');
            }
            return;
        }
        const rt = renderer.getRenderTarget();
        if (rt) _SIZE.set(rt.width, rt.height);
        else renderer.getDrawingBufferSize(_SIZE);
        const w = _SIZE.x, h = _SIZE.y;
        const type = rt ? rt.texture.type : THREE.UnsignedByteType;
        if (!tex || tex.image.width !== w || tex.image.height !== h
            || tex.type !== type) {
            if (tex) tex.dispose();
            if (fbo) { gl.deleteFramebuffer(fbo); fbo = null; }
            tex = new THREE.FramebufferTexture(w, h);
            tex.type = type;
            tex.colorSpace = THREE.NoColorSpace;
            tex.minFilter = THREE.LinearFilter;
            tex.magFilter = THREE.LinearFilter;
            mat.uniforms.uScene.value = tex;
        }
        renderer.initTexture(tex);
        const handle = renderer.properties.get(tex).__webglTexture;
        if (!handle) return;
        if (!fbo) fbo = gl.createFramebuffer();
        const prev = gl.getParameter(gl.DRAW_FRAMEBUFFER_BINDING);
        try {
            gl.bindFramebuffer(gl.DRAW_FRAMEBUFFER, fbo);
            gl.framebufferTexture2D(gl.DRAW_FRAMEBUFFER, gl.COLOR_ATTACHMENT0,
                                    gl.TEXTURE_2D, handle, 0);
            gl.blitFramebuffer(0, 0, w, h, 0, 0, w, h,
                               gl.COLOR_BUFFER_BIT, gl.NEAREST);
        } finally {
            // Restore even if an integration supplies a throwing GL wrapper.
            gl.bindFramebuffer(gl.DRAW_FRAMEBUFFER, prev);
        }
        mat.uniforms.uResolution.value.set(w, h);
        // Straight to canvas the frame is already sRGB-encoded and
        // <colorspace_fragment> is about to encode it again; into a
        // render target it is linear and that chunk is identity.
        mat.uniforms.uEncoded.value =
            (!rt && renderer.outputColorSpace === THREE.SRGBColorSpace)
                ? 1 : 0;
    };
    grab.dispose = () => {
        if (disposed) return;
        disposed = true;
        release();
        context = null;
    };
    return grab;
}

/**
 * Air over a fire, an exhaust or a summer road: the backdrop, moved.
 *
 * Draws NOTHING of its own. The fragment's only colour source is a
 * copy of the frame taken the instant before this card is drawn,
 * sampled at coordinates pushed around by a rising noise field — so
 * there is no shape to see, only a wobble in whatever stands behind.
 * Verified over a striped backdrop: a stripe edge travels about 6 px
 * in a 560 px frame at the default strength.
 *
 * WHAT IT CANNOT DO, because the copy carries colour and no depth:
 * (1) an object standing IN FRONT of the card can be smeared into it,
 * since the sample has no way to reject a nearer pixel — keep the card
 * behind the things it belongs to; (2) it cannot displace anything
 * drawn AFTER it (a later transparent pass, bloom, the AO blend);
 * (3) it costs one full-frame copy per shimmer per view, so use one
 * card per heat source, not a field of them.
 *
 * @param {object} [opts]
 *   `width` / `height` card size in metres (default 2.4 x 3.2); the
 *   card billboards about its own centre and rests on y = 0, so put
 *   the group AT the fire; `strength` peak displacement as a fraction
 *   of FRAME HEIGHT (default 0.012 — refraction is an angle, so the
 *   screen shift of a distant plume is the same as a near one);
 *   `speed` how fast the cells rise (default 1); `cells` how many
 *   convection cells span the card's height (default 3.4 — fewer is
 *   an engine's slow roll, more is a fire's boil); `seed` PRNG seed
 *   (default 5).
 * @returns {THREE.Group} Named `HeatShimmer`, resting on y = 0, with
 *   `userData.update(t)`. No `ambient` escape and none needed: a
 *   material whose output IS the backdrop cannot brighten a frame it
 *   never adds to.
 */
export function makeHeatShimmer(opts = {}) {
    const width = opts.width === undefined ? 2.4 : opts.width;
    const height = opts.height === undefined ? 3.2 : opts.height;
    const strength = opts.strength === undefined ? 0.012 : opts.strength;
    const speed = opts.speed === undefined ? 1 : opts.speed;
    // Cell SIZE is set by the air, not by the card: scaling the count
    // with the card keeps a metre-wide cell on a 3 m plume and on a
    // 30 m one, where a fixed count would give the big card boulders.
    const cells = opts.cells === undefined
        ? Math.max(2.5, height / 0.95) : opts.cells;
    const rnd = lehmer(opts.seed === undefined ? 5 : opts.seed);

    // The billboard sweeps a sphere about the card's centre, which is
    // half a card up: a stated radius, never instancedQuad's 10 km
    // default, or a rig that frames on it frames 10 km of empty air.
    const reach = height * 0.5 + Math.hypot(width, height) * 0.5;
    const geom = instancedQuad(1, width, height, reach);
    const mat = shimmerMaterial(
        strength, speed, cells, width / height, height, rnd() * 97);
    const mesh = new THREE.Mesh(geom, mat);
    mesh.name = 'HeatShimmer';
    // Last of the transparents, so the copy holds everything the
    // shimmer is supposed to bend.
    mesh.renderOrder = ORDER_SHIMMER;

    const g = new THREE.Group();
    g.name = 'HeatShimmer';
    g.add(mesh);
    keepOutOfDepthPasses(g);
    // keepOutOfDepthPasses OWNS onBeforeRender (it blanks the draw
    // range under an override material), so the grab wraps its guard
    // instead of replacing it — and only fires for our own material,
    // never inside the AO or shadow pass.
    const guard = mesh.onBeforeRender;
    const grab = frameGrabber(mat);
    mesh.onBeforeRender = (r, s, cam, geo, m) => {
        guard.call(mesh, r, s, cam, geo, m);
        if (m === mat) grab(r);
    };
    g.userData.update = g.userData.tick = (t) => tickShaders(g, t);
    const owned = snapshotResources(g);
    owned.add(grab);
    return attachDisposal(g, owned);
}

/** Resample the grabbed frame; emit no colour that is not in it. */
function shimmerMaterial(strength, speed, cells, aspect, height, phase) {
    return makeShaderMaterial({
        name: 'HeatShimmer',
        uniforms: {
            uScene: { value: null },
            uResolution: { value: new THREE.Vector2(1, 1) },
            uEncoded: { value: 0 },
            uStrength: { value: strength },
            uSpeed: { value: speed },
            uCells: { value: cells },
            uAspect: { value: aspect },
            uPhase: { value: phase },
            uCentre: { value: new THREE.Vector3(0, height * 0.5, 0) },
        },
        varyings: 'varying vec2 vUv;',
        vertexHead: 'attribute vec3 aCorner; uniform vec3 uCentre;',
        vertexMain: [
            '  vUv = uv;',
            // Rows of the model-view rotation are the LOCAL directions
            // that map to screen x and y, so the card faces the camera
            // whatever the group is turned to.
            '  mat3 mv3 = mat3(modelViewMatrix);',
            '  vec3 rt = vec3(mv3[0][0], mv3[1][0], mv3[2][0]);',
            '  vec3 up = vec3(mv3[0][1], mv3[1][1], mv3[2][1]);',
            '  transformed = uCentre + rt * aCorner.x + up * aCorner.y;',
        ].join('\n'),
        fragmentHead: [
            'uniform sampler2D uScene;',
            'uniform vec2 uResolution;',
            'uniform float uStrength; uniform float uSpeed;',
            'uniform float uCells; uniform float uAspect;',
            'uniform float uPhase; uniform float uEncoded;',
            // The exact sRGB EOTF, because this has to be the precise
            // inverse of the encode three is about to re-apply.
            'vec3 astraCelDecode(vec3 c) {',
            '  return mix(pow((c + 0.055) / 1.055, vec3(2.4)),',
            '      c / 12.92, step(c, vec3(0.04045)));',
            '}',
        ].join('\n'),
        fragmentMain: [
            '  vec2 uv = gl_FragCoord.xy / uResolution;',
            '  float t = uTime * uSpeed;',
            '  vec2 q = vec2(vUv.x * uCells * uAspect, vUv.y * uCells);',
            // Cells RISE, so the field scrolls down through them, and
            // the two axes read different noise or the whole card
            // slides sideways as one sheet.
            '  float nx = astraFbm2(q + vec2(uPhase, -t), 3);',
            '  float ny = astraFbm2(q + vec2(uPhase + 31.7, -t * 1.27),',
            '      3);',
            // astraFbm2 lands in 0..0.875 about a mean of 0.4375 and
            // its octaves almost never align, so the raw signal swings
            // by about a tenth: centred and stretched, or uStrength
            // means a twelfth of what it says.
            '  vec2 n = clamp((vec2(nx, ny) - 0.4375) * 4.0,',
            '      vec2(-1.0), vec2(1.0));',
            // Hot air spreads and slows as it climbs, so the wobble
            // grows up the plume rather than being uniform.
            '  vec2 push = n * uStrength * (0.30 + 0.95 * vUv.y);',
            // uStrength is a fraction of frame HEIGHT; x has to be
            // divided by the aspect or the wobble is anisotropic.
            '  push.x *= uResolution.y / uResolution.x;',
            '  vec3 behind = texture2D(uScene, uv + push).rgb;',
            '  behind = mix(behind, astraCelDecode(behind), uEncoded);',
            // The envelope: a plume, not a rectangle. A hard edge here
            // is the one way this effect becomes a visible shape.
            '  float wan = (astraFbm2(vec2(uPhase, vUv.y * 1.7 - t * 0.5),',
            '      2) - 0.5) * 0.34;',
            '  float u = abs(vUv.x - 0.5 + wan) * 2.0;',
            '  float body = 0.45 + 0.85 * astraFbm2(q * 0.75',
            '      + vec2(uPhase, -t * 0.7), 3);',
            '  float a = (1.0 - smoothstep(0.30, 1.0, u))',
            '      * smoothstep(0.0, 0.12, vUv.y)',
            '      * (1.0 - smoothstep(0.40, 1.0, vUv.y)) * body;',
            '  a = clamp(a * 1.9, 0.0, 1.0);',
            '  if (a < 0.004) discard;',
            '  gl_FragColor = vec4(behind, a);',
        ].join('\n'),
        // additive:true buys the ALPHA fade into fog — the ordinary
        // fog chunk would mix the resampled pixel toward the fog
        // colour, which paints haze the frame already has. The blend
        // stays NORMAL: this replaces pixels, it does not add light.
        additive: true,
        blending: THREE.NormalBlending,
        // Tone mapping already happened to the pixels being resampled;
        // running it again is the one way this shader could change a
        // colour instead of moving it.
        toneMapped: false,
        depthWrite: false,
        transparent: true,
        side: THREE.DoubleSide,
    });
}

/**
 * A night sky: mostly faint stars, a Milky Way, and a slow twinkle.
 *
 * Brightness is drawn from the real one — the number of stars brighter
 * than magnitude m grows as 10^(0.6 m), so half of any field sits past
 * magnitude 5.5 and under 1% of it is what anyone would call bright.
 * A field of equal dots is the giveaway that it was not.
 *
 * Sits OUTSIDE the world and never occludes it: no depth write, no
 * shadow, and every star fades out below ~6 degrees of elevation the
 * way real extinction takes them, so a horizon ridge is never speckled.
 * Its meshes are named `StarField` / `MilkyWayStars` on purpose — the
 * scene renderer keys its content-fit and occluder filters on `star` /
 * `sky` in a mesh name, and a 2 km sphere that misses those filters
 * becomes the content bbox and pushes every camera out of the scene.
 *
 * Combine with a dome, never instead of one: `worldShell({mood:
 * 'night'})` (lib/environment.js) draws the gradient this sits in
 * front of, or `makeSky(scene, { rig })` with a night `sunRig()` for
 * a graded physical dome. Both leave `scene.background` null and
 * neither writes depth, so the stars paint over either one.
 *
 * @param {object} [opts]
 *   `count` stars (default 1400); `radius` metres to the star sphere
 *   (default 2000 — inside `worldShell`'s 4 km dome and the camera's
 *   far plane, outside any content); `magnitude` overall brightness
 *   gain for both stars and unresolved Milky Way light (default 1;
 *   0 hides both); `twinkle` scintillation depth 0..1 (default
 *   0.35, over periods of 4-9 s); `milkyWay` the band of unresolved
 *   light, which also pulls part of the field into it (default true);
 *   `seed` PRNG seed (default 3); `ambient` how bright the frame
 *   behind is, 0 a real night to 1 daylight (default 0.2). Added
 *   light cannot see what it adds to, so from 0.35 up the field
 *   switches from ADDING to a plain blend that cannot clip; pass
 *   `ambient: 0.9` when the sky behind is genuinely bright.
 * @returns {THREE.Group} Named `Stars`, centred on the camera's world
 *   — put it at the origin — with `userData.update(t)`.
 */
export function makeStars(opts = {}) {
    const count = Math.max(1, Math.round(
        opts.count === undefined ? 1400 : opts.count));
    const radius = opts.radius === undefined ? 2000 : opts.radius;
    const gain = Math.max(0, opts.magnitude === undefined ? 1 : opts.magnitude);
    const twinkle = Math.min(Math.max(
        opts.twinkle === undefined ? 0.35 : opts.twinkle, 0), 1);
    const seed = opts.seed === undefined ? 3 : opts.seed;
    // 0.2, not the 0.85 this shipped with. A star field belongs to ONE
    // frame — a night one — and at 0.85 the safe blend put the whole
    // sky inside a single 8-bit bucket on this renderer (modal_frac
    // 0.95, sky luminance flat at 0.19): the catalog promised stars and
    // the frame had none. The bright build is still one argument away.
    const ambient = Math.min(Math.max(
        opts.ambient === undefined ? 0.2 : opts.ambient, 0), 1);
    const bright = ambient >= 0.35;
    const rnd = lehmer(seed);
    // Galactic pole: the band is a great circle, so one vector fixes
    // where it crosses the sky.
    const pole = new THREE.Vector3(0.34, 0.83, -0.44).normalize();

    const g = new THREE.Group();
    g.name = 'Stars';
    if (opts.milkyWay !== false) g.add(milkyWayMesh(radius, pole, bright, gain));
    g.add(starMesh(count, radius, gain, twinkle, rnd, pole, bright,
                   opts.milkyWay !== false));
    g.userData.update = g.userData.tick = (t) => tickShaders(g, t);
    return attachDisposal(keepOutOfDepthPasses(g), snapshotResources(g));
}

/**
 * Relative flux for one star, drawn from the magnitude distribution.
 *
 * Inverse-samples N(<= m) ~ 10^(0.6 m) over magnitude 0..6 and returns
 * the linear flux 10^(-0.4 m) — 1 for the brightest star in the field,
 * 0.004 for the faintest, median about 0.006. That skew IS the sky.
 */
function starFlux(u) {
    const top = Math.pow(10, 0.6 * 6);
    const m = Math.log(1 + u * (top - 1)) / Math.LN10 / 0.6;
    return Math.pow(10, -0.4 * m);
}

/** The stars themselves: GTAO-safe billboards, one per instance. */
function starMesh(count, radius, gain, twinkle, rnd, pole, bright,
                  banded) {
    const geom = instancedQuad(count, 1, 1, radius * 1.02);
    const dir = new Float32Array(count * 3);
    const star = new Float32Array(count * 4);
    const v = new THREE.Vector3();
    for (let i = 0; i < count; i++) {
        // Upper hemisphere only: a star under the horizon is a star
        // behind the ground, and the shader fades the last few degrees.
        v.set(rnd() * 2 - 1, rnd(), rnd() * 2 - 1);
        if (v.lengthSq() < 1e-6) v.set(0, 1, 0);
        v.normalize();
        // Part of the field belongs to the band: flattening a share of
        // it toward the galactic plane is what makes the Milky Way
        // read as stars rather than as painted haze.
        if (banded && rnd() < 0.42) {
            v.addScaledVector(pole, -v.dot(pole) * (0.55 + 0.4 * rnd()));
            v.normalize();
        }
        dir[i * 3] = v.x; dir[i * 3 + 1] = v.y; dir[i * 3 + 2] = v.z;
        star[i * 4] = starFlux(rnd());
        star[i * 4 + 1] = rnd();
        // 4-9 s periods: the eye reads anything faster as fireflies.
        star[i * 4 + 2] = 0.7 + 0.85 * rnd();
        star[i * 4 + 3] = rnd();
    }
    geom.setAttribute('aDir', new THREE.InstancedBufferAttribute(dir, 3));
    geom.setAttribute('aStar', new THREE.InstancedBufferAttribute(star, 4));
    const mesh = new THREE.Mesh(
        geom, starMaterial(radius, gain, twinkle, bright));
    mesh.name = 'StarField';
    mesh.renderOrder = ORDER_STARS;
    return mesh;
}

function starMaterial(radius, gain, twinkle, bright) {
    return makeShaderMaterial({
        name: 'StarField',
        uniforms: {
            // ~2 px across at 560 px and fov 55 for the faintest star,
            // ~5 px for the brightest: a point source, not a disc.
            uSize: { value: radius * 0.0042 },
            uRadius: { value: radius },
            uTwinkle: { value: twinkle },
            uGain: { value: gain * (bright ? 0.55 : 1.0) },
        },
        varyings: 'varying vec2 vUv; varying float vGain;'
            + ' varying vec3 vTint;',
        vertexHead: 'attribute vec3 aCorner; attribute vec3 aDir;'
            + ' attribute vec4 aStar; uniform float uSize;'
            + ' uniform float uRadius; uniform float uTwinkle;',
        vertexMain: [
            '  vUv = uv;',
            '  float f = aStar.x;',
            '  float tw = 1.0 + uTwinkle * sin(uTime * aStar.z',
            '      + aStar.y * 6.2831853);',
            // Extinction: the last few degrees of sky eat a star, which
            // is also what keeps the field off a horizon silhouette.
            '  vGain = pow(f, 0.38) * tw * smoothstep(0.0, 0.11, aDir.y);',
            // Spectral class, biased the way a real field is: warm K/M
            // stars are the minority, so the sample is pushed toward
            // the blue-white end rather than split down the middle.
            '  vec3 hot = mix(vec3(1.0, 0.74, 0.52), vec3(0.70, 0.81, 1.0),',
            '      pow(aStar.w, 0.7));',
            // At threshold the eye has no colour at all: only the
            // bright ones may show their class, or a faint field turns
            // into scattered red and blue confetti.
            '  vTint = mix(vec3(0.93, 0.95, 1.0), hot,',
            '      0.22 + 0.78 * pow(f, 0.30));',
            '  mat3 mv3 = mat3(modelViewMatrix);',
            '  vec3 rt = vec3(mv3[0][0], mv3[1][0], mv3[2][0]);',
            '  vec3 up = vec3(mv3[0][1], mv3[1][1], mv3[2][1]);',
            '  float s = uSize * (0.55 + 0.85 * pow(f, 0.25));',
            '  transformed = aDir * uRadius',
            '      + (rt * aCorner.x + up * aCorner.y) * s;',
        ].join('\n'),
        fragmentHead: 'uniform float uGain;',
        fragmentMain: [
            '  float d = length(vUv - 0.5) * 2.0;',
            '  if (d > 1.0) discard;',
            // A tight core in a faint halo. A flat disc is the look
            // that reads as confetti.
            '  float a = (pow(1.0 - d, 4.0) + 0.20 * pow(1.0 - d, 1.4))',
            '      * vGain * uGain;',
            // A halo this soft steps in 8-bit: one sub-LSB of noise per
            // pixel costs nothing and buys the rings back as grain.
            '  a += (astraHash21(gl_FragCoord.xy) - 0.5) * 0.006;',
            '  if (a < 0.004) discard;',
            '  gl_FragColor = vec4(vTint, clamp(a, 0.0, 1.0));',
        ].join('\n'),
        // 3dcode: no-fog — a star is at infinity, and FogExp2 at the
        // night density erases anything past ~400 m. worldShell's own
        // dome opts out the same way.
        fog: false,
        blending: bright ? THREE.NormalBlending : THREE.AdditiveBlending,
        transparent: true,
        depthWrite: false,
    });
}

/** The band: unresolved light, dust lanes, and a bulge to aim at. */
function milkyWayMesh(radius, pole, bright, gain) {
    const geom = new THREE.SphereGeometry(radius * 1.01, 48, 24);
    // Two axes across the band give the noise a seamless 2D coordinate
    // — an atan around the pole would print a meridian down the sky.
    const a = new THREE.Vector3(0, 1, 0).cross(pole).normalize();
    const b = new THREE.Vector3().crossVectors(pole, a).normalize();
    const core = a.clone().multiplyScalar(0.72)
        .addScaledVector(b, 0.55).addScaledVector(pole, 0.06).normalize();
    const mat = makeShaderMaterial({
        name: 'MilkyWay',
        uniforms: {
            uPole: { value: pole.clone() },
            uAxisA: { value: a },
            uAxisB: { value: b },
            uCore: { value: core },
            // Unresolved starlight is faint and nearly neutral. The
            // previous warm, high-alpha band became a luminous fog bank.
            uArm: { value: new THREE.Color(0xc3c9d9) },
            uHub: { value: new THREE.Color(0xd5cbbb) },
            uGain: { value: gain * (bright ? 0.018 : 0.045) },
        },
        varyings: 'varying vec3 vDir;',
        vertexMain: '  vDir = normalize(position);',
        fragmentHead: 'uniform vec3 uPole; uniform vec3 uAxisA;'
            + ' uniform vec3 uAxisB; uniform vec3 uCore;'
            + ' uniform vec3 uArm; uniform vec3 uHub;'
            + ' uniform float uGain;',
        fragmentMain: [
            '  vec3 d = normalize(vDir);',
            '  float s = dot(d, uPole);',
            '  float band = exp(-(s * s) / 0.009);',
            // Angular detail follows the galactic plane without a seam.
            // Fine star clouds sit inside the narrow unresolved band.
            '  vec2 p = vec2(dot(d, uAxisA), dot(d, uAxisB)) * 22.0;',
            '  float n = astraFbm2(p + vec2(s * 12.0, 0.0), 4);',
            // Dark lanes are dust in front of the band, so they CUT it
            // rather than tinting it; a smooth gaussian is a smear.
            '  float lanes = smoothstep(0.28, 0.72, n);',
            // A second field finer again: star clouds INSIDE the arms.
            // One scale of noise is a smear at any contrast.
            '  float fine = astraFbm2(p * 2.9 + vec2(11.0, s * 4.0), 3);',
            '  lanes *= 0.45 + 0.75 * fine;',
            // The Great Rift: dust lying IN the plane, so it splits the
            // band down its own spine instead of dimming it evenly.
            // Without it the brightest pixel is the exact centre line,
            // which is the one place the real band is darkest.
            '  float spine = abs(s + (astraNoise2(p * 0.35) - 0.5) * 0.025);',
            '  float rift = smoothstep(0.012, 0.065, spine);',
            '  float bulge = exp(-(1.0 - dot(d, uCore)) / 0.055);',
            '  float a = band * (0.08 + 0.92 * lanes)',
            '      * (0.55 + 0.75 * bulge) * mix(0.20, 1.0, rift) * uGain',
            // Extinction is a LONG ramp. Ending it at 0.12 put the
            // whole fade inside the sky a level camera actually frames,
            // and drew a straight horizontal edge across the band.
            '      * smoothstep(-0.03, 0.26, d.y);',
            // Warm hub, cool arms, and the star clouds carry the mix so
            // the band has warm and cold patches instead of one tint.
            '  vec3 c = mix(uArm, uHub,',
            '      clamp(bulge * 0.65 + (fine - 0.5) * 0.25, 0.0, 1.0));',
            // Relative grain preserves zero and scales with magnitude;
            // an absolute alpha floor would resurrect a disabled band.
            '  a *= 1.0 + (astraHash21(gl_FragCoord.xy) - 0.5) * 0.10;',
            '  if (a <= 0.0) discard;',
            '  gl_FragColor = vec4(c, clamp(a, 0.0, 1.0));',
        ].join('\n'),
        // 3dcode: no-fog — see StarField.
        fog: false,
        side: THREE.BackSide,
        blending: bright ? THREE.NormalBlending : THREE.AdditiveBlending,
        transparent: true,
        depthWrite: false,
    });
    const mesh = new THREE.Mesh(geom, mat);
    mesh.name = 'MilkyWayStars';
    mesh.renderOrder = ORDER_MILKYWAY;
    return mesh;
}

/**
 * The vertical emission profile of one curtain, bottom edge to top.
 *
 * An aurora is brightest at its LOWER BORDER, where the electrons
 * finally stop, and fades up the column — a curtain lit evenly, or
 * lit at the top, is the tell. Baked per vertex so the profile is
 * data the CPU owns rather than a constant buried in GLSL.
 */
function auroraGlow(v) {
    const rim = 0.35 + 0.65 * Math.exp(-v * 16);
    const k = 1 - Math.min(1, Math.max(0, (v - 0.72) / 0.28));
    return rim * Math.exp(-v * 2.3) * k * k * (3 - 2 * k);
}

/**
 * Curtains of light: vertical rays, folding, brightest along the foot.
 *
 * Built as ribbons, not sheets of one brightness: each carries a baked
 * `aGlow` profile that falls from its bottom edge upward, the rays run
 * across it as antialiased strokes, and the folds brighten where they
 * turn edge-on because a sheet's chord through the eye grows there —
 * that edge-on flare is what makes an aurora read as a hanging curtain
 * instead of a painted band.
 *
 * The mesh is named `AuroraSkyCurtains`: the scene renderer excludes
 * sky envelopes from its content fit by the token `sky` in a mesh
 * name, and a 1.4 km curtain that misses it becomes the content bbox.
 *
 * @param {object} [opts]
 *   `radius` metres from the origin to the curtain band (default 1400
 *   — inside `makeStars`'s 2 km sphere); `height` metres of curtain
 *   (default 400); the foot hangs at `radius * 0.30`, so translate the
 *   group in Y to raise or lower the display; `color` the main
 *   emission (default 0x54ffa8, the 557.7 nm oxygen green — the rim
 *   reddens and the top goes violet on their own); `activity` 0..1
 *   (default 0.5) driving how many curtains there are, how hard they
 *   fold and how fast they drift; `seed` PRNG seed (default 9);
 *   `ambient` how bright the frame behind is, 0 a real night to 1
 *   daylight (default 0.2) — see `makeStars`; pass `ambient: 0.9` when
 *   the sky behind is genuinely bright.
 *
 * The curtains are laid one per equal slice of the compass, jittered
 * inside it, so a display always crosses whatever the camera is looking
 * at — n independent draws leave holes a hundred degrees wide.
 * @returns {THREE.Group} Named `Aurora`, with `userData.update(t)`.
 */
export function makeAurora(opts = {}) {
    const radius = opts.radius === undefined ? 1400 : opts.radius;
    const height = opts.height === undefined ? 400 : opts.height;
    const activity = Math.min(Math.max(
        opts.activity === undefined ? 0.5 : opts.activity, 0), 1);
    const seed = opts.seed === undefined ? 9 : opts.seed;
    // 0.2 for the same reason as `makeStars`: an aurora is a night
    // effect, and the daylight-safe blend rendered nothing here.
    const ambient = Math.min(Math.max(
        opts.ambient === undefined ? 0.2 : opts.ambient, 0), 1);
    const bright = ambient >= 0.35;
    const color = opts.color
        ? new THREE.Color(opts.color) : new THREE.Color(0x54ffa8);
    const rnd = lehmer(seed);
    const n = 3 + Math.round(activity * 4);
    const base = radius * 0.30;
    const swing = radius * (0.02 + 0.10 * activity);

    const geom = curtainGeometry(n, rnd, radius, base, height, activity);
    // The vertex stage folds the ribbon after the sphere is computed,
    // so the stated one has to allow for the whole swing or the band
    // is culled the moment a fold carries it past the edge.
    geom.boundingSphere.radius += swing;
    const mesh = new THREE.Mesh(
        geom, auroraMaterial(color, activity, swing, bright));
    mesh.name = 'AuroraSkyCurtains';
    mesh.renderOrder = ORDER_AURORA;

    const g = new THREE.Group();
    g.name = 'Aurora';
    g.add(mesh);
    g.userData.update = g.userData.tick = (t) => tickShaders(g, t);
    return attachDisposal(keepOutOfDepthPasses(g), snapshotResources(g));
}

/** All curtains in one ribbon geometry, tagged by `aCurtain`. */
function curtainGeometry(n, rnd, radius, base, height, activity) {
    const nu = 128, nv = 24;
    const pos = [], uv = [], glow = [], tag = [], idx = [];
    for (let c = 0; c < n; c++) {
        // STRATIFIED, not random: n independent draws clump, and a
        // measured one (seed 11, n = 5) left a 100-degree hole that a
        // camera looking into it saw an empty sky through. The real
        // auroral oval is an ARC across the whole sky, so one curtain
        // per equal slice — jittered inside it — is both truer and the
        // only version that cannot miss the frame entirely.
        const slice = (Math.PI * 2) / n;
        const a0 = (c + rnd()) * slice;
        // A curtain covers PART of its slice. The span was an absolute
        // arc, so five of them at this activity ran shoulder to
        // shoulder all the way round and the display rendered as one
        // unbroken wall of bars. Measured against the slice it always
        // leaves sky between the curtains, which is the difference
        // between a display and a fence.
        const span = slice * (0.34 + 0.30 * rnd()) * (0.75 + 0.5 * activity);
        const bow = (0.08 + 0.22 * rnd()) * radius;
        const ph = rnd() * Math.PI * 2;
        const first = pos.length / 3;
        for (let j = 0; j <= nv; j++) {
            // v packed toward the foot: the bright rim is a tenth of
            // the column and an even ladder smears it into a gradient.
            const v = Math.pow(j / nv, 1.7);
            const y = base + height * v;
            const gl = auroraGlow(v);
            for (let i = 0; i <= nu; i++) {
                const u = i / nu;
                const a = a0 + (u - 0.5) * span;
                // Two beats of bow: one arc is a fence, two make the
                // hairpin folds a curtain actually hangs in.
                const r = radius
                    + bow * Math.sin(u * 6.2831853 * 1.5 + ph)
                    + bow * 0.45 * Math.sin(u * 6.2831853 * 3.7 + ph * 2.1);
                pos.push(Math.cos(a) * r, y, Math.sin(a) * r);
                uv.push(u, v);
                glow.push(gl);
                tag.push((c + 0.5) / n, radius * span);
            }
        }
        for (let j = 0; j < nv; j++) {
            for (let i = 0; i < nu; i++) {
                const p = first + j * (nu + 1) + i;
                idx.push(p, p + nu + 1, p + 1,
                         p + 1, p + nu + 1, p + nu + 2);
            }
        }
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
    g.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2));
    g.setAttribute('aGlow', new THREE.Float32BufferAttribute(glow, 1));
    g.setAttribute('aCurtain', new THREE.Float32BufferAttribute(tag, 2));
    g.setIndex(idx);
    g.computeVertexNormals();
    g.computeBoundingSphere();
    return g;
}

function auroraMaterial(color, activity, swing, bright) {
    const tint = bright ? color.clone().lerp(new THREE.Color(0xffffff),
                                             0.45) : color.clone();
    return makeShaderMaterial({
        name: 'Aurora',
        uniforms: {
            uColor: { value: tint },
            uRim: { value: new THREE.Color(0xff5f9e) },
            // Green -> TEAL -> violet, in two stops. Mixing 557.7 nm
            // green straight to violet passes through grey at the
            // halfway point, and a grey band up the middle of a curtain
            // is exactly the wash this was showing.
            uMid: { value: new THREE.Color(0x3ce6b0) },
            uTop: { value: new THREE.Color(0xa855e8) },
            uSwing: { value: swing },
            uDrift: { value: 0.03 + 0.09 * activity },
            uRays: { value: 26 + 34 * activity },
            uGain: { value: bright ? 0.30 : 0.85 },
            // The ceiling matters more than the gain. ACES desaturates
            // as it rolls off, so a curtain allowed to reach alpha 1
            // arrives WHITE — measured: 2.1% of the frame clipped past
            // 0.97 and the green gone. Held here, the same curtain
            // stays inside the part of the curve that keeps its hue.
            uPeak: { value: bright ? 0.26 : 0.50 },
        },
        varyings: 'varying vec2 vUv; varying float vGlow;'
            + ' varying vec3 vN; varying vec3 vW; varying float vTag;',
        vertexHead: [
            'attribute float aGlow;',
            'attribute vec2 aCurtain;',
            'uniform float uSwing; uniform float uDrift;',
            // One fold field, sampled twice so the shading normal can
            // follow the fold instead of the flat ribbon it left.
            'float astraCelFold(float u, float tag) {',
            '  return (astraFbm2(vec2(u * 2.6 + tag * 17.0,',
            '      uTime * uDrift), 3) - 0.5) * uSwing;',
            '}',
        ].join('\n'),
        vertexMain: [
            '  vUv = uv;',
            '  vGlow = aGlow;',
            '  vTag = aCurtain.x;',
            '  vec3 nrm = normalize(normal);',
            '  float f0 = astraCelFold(uv.x, aCurtain.x);',
            '  float f1 = astraCelFold(uv.x + 0.02, aCurtain.x);',
            '  transformed += nrm * f0;',
            '  vec3 tg = normalize(cross(vec3(0.0, 1.0, 0.0), nrm));',
            '  float slope = (f1 - f0) / max(0.02 * aCurtain.y, 1e-3);',
            '  vN = astraNormalTransform(mat3(modelMatrix),',
            '      normalize(nrm - tg * slope));',
            '  vW = (modelMatrix * vec4(transformed, 1.0)).xyz;',
        ].join('\n'),
        fragmentHead: 'uniform vec3 uColor; uniform vec3 uRim;'
            + ' uniform vec3 uMid; uniform vec3 uTop; uniform float uRays;'
            + ' uniform float uDrift; uniform float uGain;'
            + ' uniform float uPeak;',
        fragmentMain: [
            '  float v = vUv.y;',
            '  float x = vUv.x * uRays + vTag * 23.0 + uTime * uDrift;',
            // A warped filament coordinate breaks the even barcode spacing.
            // The broad envelope supplies luminous folds; narrow rays carry
            // fine detail without forcing every column to the same height.
            '  float fold = astraFbm2(vec2(vUv.x * 5.5 + vTag * 13.0,',
            '      uTime * uDrift * 0.55), 3);',
            '  x += (fold - 0.5) * 3.2;',
            '  x += (astraNoise2(vec2(x * 0.41, v * 0.7 + vTag)) - 0.5)',
            '      * (0.5 + v * 0.8);',
            '  float ray = 0.0;',
            '  float pixelWidth = max(fwidth(x), 0.001);',
            '  for (int k = -1; k <= 1; k++) {',
            '    float rayId = floor(x) + float(k);',
            '    float centre = rayId + 0.16',
            '        + 0.68 * astraHash11(rayId * 1.37 + 2.0);',
            '    float w = 0.05',
            '        + 0.13 * astraHash11(rayId * 0.37 + 2.0);',
            '    float width = sqrt(w * w + pixelWidth * pixelWidth);',
            '    float distance = x - centre;',
            '    float strand = exp(-distance * distance / (width * width))',
            '        * w / width;',
            '    float rayTop = 0.32 + 0.64 * astraNoise2(',
            '        vec2(rayId * 0.27 + vTag * 7.0, uTime * 0.035));',
            '    strand *= 1.0 - smoothstep(rayTop * 0.40, rayTop, v);',
            '    strand *= 0.20 + 1.25 * astraNoise2(vec2(rayId * 0.71,',
            '        uTime * 0.06 + vTag * 5.0));',
            '    ray += strand;',
            '  }',
            '  float curtain = smoothstep(0.16, 0.78, fold);',
            '  ray = (0.35 * curtain + 0.95 * ray) * (0.35 + curtain);',
            '  float foot = 0.018 + 0.035 * astraNoise2(',
            '      vec2(vUv.x * 13.0 + vTag * 11.0, uTime * 0.045));',
            '  ray *= smoothstep(foot, foot + 0.035, v);',
            '  vec3 eye = normalize(cameraPosition - vW);',
            // A sheet is thin: the eye cuts a LONGER chord through it
            // edge-on, so a fold turning away flares instead of
            // vanishing. Clamped, or the silhouette divides by zero.
            '  float chord = 1.0 / max(astraFacing(vN, eye), 0.46);',
            '  float ends = smoothstep(0.0, 0.10, vUv.x)',
            '      * (1.0 - smoothstep(0.90, 1.0, vUv.x));',
            '  float a = vGlow * ray * chord * ends * uGain;',
            // Saturating toward uPeak rather than toward 1: same slope
            // where the curtain is faint, a hard hue-preserving ceiling
            // where a fold turns edge-on.
            '  a = uPeak * (1.0 - exp(-a * 2.1));',
            '  a += (astraHash21(gl_FragCoord.xy) - 0.5) * 0.004;',
            '  if (a < 0.003) discard;',
            // The green body owns most of the column. Ramping to the
            // cool stop from v = 0.05 turned the whole LOWER half —
            // the half a level camera ever frames — teal, and 557.7 nm
            // green is the one colour that says aurora.
            '  vec3 c = mix(uColor, uMid, smoothstep(0.22, 0.68, v));',
            '  c = mix(c, uTop, smoothstep(0.62, 0.98, v));',
            // The lower border is where the electrons stop and the
            // nitrogen answers in magenta — the one warm note in the
            // whole display, and the thing that dates a fake aurora.
            '  c = mix(c, uRim, (1.0 - smoothstep(0.0, 0.075, v)) * 0.72);',
            // No two curtains in one display are the same green. One
            // hash per curtain, so the tint is stable in time and the
            // band never reads as a single printed colour.
            '  float hj = astraHash11(vTag * 13.7 + 4.0);',
            '  c *= mix(vec3(1.14, 0.98, 0.76), vec3(0.76, 1.0, 1.20), hj);',
            '  gl_FragColor = vec4(c, clamp(a, 0.0, 1.0));',
        ].join('\n'),
        // 3dcode: no-fog — the curtain hangs a kilometre out, past
        // every scene fog density this engine sets.
        fog: false,
        side: THREE.DoubleSide,
        blending: bright ? THREE.NormalBlending : THREE.AdditiveBlending,
        transparent: true,
        depthWrite: false,
    });
}

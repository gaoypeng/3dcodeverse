/**
 * A skein of birds crossing the sky — the cheapest motion there is.
 *
 * A still scene with nothing moving in it reads as a model shoot, and
 * animating a flock on the CPU means rebuilding a matrix per bird per
 * frame, which caps the count at the point where a flock stops looking
 * like one. So EVERY moving part lives in the vertex shader driven by
 * uTime: the whole flock rides a closed path, each bird holds its own
 * lag and offset off that path, and its wings beat on its own phase.
 * Nothing is touched per frame but one float, so ten thousand birds
 * cost what ten do, in ONE draw call.
 *
 * A bird at distance is two strokes. There is no bird model here: the
 * quad is a screen-aligned billboard whose span axis is the projection
 * of the real wing axis (so a bird flying at the camera foreshortens
 * to a mark), and the silhouette — a V whose tips lag the root — is
 * drawn in the fragment. Silhouette and motion carry it.
 *
 * COLOUR is lit, not painted. A skein is the one effect a viewer has
 * seen ten thousand times, so the tells are cheap to spot: birds all
 * one flat value, and a wingbeat "flash" that mixes toward WHITE on
 * every bird at once. Both are gone. Each bird carries its own albedo
 * (a warm/cool tilt and a value spread off `color`), the two wings
 * ROLL opposite ways through the stroke so one catches the sun while
 * the other is edge-on, and every bright thing on the bird — the
 * membrane sheen, the light through the backlit primaries — arrives
 * in `sunColor`, with `skyColor` as the ambient that keeps the shaded
 * side off black. Pass the scene's own rig colours and the flock
 * belongs to the frame it flies over.
 */

import * as THREE from 'three';

import { mulberry32 } from './noise.js';
import {
    instancedQuad, keepOutOfDepthPasses, makeShaderMaterial, readVec3,
    tickShaders,
} from './shader.js';

const _TAU = Math.PI * 2;
const _MAX_PTS = 8;   // the size of the shader's uPath array

/** Default route: a shallow ellipse over the scene, gently undulating. */
function _defaultPath(extent, height) {
    const pts = [];
    for (let k = 0; k < 6; k++) {
        const a = (k / 6) * _TAU;
        pts.push(new THREE.Vector3(
            Math.cos(a) * extent,
            height * (1 + 0.12 * Math.sin(a * 2)),
            Math.sin(a) * extent * 0.62));
    }
    return pts;
}

/**
 * Build a flock travelling as a loose skein, animated in the shader.
 *
 * @param {object} [opts]
 *   `count` individuals (default 120) — one draw call at any count;
 *   `kind` 'bird' (default) or 'fish', which swaps the silhouette and
 *   turns the body along the travel direction; `extent` radius in
 *   metres of the default flight loop (default 60); `height` its
 *   altitude in metres (default 28); `speed` metres per second along
 *   the path (default 9); `color` the ALBEDO the plumage is varied
 *   around, not the pixel colour — it is lit here; `wingBeat` beats
 *   per second (default 3.2 birds / 1.6 fish); `sunDir` direction the
 *   KEY LIGHT comes from — pass `rig.lightDir`, not `rig.sunDir`,
 *   because at night the light is the moon and `sunDir` is under the
 *   horizon, where this turns the sun term off; `sunColor` that light's
 *   colour (pass `rig.sun.color`); `skyColor` the ambient the shaded
 *   side is lit by (pass `rig.fill.color`, or the water's tint for a
 *   school); `seed` PRNG seed (default 11); `path` 3 to 8 waypoints
 *   ([x,y,z] or Vector3) of a CLOSED loop replacing the default one;
 *   `size` wingspan in metres (default extent/20, clamped 0.35..2.4).
 * @returns {THREE.Group} Named `Flock`, holding ONE instanced mesh,
 *   with `userData.tick(t)` advancing the only thing that changes.
 */
export function makeFlock(opts = {}) {
    const count = Math.max(1, opts.count === undefined ? 120 : opts.count);
    const kind = opts.kind === 'fish' ? 1 : 0;
    const extent = opts.extent === undefined ? 60 : opts.extent;
    const height = opts.height === undefined ? 28 : opts.height;
    const speed = opts.speed === undefined ? 9 : opts.speed;
    const beat = opts.wingBeat === undefined
        ? (kind ? 1.6 : 3.2) : opts.wingBeat;
    const seed = opts.seed === undefined ? 11 : opts.seed;
    const size = opts.size === undefined
        ? Math.min(2.4, Math.max(0.35, extent * 0.05)) : opts.size;
    // An ALBEDO, so it is lit rather than drawn: 0x23262c reflects 1.5%
    // of what lands on it, which is a hole in the sky at any exposure.
    // A rook's back is nearer 4%, and that is what reads as a bird.
    const color = new THREE.Color(
        opts.color === undefined ? (kind ? 0x9fb4c2 : 0x36393f) : opts.color);
    const sun = readVec3(opts.sunDir, 0.45, 0.78, 0.35).normalize();
    // Defaults are a daylight rig's own two colours; a caller with a
    // rig should hand over its sun and its sky and stop guessing.
    const sunCol = new THREE.Color(
        opts.sunColor === undefined ? 0xffe9cf : opts.sunColor);
    const skyCol = new THREE.Color(
        opts.skyColor === undefined ? 0x93b4dd : opts.skyColor);

    const pts = (opts.path && opts.path.length >= 3
        ? opts.path.map((p) => readVec3(p))
        : _defaultPath(extent, height)).slice(0, _MAX_PTS);
    // Uniform Catmull-Rom, closed: the same curve the shader evaluates,
    // so `speed` is honest metres per second rather than a loop rate.
    const curve = new THREE.CatmullRomCurve3(pts, true, 'catmullrom', 0.5);
    const loop = Math.max(curve.getLength(), 1e-3);

    const geom = instancedQuad(count, 1, 1);
    const spread = size * 6;
    // Strung out along the route, never wrapped onto its own tail.
    const skein = Math.min(loop * 0.45, count * size * 2.4);
    const rand = mulberry32(seed);
    const off = new Float32Array(count * 3);
    const ext = new Float32Array(count * 4);
    let wide = 0, tall = 0;
    for (let i = 0; i < count; i++) {
        const t = count > 1 ? i / (count - 1) : 0;
        const lag = t * skein + (rand() - 0.5) * (skein / count);
        // A skein is a wavy line, not a block: the slow sinusoid is the
        // shape, the jitter keeps it from reading as a drawn curve.
        off[i * 3] = Math.sin(t * 5.0) * spread * 0.5
            + (rand() - 0.5) * spread * 0.7;
        off[i * 3 + 1] = Math.sin(t * 3.3 + 1.0) * spread * 0.22
            + (rand() - 0.5) * spread * 0.45;
        off[i * 3 + 2] = lag / loop;
        ext[i * 4] = rand() * _TAU;
        ext[i * 4 + 1] = 0.78 + rand() * 0.5;
        ext[i * 4 + 2] = 0.85 + rand() * 0.35;
        ext[i * 4 + 3] = size * (0.25 + rand() * 0.55);
        wide = Math.max(wide, Math.abs(off[i * 3]) + ext[i * 4 + 3] * 1.4);
        tall = Math.max(tall, Math.abs(off[i * 3 + 1]) + ext[i * 4 + 3]);
    }
    geom.setAttribute('iOff', new THREE.InstancedBufferAttribute(off, 3));
    geom.setAttribute('iExtra', new THREE.InstancedBufferAttribute(ext, 4));
    // `position` is all zeros, so the bounds three would compute are a
    // point at the origin: framing, culling and the census all need the
    // volume the shader actually flies the flock through.
    const box = new THREE.Box3().setFromPoints(curve.getPoints(64));
    box.expandByVector(new THREE.Vector3(
        wide + size, tall + size, wide + size));
    geom.boundingBox = box;
    geom.boundingSphere = box.getBoundingSphere(new THREE.Sphere());

    const path = [];
    for (let k = 0; k < _MAX_PTS; k++) {
        path.push(pts[Math.min(k, pts.length - 1)].clone());
    }
    const mesh = new THREE.Mesh(geom, flockMaterial({
        path, n: pts.length, rate: speed / loop, size: size * 0.5,
        beat, kind, color, sun, sunCol, skyCol,
    }));
    mesh.name = kind ? 'FlockFish' : 'FlockBirds';
    mesh.frustumCulled = false;   // every position lives in the shader
    mesh.renderOrder = 3;

    const g = new THREE.Group();
    g.name = 'Flock';
    g.add(mesh);
    g.userData.tick = (t) => tickShaders(g, t);
    return keepOutOfDepthPasses(g);
}

/** The one material: path, formation, beat and silhouette, all in GLSL. */
function flockMaterial(o) {
    return makeShaderMaterial({
        name: 'Flock',
        uniforms: {
            uPath: { value: o.path },
            uPathN: { value: o.n },
            uRate: { value: o.rate },
            uSize: { value: o.size },
            uBeat: { value: o.beat },
            uKind: { value: o.kind },
            uColor: { value: o.color },
            uSun: { value: o.sun },
            uSunCol: { value: o.sunCol },
            uSky: { value: o.skyCol },
        },
        varyings: [
            'varying vec2 vUv; varying float vPhase;',
            // x,y: the sun against the bird's own up and wing axes —
            // the roll of the stroke rotates between them. z: how much
            // the flock is BACKLIT from this camera. w: this bird's
            // stroke depth, which is also its silhouette's bend.
            'varying vec4 vLit; varying vec3 vTint;',
        ].join(' '),
        vertexHead: [
            'attribute vec3 aCorner;',
            'attribute vec3 iOff;',
            'attribute vec4 iExtra;',
            'uniform vec3 uPath[8];',
            'uniform float uPathN; uniform float uRate;',
            'uniform float uSize; uniform float uBeat; uniform float uKind;',
            'uniform vec3 uColor; uniform vec3 uSun;',
            // Indexed by a loop counter, which is the one form of array
            // indexing every GLSL dialect here accepts.
            'vec3 flockPt(float i) {',
            '  int idx = int(i + 0.5);',
            '  vec3 p = uPath[0];',
            '  for (int k = 0; k < 8; k++) { if (k == idx) p = uPath[k]; }',
            '  return p;',
            '}',
            'vec3 flockPath(float u) {',
            '  float n = uPathN;',
            '  float s = fract(u) * n;',
            '  float i = floor(s), f = s - i;',
            '  vec3 p0 = flockPt(mod(i - 1.0, n)), p1 = flockPt(mod(i, n));',
            '  vec3 p2 = flockPt(mod(i + 1.0, n));',
            '  vec3 p3 = flockPt(mod(i + 2.0, n));',
            '  return 0.5 * (2.0 * p1 + (p2 - p0) * f',
            '      + (2.0 * p0 - 5.0 * p1 + 4.0 * p2 - p3) * f * f',
            '      + (3.0 * p1 - 3.0 * p2 + p3 - p0) * f * f * f);',
            '}',
        ].join('\n'),
        vertexMain: [
            '  vUv = uv;',
            // The flock rides the loop as one; the bird's own lag is
            // what makes it FOLLOW rather than march in a slab.
            '  float u = uTime * uRate - iOff.z;',
            '  vec3 c = flockPath(u);',
            '  vec3 fwd = normalize(flockPath(u + 0.004) - c',
            '      + vec3(1e-5, 0.0, 0.0));',
            '  vec3 sd = cross(fwd, vec3(0.0, 1.0, 0.0));',
            '  float sl = length(sd);',
            '  sd = sl > 1e-4 ? sd / sl : vec3(1.0, 0.0, 0.0);',
            '  vec3 ud = cross(sd, fwd);',
            // Decorrelated bob: offsets under a few turns leave the
            // flock breathing in unison, which reads as one object.
            '  float st = astraStagger(iExtra.x);',
            '  c += sd * (iOff.x + sin(uTime * 0.63 + st) * iExtra.w * 1.4)',
            '     + ud * (iOff.y + sin(uTime * 0.91 + st * 1.7) * iExtra.w);',
            '  vPhase = uTime * uBeat * iExtra.z * 6.2831 + iExtra.x;',
            // Camera axes in LOCAL space (rows of modelViewMatrix), so
            // the billboard survives a rotated or moved parent group.
            '  vec3 camR = vec3(modelViewMatrix[0][0], modelViewMatrix[1][0],',
            '      modelViewMatrix[2][0]);',
            '  vec3 camU = vec3(modelViewMatrix[0][1], modelViewMatrix[1][1],',
            '      modelViewMatrix[2][1]);',
            // Span across flight for a bird, along it for a fish; the
            // quad takes its width from that axis PROJECTED on screen,
            // so a bird coming at the camera loses its span.
            '  vec3 ax = mix(sd, fwd, uKind);',
            '  vec2 e = vec2(dot(ax, camR), dot(ax, camU));',
            '  float el = length(e);',
            '  vec2 ex = el > 1e-4 ? e / el : vec2(1.0, 0.0);',
            '  vec2 ey = vec2(-ex.y, ex.x);',
            '  ey = ey.y < 0.0 ? -ey : ey;',
            '  float sz = uSize * iExtra.y;',
            '  vec2 q = ex * (aCorner.x * 2.0 * sz * clamp(el, 0.34, 1.0))',
            '      + ey * (aCorner.y * 2.0 * sz * 0.8);',
            '  transformed = c + camR * q.x + camU * q.y;',
            // ONE albedo across the flock is the tell of an instanced
            // field. A skein is juveniles and adults, dry backs and
            // wet ones: a value spread, plus a warm/cool tilt (not a
            // hue rotation — that returns a near-grey bird unchanged).
            '  float bv = astraHash11(iExtra.x * 7.13 + 3.1);',
            '  float bw = astraHash11(iExtra.x * 3.77 + 11.9);',
            '  float tw = (bw - 0.5) * 0.5;',
            '  vTint = uColor * (0.62 + bv * 1.02)',
            '      * vec3(1.0 + tw, 1.0, 1.0 - tw);',
            // A fifth of any skein is gliding at any instant, wings
            // held: a frozen frame in which every bird holds the same V
            // is the second tell, after the flat colour.
            '  float gl = astraHash11(iExtra.x * 13.31 + 0.7);',
            // The sun in the bird's OWN frame (x against its up axis, y
            // against its wing axis), so the fragment can roll each wing
            // between the two through the stroke, and how backlit the
            // flock is from this camera.
            '  vLit = vec4(dot(ud, uSun), dot(sd, uSun),',
            '      smoothstep(0.05, 0.85, dot(uSun, -vec3(',
            '          modelViewMatrix[0][2], modelViewMatrix[1][2],',
            '          modelViewMatrix[2][2]))),',
            '      mix(0.30, 1.0, smoothstep(0.0, 0.32, gl)));',
        ].join('\n'),
        fragmentHead: [
            'uniform vec3 uColor; uniform vec3 uSun; uniform float uKind;',
            'uniform vec3 uSunCol; uniform vec3 uSky;',
        ].join('\n'),
        fragmentMain: [
            '  vec2 q = vUv * 2.0 - 1.0;',
            '  float au = abs(q.x);',
            '  float cl, h;',
            '  float stroke = 0.0;',
            '  if (uKind < 0.5) {',
            // Two strokes: one line per wing, tips lagging the root
            // through the beat, thick at the body and gone at the tip.
            '    stroke = sin(vPhase - au * 0.9);',
            '    cl = stroke * 0.72 * pow(au, 1.3) * vLit.w;',
            // A gaussian body summed onto the wing line gives a hard
            // four-cornered DIAMOND at close range — the one place this
            // mark is read as a shape rather than a speck. An ellipse
            // for the body and a tapering wing keep the round back and
            // the thin primaries a bird actually has.
            '    float body = 0.25 * sqrt(max(0.0, 1.0 - au * au / 0.018));',
            '    float wing = 0.068 * (1.0 - 0.5 * au)',
            '        * (1.0 - smoothstep(0.78, 1.0, au));',
            // Union by hypot, not max: a max leaves a crease at the
            // shoulder, and the body's own axis is ACROSS the span, so
            // the ellipse is narrow in x and long in y.
            '    h = sqrt(body * body + wing * wing);',
            '  } else {',
            '    stroke = sin(vPhase + q.x * 2.6);',
            '    cl = stroke * 0.15 * (0.32 + 0.68 * (0.5 - q.x * 0.5));',
            '    h = 0.30 * sqrt(max(0.0, 1.0 - q.x * q.x))',
            '        + 0.26 * smoothstep(-0.55, -1.0, q.x);',
            '  }',
            '  float d = abs(q.y - cl);',
            // A bird is a handful of pixels: without gradient-width
            // antialiasing the silhouette stipples out of the frame.
            '  float aa = max(fwidth(d), 0.004);',
            '  float a = 1.0 - smoothstep(h - aa, h + aa, d);',
            '  if (a < 0.01) discard;',
            // The wings are MIRROR images: through the stroke they roll
            // opposite ways about the flight line, so one turns its
            // upper surface into the sun while the other goes edge-on.
            // That asymmetry is the shimmer of a real skein — a flash
            // keyed on the beat alone lights every bird identically and
            // reads as a flickering white speck field.
            '  float roll = cos(vPhase - au * 0.9) * 1.15 * vLit.w',
            '      * (q.x < 0.0 ? -1.0 : 1.0);',
            // A fish rolls about its own long axis instead, and shows
            // its FLANK: the axes swap.
            '  float ndl = mix(vLit.x, vLit.y, uKind) * cos(roll)',
            '      + mix(vLit.y, vLit.x, uKind) * sin(roll);',
            '  float up = clamp(ndl, 0.0, 1.0);',
            // Below the horizon there is no sun to catch, and the
            // ambient goes with it — the flock is then a shape against
            // whatever the sky still has.
            '  float dayF = smoothstep(-0.18, 0.12, uSun.y);',
            // IRRADIANCE, not a lerp weight. The open sky is a hemisphere
            // of light: its integral is a multiple of the sky's own
            // radiance, not a fraction of it, and a shaded bird lit at
            // "0.26 of sky" landed on 7/255 — a hole punched in the
            // frame. These two scalars put a dark bird's shaded side at
            // ~24/255 with the sky's hue still in it, and its sunlit
            // side four times that, which is where a rook photographs.
            '  vec3 irr = (uSky * (1.34 + 0.40 * clamp(-ndl, 0.0, 1.0))',
            '      * mix(0.30, 1.0, dayF)',
            '      + uSunCol * (1.74 * up * dayF))',
            // A fish is under WATER: the same sky arrives at it filtered
            // and halved. Without this a silver flank (a genuine 40%
            // albedo) meets full daylight and clips to white paper.
            '      * mix(1.0, 0.45, uKind);',
            '  vec3 c = vTint * irr;',
            // The membrane is glossy: at the roll that lines a wing up
            // with the sun the whole stroke flares. Narrow, and in the
            // colour of the SUN — a flare toward white is the tell of
            // a flock painted rather than lit.
            '  c += uSunCol * (pow(up, 5.0) * dayF * 0.30',
            '      * (1.0 - uKind * 0.3));',
            // Backlit, the light comes THROUGH the wing: a flock
            // against the sun is not a field of black specks, it is
            // black BODIES with lit primaries. The band stops short of
            // the outer tip, where the silhouette is one antialiased
            // pixel and any glow lands on the sky instead of the bird.
            '  float thin = smoothstep(0.22, 0.72, au)',
            '      * (1.0 - smoothstep(0.86, 1.0, au));',
            '  c += uSunCol * (vLit.z * dayF * 0.26 * (1.0 - uKind * 0.5)',
            '      * thin * (0.45 + 0.55 * clamp(-ndl, 0.0, 1.0)));',
            '  gl_FragColor = vec4(c, a);',
        ].join('\n'),
        transparent: true,
        depthWrite: false,
        side: THREE.DoubleSide,
    });
}

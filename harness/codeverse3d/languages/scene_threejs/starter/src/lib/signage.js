/**
 * Readable lettering: real extruded glyphs from the fonts the npm
 * three package already ships — no new deps, no import-map edits.
 * The load path is DUAL-RUNTIME and both halves are load-bearing:
 * headless Chrome fetches over HTTP, plain node (census / assets_api)
 * reads the same file off disk.  `makeText` is async: await ONCE at
 * module top level, `.clone()` per copy inside `build()`.
 *
 * Font path, ported 2026-09-01 — BOTH halves were broken on this host,
 * because both assumed a `node_modules` next to the app:
 *   · browser — the workspace is served at `/` and three at
 *     `/__runtime/node_modules/`, so `../../node_modules/...` 404d and
 *     the module only survived by falling through to its CDN pin: it
 *     did not work offline, and the harness counts every 404 as a
 *     console error (2 per render, run marked not-ok).
 *   · node — a workspace / test probe has no parent `node_modules` at
 *     all, so `createRequire().resolve('three/examples/fonts/…')` threw
 *     MODULE_NOT_FOUND and every census-side makeText died.
 * Both now ask `import.meta.resolve` where `three` itself came from —
 * the page's import map in Chrome, the module resolver (our
 * `resolve_three.mjs` hook included) in node — and the old guesses are
 * kept as later candidates for the reference app's own layout.
 *
 * Grading, same pass: extruded letters lit only by sky + environment —
 * the normal case, since a sign faces the viewer and the sun rarely
 * does — used to read as a flat pale cutout.  The glyphs now carry a
 * paint-variance vertex colour (mottle + weathering + darker extrusion
 * walls + a bright bevel rim), so the depth reads without a key light,
 * they cast shadows by default, and an emissive sign carries its own
 * practical so it LIGHTS the board it is bolted to instead of sitting
 * on it as a decal.
 */

import * as THREE from 'three';
import { FontLoader } from 'three/addons/loaders/FontLoader.js';
import { TextGeometry } from 'three/addons/geometries/TextGeometry.js';

const FONT_FILES = {
  regular: 'helvetiker_regular.typeface.json',
  bold: 'helvetiker_bold.typeface.json',
};

// The reference kept a CDN pin here as a last resort.  It is deleted:
// this contract forbids the network outright, the run is offline, and
// the only thing it ever did on this host was mask the `../../node_modules`
// 404 that `import.meta.resolve` (above) now resolves properly.
const IS_NODE = typeof process !== 'undefined' &&
    !!(process.versions && process.versions.node);

const _fontCache = new Map();

/** Font URLs to try, best first — ONE list for both runtimes.
 * `import.meta.resolve` answers from the page's import map in Chrome and
 * from the module resolver (our `resolve_three.mjs` hook included) in
 * node, so "wherever three itself came from" is the same question in
 * both, and this module has already proved three resolves: it imports
 * it at the top.  Everything after those two entries is a guess kept
 * for other layouts. */
function _fontUrls(file) {
  const urls = [];
  const add = (u) => { if (u && urls.indexOf(u) === -1) urls.push(u); };
  const resolve = (typeof import.meta.resolve === 'function')
      ? import.meta.resolve : null;
  if (resolve) {
    try {
      // examples/jsm/loaders/FontLoader.js -> examples/fonts/
      add(new URL('../../fonts/' + file,
                  resolve('three/addons/loaders/FontLoader.js')).href);
    } catch (_e) { /* no such entry — try the next form */ }
    try {
      // build/three.module.js -> examples/fonts/
      add(new URL('../examples/fonts/' + file, resolve('three')).href);
    } catch (_e) { /* ditto */ }
  }
  // a node_modules sibling of src/ (the reference app's own layout)
  add(new URL('../../node_modules/three/examples/fonts/' + file,
              import.meta.url).href);
  return urls;
}

async function _loadFontJson(file) {
  const tried = [];
  const urls = _fontUrls(file);
  if (IS_NODE) {
    // an app whose node_modules is a PARENT of this file resolves the
    // font through the three exports map; a bare workspace (our runs,
    // the test probes) does not, and falls back to the URL list.
    try {
      const { createRequire } = await import('node:module');
      urls.push(createRequire(import.meta.url)
          .resolve('three/examples/fonts/' + file));
    } catch (_e) { /* no parent node_modules */ }
  }
  for (const url of urls) {
    try {
      if (IS_NODE && !/^https?:/.test(url)) {
        const { readFile } = await import('node:fs/promises');
        const { fileURLToPath } = await import('node:url');
        const p = url.startsWith('file:') ? fileURLToPath(url) : url;
        return JSON.parse(await readFile(p, 'utf-8'));
      }
      const r = await fetch(url);
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return await r.json();
    } catch (e) {
      tried.push(url + ': ' + ((e && e.message) || e));
    }
  }
  throw new Error('font load failed: ' + file + ' [' + tried.join(' | ') + ']');
}

/**
 * Load a shipped helvetiker font, cached per variant.
 *
 * @param {object} [opts] `bold` (default false) picks the bold face.
 * @returns {Promise<object>} A parsed `Font` for TextGeometry.
 */
export function loadHelvetiker(opts = {}) {
  const file = FONT_FILES[opts.bold ? 'bold' : 'regular'];
  let p = _fontCache.get(file);
  if (!p) {
    p = _loadFontJson(file).then((json) => new FontLoader().parse(json));
    _fontCache.set(file, p);
  }
  return p;
}

/**
 * Paint variance as a vertex colour, in place.  Deterministic (a pure
 * function of position + normal), so a re-render of the same string is
 * byte-identical.  Three parts, all measured on this pipeline against
 * a sign lit by sky + environment only:
 *   · mottle — two sine octaves in glyph-relative units; paint, print
 *     and enamel are never one flat value, and the variance is what
 *     stops a big letter from reading as a screen-space cutout
 *   · weathering — the bottom of a letter is dirtier than its top
 *   · form — the extrusion WALLS go down to 0.80 (they see far less
 *     sky than the face) while the BEVEL rim, which does catch it,
 *     goes up to +10 %.  Both are read off |n.z|, so this needs no
 *     material groups and survives any bevel setting.
 */
const MOTTLE = 0.070;        // paint value swing, +/- (peak |m| is ~1.02)
const MOTTLE_HUE = 0.034;    // warm/cool swing riding the same signal
// Everything below is NORMALISED to a ceiling of exactly 1.0, so a vertex
// colour can only ever DARKEN: `color` stays the true albedo (the brief's
// 0.02-0.8 linear band is a ceiling, and a rim that multiplied it by 1.14
// would have quietly broken it).
const FORM_PEAK = 1 + 0.10;
const MOTTLE_PEAK = (1 + MOTTLE * 1.02) * (1 + MOTTLE_HUE * 1.02);

function _paintVariance(geo, size, amount) {
  const pos = geo.attributes.position;
  const nrm = geo.attributes.normal;
  if (!pos || !nrm) return;
  const n = pos.count;
  const col = new Float32Array(n * 3);
  const s = 1 / Math.max(size, 1e-4);
  for (let i = 0; i < n; i++) {
    const x = pos.array[i * 3] * s;
    const y = pos.array[i * 3 + 1] * s;
    const nz = Math.abs(nrm.array[i * 3 + 2]);

    // mottle in [-1, 1]-ish, hue-signed: warm where the paint is thick
    const m = 0.46 * Math.sin(x * 2.7 + 0.9) +
              0.26 * Math.sin(x * 7.3 + y * 3.1 + 2.1) +
              0.30 * Math.sin(y * 4.6 - 1.3);
    // grime in the first 0.35 of cap height
    const grime = 1 - 0.06 * Math.max(0, 1 - Math.max(0, y) / 0.35);
    // form: walls dark, bevel rim the brightest thing on the glyph
    const wall = 1 - Math.min(1, nz * 1.6);
    const rim = 4 * nz * (1 - nz);
    const form = (1 - 0.22 * wall + 0.10 * rim) / FORM_PEAK;

    const v = (1 + MOTTLE * m) * grime * form / MOTTLE_PEAK;
    const hue = MOTTLE_HUE * m;
    // `amount` lerps the whole thing back towards flat white
    col[i * 3] = 1 + amount * (v * (1 + hue) - 1);
    col[i * 3 + 1] = 1 + amount * (v - 1);
    col[i * 3 + 2] = 1 + amount * (v * (1 - hue) - 1);
  }
  geo.setAttribute('color', new THREE.BufferAttribute(col, 3));
}

/**
 * Build one line of real extruded 3D text: centered on X/Z, BASELINE
 * at y = 0, facing +Z (`userData.size` carries the measured bbox).
 *
 * @param {string} str One line of text (no newlines — stack meshes for
 *   multiple lines).
 * @param {object} [opts] `size` glyph height in metres (default 1);
 *   `depth` extrusion depth (default 0.15 * size); `bold` (default
 *   false); `bevel` (default true — softened edges, razor edges read
 *   as CG, and the bevel rim is where a sky-lit letter gets its
 *   catchlight); `curveSegments` (default 8); `color` (default
 *   0xe7e3da — a paint white: 0xffffff-class albedo bakes a letter to
 *   a flat blown slab under ACES at exposure 1.0), `roughness`
 *   (default 0.45), `metalness` (default 0.1); `variation` 0..1
 *   (default 1) scales the baked paint mottle / wall shading, 0 drops
 *   the vertex colours entirely (a `material` you pass in reads them
 *   only if it sets `vertexColors`); `emissive` + `emissiveIntensity`
 *   (default 1.6 when emissive is set — the post chain's bloom only
 *   adds a soft halo, so 1.5-4 is the whole usable range and 20 is a
 *   white blob) make a neon / backlit sign; `light` the practical an
 *   emissive sign throws on its own board — `false` to drop it (do
 *   that for a facade carrying dozens of signs: this is one real
 *   PointLight per line), a number to set its candela directly.  The
 *   default is derived — `1.8 * emissiveIntensity * inked area` —
 *   measured on a 0.42 m OPEN sign 0.4 m off its board: at night the
 *   board just above it went from r 0.174 / warmth +0.023 to r 0.265 /
 *   warmth +0.108, and by day the sun still owns the frame;
 *   `shadow: false` opts out of
 *   cast/receive shadows (on by default — the glyph shadow on the
 *   board is what says "extruded", not "decal"); `material` replaces
 *   the built material entirely; `font` supplies a pre-loaded Font and
 *   skips loading.
 * @returns {Promise<THREE.Mesh>} Mesh named `SignText`; the practical,
 *   when there is one, is `mesh.userData.light`.
 */
export async function makeText(str, opts = {}) {
  const font = opts.font || await loadHelvetiker({ bold: !!opts.bold });
  const size = opts.size === undefined ? 1 : opts.size;
  const depth = opts.depth === undefined ? 0.15 * size : opts.depth;
  const bevel = opts.bevel === undefined ? true : !!opts.bevel;

  const geo = new TextGeometry(String(str), {
    font: font,
    size: size,
    depth: depth,
    curveSegments:
        opts.curveSegments === undefined ? 8 : opts.curveSegments,
    bevelEnabled: bevel,
    // A wider, rounder bevel than the reference's razor lip: this ring
    // is the only part of a front-facing letter that sees the sky, and
    // it carries the catchlight.  `bevelSize` is in-plane, so it grows
    // the glyph box — 0.015 is the most that keeps a size-1 line inside
    // the 1.1 m the placement contract is measured against.
    bevelThickness: 0.024 * size,
    bevelSize: 0.015 * size,
    bevelSegments: 3,
  });
  geo.computeBoundingBox();
  const bb = geo.boundingBox;
  geo.translate(
      -0.5 * (bb.min.x + bb.max.x), 0, -0.5 * (bb.min.z + bb.max.z));

  const variation = opts.variation === undefined
      ? 1 : Math.max(0, Math.min(1, opts.variation));
  if (variation > 0) _paintVariance(geo, size, variation);

  let mat = opts.material;
  if (!mat) {
    mat = new THREE.MeshStandardMaterial({
      color: opts.color === undefined ? 0xe7e3da : opts.color,
      roughness: opts.roughness === undefined ? 0.45 : opts.roughness,
      metalness: opts.metalness === undefined ? 0.1 : opts.metalness,
      vertexColors: variation > 0,
    });
    if (opts.emissive !== undefined) {
      mat.emissive = new THREE.Color(opts.emissive);
      mat.emissiveIntensity = opts.emissiveIntensity === undefined
          ? 1.6 : opts.emissiveIntensity;
    }
  }

  const mesh = new THREE.Mesh(geo, mat);
  mesh.name = 'SignText';
  const w = bb.max.x - bb.min.x;
  const h = bb.max.y - bb.min.y;
  const d = bb.max.z - bb.min.z;
  mesh.userData.size = { w: w, h: h, d: d };
  // Lettering is MOUNTED — on a board, a facade, a plinth — so the
  // host's settle pass (runtime_js/lib/host_placement.mjs) must never
  // drop it onto the terrain the way it seats a floating crate.
  mesh.userData.placement = 'free';
  if (opts.shadow === undefined || opts.shadow) {
    mesh.castShadow = true;
    mesh.receiveShadow = true;
  }

  // A neon / backlit sign that lights nothing is a decal.  Derive the
  // candela from what the letters actually emit: radiance x inked area
  // (~28 % of the bbox for a line of caps) x a lambertian-ish gain.
  const emi = mat.emissive;
  const emiI = mat.emissiveIntensity === undefined ? 1 : mat.emissiveIntensity;
  const lit = !!emi && (emi.r + emi.g + emi.b) > 0.01 && emiI > 0;
  if (lit && opts.light !== false) {
    const area = Math.max(0.02, 0.28 * w * h);
    const cd = typeof opts.light === 'number'
        ? opts.light : 1.8 * emiI * area;
    const light = new THREE.PointLight(emi.clone(), cd, 0, 2);
    light.name = 'SignGlow';
    // just off the letter faces, at the optical centre of the line
    light.position.set(0, 0.5 * h, 0.5 * d + 0.55 * size);
    light.castShadow = false;
    mesh.add(light);
    mesh.userData.light = light;
  }
  return mesh;
}

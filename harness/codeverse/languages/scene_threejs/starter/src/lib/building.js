/**
 * A facade with real openings, instead of a textured box. The fix is
 * depth, not a texture: spandrel bands and mullions with GAPS onto a
 * dark recessed shell, a glazed ground floor, plinth and projecting
 * cornice — real geometry that holds in silhouette and casts shadow.
 * Cheap by design: one merged mesh per material, ~800-1,500 triangles
 * for an eight-storey block.
 */

import * as THREE from 'three';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import * as MAT from './materials.js';

/** Metres of wall per texture tile. One number for the whole module, so
 *  a mullion, a spandrel and a cornice share one masonry scale. */
const UV_TILE = 2.6;

/**
 * A box as geometry, positioned by its centre, with its UVs scaled to a
 * CONSTANT texel density.
 *
 * three gives every box face a full 0..1 UV whatever that face measures,
 * so one merged facade stretched a single brick tile across a 14 m wall
 * and squeezed the same tile onto a 0.34 m mullion — which is why the
 * piers read as vertical woodgrain stripes next to a flat wall. Scaling
 * u,v by the face's own metres fixes it for every part at once (the
 * library's noise maps are all RepeatWrapping, so uv > 1 tiles).
 */
function bx(w, h, d, x, y, z) {
  const g = new THREE.BoxGeometry(w, h, d);
  const uv = g.attributes.uv;
  // BoxGeometry lays its 24 vertices out px, nx, py, ny, pz, nz.
  const dims = [[d, h], [d, h], [w, d], [w, d], [w, h], [w, h]];
  for (let f = 0; f < 6; f++) {
    const su = dims[f][0] / UV_TILE, sv = dims[f][1] / UV_TILE;
    for (let i = f * 4; i < f * 4 + 4; i++) {
      uv.setXY(i, uv.getX(i) * su, uv.getY(i) * sv);
    }
  }
  uv.needsUpdate = true;
  g.translate(x, y, z);
  return g;
}

/**
 * The warm interior-glow card behind a lit window — one definition so
 * `block()`, `cottage()` and `casement()` glow alike. At dusk, lit
 * windows are the strongest habitation cue.
 *
 * Emissive stays in the 1.1-2.6 band: bloom-friendly, and a lit pane
 * that peaks at 20 is a white hole with no colour left in it.
 */
function litMat(o = {}) {
  return MAT.plaster({
      color: o.color === undefined ? 0xffdca8 : o.color,
      roughness: 1,
      hueBreak: 0.02,
      extra: { emissive: new THREE.Color(
                   o.emissive === undefined ? 0xffca7a : o.emissive),
               emissiveIntensity: o.intensity === undefined
                   ? 1.5 : o.intensity } });
}

/**
 * A LIT FACADE IS NOT ONE COLOUR. Every window on one emissive material
 * turns a night block into a single glowing slab; real windows split by
 * room — tungsten living rooms, warm-white bedrooms, the odd cool
 * kitchen or stairwell — and that split IS the night read. Three
 * buckets keep it to three merged meshes per building.
 */
const LIT_CLASSES = [
  { color: 0xffd9ad, emissive: 0xffb765, intensity: 1.9 },   // tungsten
  { color: 0xffe6c4, emissive: 0xffd39a, intensity: 1.25 },  // warm white
  { color: 0xdfe9ff, emissive: 0xbcd4ff, intensity: 1.55 },  // cool room
];

/**
 * Wall/trim albedos are held under 0.8 sRGB. Plaster and travertine
 * ship near 0.92, which under exposure 1.0 with no post chain lands a
 * whole facade at the top of the range — a white card that no shadow
 * can shape and no cornice can read against.
 */
const STYLES = {
  // Stock brick, not pillarbox: 0x9c4a35 under ACES at exposure 1.0
  // came out a saturated candy red across a whole facade. Lower
  // contrast too — a merged box gives every face a full 0..1 UV, so a
  // slim mullion stretches the noise into stripes.
  brick: { wall: () => MAT.brick({ color: 0x8d5341, contrast: 0.3 }),
           floorH: 3.2, bayW: 3.4,
           trim: () => MAT.travertine({ color: 0xc3b79e }),
           glassColor: 0x37454f },
  masonry: { wall: () => MAT.plaster({ color: 0xc9bcaa }), floorH: 3.4,
             bayW: 3.8, trim: () => MAT.travertine({ color: 0xcbbfa8 }),
             glassColor: 0x3c4b58 },
  stone: { wall: () => MAT.granite(), floorH: 3.6, bayW: 4.0,
           trim: () => MAT.travertine({ color: 0xc7bca6 }),
           glassColor: 0x374651 },
  glass: { wall: () => MAT.brushedSteel(), floorH: 3.9, bayW: 5.0,
           trim: () => MAT.brushedSteel(), glassColor: 0x46606f },
};

/**
 * Glazing. A dielectric with a fresnel ramp, tinted per style, and a
 * lifted `envMapIntensity` so the panes mirror the sky instead of
 * reading as black rectangles punched in the wall.
 */
function glazing(color, k = 0) {
  // Two batches per facade. Real glazing is never one value — blinds,
  // curtains, a room behind one pane and none behind the next — and a
  // grid of identical panes is the "flat glass tower" verdict.
  const c = new THREE.Color(color);
  if (k) c.offsetHSL(0.02, -0.05, 0.05);
  return MAT.glass({ color: c.getHex(), opacity: k ? 0.55 : 0.66,
      extra: { envMapIntensity: 2.2, roughness: k ? 0.14 : 0.08 } });
}

/**
 * The shell seen through every opening. NOT black: a room's back wall
 * still catches sky through the window, and 0x14161a rendered as a
 * hole. This is dark enough to read as depth and light enough that the
 * environment shapes it.
 */
function revealMat() {
  return MAT.plaster({ color: 0x24262c, roughness: 1, hueBreak: 0.05 });
}

/**
 * Build a block with modelled openings, a plinth and a cornice.
 *
 * @param {object} opts
 *   `w` width, `d` depth, `h` height in metres; `style` one of
 *   `brick | masonry | stone | glass` (default `masonry`); `rand` a
 *   seeded PRNG (REQUIRED for variation -- no `Math.random`);
 *   `color` overrides the wall colour; `ground` `'storefront'` (default,
 *   a taller glazed base) or `'plain'`; `lit` 0..1 fraction of windows
 *   that glow, for night scenes (default 0); `name`.
 * @returns {THREE.Group} Resting on y=0, centred on x/z, facing -Z,
 *   with `userData.forward` and `userData.floors`. Meshes are named
 *   `Walls`, `Trim`, `Glazing`, `Reveals` and — only when `lit` — one
 *   `LitRooms*` per lit class. `LitRooms` deliberately does NOT match
 *   `mirror.js` `glazeFacade`'s `/glaz|pane/` sweep, so mirroring a
 *   facade leaves its lit windows glowing.
 */
export function block(opts = {}) {
  const rand = opts.rand || (() => 0.5);
  const w = opts.w || 14;
  const d = opts.d || 12;
  const h = opts.h || 22;
  const st = STYLES[opts.style] || STYLES.masonry;

  // A DISTRICT OF ONE COLOUR reads as one stamped asset however varied
  // the massing is. `tint` clones the cached material into nine hue +
  // lightness buckets, so a hundred blocks cost nine materials.
  let wallMat = st.wall();
  if (opts.color !== undefined) {
    wallMat = wallMat.clone();
    wallMat.userData = {};
    wallMat.color = new THREE.Color(opts.color);
  } else {
    // Half of `tint`'s range: its full swing is +-25% lightness, which
    // walks brick from stock to candy. A street wants a batch spread,
    // not four different bricks.
    wallMat = MAT.tint(wallMat, 0.5 + (rand() - 0.5) * 0.6);
  }
  const trimMat = st.trim();
  const glassMats = [glazing(st.glassColor, 0), glazing(st.glassColor, 1)];
  const darkMat = revealMat();

  // A storefront base is taller than the floors above it, which is most
  // of what makes a street read as a street rather than a stack.
  const baseH = opts.ground === 'plain' ? 0
      : Math.min(h * 0.28, st.floorH * 1.45);
  const upperH = h - baseH;
  const floors = Math.max(1, Math.round(upperH / st.floorH));
  const fh = upperH / floors;
  const bays = Math.max(1, Math.round(w / st.bayW));
  const baysD = Math.max(1, Math.round(d / st.bayW));

  const wall = [];      // spandrels + mullions + plinth
  const trim = [];      // cornice, sills, string courses
  const glassBins = [[], []];   // panes, set back inside the openings
  const glass = glassBins[0];   // the storefront runs on one batch
  const dark = [];      // the recess shell seen through the openings
  const litBins = LIT_CLASSES.map(() => []);   // lit panes, by room class

  // The shell that gives every opening a dark interior behind it. It is
  // inset, so what you see through a window is depth, not the sky.
  const inset = 0.42;
  dark.push(bx(w - inset * 2, h, d - inset * 2, 0, h / 2, 0));

  const plinth = Math.min(0.6, h * 0.03);
  wall.push(bx(w + 0.22, plinth, d + 0.22, 0, plinth / 2, 0));

  // Ground floor: wide glazing between slim piers.
  if (baseH > 0) {
    const pierW = Math.max(0.45, w * 0.035);
    for (const [len, along, faceZ] of
        [[w, 'x', d / 2], [w, 'x', -d / 2], [d, 'z', w / 2], [d, 'z', -w / 2]]) {
      const n = Math.max(2, Math.round(len / (st.bayW * 1.6)));
      for (let i = 0; i <= n; i++) {
        const t = -len / 2 + (len / n) * i;
        const g = along === 'x'
            ? bx(pierW, baseH, 0.5, t, plinth + baseH / 2, faceZ)
            : bx(0.5, baseH, pierW, faceZ, plinth + baseH / 2, t);
        wall.push(g);
      }
      const gp = along === 'x'
          ? bx(len - pierW, baseH * 0.86, 0.12, 0,
               plinth + baseH / 2, faceZ - Math.sign(faceZ) * 0.3)
          : bx(0.12, baseH * 0.86, len - pierW,
               faceZ - Math.sign(faceZ) * 0.3, plinth + baseH / 2, 0);
      glass.push(gp);
    }
    trim.push(bx(w + 0.5, 0.28, d + 0.5, 0, plinth + baseH + 0.14, 0));
  }

  // Upper floors, each face built as spandrel bands + mullions with the
  // gaps between them left OPEN onto the dark shell.
  const y0 = plinth + baseH;
  const spandrel = fh * 0.34;           // solid band under each window
  const openH = fh - spandrel;
  for (let f = 0; f < floors; f++) {
    const yb = y0 + f * fh;
    for (const [len, along, faceOff, n] of
        [[w, 'x', d / 2, bays], [w, 'x', -d / 2, bays],
         [d, 'z', w / 2, baysD], [d, 'z', -w / 2, baysD]]) {
      const s = Math.sign(faceOff);
      // Spandrel band across the whole face.
      wall.push(along === 'x'
          ? bx(len, spandrel, 0.5, 0, yb + spandrel / 2, faceOff)
          : bx(0.5, spandrel, len, faceOff, yb + spandrel / 2, 0));
      // Mullions between bays.
      const mw = Math.max(0.34, len / n * 0.22);
      for (let i = 0; i <= n; i++) {
        const t = -len / 2 + (len / n) * i;
        wall.push(along === 'x'
            ? bx(mw, openH, 0.5, t, yb + spandrel + openH / 2, faceOff)
            : bx(0.5, openH, mw, faceOff, yb + spandrel + openH / 2, t));
      }
      // A pane per bay, set back 0.22 m so the reveal reads as depth.
      for (let i = 0; i < n; i++) {
        const t = -len / 2 + (len / n) * (i + 0.5);
        const pw = len / n - mw;
        const lit = opts.lit && rand() < opts.lit;
        const geo = along === 'x'
            ? bx(pw, openH * 0.95, 0.1, t, yb + spandrel + openH / 2,
                 faceOff - s * 0.22)
            : bx(0.1, openH * 0.95, pw, faceOff - s * 0.22,
                 yb + spandrel + openH / 2, t);
        // A lit pane is its OWN card, never the shell: pushing it onto
        // `dark` and then making the whole shell emissive lit every
        // window in the building from one 22 m glowing box, so `lit:
        // 0.4` still came out as a solid lantern.
        if (lit) litBins[Math.floor(rand() * LIT_CLASSES.length)
                         % LIT_CLASSES.length].push(geo);
        else glassBins[rand() < 0.72 ? 0 : 1].push(geo);
      }
      // A sill ledge under the openings — the "missing trim" complaint.
      trim.push(along === 'x'
          ? bx(len, 0.13, 0.72, 0, yb + spandrel + 0.06, faceOff)
          : bx(0.72, 0.13, len, faceOff, yb + spandrel + 0.06, 0));
    }
  }

  // Cornice + parapet: a flat top edge reads as an untouched box. The
  // cornice must clear the projecting corner mullions, not just the
  // wall plane, or the roofline still comes out flush.
  trim.push(bx(w + 1.3, 0.42, d + 1.3, 0, h + 0.21, 0));
  wall.push(bx(w + 0.5, 0.75, d + 0.5, 0, h + 0.42 + 0.375, 0));

  const root = new THREE.Group();
  root.name = opts.name || 'Block';
  const parts = [[wall, wallMat, 'Walls'],
                 [trim, trimMat, 'Trim'],
                 [glassBins[0], glassMats[0], 'Glazing'],
                 [glassBins[1], glassMats[1], 'Glazing2'],
                 [dark, darkMat, 'Reveals']];
  litBins.forEach((geos, i) => parts.push(
      [geos, litMat(LIT_CLASSES[i]), i ? 'LitRooms' + (i + 1) : 'LitRooms']));
  for (const [geos, mat, nm] of parts) {
    if (!geos.length) continue;
    const merged = geos.length === 1 ? geos[0] : mergeGeometries(geos, false);
    if (!merged) continue;
    const m = new THREE.Mesh(merged, mat);
    m.name = nm;
    // The shell and the lit cards live inside the openings; casting
    // from them only stipples the reveal they are meant to fill.
    m.castShadow = nm !== 'Reveals' && !nm.startsWith('LitRooms');
    m.receiveShadow = true;
    root.add(m);
  }
  root.userData.forward = '-Z';
  root.userData.floors = floors + (baseH > 0 ? 1 : 0);
  return root;
}

/**
 * A casement window assembly, because a flush quad is not a window.
 * The good form in one call: a frame standing proud of the wall, the
 * pane recessed >= 0.1 m behind it, a mullion cross, a projecting
 * sill, optional louvered shutters hinged open ~55 degrees, and a
 * `lit` path that swaps the backing for the warm card `block()` uses.
 *
 * @param {object} opts
 *   `w`, `h` opening metres (default 1.1 x 1.4); `frameColor`;
 *   `shutters` boolean, paired louvered panels hinged open ~55 deg
 *   (default false); `shutterColor`; `lit` true or a 0..1 chance the
 *   window glows warm (default 0); `interior` true builds the ROOM
 *   side of the window instead (see below); `view` `'day' | 'night'`
 *   for the interior daylight card; `rand` seeded PRNG; `name`.
 * @returns {THREE.Group} Centred on the opening, mount plane at z = 0,
 *   facing -Z (the outside), with `userData.forward`. Position it at
 *   the window's centre ON the wall face; the frame and sill project
 *   outward (-Z), the pane and backing sit inside the wall (+Z).
 *   With `interior: true` the group instead faces +Z (the room).
 */
export function casement(opts = {}) {
  if (opts.interior) return interiorCasement(opts);
  const rand = opts.rand || (() => 0.5);
  const w = opts.w || 1.1;
  const h = opts.h || 1.4;
  const lit = opts.lit === true || (opts.lit ? rand() < opts.lit : false);
  const t = Math.max(0.06, w * 0.06);   // frame border width
  const proud = 0.06;                   // frame front, beyond the wall
  const deep = 0.16;                    // frame depth, into the wall
  const zc = -proud + deep / 2;

  // Joinery paint, held under 0.8 albedo: 0xf4efe6 is a 0.96 white that
  // clips against a plaster wall instead of sitting on it.
  const frameMat = MAT.paintedWood({ color: opts.frameColor === undefined
      ? 0xcbc4b6 : opts.frameColor });
  const glassMat = glazing(0x3c4b58);

  // Frame: four borders around the opening, plus the mullion cross that
  // splits the glazing into four lights — the depth cue a decal lacks.
  const frame = [
    bx(w + 2 * t, t, deep, 0, h / 2 + t / 2, zc),
    bx(w + 2 * t, t, deep, 0, -h / 2 - t / 2, zc),
    bx(t, h, deep, -w / 2 - t / 2, 0, zc),
    bx(t, h, deep, w / 2 + t / 2, 0, zc),
    bx(0.05, h, 0.05, 0, 0, 0.05),
    bx(w, 0.05, 0.05, 0, 0, 0.05),
  ];
  // The pane sits DEEP in the reveal: >= 0.1 m behind the frame front,
  // which is what makes the opening read as depth in raking light.
  const pane = bx(w, h, 0.02, 0, 0, proud + 0.02);
  // Behind the pane, an interior card: warm-lit when `lit`, dark
  // otherwise, so what you see through the glass is never the sky.
  const backing = bx(w, h, 0.02, 0, 0, proud + 0.07);
  // The sill projects past even the proud frame and sheds the rain.
  const sill = bx(w + 2 * t + 0.16, 0.07, 0.24, 0, -h / 2 - t - 0.035,
      -0.04);

  const root = new THREE.Group();
  root.name = opts.name || 'Casement';
  for (const [geos, mat, nm] of [
      [frame, frameMat, 'Frame'],
      [[sill], frameMat, 'Sill'],
      [[pane], glassMat, 'Pane'],
      // One window is one room: it draws a WARM class (tungsten or warm
      // white), never the cool one — a lone blue window reads as a fault.
      [[backing], lit ? litMat(LIT_CLASSES[rand() < 0.5 ? 0 : 1])
          : revealMat(), 'Backing']]) {
    const merged = geos.length === 1 ? geos[0] : mergeGeometries(geos, false);
    const m = new THREE.Mesh(merged, mat);
    m.name = nm;
    m.castShadow = nm !== 'Backing';
    m.receiveShadow = true;
    root.add(m);
  }

  // Shutters: a stile-and-rail border with tilted louver slats, hinged
  // at the jamb and swung ~55 degrees out from the wall plane — the
  // form the best audited harbour scene hand-built at 58 degrees.
  if (opts.shutters) {
    const open = (55 / 180) * Math.PI;
    const sw = w * 0.52, sh = h + t;
    const st = 0.07, th = 0.03;
    const smat = MAT.paintedWood({ color: opts.shutterColor === undefined
        ? 0x44584c : opts.shutterColor });
    for (const sgn of [-1, 1]) {
      // Built extending from the hinge (x = 0) toward the window
      // centre, i.e. along -sgn — the CLOSED orientation — then the
      // open swing is baked in as a rotation about the hinge.
      const cx = -sgn * sw / 2;
      const gs = [
        bx(sw, st, th, cx, sh / 2 - st / 2, 0),
        bx(sw, st, th, cx, -sh / 2 + st / 2, 0),
        bx(st, sh, th, -sgn * st / 2, 0, 0),
        bx(st, sh, th, -sgn * (sw - st / 2), 0, 0),
      ];
      const slats = 5;
      const span = sh - 2 * st;
      for (let i = 0; i < slats; i++) {
        const g = new THREE.BoxGeometry(sw - 2 * st, 0.09, 0.02);
        g.rotateX(0.6);
        g.translate(cx, -span / 2 + (span / slats) * (i + 0.5), 0);
        gs.push(g);
      }
      const merged = mergeGeometries(gs, false);
      // -sgn * open swings the free edge toward -Z: outward, off the
      // wall, never through it.
      merged.applyMatrix4(new THREE.Matrix4().makeRotationY(-sgn * open));
      merged.translate(sgn * (w / 2 + t), 0, -proud - th);
      const m = new THREE.Mesh(merged, smat);
      m.name = sgn < 0 ? 'ShutterL' : 'ShutterR';
      m.castShadow = true;
      m.receiveShadow = true;
      root.add(m);
    }
  }
  root.userData.forward = '-Z';
  return root;
}

/**
 * The room side of a window (interior windows ship as black holes —
 * the brightest surface a room has reading as its darkest). The film-
 * set trick: frame + sill reveal on the room side, a pane, and a
 * bright emissive daylight card OUTSIDE the pane, oversized so an
 * oblique camera never sees its edge; `view: 'night'` swaps the card.
 * Reached via `casement({ interior: true })`; not exported.
 *
 * @param {object} opts
 *   `w`, `h` opening metres (default 1.1 x 1.4); `frameColor`;
 *   `view` `'day'` (default) or `'night'`; `name`.
 * @returns {THREE.Group} Centred on the opening, wall plane at z = 0,
 *   facing +Z (the room), with `userData.forward`. The frame stands
 *   proud into the room and the sill stool projects past it; the pane
 *   sits inside the wall (-Z) and the daylight card outside the pane.
 */
function interiorCasement(opts) {
  const w = opts.w || 1.1;
  const h = opts.h || 1.4;
  const t = Math.max(0.06, w * 0.06);   // frame border width
  const proud = 0.06;                   // frame front, into the room
  const deep = 0.16;                    // frame depth, into the wall
  const zc = proud - deep / 2;

  const frameMat = MAT.paintedWood({ color: opts.frameColor === undefined
      ? 0xf4efe6 : opts.frameColor });
  // Light tint, low opacity: the card behind must shine THROUGH.
  const glassMat = MAT.glass({ color: 0xcfe0ee, opacity: 0.3 });

  // Frame borders plus the mullion cross, all on the room side of the
  // pane -- the reveal depth that separates a window from a decal.
  const frame = [
    bx(w + 2 * t, t, deep, 0, h / 2 + t / 2, zc),
    bx(w + 2 * t, t, deep, 0, -h / 2 - t / 2, zc),
    bx(t, h, deep, -w / 2 - t / 2, 0, zc),
    bx(t, h, deep, w / 2 + t / 2, 0, zc),
    bx(0.05, h, 0.05, 0, 0, -0.05),
    bx(w, 0.05, 0.05, 0, 0, -0.05),
  ];
  const pane = bx(w, h, 0.02, 0, 0, -(proud + 0.02));
  // The daylight card: OUTSIDE the pane (lower Z) and oversized, so an
  // oblique interior camera still sees sky, never the card's edge.
  const card = bx(w + 0.5, h + 0.5, 0.02, 0, 0, -(proud + 0.09));
  const cardMat = opts.view === 'night'
      ? litMat({ color: 0x0d1830, emissive: 0x0d1830, intensity: 0.4 })
      : litMat({ color: 0xdfeaff, emissive: 0xdfeaff, intensity: 2.2 });
  // The stool projects past the proud frame INTO the room.
  const sill = bx(w + 2 * t + 0.12, 0.05, 0.24, 0, -h / 2 - 0.025, 0.04);

  const root = new THREE.Group();
  root.name = opts.name || 'Casement';
  for (const [geo, mat, nm] of [
      [mergeGeometries(frame, false), frameMat, 'Frame'],
      [sill, frameMat, 'Sill'],
      [pane, glassMat, 'Pane'],
      [card, cardMat, 'Daylight']]) {
    const m = new THREE.Mesh(geo, mat);
    m.name = nm;
    m.castShadow = nm === 'Frame' || nm === 'Sill';
    m.receiveShadow = nm !== 'Daylight';
    root.add(m);
  }
  root.userData.forward = '+Z';
  return root;
}

/**
 * A cottage window, built ON a solid wall: a frame ring standing proud
 * of the wall face, the pane just outside that face behind the ring, a
 * mullion cross and a projecting sill.
 *
 * The pane CANNOT sit inside the wall the way `casement()`'s does —
 * a cottage wall is one solid box with no opening cut in it, so a pane
 * behind the face is simply buried (it was, and every cottage window
 * shipped as an opaque slab). The depth comes from the proud frame and
 * the sill shadow instead.
 *
 * @param {number} x centre, {number} y centre, {number} z the wall face
 * @param {number} s +1 or -1, which way the face looks
 * @param {number} ww opening width, {number} wh opening height
 * @param {Array} trim geometry sink for frame, cross and sill
 * @returns {THREE.BufferGeometry} the pane, for `glass` or a lit bin
 */
function cottageWindow(x, y, z, s, ww, wh, trim) {
  const t = 0.1;                    // frame bar
  const fz = z + s * 0.09;          // ring centre: proud of the wall
  trim.push(
      bx(ww + 2 * t, t, 0.17, x, y + wh / 2 + t / 2, fz),
      bx(ww + 2 * t, t, 0.17, x, y - wh / 2 - t / 2, fz),
      bx(t, wh, 0.17, x - ww / 2 - t / 2, y, fz),
      bx(t, wh, 0.17, x + ww / 2 + t / 2, y, fz),
      bx(0.045, wh, 0.05, x, y, z + s * 0.06),
      bx(ww, 0.045, 0.05, x, y, z + s * 0.06),
      // The sill projects past even the ring, and its shadow is what
      // sells the opening in raking light.
      bx(ww + 2 * t + 0.16, 0.06, 0.22, x, y - wh / 2 - t - 0.03,
         z + s * 0.1));
  return bx(ww, wh, 0.05, x, y, z + s * 0.025);
}

/**
 * The same window on a face that does NOT look along Z: built at the
 * origin looking -Z, then moved by `M`.
 * @returns {THREE.BufferGeometry} the pane
 */
function placedWindow(ww, wh, trim, M) {
  const local = [];
  const pane = cottageWindow(0, 0, 0, -1, ww, wh, local);
  for (const g of local) trim.push(g.applyMatrix4(M));
  return pane.applyMatrix4(M);
}

/**
 * A pitched-roof village building: cottage, shop, inn, centre. The
 * roof IS the silhouette — pitch, overhanging eaves, ridge, chimney;
 * a village of flat-topped boxes reads as a housing estate.
 *
 * @param {object} opts
 *   `w`, `d` footprint metres, `h` wall height to the eaves (default 3),
 *   `roof` `gable | hip` (default gable), `pitch` roof height as a
 *   multiple of half-width (default 0.85), `roofColor`, `wallColor`,
 *   `trimColor` (joinery: frames, sills, door surround), `doorColor`,
 *   `chimney` boolean (default true), `dormers` count along the -X
 *   slope (the ridge runs front-to-back, so the gables — door, main
 *   windows — face +-Z and the slopes face +-X) (default 0),
 *   `lit` true or a 0..1 fraction of
 *   windows that glow warm for dusk/night (default 0), `rand` seeded
 *   PRNG, `name`.
 * @returns {THREE.Group} Resting on y=0, centred on x/z, facing -Z.
 *   Meshes: `Walls`, `Roof`, `Trim` (joinery), `Glazing`, `Door` and,
 *   when `lit`, `LitRooms*`.
 */
export function cottage(opts = {}) {
  const rand = opts.rand || (() => 0.5);
  const w = opts.w || 7;
  const d = opts.d || 6;
  const h = opts.h || 3;
  const pitch = (opts.pitch === undefined ? 0.85 : opts.pitch) * w / 2;
  const eave = Math.max(0.28, w * 0.06);   // overhang: reads as a roof

  // Limewash, not a white card: 0xf2ece0 is 0.95 albedo, which under
  // exposure 1.0 leaves a cottage the brightest thing in a daylit
  // frame — brighter than the sky it stands against.
  const wallMat = MAT.tint(MAT.plaster({ color: opts.wallColor === undefined
      ? 0xcdc2ae : opts.wallColor }), opts.wallColor === undefined
      ? 0.5 + (rand() - 0.5) * 0.6 : 0.5);
  // Tile is fired clay, and a roof of it is never ONE red: the seeded
  // variant walks the hue and lightness a kiln does.
  const roofMat = MAT.terracotta({ color: opts.roofColor === undefined
      ? 0xa8492f : opts.roofColor, repeat: 3, hueBreak: 0.18,
      variant: opts.roofColor === undefined ? rand() : 0.5, spread: 0.1 });
  const trimMat = MAT.paintedWood({ color: opts.trimColor === undefined
      ? 0x6b4a33 : opts.trimColor });
  const doorMat = MAT.paintedWood({ color: opts.doorColor === undefined
      ? 0x3f5148 : opts.doorColor });
  const glassMat = glazing(0x3c4b58);
  // The lit path block() has and cottage() measurably lacked: at dusk
  // every audited failing scene had zero window glow.
  const litP = opts.lit === true ? 1 : (opts.lit || 0);

  const walls = [], roof = [], trim = [], glass = [], door = [], stack = [];
  const litBins = LIT_CLASSES.map(() => []);
  walls.push(bx(w, h, d, 0, h / 2, 0));

  // Windows: a frame ring proud of the wall with the pane behind it —
  // NOT a solid slab, which is what a filled frame box reads as.
  const bays = Math.max(1, Math.round(w / 2.6));
  const ww = Math.min(1.1, w / bays * 0.5), wh = Math.min(1.3, h * 0.45);
  for (let i = 0; i < bays; i++) {
    const x = -w / 2 + (w / bays) * (i + 0.5);
    for (const z of [d / 2, -d / 2]) {
      const s = Math.sign(z);
      const pane = cottageWindow(x, h * 0.55, z, s, ww, wh, trim);
      // A cottage lights room by room, so its warm classes only.
      if (litP && rand() < litP) litBins[rand() < 0.6 ? 0 : 1].push(pane);
      else glass.push(pane);
    }
  }
  // The EAVES walls (+-X) had no openings at all: whichever way a
  // cottage was turned, one long blank elevation faced the camera.
  const sideBays = Math.max(1, Math.round(d / 2.8));
  for (let i = 0; i < sideBays; i++) {
    const z = -d / 2 + (d / sideBays) * (i + 0.5);
    for (const sx of [-1, 1]) {
      // -Z rotated by -sx*90 deg looks along +-X: the sign matters, the
      // wrong one buries the frame in the wall it should stand on.
      const M = new THREE.Matrix4().makeRotationY(-sx * Math.PI / 2)
          .setPosition(sx * w / 2, h * 0.55, z);
      const pane = placedWindow(ww, wh, trim, M);
      if (litP && rand() < litP) litBins[rand() < 0.6 ? 0 : 1].push(pane);
      else glass.push(pane);
    }
  }
  // Door on the front face: a surround standing proud, a leaf recessed
  // inside it, a threshold step. A flush slab reads as painted-on.
  const dz = -d / 2;
  trim.push(
      bx(1.32, 0.14, 0.18, 0, 2.31, dz - 0.09),
      bx(0.14, 2.24, 0.18, -0.66, 1.19, dz - 0.09),
      bx(0.14, 2.24, 0.18, 0.66, 1.19, dz - 0.09),
      bx(1.5, 0.1, 0.5, 0, 0.05, dz - 0.16));
  door.push(bx(1.16, 2.18, 0.09, 0, 1.11, dz - 0.045),
            bx(0.1, 0.1, 0.06, 0.42, 1.05, dz - 0.12));

  // ROOF. A gable is two slabs meeting at a ridge plus two end walls;
  // a hip slopes on all four sides.
  const rw = w + eave * 2, rd = d + eave * 2;
  if (opts.roof === 'hip') {
    const g = new THREE.CylinderGeometry(0.001, Math.SQRT1_2, pitch, 4);
    g.rotateY(Math.PI / 4);
    g.scale(rw / Math.SQRT2 * Math.SQRT2, 1, rd / Math.SQRT2 * Math.SQRT2);
    g.translate(0, h + pitch / 2, 0);
    roof.push(g);
  } else {
    const slope = Math.hypot(pitch, rw / 2);
    for (const s of [-1, 1]) {
      const g = new THREE.BoxGeometry(slope, 0.16, rd);
      g.translate(0, 0, 0);
      // NEGATIVE s: the slab's +X end must fall AWAY from the ridge,
      // else the roof comes out as a V — invisible in a bbox, so a
      // test measures where the roof's high point actually is.
      const m = new THREE.Matrix4().makeRotationZ(
          -s * Math.atan2(pitch, rw / 2));
      g.applyMatrix4(m);
      g.translate(s * rw / 4, h + pitch / 2, 0);
      roof.push(g);
    }
    // A ridge cap along the join: the one line every tiled roof has,
    // and its absence is why two slabs read as folded cardboard.
    roof.push(bx(0.36, 0.15, rd + 0.06, 0, h + pitch - 0.06, 0));
    // Gable end walls: a 3-sided cylinder is a prism along its OWN
    // axis — rotateX lays it down to face the viewer (rotateY just
    // spins it in place). A unit 3-gon spans 1.732 x 1.5 with base at
    // y = -0.5, so scale to (w, pitch) and lift the base to the eaves.
    //
    // The SIGN of that rotateX decides which way up the triangle is,
    // and it was positive: three's first radial vertex sits at +Z, so
    // +90 deg sends the apex to y = -1 and leaves a FLAT edge at +0.5.
    // Measured (rotateX(+PI/2) spans y -1..+0.5, rotateX(-PI/2) spans
    // -0.5..+1): every gable shipped upside down, its two top corners
    // sticking out through the roof slopes as white wings, and the
    // triangle a bbox test sees is identical either way.
    for (const s of [-1, 1]) {
      const g = new THREE.CylinderGeometry(1, 1, 0.2, 3);
      g.rotateX(-Math.PI / 2);
      g.scale(w / 1.7320508, pitch / 1.5, 1);
      g.translate(0, h + pitch / 3, s * (d / 2 - 0.08));
      walls.push(g);
    }
  }
  if (opts.chimney !== false) {
    const cx = w * (0.18 + rand() * 0.16);
    // A stack is masonry, not limewash: merged into `walls` it came out
    // the same white as the cottage and vanished against it.
    stack.push(bx(0.7, pitch + 1.1, 0.7, cx, h + (pitch + 1.1) / 2 - 0.2,
        d * 0.16));
    roof.push(bx(0.92, 0.2, 0.92, cx, h + pitch + 0.85, d * 0.16));
  }
  // DORMERS RIDE A SLOPE. The ridge runs front-to-back (along Z), so
  // the slopes face +-X and the gables face +-Z; a dormer spread along
  // X at one Z, as this was, is a box buried INSIDE the roof mass —
  // it rendered as nothing at all. They march along the ridge instead,
  // set on the -X slope, each protruding through the tiles it sits in.
  const nd = opts.dormers || 0;
  for (let i = 0; i < nd; i++) {
    const z = -d / 2 + (d / (nd + 1)) * (i + 1);
    const y = h + pitch * 0.42;
    const xs = -(rw / 2) * (1 - 0.42);      // the slope surface, at y
    walls.push(bx(1.3, 1.1, 1.3, xs - 0.3, y, z));
    // Its own little cap, or the dormer is a box growing out of tiles.
    roof.push(bx(1.6, 0.13, 1.6, xs - 0.3, y + 0.58, z));
    // Built facing -Z, then swung to face -X onto the slope it opens on.
    const pane = placedWindow(0.72, 0.62, trim,
        new THREE.Matrix4().makeRotationY(Math.PI / 2)
            .setPosition(xs - 0.95, y, z));
    if (litP && rand() < litP) litBins[rand() < 0.6 ? 0 : 1].push(pane);
    else glass.push(pane);
  }

  const root = new THREE.Group();
  root.name = opts.name || 'Cottage';
  // Joinery is NOT roof tile. The frames, sills and door surround used
  // to be merged into the Roof mesh and came out terracotta: red
  // rectangles pasted on a white wall, which is what the flat-facade
  // complaint actually looked like on a cottage.
  const parts = [[walls, wallMat, 'Walls'],
                 [roof, roofMat, 'Roof'],
                 [stack, MAT.brick({ color: 0x8f5843, variant: rand() }),
                  'Chimney'],
                 [trim, trimMat, 'Trim'],
                 [door, doorMat, 'Door'],
                 [glass, glassMat, 'Glazing']];
  litBins.forEach((geos, i) => parts.push(
      [geos, litMat(LIT_CLASSES[i]), i ? 'LitRooms' + (i + 1) : 'LitRooms']));
  for (const [geos, mat, nm] of parts) {
    if (!geos.length) continue;
    const merged = geos.length === 1 ? geos[0] : mergeGeometries(geos, false);
    if (!merged) continue;
    const m = new THREE.Mesh(merged, mat);
    m.name = nm;
    m.castShadow = !nm.startsWith('LitRooms');
    m.receiveShadow = true;
    root.add(m);
  }
  root.userData.forward = '-Z';
  root.userData.ridgeY = h + pitch;
  return root;
}

/**
 * A setback tower: the Manhattan silhouette, in tiers. Towers STEP IN
 * as they rise; each tier is a `block()`, so every tier keeps real
 * openings.
 *
 * @param {object} opts
 *   `w`, `d`, `h` overall metres; `tiers` (default 3); `style` passed
 *   to `block()`; `taper` fraction each tier narrows by (default 0.22);
 *   `crown` boolean spire/mast on top (default true); `rand`, `lit`.
 * @returns {THREE.Group}
 */
export function tower(opts = {}) {
  const rand = opts.rand || (() => 0.5);
  const tiers = Math.max(1, opts.tiers || 3);
  const taper = opts.taper === undefined ? 0.22 : opts.taper;
  let w = opts.w || 22, d = opts.d || 20;
  const h = opts.h || 90;
  const root = new THREE.Group();
  root.name = opts.name || 'Tower';
  let y = 0;
  for (let i = 0; i < tiers; i++) {
    // Lower tiers are taller: a stack of equal slabs reads as a layer
    // cake, and the real setback ratio is roughly halving.
    const share = (tiers - i) / ((tiers * (tiers + 1)) / 2);
    const th = h * share;
    const t = block({ w, d, h: th, style: opts.style, rand,
        lit: opts.lit, ground: i === 0 ? 'storefront' : 'plain' });
    t.position.y = y;
    t.name = 'Tier' + (i + 1);
    root.add(t);
    y += th;
    w *= 1 - taper; d *= 1 - taper;
  }
  if (opts.crown !== false) {
    const mast = new THREE.Mesh(
        new THREE.CylinderGeometry(w * 0.03, w * 0.09, h * 0.09, 8),
        MAT.brushedSteel());
    mast.position.y = y + h * 0.045;
    mast.castShadow = true;
    mast.name = 'Crown';
    root.add(mast);
  }
  root.userData.forward = '-Z';
  void rand;
  return root;
}

/**
 * Scatter roof furniture on a flat-topped block: water tanks, plant.
 * A bare flat roof is the most visible emptiness in an aerial city
 * shot after the ground plane itself.
 *
 * @param {THREE.Object3D} host Block to add onto (roof assumed at its
 *   bbox top).
 * @param {object} opts `rand` (REQUIRED), `tanks` count (default 1),
 *   `units` HVAC boxes (default 3), `mast` boolean.
 * @returns {THREE.Object3D} host
 */
export function roofClutter(host, opts = {}) {
  const rand = opts.rand || (() => 0.5);
  host.updateMatrixWorld(true);
  const b = new THREE.Box3().setFromObject(host);
  const y = b.max.y;
  const w = (b.max.x - b.min.x) * 0.7, d = (b.max.z - b.min.z) * 0.7;
  const g = new THREE.Group();
  g.name = 'RoofClutter';
  const wood = MAT.weatheredWood();
  const steel = MAT.brushedSteel({ color: 0x8d949b });
  for (let i = 0; i < (opts.tanks === undefined ? 1 : opts.tanks); i++) {
    const r = Math.min(1.7, w * 0.16);
    const tank = new THREE.Mesh(
        new THREE.CylinderGeometry(r, r * 1.04, r * 2.1, 12), wood);
    const cone = new THREE.Mesh(new THREE.ConeGeometry(r * 1.12, r * 0.7, 12),
        wood);
    const legs = new THREE.Group();
    for (let k = 0; k < 4; k++) {
      const a = (k / 4) * Math.PI * 2 + 0.4;
      const l = new THREE.Mesh(
          new THREE.CylinderGeometry(r * 0.07, r * 0.07, r * 1.5, 6), steel);
      l.position.set(Math.cos(a) * r * 0.72, r * 0.75, Math.sin(a) * r * 0.72);
      legs.add(l);
    }
    const t = new THREE.Group();
    tank.position.y = r * 1.5 + r * 1.05;
    cone.position.y = r * 1.5 + r * 2.1 + r * 0.35;
    t.add(legs, tank, cone);
    t.position.set((rand() - 0.5) * w, y, (rand() - 0.5) * d);
    g.add(t);
  }
  for (let i = 0; i < (opts.units === undefined ? 3 : opts.units); i++) {
    // Its OWN height sets where it sits. A fixed 0.45 lift buried any
    // unit over 0.9 m tall in the roof it stands on (measured: 0.09 m
    // under, on a box the sampler can make 1.2 m).
    const uh = 0.7 + rand() * 0.5;
    const u = new THREE.Mesh(
        new THREE.BoxGeometry(1.1 + rand(), uh, 0.9 + rand()),
        // Plant is replaced piecemeal over decades: three identical grey
        // boxes on one axis read as a copy, so each takes its own paint
        // bucket and its own yaw.
        MAT.tint(steel, rand()));
    u.position.set((rand() - 0.5) * w, y + uh / 2, (rand() - 0.5) * d);
    u.rotation.y = (rand() - 0.5) * 0.9;
    g.add(u);
  }
  g.traverse((o) => { if (o.isMesh) { o.castShadow = true;
      o.receiveShadow = true; } });
  host.add(g);
  return host;
}

/**
 * Block spans and road gaps along one district axis, centred on 0.
 *
 * Every 4th gap widens to `avenue` when `avenue` is non-zero — the
 * Manhattan rhythm of narrow cross-streets against wide avenues.
 */
function citySpans(total, cell, street, avenue) {
  const cells = [];
  const roads = [];
  let x = 0;
  let i = 0;
  while (x + cell <= total + 1e-6) {
    cells.push([x, x + cell]);
    const gw = avenue && (i + 1) % 4 === 0 ? avenue : street;
    if (x + cell + gw + cell <= total + 1e-6) {
      roads.push({ center: x + cell + gw / 2, width: gw });
    }
    x += cell + gw;
    i += 1;
  }
  const off = cells.length ? -cells[cells.length - 1][1] / 2 : 0;
  return {
    cells: cells.map(([a, b]) => [a + off, b + off]),
    roads: roads.map((r) => ({ center: r.center + off, width: r.width })),
  };
}

/** Split one block into 2-6 lots: full-width strips, or two columns. */
function cityLots(rand, bw, bd) {
  const r = rand();
  const n = r < 0.4 ? 2 : r < 0.7 ? 3 : r < 0.85 ? 4 : r < 0.95 ? 5 : 6;
  const cols = n <= 3 ? [n] : [Math.floor(n / 2), Math.ceil(n / 2)];
  const lots = [];
  const cw = bw / cols.length;
  for (let c = 0; c < cols.length; c++) {
    for (let k = 0; k < cols[c]; k++) {
      lots.push({ x: c * cw, z: (bd / cols[c]) * k, w: cw, d: bd / cols[c] });
    }
  }
  return lots;
}

/**
 * Fill a district rectangle with believable city fabric in ONE call —
 * density is a LAYOUT, not an asset, so this function IS the layout:
 * streets/avenues are real gaps, each block subdivides into 2-6 lots,
 * every built lot gets a `block()` (or a `tower()` where the skyline
 * says tall) with seeded variety, and heights follow `skyline(x, z)`
 * so the district reads as one coherent skyline, not uniform stamps.
 *
 * @param {object} opts
 *   `rand` seeded PRNG (REQUIRED); `width`, `depth` district metres
 *   (default 600 x 600); `blockW`, `blockD` block metres (default
 *   55 x 90); `streetW` street gap (default 14); `avenueW` every 4th
 *   north-south street widens to this (default 24); `density` 0..1
 *   fraction of lots built (default 0.85 — the rest stay empty, like
 *   parking lots); `skyline` `(x, z) => heightScale` (default 1);
 *   `styles` array of `block()` styles to draw from; `lit` passed to
 *   every building for dusk/night; `name`.
 * @returns {{group: THREE.Group, buildings: number,
 *   footprintFrac: number}} `group` rests on y=0, centred on x/z.
 *   `buildings` is the built count and `footprintFrac` the built lot
 *   area over district area, so callers and tests can MEASURE
 *   fullness. `group.userData.streets` lists road centrelines
 *   (`alongZ: [{x, width}]`, `alongX: [{z, width}]`) for road
 *   surfaces and traffic routes.
 */
export function cityFabric(opts = {}) {
  const rand = opts.rand || (() => 0.5);
  const width = opts.width || 600;
  const depth = opts.depth || 600;
  const blockW = opts.blockW || 55;
  const blockD = opts.blockD || 90;
  const streetW = opts.streetW || 14;
  const avenueW = opts.avenueW || 24;
  const density = opts.density === undefined ? 0.85 : opts.density;
  const skyline = opts.skyline || (() => 1);
  const styles = opts.styles || ['masonry', 'brick', 'stone', 'glass'];

  const gx = citySpans(width, blockW, streetW, avenueW);
  const gz = citySpans(depth, blockD, streetW, 0);

  const group = new THREE.Group();
  group.name = opts.name || 'CityFabric';
  group.userData.streets = {
    alongZ: gx.roads.map((r) => ({ x: r.center, width: r.width })),
    alongX: gz.roads.map((r) => ({ z: r.center, width: r.width })),
  };

  let buildings = 0;
  let builtArea = 0;
  for (let ci = 0; ci < gx.cells.length; ci++) {
    const [x0] = gx.cells[ci];
    // Which side of this block column fronts an avenue, if either.
    const aveEast = (ci + 1) % 4 === 0 && ci + 1 < gx.cells.length;
    const aveWest = ci > 0 && ci % 4 === 0;
    for (let ri = 0; ri < gz.cells.length; ri++) {
      const [z0] = gz.cells[ri];
      for (const lot of cityLots(rand, blockW, blockD)) {
        if (rand() >= density) continue;   // an empty lot, not a hole
        // Sidewalk + light-well inset keeps every wall (and the
        // cornice that projects past it) strictly inside the block.
        const inset = 1.6 + rand() * 1.4;
        const lw = lot.w - inset;
        const ld = lot.d - inset;
        const cx = x0 + lot.x + lot.w / 2;
        const cz = z0 + lot.z + lot.d / 2;
        const scale = Math.max(0, skyline(cx, cz));
        let h = (14 + rand() * 26) * scale * (0.7 + rand() * 0.6);
        if (rand() < 0.07) h *= 2.4;       // the odd outlier high-rise
        h = Math.max(8, h);
        const onAvenue = (aveEast && lot.x + lot.w > blockW - 0.5)
            || (aveWest && lot.x < 0.5);
        const rot = onAvenue && rand() < 0.5;
        const style = h > 60 && rand() < 0.5 ? 'glass'
            : styles[Math.floor(rand() * styles.length) % styles.length];
        const b = h > 60
            ? tower({ w: rot ? ld : lw, d: rot ? lw : ld, h, style, rand,
                      lit: opts.lit, tiers: h > 130 ? 4 : 3 })
            : block({ w: rot ? ld : lw, d: rot ? lw : ld, h, style, rand,
                      lit: opts.lit });
        if (rot) b.rotation.y = aveEast ? -Math.PI / 2 : Math.PI / 2;
        b.position.set(cx, 0, cz);
        group.add(b);
        buildings += 1;
        builtArea += lw * ld;
      }
    }
  }
  return { group, buildings, footprintFrac: builtArea / (width * depth) };
}

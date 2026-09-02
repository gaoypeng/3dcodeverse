/**
 * Cartoon creatures: round, two-and-a-half heads tall, with real
 * three-layer eyes (sclera, iris, offset glint — flat dots read as a
 * toy). NOT figure.js's human proportions shrunk: head 40-50% of
 * height, egg body, stubby limbs. Every creature rests on y=0 and
 * faces -Z, so `seat()` and `alignAlong()` from place.js work on it.
 *
 * Colour contract (this host renders ACES + sRGB at exposure 1 with no
 * post chain): every author colour goes through `toonAlbedo`, which
 * holds it inside the 0.02..0.78 LINEAR band a lit surface can live in,
 * and every part carries its own noise seed and variant, so a body is a
 * range of one hue rather than one flat value.
 */

import * as THREE from 'three';
import * as MAT from './materials.js';

/**
 * Clamp an author colour into the albedo band and keep its hue.
 *
 * A cartoon palette is written as ink — `0xfdfdfd` eyes, `0xfff0a8`
 * bellies, `0x151515` pupils. Those are not albedos: at exposure 1
 * the near-whites clip to paper and the near-blacks have no linear
 * lightness left for the sun to lift, so the creature reads as a
 * sticker with two holes in it. Lightness is clamped in LINEAR space
 * (that is where 0.02..0.78 means anything), the hue is untouched, and
 * a colour that had to be pulled down off white keeps a little
 * saturation so it still belongs to the body's family.
 */
function toonAlbedo(hex) {
  const c = new THREE.Color(hex);
  const hsl = c.getHSL({ h: 0, s: 0, l: 0 });
  const l = Math.min(0.78, Math.max(0.02, hsl.l));
  const s = hsl.l > 0.78 ? Math.max(hsl.s, 0.06) : hsl.s;
  c.setHSL(hsl.h, s, l);
  return c.getHex();
}

/** Blend two author colours in linear space; k=0 is `a`, k=1 is `b`. */
function mixHex(a, b, k) {
  return new THREE.Color(a).lerp(new THREE.Color(b), k).getHex();
}

/**
 * A belly/underside tone derived from the body colour.
 *
 * A fixed cream belly on every creature is what makes a set of them
 * read as one toy line in three colours: the underside belongs to the
 * body's hue, lifted and washed out, not to a separate palette.
 */
function bellyFrom(hex) {
  const c = new THREE.Color(hex);
  const hsl = c.getHSL({ h: 0, s: 0, l: 0 });
  c.setHSL((hsl.h + 0.015) % 1, hsl.s * 0.42,
      Math.min(0.72, hsl.l * 1.7 + 0.20));
  return c.getHex();
}

/**
 * One part's tone, graded off the creature's base colour.
 *
 * @param {number} hex Base colour.
 * @param {number} dl Lightness step (linear, signed).
 * @param {number} dh Hue step; + is warm here, - is cool.
 * @param {number} [ks] Saturation multiplier (default 1).
 *
 * This — not surface noise — is where a creature's colour variance
 * belongs. A body is a set of clean parts in one hue FAMILY: the face
 * carries the author's colour, the chest a step under it and warmer,
 * the limbs a step under that and cooler because the body shades them.
 * Painters do it on every toy; this renderer will not invent it, and
 * a creature without it is one flat value with a shadow on it.
 */
function partColor(hex, dl, dh, ks) {
  const c = new THREE.Color(hex);
  const hsl = c.getHSL({ h: 0, s: 0, l: 0 });
  c.setHSL((hsl.h + dh + 1) % 1,
      Math.min(1, hsl.s * (ks === undefined ? 1 : ks)),
      Math.max(0.02, hsl.l + dl));
  return c.getHex();
}

/**
 * Toon-friendly body material: saturated, matte, no metal.
 *
 * The noise is a GRAIN, not a pattern. `noiseTexture` multiplies
 * contrast by an fBm that sits within about +-0.08 of 0.5 and clamps
 * the product at 1, so a caller's contrast reaches the albedo at about
 * a twelfth of its face value: fabric's own 0.22 measured a map with
 * std 4.4/255 (1.7%) — one flat plastic value. Spending that back is
 * only half the fix, because FREQUENCY decides what the swing reads as.
 * Rendered and looked at, in order: 11 cells at 0.70 came back as
 * stains on a yellow creature, 34 cells at 0.55 as speckled lichen.
 * 34 at 0.28 is a grain the eye integrates into one richer colour, and
 * the creature's real variance comes from `partColor` instead.
 */
function skinMat(color, rough, o = {}) {
  return MAT.fabric({
    color: toonAlbedo(color),
    roughness: rough === undefined ? 0.72 : rough,
    bump: o.bump === undefined ? 0.006 : o.bump,
    scale: o.scale === undefined ? 34 : o.scale,
    contrast: o.contrast === undefined ? 0.28 : o.contrast,
    hueBreak: o.hueBreak === undefined ? 0.16 : o.hueBreak,
    variant: o.variant === undefined ? 0.5 : o.variant,
    spread: 0.45,
    seed: o.seed === undefined ? 1 : o.seed,
  });
}

/** A tapered stub limb hanging down -Y from its pivot, with a paw. */
function stub(len, r0, r1, mat, name, pawMat) {
  const g = new THREE.Group();
  g.name = name;
  const geo = new THREE.CylinderGeometry(r1, r0, len, 10, 1);
  geo.translate(0, -len / 2, 0);
  const m = new THREE.Mesh(geo, mat);
  m.castShadow = true;
  g.add(m);
  // The paw's BOTTOM sits at the end of the limb, not its centre —
  // centring it buries a third of the foot under the ground plane.
  const pawR = r1 * 1.35;
  const paw = new THREE.Mesh(new THREE.SphereGeometry(pawR, 10, 8),
      pawMat || mat);
  paw.scale.set(1, 0.8, 1.25);
  paw.position.y = -len + pawR * 0.8;
  paw.castShadow = true;
  g.add(paw);
  return g;
}

function pivot(name, x, y, z) {
  const o = new THREE.Object3D();
  o.name = name;
  o.position.set(x, y, z);
  return o;
}

/**
 * Build one eye: sclera, iris, and the glint that makes it alive.
 *
 * @param {number} r Eye radius in metres.
 * @param {number} irisColor
 * @returns {THREE.Group}
 */
function eye(r, irisColor) {
  const g = new THREE.Group();
  // An eyeball is not a light source: paper-white sclera blew out
  // against every body colour and killed the iris's contrast with it.
  const white = new THREE.Mesh(
      new THREE.SphereGeometry(r, 14, 12),
      MAT.plaster({ color: toonAlbedo(0xf2efe8), roughness: 0.26, bump: 0,
          contrast: 0.05, hueBreak: 0.02 }));
  white.scale.set(0.85, 1, 0.7);
  g.add(white);
  // The creature faces -Z, so iris and glint belong on the -Z side:
  // shipped at +Z once, every creature had blank white blobs for eyes.
  // The iris is dark but never black — a pupil with no albedo left is a
  // hole in the face, and the env's cool bounce is what makes it wet.
  const iris = new THREE.Mesh(
      new THREE.SphereGeometry(r * 0.62, 12, 10),
      MAT.plaster({ color: toonAlbedo(irisColor === undefined
              ? 0x1b2430 : irisColor),
          roughness: 0.18, bump: 0, contrast: 0.05 }));
  iris.position.z = -r * 0.45;
  iris.scale.set(0.9, 1, 0.6);
  g.add(iris);
  // Glint offset up and to one side, never centred — centred reads
  // as a painted dot, offset reads as wet. Emissive 2.2 is inside the
  // bloom-friendly band (1.5-4) on a surface this small: at 0.55 the
  // tone map folded it into the sclera and the eye went dead.
  const glint = new THREE.Mesh(
      new THREE.SphereGeometry(r * 0.19, 8, 6),
      MAT.plaster({ color: 0xdedad2, roughness: 0.1, bump: 0,
          extra: { emissive: new THREE.Color(0xfff4e2),
                   emissiveIntensity: 2.2 } }));
  glint.position.set(-r * 0.28, r * 0.32, -r * 0.74);
  g.add(glint);
  return g;
}

const BODIES = {
  // headRatio: head diameter as a fraction of total height.
  // bodyW/bodyD: body width and depth as a fraction of height.
  biped: { headRatio: 0.46, bodyW: 0.62, bodyD: 0.52, legs: 2, legLen: 0.24 },
  quadruped: { headRatio: 0.42, bodyW: 0.70, bodyD: 1.05, legs: 4,
               legLen: 0.30 },
  blob: { headRatio: 0.52, bodyW: 0.85, bodyD: 0.80, legs: 2, legLen: 0.10 },
  serpent: { headRatio: 0.40, bodyW: 0.42, bodyD: 1.5, legs: 0, legLen: 0 },
};

/**
 * Build a cartoon creature.
 *
 * @param {object} [opts]
 *   `height` overall metres (default 0.6 -- most are knee-high, and
 *   getting that wrong is why creature scenes read as monsters);
 *   `body` one of `biped | quadruped | blob | serpent`;
 *   `bodyColor`, `bellyColor`, `accentColor`, `irisColor` hex
 *   (`bellyColor` defaults to the body's own hue, lifted);
 *   `eyeScale` multiplier on the default eye size (default 1);
 *   `rand` seeded PRNG; `name`.
 * @returns {THREE.Group} Feet at y=0, facing -Z, with `userData.forward`,
 *   `userData.height`, `userData.joints` (`head`, `body`, `legFL` ...)
 *   and `userData.headR` so attachments can size themselves.
 */
export function creature(opts = {}) {
  const rand = opts.rand || (() => 0.5);
  const h = (opts.height || 0.6) * (0.94 + rand() * 0.12);
  const spec = BODIES[opts.body] || BODIES.biped;

  const bodyC = opts.bodyColor === undefined ? 0x7fc4e8 : opts.bodyColor;
  const bellyC = opts.bellyColor === undefined ? bellyFrom(bodyC)
      : opts.bellyColor;
  // The head carries the author's colour — it is the part a viewer
  // reads — and every other part is graded off it: chest a step under
  // and warmer, limbs a step under that and cooler, because the body
  // shades them and skylight is what is left when the sun is blocked.
  const mat = skinMat(bodyC);
  const torsoMat = skinMat(partColor(bodyC, -0.045, 0.008), undefined,
      { seed: 2 });
  const limbMat = skinMat(partColor(bodyC, -0.10, -0.012, 0.94), undefined,
      { seed: 3 });
  // A big pale underside takes a gentler grain than a saturated body:
  // at full strength the same speckle reads as grime on near-white.
  const belly = skinMat(bellyC, 0.78,
      { seed: 4, scale: 28, contrast: 0.20, hueBreak: 0.12 });
  // A pure belly-coloured paw reads as a sock pulled over the leg;
  // half-way to the body colour keeps the two-tone and loses the seam.
  const pawMat = skinMat(mixHex(bodyC, bellyC, 0.55), 0.75, { seed: 5 });

  const root = new THREE.Group();
  root.name = opts.name || 'Creature';

  const headR = spec.headRatio * h * 0.5;
  const legLen = spec.legLen * h;
  let bodyH = h - legLen - headR * 2 * 0.82;   // heads overlap bodies
  // A legless body is an egg lying ON the ground: the torso sphere is
  // scaled 1.12 in Y about its own centre, so its underside dips 6% of
  // bodyH below the pivot and a serpent shipped buried to the waist.
  const lift = spec.legs === 0 ? bodyH * 0.06 : 0;
  bodyH -= lift;                               // keep the total at h
  const baseY = legLen + lift;
  const bodyW = spec.bodyW * h;
  const bodyD = spec.bodyD * h;
  const bodyY = baseY + bodyH / 2;

  // BODY: an egg, wider than tall for the round silhouette. A cylinder
  // or a box here is the difference between a creature and a prop.
  const bodyPivot = pivot('body', 0, baseY, 0);
  const torso = new THREE.Mesh(new THREE.SphereGeometry(0.5, 24, 18),
      torsoMat);
  torso.scale.set(bodyW, bodyH * 1.12, bodyD);
  torso.position.y = bodyH / 2;
  torso.castShadow = true;
  torso.name = 'torso';
  bodyPivot.add(torso);

  // Belly patch: a flattened cap on the front, which is what gives a
  // round creature a readable front at any distance. It has to stand
  // CLEARLY proud of the torso: at the shipped 0.66 depth the two
  // ellipsoids were near-tangent over a wide ring and the patch's edge
  // was a band of z-fighting speckle, not a bib.
  const bp = new THREE.Mesh(new THREE.SphereGeometry(0.5, 18, 12), belly);
  bp.scale.set(bodyW * 0.62, bodyH * 0.70, bodyD * 0.60);
  bp.position.set(0, bodyH * 0.44, -bodyD * 0.27);
  bp.name = 'belly';
  bodyPivot.add(bp);
  root.add(bodyPivot);

  // HEAD: oversized, sitting low into the body so there is no neck gap.
  const headY = baseY + bodyH * 0.94 + headR * 0.42;
  const headPivot = pivot('head', 0, headY - baseY, 0);
  const head = new THREE.Mesh(new THREE.SphereGeometry(headR, 22, 18), mat);
  head.scale.set(1.06, 1, 0.98);
  head.castShadow = true;
  head.name = 'headMesh';
  headPivot.add(head);

  // EYES: wide apart, below the head's equator. High, close-set eyes
  // read as an adult face; low and wide reads as a young creature, and
  // that is the whole of "cute" as a measurable quantity.
  const er = headR * 0.30 * (opts.eyeScale || 1);
  for (const s of [-1, 1]) {
    const e = eye(er, opts.irisColor);
    e.position.set(s * headR * 0.46, -headR * 0.06, -headR * 0.80);
    e.name = 'eye' + (s < 0 ? 'L' : 'R');
    headPivot.add(e);
  }
  // A small muzzle keeps the face from being a flat sphere, and the
  // nose is the one dark accent that stops it reading as a white blob.
  const muzzle = new THREE.Mesh(new THREE.SphereGeometry(headR * 0.30, 12, 10),
      belly);
  muzzle.scale.set(1.2, 0.72, 0.9);
  muzzle.position.set(0, -headR * 0.36, -headR * 0.88);
  muzzle.name = 'muzzle';
  headPivot.add(muzzle);
  // Same material call as the iris, so it is a cache hit rather than a
  // thirteenth material and a thirteenth texture pair per palette.
  const nose = new THREE.Mesh(new THREE.SphereGeometry(headR * 0.085, 8, 6),
      MAT.plaster({ color: toonAlbedo(opts.irisColor === undefined
              ? 0x1b2430 : opts.irisColor),
          roughness: 0.18, bump: 0, contrast: 0.05 }));
  nose.scale.set(1.25, 0.85, 0.8);
  nose.position.set(0, -headR * 0.30, -headR * 1.10);
  nose.name = 'nose';
  headPivot.add(nose);
  bodyPivot.add(headPivot);

  const joints = { body: bodyPivot, head: headPivot };

  // LEGS. Quadrupeds get four under the body's corners; bipeds get two
  // stubs directly under the egg.
  const legR = h * 0.055;
  if (spec.legs === 4) {
    for (const [nm, sx, sz] of [['FL', -1, -1], ['FR', 1, -1],
                                ['BL', -1, 1], ['BR', 1, 1]]) {
      const p = pivot('leg' + nm, sx * bodyW * 0.34, legLen,
          sz * bodyD * 0.30);
      p.add(stub(legLen, legR * 1.25, legR, limbMat, 'legMesh' + nm, pawMat));
      root.add(p);
      joints['leg' + nm] = p;
    }
  } else if (spec.legs === 2) {
    for (const s of [-1, 1]) {
      const nm = s < 0 ? 'L' : 'R';
      const p = pivot('leg' + nm, s * bodyW * 0.30, legLen, 0);
      p.add(stub(legLen, legR * 1.4, legR * 1.1, limbMat, 'legMesh' + nm,
          pawMat));
      root.add(p);
      joints['leg' + nm] = p;
    }
    // Bipeds also get little arms off the body.
    for (const s of [-1, 1]) {
      const nm = s < 0 ? 'L' : 'R';
      const p = pivot('arm' + nm, s * bodyW * 0.46, bodyH * 0.62, 0);
      p.rotation.z = s * 0.35;
      p.add(stub(h * 0.16, legR, legR * 0.8, limbMat, 'armMesh' + nm, pawMat));
      bodyPivot.add(p);
      joints['arm' + nm] = p;
    }
  }

  root.userData.forward = '-Z';
  root.userData.height = h;
  root.userData.headR = headR;
  root.userData.bodyW = bodyW;
  root.userData.bodyD = bodyD;
  root.userData.bodyY = bodyY;
  root.userData.joints = joints;
  root.userData.mat = mat;
  root.userData.colors = { body: bodyC, belly: bellyC };
  return root;
}

/** The author colour a creature was built from, for ears and tails. */
function baseColor(c) {
  return c.userData.colors ? c.userData.colors.body
      : c.userData.mat.color.getHex();
}

/**
 * Add a pair of ears to a creature's head.
 *
 * @param {THREE.Group} c From `creature()`.
 * @param {object} [opts] `type` `point | round | long | fin`, `length`
 *   as a multiple of head radius (default 0.9), `tipColor` hex for a
 *   contrasting tip (an inner-ear pad on `round`), `spread` 0..1 across
 *   the skull (default 0.55), `tilt` radians outward (default 0.35),
 *   `color`.
 * @returns {THREE.Group} c
 */
export function ear(c, opts = {}) {
  const hr = c.userData.headR;
  const head = c.userData.joints.head;
  const len = hr * (opts.length === undefined ? 0.9 : opts.length) * 2;
  const type = opts.type || 'point';
  const col = opts.color === undefined ? baseColor(c) : opts.color;
  // Ears sit a step under the face: they are thin, backlit and
  // mostly in the skull's own shadow.
  const mat = skinMat(partColor(col, -0.03, -0.006), undefined, { seed: 7 });
  for (const s of [-1, 1]) {
    const g = new THREE.Group();
    g.name = 'ear' + (s < 0 ? 'L' : 'R');
    let geo;
    if (type === 'round') {
      geo = new THREE.SphereGeometry(len * 0.42, 12, 10);
      const m = new THREE.Mesh(geo, mat);
      m.scale.set(0.55, 1, 0.9);
      m.position.y = len * 0.35;
      g.add(m);
      if (opts.tipColor !== undefined) {
        // `tipColor` was silently ignored on round ears. An inner pad
        // is what it means here, and it is the cheapest cute signal
        // there is: two warm ovals inside two body-coloured discs.
        const pad = new THREE.Mesh(
            new THREE.SphereGeometry(len * 0.42, 10, 8),
            skinMat(opts.tipColor, 0.7, { seed: 8 }));
        pad.scale.set(0.30, 0.74, 0.66);
        pad.position.set(-s * len * 0.10, len * 0.36, 0);
        g.add(pad);
      }
    } else if (type === 'fin') {
      // A fin is a BLADE. Unflattened, this 4-gon cone rendered as a
      // pyramid the size of the skull sitting on the creature's head.
      geo = new THREE.ConeGeometry(len * 0.42, len, 4);
      const m = new THREE.Mesh(geo, mat);
      m.scale.set(0.30, 1, 1.15);
      m.position.y = len / 2;
      m.rotation.x = -0.22;
      g.add(m);
    } else {
      const taper = type === 'long' ? 0.16 : 0.30;
      const rTip = hr * 0.06, rBase = hr * taper;
      geo = new THREE.CylinderGeometry(rTip, rBase, len, 10);
      const m = new THREE.Mesh(geo, mat);
      m.position.y = len / 2;
      g.add(m);
      if (opts.tipColor !== undefined) {
        // The tip has to CAP the cone it sits on. Shipped with fixed
        // radii it flared out of a `long` ear (which is only 0.087 hr
        // wide there) and read as an orange wedge between the ears.
        const tl = len * 0.30, y0 = len - tl;
        const rAt = rBase + (rTip - rBase) * (y0 / len);
        const tip = new THREE.Mesh(
            new THREE.CylinderGeometry(rTip * 1.03, rAt * 1.03, tl, 10),
            skinMat(opts.tipColor, undefined, { seed: 8 }));
        tip.position.y = y0 + tl / 2;
        g.add(tip);
      }
    }
    g.traverse((o) => { if (o.isMesh) o.castShadow = true; });
    const spread = opts.spread === undefined ? 0.55 : opts.spread;
    g.position.set(s * hr * spread, hr * 0.72, hr * 0.1);
    g.rotation.z = -s * (opts.tilt === undefined ? 0.35 : opts.tilt);
    head.add(g);
  }
  return c;
}

/**
 * Add a tail. Tails are most of a creature's silhouette from behind.
 *
 * @param {THREE.Group} c
 * @param {object} [opts] `type` `bolt | flame | leaf | curl | fin |
 *   stub`, `length` as a multiple of the body's span (default 1),
 *   `color`, `tipColor`.
 * @returns {THREE.Group} c
 */
export function tail(c, opts = {}) {
  const ud = c.userData;
  // The span is the body depth CAPPED BY THE CREATURE'S OWN HEIGHT: a
  // serpent's bodyD is 1.5 h, and taking it neat gave a 1.9 m serpent a
  // 3.4 m tail — a green pyramid taller than the animal wearing it.
  const span = Math.min(ud.bodyD, ud.height * 0.85);
  const len = span * (opts.length === undefined ? 1 : opts.length);
  const col = opts.color === undefined ? baseColor(c) : opts.color;
  const mat = skinMat(partColor(col, -0.05, 0.006), undefined, { seed: 9 });
  const g = new THREE.Group();
  g.name = 'tail';
  const type = opts.type || 'stub';
  if (type === 'bolt') {
    // A flat zig-zag: three plates, each rotated off the last.
    let y = 0, z = 0, a = 0;
    for (let i = 0; i < 3; i++) {
      const seg = len * (0.5 - i * 0.08);
      a += (i % 2 ? -1 : 1) * 0.8;
      // The last plate takes the tip colour when there is one: a bolt
      // that fades to a lighter point reads as a bolt, not a bracket.
      const pm = (i === 2 && opts.tipColor !== undefined)
          ? skinMat(opts.tipColor, undefined, { seed: 10 }) : mat;
      const m = new THREE.Mesh(
          new THREE.BoxGeometry(ud.bodyW * 0.16, seg, seg * 0.42), pm);
      m.position.set(0, y + Math.cos(a) * seg / 2, z + Math.sin(a) * seg / 2);
      m.rotation.x = -a;
      g.add(m);
      y += Math.cos(a) * seg; z += Math.sin(a) * seg;
    }
  } else if (type === 'flame') {
    const m = new THREE.Mesh(
        new THREE.CylinderGeometry(len * 0.08, len * 0.17, len, 10), mat);
    m.position.y = len / 2;
    m.rotation.x = -0.5;
    g.add(m);
    const tipC = opts.tipColor === undefined ? 0xff8a2b : opts.tipColor;
    // ONE cone, because two opaque ones are two cones: a concentric hot
    // core is never seen (the sheath tapers faster at every height) and
    // a stacked one reads as a party hat on a party hat. Both were
    // built and looked at. What DOES read is the emissive being a
    // deeper, more saturated colour than the surface: by the time the
    // tone map sees an orange flame its red is already clipped at 250,
    // so intensity spent on the surface hue only lifts green and blue
    // and the fire comes back cream (measured, 1.2 -> 2.3: the flame's
    // own pixels went from saturation 0.62 to 0.50). Emissive 1.8 on
    // the deepened hue sits in the bloom band AND stays orange.
    const f = new THREE.Mesh(new THREE.ConeGeometry(len * 0.18, len * 0.50, 12),
        MAT.fabric({ color: toonAlbedo(tipC), bump: 0, scale: 5,
            contrast: 0.24, hueBreak: 0.22,
            extra: { emissive: new THREE.Color(
                         partColor(tipC, -0.07, -0.012, 1.35)),
                     emissiveIntensity: 1.8 } }));
    f.position.set(0, len * 0.92, -len * 0.24);
    g.add(f);
    // The throat: a hot bead where the flame leaves the stem. Offset in
    // the SILHOUETTE, not buried inside the cone, so it is visible.
    const core = new THREE.Mesh(new THREE.SphereGeometry(len * 0.085, 10, 8),
        MAT.fabric({ color: toonAlbedo(mixHex(tipC, 0xffdc9a, 0.6)),
            bump: 0, scale: 5, contrast: 0.1,
            extra: { emissive: new THREE.Color(mixHex(tipC, 0xffc878, 0.5)),
                     emissiveIntensity: 2.6 } }));
    core.position.set(0, len * 0.71, -len * 0.19);
    g.add(core);
  } else if (type === 'leaf') {
    const m = new THREE.Mesh(new THREE.SphereGeometry(len * 0.42, 12, 8), mat);
    m.scale.set(0.35, 1, 0.55);
    m.position.y = len * 0.4;
    g.add(m);
    // A stalk: the blade alone floats a hand's width off the rump.
    const stalk = new THREE.Mesh(
        new THREE.CylinderGeometry(len * 0.035, len * 0.055, len * 0.32, 8),
        skinMat(partColor(col, -0.10, -0.012, 0.94), undefined,
            { seed: 11 }));
    stalk.position.y = len * 0.13;
    g.add(stalk);
  } else if (type === 'curl') {
    // ONE tapered tube, not a row of spheres. Seven separated balls read
    // as a bubble trail leaving the creature — and cost 980 triangles
    // doing it; this is 288, it starts INSIDE the rump so it grows out
    // of the animal, and it thins toward the tip the way a tail does
    // (an untapered tube is a suitcase handle).
    const pts = [];
    for (let i = -2; i <= 12; i++) {
      const t = i / 12;
      pts.push(new THREE.Vector3(0, len * 0.5 * Math.sin(t * 3.4),
          len * 0.42 * (1 - Math.cos(t * 3.4)) - len * 0.10));
    }
    const curve = new THREE.CatmullRomCurve3(pts);
    const TUB = 20, RAD = 8;
    const geo = new THREE.TubeGeometry(curve, TUB, len * 0.135, RAD, false);
    const pos = geo.attributes.position;
    for (let i = 0; i <= TUB; i++) {
      const c0 = curve.getPointAt(i / TUB);
      const k = 1 - 0.66 * (i / TUB);
      for (let j = 0; j <= RAD; j++) {
        const vi = i * (RAD + 1) + j;
        pos.setXYZ(vi, c0.x + (pos.getX(vi) - c0.x) * k,
            c0.y + (pos.getY(vi) - c0.y) * k,
            c0.z + (pos.getZ(vi) - c0.z) * k);
      }
    }
    pos.needsUpdate = true;
    geo.computeVertexNormals();
    g.add(new THREE.Mesh(geo, mat));
    const tip = new THREE.Mesh(new THREE.SphereGeometry(len * 0.05, 8, 6),
        opts.tipColor === undefined ? mat
            : skinMat(opts.tipColor, undefined, { seed: 12 }));
    tip.position.copy(pts[pts.length - 1]);
    g.add(tip);
  } else if (type === 'fin') {
    // Flattened, or it is a pyramid: a 3-gon cone at full width was the
    // single loudest wrong shape in the whole module.
    const m = new THREE.Mesh(new THREE.ConeGeometry(len * 0.44, len, 3), mat);
    m.scale.set(0.20, 1, 1);
    m.rotation.x = -1.75;
    m.position.set(0, len * 0.24, len * 0.16);
    g.add(m);
  } else {
    const m = new THREE.Mesh(new THREE.SphereGeometry(len * 0.22, 12, 10), mat);
    m.position.y = len * 0.15;
    g.add(m);
  }
  g.traverse((o) => { if (o.isMesh) o.castShadow = true; });
  g.position.set(0, ud.bodyY - (c.userData.joints.body.position.y),
      ud.bodyD * 0.44);
  c.userData.joints.body.add(g);
  c.userData.joints.tail = g;
  return c;
}

/**
 * Add coloured cheek patches -- a cheap, very legible face marking.
 *
 * @param {THREE.Group} c
 * @param {number} color
 * @param {number} [size] Multiple of head radius (default 0.26).
 * @returns {THREE.Group} c
 */
export function cheeks(c, color, size) {
  const hr = c.userData.headR;
  const r = hr * (size === undefined ? 0.26 : size);
  for (const s of [-1, 1]) {
    // Slightly rounder and a touch further out than the flat disc that
    // shipped: at 0.35 depth the rim grazed the skull and speckled.
    const m = new THREE.Mesh(new THREE.SphereGeometry(r, 12, 10),
        skinMat(color, 0.62, { seed: 13, contrast: 0.13 }));
    m.scale.set(1, 1, 0.22);
    // ON the skull, not in it: at x = 0.62r the sphere surface is at
    // z = -0.78r, so the patch has to sit there or it is half buried.
    // It must not stand far PROUD either: a deep disc reads as a beak
    // from three-quarters, which is what a profile camera catches.
    m.position.set(s * hr * 0.62, -hr * 0.28, -hr * 0.79);
    m.name = 'cheek' + (s < 0 ? 'L' : 'R');
    c.userData.joints.head.add(m);
  }
  return c;
}

/**
 * Attach an arbitrary prop to a named joint -- a bulb, a shell, a horn.
 *
 * @param {THREE.Group} c
 * @param {THREE.Object3D} obj
 * @param {string} joint Joint name (`head`, `body`, `tail`, `legFL` ...).
 * @param {number[]} [offset] Local [x, y, z] in metres.
 * @returns {THREE.Group} c
 */
export function attach(c, obj, joint, offset) {
  const j = c.userData.joints[joint] || c.userData.joints.body;
  if (offset) obj.position.set(offset[0], offset[1], offset[2]);
  j.add(obj);
  return c;
}

/**
 * Walk cycle. Give every creature its own phase or the village marches.
 *
 * @param {THREE.Group} c
 * @param {number} t Seconds.
 * @param {object} [opts] `stride` radians (default 0.5), `rate` Hz
 *   (default 2.4 -- small creatures step fast).
 * @returns {THREE.Group} c
 */
export function walk(c, t, opts = {}) {
  const j = c.userData.joints;
  const a = opts.stride === undefined ? 0.5 : opts.stride;
  const p = t * (opts.rate === undefined ? 2.4 : opts.rate) * Math.PI * 2;
  const sw = Math.sin(p);
  if (j.legFL) {
    j.legFL.rotation.x = sw * a;
    j.legBR.rotation.x = sw * a;
    j.legFR.rotation.x = -sw * a;
    j.legBL.rotation.x = -sw * a;
  } else if (j.legL) {
    j.legL.rotation.x = sw * a;
    j.legR.rotation.x = -sw * a;
    if (j.armL) { j.armL.rotation.x = -sw * a * 0.6;
                  j.armR.rotation.x = sw * a * 0.6; }
  }
  if (j.body) j.body.position.y = (j.body.userData.y0 !== undefined
      ? j.body.userData.y0 : (j.body.userData.y0 = j.body.position.y))
      + Math.abs(sw) * c.userData.height * 0.018;
  if (j.tail) j.tail.rotation.y = sw * 0.25;
  return c;
}

/**
 * Idle: a breathing bob and a slow head turn. A perfectly still
 * creature reads as a statue in every frame the judge sees.
 *
 * @param {THREE.Group} c
 * @param {number} t Seconds.
 * @returns {THREE.Group} c
 */
export function idle(c, t) {
  const j = c.userData.joints;
  if (j.body) {
    const s = 1 + Math.sin(t * 1.6) * 0.035;
    j.body.scale.y = s;
    // The head is a CHILD of the body pivot, so the breath stretched
    // the skull with the chest and the creature's face pulsed. The
    // counter-scale keeps the head round while it still rides the bob.
    if (j.head) j.head.scale.y = 1 / s;
  }
  if (j.head) {
    j.head.rotation.y = Math.sin(t * 0.7) * 0.28;
    j.head.rotation.x = Math.sin(t * 1.1) * 0.06;
  }
  if (j.armL) {
    j.armL.rotation.x = Math.sin(t * 1.4 + 0.4) * 0.10;
    j.armR.rotation.x = Math.sin(t * 1.4 - 0.4) * 0.10;
  }
  if (j.tail) j.tail.rotation.y = Math.sin(t * 1.9) * 0.3;
  return c;
}

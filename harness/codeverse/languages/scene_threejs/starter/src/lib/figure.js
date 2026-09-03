/**
 * A human, with real proportions and joints that pose — the guessed
 * arithmetic (seat heights, gait, tapers) ships as functions because
 * prose never landed. Standard 7.5-head figure, FEET AT y=0, facing
 * -Z, so `seat()` and `alignAlong()` from place.js work unchanged.
 * `walk()` owns the gait contract: pass the route's `speed` and the
 * feet match the ground covered by construction.
 */

import * as THREE from 'three';
import * as MAT from './materials.js';

/**
 * A tapered limb segment hanging down -Y from its pivot. The taper is
 * most of what makes a silhouette read as a person at 30 metres.
 *
 * @param {number} len Segment length in metres.
 * @param {number} r0 Radius at the pivot end.
 * @param {number} r1 Radius at the free end.
 * @param {THREE.Material} mat
 * @param {string} name
 * @returns {THREE.Mesh} Positioned so its top sits at the pivot.
 */
function seg(len, r0, r1, mat, name) {
  const g = new THREE.CylinderGeometry(r1, r0, len, 8, 1, false);
  g.translate(0, -len / 2, 0);
  const m = new THREE.Mesh(g, mat);
  m.name = name;
  m.castShadow = true;
  // A ball cap at the pivot: a flat-capped cylinder opens a visible
  // wedge at every bent knee and elbow (worst on seated crowds at
  // close cameras). ~30 tris buys articulation at any bend angle.
  const cap = new THREE.Mesh(new THREE.SphereGeometry(r0, 6, 5), mat);
  cap.castShadow = true;
  m.add(cap);
  return m;
}

/** An empty pivot at a local point, named so poses can find it. */
function pivot(name, x, y, z) {
  const o = new THREE.Object3D();
  o.name = name;
  o.position.set(x, y, z);
  return o;
}

// A ladder of complexions, warm through olive to deep, with the HUE
// moving as well as the value (a crowd graded only in lightness reads
// as one person under five lamps). Every entry stays inside the
// non-emissive albedo window: the brightest channel here is 0xcc, and
// the old code's real bug was handing MAT.skin `color: undefined`,
// which `Object.assign` copied OVER the default — every face in every
// scene rendered at albedo 1.0, i.e. a blown white ball.
const SKIN_TONES = [0xcca07d, 0xc08e63, 0xa2764c, 0x8a6446, 0x805334,
                    0x5b3c2a];

// One shared eye material for the whole scene: low roughness so the
// environment puts a single specular dot on each eye, which is the
// entire difference between a face and a ball at a close camera.
let _eyeMat = null;
function eyeMaterial() {
  if (!_eyeMat) {
    // Dark, but not below the albedo floor the rest of the figure keeps:
    // a pupil at linear 0.013 is a hole, and the specular dot is what
    // sells an eye anyway.
    _eyeMat = new THREE.MeshStandardMaterial(
        { color: 0x352a20, roughness: 0.22, metalness: 0 });
  }
  return _eyeMat;
}

/** Never let a colour fall to a light-swallowing void (dark hair, dark
 *  shoes): floor its LINEAR lightness, keeping hue and saturation. */
function floorValue(hex, minL) {
  const c = new THREE.Color(hex);
  const hsl = c.getHSL({ h: 0, s: 0, l: 0 });
  if (hsl.l < minL) c.setHSL(hsl.h, hsl.s, minL);
  return c;
}

/**
 * Build an articulated human.
 *
 * @param {object} [opts]
 *   `height` metres (default 1.72), `build` 0.8 slight to 1.25 heavy
 *   (default 1), `fem` 0..1 slides the shoulder:hip width ratio from
 *   the measured male 1.4 to the female 1.2 biacromial:biiliac mean —
 *   two scalars that turn one generator into a mixed crowd, `rand`
 *   seeded PRNG used for the small per-person variation that keeps a
 *   crowd from looking cloned, `skin`, `top`, `bottom`, `hair` hex
 *   colours (`skin` unset picks a complexion off SKIN_TONES from the
 *   same draw that sets the material variant — so a crowd is mixed
 *   without multiplying the material cache), `face` false to drop the
 *   eyes and nose on figures that will never be seen close, `name`.
 * @returns {THREE.Group} Feet at y=0, facing -Z, with `userData.forward`
 *   `'-Z'`, `userData.height`, and `userData.joints` naming every pivot
 *   (`waist`, `neck`, `hipL/R`, `kneeL/R`, `ankleL/R`, `shoulderL/R`,
 *   `elbowL/R`).
 */
export function figure(opts = {}) {
  const rand = opts.rand || (() => 0.5);
  const h = (opts.height || 1.72) * (0.96 + rand() * 0.08);
  const b = opts.build || (0.9 + rand() * 0.25);
  const fem = opts.fem === undefined ? 0 : opts.fem;

  // ONE draw picks both the complexion and the material variant, so the
  // skin cache stays at one material per variant bucket however many
  // tones the ladder holds — a crowd costs no more textures than before.
  const skinV = rand();
  // Quantised to the SAME 9 buckets materials.js quantises `variant`
  // into, so the tone is a function of the bucket and the pair stays
  // 1:1 — a ladder of six tones still costs nine skin materials, not
  // the thirteen two independent partitions would produce.
  const skinQ = Math.round(skinV * 8) / 8;
  const skinCol = opts.skin === undefined
      ? SKIN_TONES[Math.min(SKIN_TONES.length - 1,
          Math.floor(skinQ * SKIN_TONES.length))]
      : opts.skin;
  // Hue break, not just value break: real skin is pink at the knuckles
  // and olive in the shadow, and a flat albedo is what makes a figure
  // read as a mannequin however good the silhouette is.
  const skin = MAT.skin({ color: skinCol, variant: skinV,
    contrast: 0.13, hueBreak: 0.15, scale: 26, roughness: 0.58 });
  const top = MAT.fabric({ color: opts.top === undefined ? 0x44506b
      : opts.top, variant: rand(), contrast: 0.30, hueBreak: 0.15,
    scale: 46 });
  const bottom = MAT.fabric({ color: opts.bottom === undefined ? 0x2f3440
      : opts.bottom, variant: rand(), contrast: 0.26, hueBreak: 0.12,
    scale: 34 });
  // Leather, not the painted-wood grain the first port put on a shoe;
  // the value is floored off black so a shoe is a dark object in the
  // scene's light rather than a hole in the frame.
  // No roughness map and a high roughness on purpose: the noise
  // roughnessMap dips to a near-mirror in places, and on the UPWARD
  // face of a shoe that is a full sky reflection — measured as a
  // blown white (159,152,139) toe cap on a 0x3a albedo.
  const shoe = MAT.fabric({ color: 0x3a332c, roughness: 0.78, scale: 55,
    contrast: 0.18, hueBreak: 0.08, bump: 0.004,
    extra: { roughnessMap: null } });

  const root = new THREE.Group();
  root.name = opts.name || 'Figure';

  const hipY = 0.53 * h;
  const shoulderY = 0.82 * h;
  const shoulderX = (0.115 - 0.018 * fem) * h * b;
  const hipX = 0.052 * h;
  const thigh = 0.25 * h;
  const shin = 0.24 * h;
  const upper = 0.19 * h;
  const fore = 0.145 * h;   // canon 0.146h: the wrist lands at the crotch

  // Torso hangs off a waist pivot so a figure can lean or slump. A
  // lathe, not a cylinder: the S-curve is the strongest silhouette cue
  // and the neck ramp kills the coat-hanger read of a flat-topped torso.
  const waist = pivot('waist', 0, hipY, 0);
  const prof = [
    [0.088 * (1 + 0.12 * fem) * b, 0],       // hip
    [0.070 * (1 - 0.08 * fem) * b, 0.07],    // waist
    [0.105 * (1 - 0.08 * fem) * b, 0.19],    // chest
    [0.095 * (1 - 0.10 * fem), 0.27],        // shoulder
    [0.032, 0.29],                           // trapezius ramp to neck
  ];
  const chest = new THREE.Mesh(
      // 12 radial segments, not 10: at a close camera the 10-segment
      // lathe threw a hard vertical facet crease down the chest, and
      // the extra two rings cost 8 quads on the whole body.
      new THREE.LatheGeometry(
          prof.map((q) => new THREE.Vector2(q[0] * h, q[1] * h)), 12),
      top);
  chest.geometry.scale(1, 1, 0.62);   // people are not cylinders in plan
  chest.name = 'torso';
  chest.castShadow = true;
  waist.add(chest);

  // Pelvis: below the hip line nothing existed, so a seated figure
  // showed a see-through crotch the moment the thighs went horizontal.
  const pelvis = new THREE.Mesh(
      new THREE.SphereGeometry(0.095 * h * b * (1 + 0.10 * fem), 8, 6),
      bottom);
  pelvis.scale.set(1, 0.6, 0.62);
  pelvis.position.y = -0.02 * h;
  pelvis.name = 'pelvis';
  pelvis.castShadow = true;
  waist.add(pelvis);

  const neck = pivot('neck', 0, shoulderY - hipY + 0.02 * h, 0);
  const R = h / 15.5;
  const head = new THREE.Mesh(
      new THREE.SphereGeometry(R, 14, 12), skin);
  head.geometry.scale(0.86, 1, 0.92);
  head.position.y = R + 0.03 * h;
  head.name = 'head';
  head.castShadow = true;
  neck.add(new THREE.Mesh(
      new THREE.CylinderGeometry(0.028 * h, 0.032 * h, 0.05 * h, 8), skin));
  neck.add(head);
  // A face, at ~150 triangles. Without it the head is a ball, and a
  // ball is what makes a close figure read as a shop dummy however
  // good the gait is: the eye finds a face or it finds a mannequin.
  // `face: false` for crowds that stay past ~15 m, where these are
  // sub-pixel anyway.
  if (opts.face !== false) {
    for (const s of [-1, 1]) {
      const eye = new THREE.Mesh(
          new THREE.SphereGeometry(0.155 * R, 5, 4), eyeMaterial());
      eye.name = 'eye' + (s < 0 ? 'L' : 'R');
      // Flattened: an eye is an aperture three times wider than it is
      // tall, and a round ball of one reads as an insect.
      eye.scale.set(1, 0.58, 1);
      eye.position.set(s * 0.32 * R, -0.02 * R, -0.80 * R);
      head.add(eye);
    }
    // The nose is the whole profile: a head in silhouette against the
    // sky is otherwise a circle from every angle.
    const nose = new THREE.Mesh(
        new THREE.SphereGeometry(0.20 * R, 5, 4), skin);
    nose.scale.set(0.62, 1.05, 0.85);
    nose.position.set(0, -0.18 * R, -0.90 * R);
    head.add(nose);
  }
  if (opts.hair !== undefined) {
    const hair = new THREE.Mesh(
        // A CAP, not a helmet: 0.62pi reached below the eye line and
        // swallowed the whole face (measured — the render showed a
        // dark ball with a nose). 0.53pi stops at the temple, and the
        // 0.19 rad tilt drops the rim at the back and lifts it off the
        // brow at the front, which is what a hairline is.
        new THREE.SphereGeometry(R * 1.06, 12, 10,
            0, Math.PI * 2, 0, Math.PI * 0.53),
        // Roughness 0.62, not 0.85: hair is the one thing on a person
        // that carries a broad specular band, and the flat 0.85 felt
        // cap was reading as a helmet. The value floor keeps very dark
        // hair a dark BROWN object instead of a hole in the frame.
        MAT.fabric({ color: floorValue(opts.hair, 0.035), variant: 0.5,
          roughness: 0.62, scale: 70, contrast: 0.35, hueBreak: 0.10,
          bump: 0.004 }));
    hair.position.copy(head.position);
    hair.position.z += 0.03 * R;
    hair.position.y -= 0.02 * R;
    hair.rotation.x = 0.19;
    hair.scale.set(0.90, 1.0, 0.99);
    hair.castShadow = true;
    neck.add(hair);
  }
  waist.add(neck);

  const joints = { waist, neck };
  for (const s of [-1, 1]) {
    const tag = s < 0 ? 'L' : 'R';
    const sh = pivot('shoulder' + tag, s * shoulderX,
        shoulderY - hipY - 0.03 * h, 0);
    sh.add(seg(upper, 0.042 * h * b, 0.033 * h, top, 'upperArm' + tag));
    const el = pivot('elbow' + tag, 0, -upper, 0);
    el.add(seg(fore, 0.033 * h, 0.024 * h, skin, 'forearm' + tag));
    const hand = new THREE.Mesh(
        new THREE.SphereGeometry(0.028 * h, 8, 6), skin);
    hand.scale.set(0.8, 1.25, 0.55);
    hand.position.y = -fore - 0.015 * h;
    hand.name = 'hand' + tag;
    el.add(hand);
    sh.add(el);
    waist.add(sh);
    joints['shoulder' + tag] = sh;
    joints['elbow' + tag] = el;

    const hip = pivot('hip' + tag, s * hipX, 0, 0);
    hip.add(seg(thigh, 0.062 * h * b, 0.048 * h, bottom, 'thigh' + tag));
    const kn = pivot('knee' + tag, 0, -thigh, 0);
    kn.add(seg(shin, 0.046 * h, 0.032 * h, bottom, 'shin' + tag));
    // An ankle pivot: heel strike and toe-off are the only two moments
    // the eye reads as ground contact, and a foot welded to the shin
    // can express neither — that is the flat-footed shuffle.
    const ak = pivot('ankle' + tag, 0, -shin, 0);
    // A shoe, not a brick. The box was 12 triangles but presented one
    // large FLAT horizontal facet, and a horizontal facet in an open
    // sky is the brightest thing in the frame: measured sRGB 159 on a
    // 0x3a albedo, i.e. a white plate stuck to a black shoe, on every
    // figure in the render. A domed upper spreads that same light over
    // a curve, which is what a shoe actually does — 56 triangles, and
    // 6.4% of its area horizontal against the box's 27.2%. Rounded
    // toe, short heel, sole clamped FLAT so the feet-at-y=0 arithmetic
    // every placer depends on is unchanged.
    const fg = new THREE.SphereGeometry(1, 7, 4);
    const fp = fg.attributes.position;
    for (let i = 0; i < fp.count; i++) {
      const z = fp.getZ(i);
      fp.setXYZ(i,
          fp.getX(i) * 0.030 * h,
          Math.max(fp.getY(i) * 0.021 * h, -0.014 * h),
          z * (z < 0 ? 0.085 : 0.058) * h);   // -Z is forward: a toe
    }
    fg.computeVertexNormals();
    const foot = new THREE.Mesh(fg, shoe);
    foot.position.set(0, -0.026 * h, -0.030 * h);
    foot.name = 'foot' + tag;
    foot.castShadow = true;
    ak.add(foot);
    kn.add(ak);
    hip.add(kn);
    waist.add(hip);
    joints['hip' + tag] = hip;
    joints['knee' + tag] = kn;
    joints['ankle' + tag] = ak;
  }

  // Perfectly vertical limbs are the strongest mannequin tell: every
  // figure carries its own resting offsets, and `neutral()` restores
  // THESE, not zeros, so poses stack on a body that already stands.
  root.userData.rest = {
    shoulderL: [0, 0, -0.10], shoulderR: [0, 0, 0.10],
    elbowL: [0.10, 0, 0], elbowR: [0.10, 0, 0],
    hipL: [0, 0, -0.03], hipR: [0, 0, 0.03],
    waist: [0, (rand() - 0.5) * 0.10, 0],
    neck: [0, (rand() - 0.5) * 0.30, 0],
  };
  root.add(waist);
  root.userData.forward = '-Z';
  root.userData.height = h;
  root.userData.joints = joints;
  root.userData.hipHeight = hipY;
  return neutral(root);
}

/** Reset every joint to this figure's resting stance (not rigid zeros). */
function neutral(fig) {
  const rest = fig.userData.rest || {};
  for (const [name, j] of Object.entries(fig.userData.joints)) {
    const r = rest[name] || [0, 0, 0];
    j.rotation.set(r[0], r[1], r[2]);
  }
  fig.userData.joints.waist.position.set(0, fig.userData.hipHeight, 0);
  return fig;
}

/**
 * Sit a figure on something, with its pelvis ON the seat: the seat
 * height determines the pose (thighs horizontal, shins to the floor,
 * torso leant onto the backrest) rather than the author guessing a y.
 *
 * @param {THREE.Group} fig From `figure()`.
 * @param {THREE.Object3D|number} seatTo A chair/bench/stool (its bbox top
 *   is measured) or a seat height in metres.
 * @param {object} [opts] `lean` radians (default 0.12), `spread` radians
 *   of knee splay (default 0.06).
 * @returns {THREE.Group} fig
 */
export function sit(fig, seatTo, opts = {}) {
  // POSE WHATEVER IS ACTUALLY THE FIGURE: composers pass wrapper
  // Groups with the figure() output nested inside. Descend to the
  // first node with userData.joints; if there is none, say so.
  const posed = fig && fig.userData && fig.userData.joints
      ? fig
      : (() => {
          let hit = null;
          if (fig && fig.traverse) {
            fig.traverse((o) => {
              if (!hit && o.userData && o.userData.joints) hit = o;
            });
          }
          return hit;
        })();
  if (!posed) {
    throw new Error(
        'sit(): "' + ((fig && fig.name) || 'this object') + '" was not '
        + 'built by figure(), and neither is anything inside it — there '
        + 'are no joints to pose. Build the person with figure() from '
        + './lib/figure.js, or seat the object with seat() from '
        + './lib/place.js instead.');
  }
  fig = posed;
  const j = neutral(fig).userData.joints;
  let seatY;
  if (typeof seatTo === 'number') {
    seatY = seatTo;
  } else {
    seatTo.updateMatrixWorld(true);
    seatY = new THREE.Box3().setFromObject(seatTo).max.y;
  }
  const lean = opts.lean === undefined ? 0.12 : opts.lean;
  const spread = opts.spread === undefined ? 0.06 : opts.spread;

  // Solve the thigh angle from the seat height: on a low stool the
  // knees ride ABOVE the hips because the shins must reach the floor.
  // `u` lands the feet at y=0, clamped so a bar stool can't fold double.
  const h = fig.userData.height;
  const drop = 0.24 * h + 0.0335 * h;            // shin + ankle + sole
  const u = Math.asin(Math.max(-0.35, Math.min(0.35,
      (drop - seatY) / (0.25 * h))));
  j.waist.rotation.x = -lean;          // back onto the backrest
  for (const s of ['L', 'R']) {
    const sgn = s === 'L' ? -1 : 1;
    // `+ lean` cancels the torso's tilt, so the femur angle is set by
    // the seat and not by how far back the person is reclining.
    j['hip' + s].rotation.x = Math.PI / 2 + u + lean;
    j['hip' + s].rotation.z = sgn * spread;
    j['knee' + s].rotation.x = -(Math.PI / 2 + u);   // shin vertical
    j['shoulder' + s].rotation.x = 0.35;        // forearms toward the table
    // Positive x flexes the forearm FORWARD (measured on the rig; the
    // old -0.8 bent every seated elbow backward, behind the chair).
    j['elbow' + s].rotation.x = 0.8;
  }
  // Pelvis exactly on the seat; the legs then reach the floor on their own.
  fig.position.y = seatY - fig.userData.hipHeight;
  return fig;
}

/**
 * Pose a figure mid-stride. Call per frame with the animation clock.
 * Anti-moonwalk contract: pass `speed` — the SAME m/s the route uses —
 * and cadence + stride are derived so stepLength x cadence === speed
 * by construction; a time-only gait reads as "sliding sideways".
 *
 * @param {THREE.Group} fig From `figure()`.
 * @param {number} t Seconds (give each walker its own `phase`, or a
 *   crowd marches in lockstep).
 * @param {object} [opts] `speed` metres per second — pass the route's
 *   speed and the stride is sized to cover it exactly; `phase`
 *   per-person offset in stride cycles; or the manual knobs `stride`
 *   radians of hip swing (default 0.42) and `rate` strides per second
 *   (default 0.95, about 114 steps/min).
 * @returns {THREE.Group} fig
 */
export function walk(fig, t, opts = {}) {
  const j = fig.userData.joints;
  const h = fig.userData.height;
  const L = fig.userData.hipHeight;   // hip pivot to sole: the leg
  let a, cyc;
  if (opts.speed !== undefined) {
    const v = Math.max(0.05, opts.speed);
    const wr = 0.0065 * (h / 1.72);   // walk ratio, m per (step/min)
    const cadence = Math.min(145, Math.max(70, Math.sqrt(60 * v / wr)));
    cyc = cadence / 120;              // stride cycles per second
    // stepLen = v / (2 * cyc), so stepLen x cadence === v exactly,
    // even where the cadence clamps; a = asin(stepLen / (2 * L)).
    a = Math.asin(Math.min(0.98, v / (4 * cyc * L)));
  } else {
    a = opts.stride === undefined ? 0.42 : opts.stride;
    cyc = opts.rate === undefined ? 0.95 : opts.rate;
  }
  const p = (t * cyc + (opts.phase || 0)) * Math.PI * 2;
  const sw = Math.sin(p);
  j.hipL.rotation.x = sw * a;
  j.hipR.rotation.x = -sw * a;
  // A knee bends one way only: it flexes on the backswing, never past 0.
  j.kneeL.rotation.x = -Math.max(0, -sw) * a * 1.5;
  j.kneeR.rotation.x = -Math.max(0, sw) * a * 1.5;
  // Ankles: toes up on the landing leg (heel strike), toes down
  // pushing off behind — the two moments the eye reads as contact.
  j.ankleL.rotation.x =
      a * (0.25 * Math.max(0, sw) - 0.6 * Math.max(0, -sw));
  j.ankleR.rotation.x =
      a * (0.25 * Math.max(0, -sw) - 0.6 * Math.max(0, sw));
  // Arms counter-swing the legs, and the elbow flexes hardest when its
  // arm is forward — a straight pumping stick reads as a mannequin.
  j.shoulderL.rotation.x = -sw * a * 0.7;
  j.shoulderR.rotation.x = sw * a * 0.7;
  j.elbowL.rotation.x = 0.35 + 0.3 * Math.max(0, -sw);
  j.elbowR.rotation.x = 0.35 + 0.3 * Math.max(0, sw);
  j.waist.rotation.y = sw * 0.06;
  // Pelvis dips TWICE per stride (one dip is the classic procedural
  // limp) and sways once toward the stance foot. Both live on the
  // waist pivot, so route() still owns fig.position.
  j.waist.position.y = L - 0.6 * L * (1 - Math.cos(a * sw));
  j.waist.position.x = 0.023 * (h / 1.72) * sw;
  return fig;
}

/**
 * Raise both forearms to hold something at chest height (a tray, a box).
 *
 * @param {THREE.Group} fig
 * @param {THREE.Object3D} [held] Optional object parented between the
 *   hands, so it tracks the pose instead of floating near it.
 * @returns {THREE.Group} fig
 */
export function carry(fig, held) {
  const j = fig.userData.joints;
  for (const s of ['L', 'R']) {
    j['shoulder' + s].rotation.x = 0.5;
    // +0.6 puts the hands ON the held object's rim (the old -1.3 bent
    // the elbows backward, hands behind the body).
    j['elbow' + s].rotation.x = 0.6;
    j['shoulder' + s].rotation.z = (s === 'L' ? 1 : -1) * 0.12;
  }
  if (held) {
    // MEASURE the hands, do not guess. The held object is parented to
    // the WAIST pivot, which already sits at hipHeight — so the old
    // `0.52 * h` (a figure-root height) put the tray a clear 0.85 m
    // above the hands, i.e. balanced on top of the head. Reading the
    // posed hands back also means the offset survives any later change
    // to the shoulder/elbow angles above.
    const j2 = fig.userData.joints;
    fig.updateMatrixWorld(true);
    const mid = new THREE.Vector3();
    const p = new THREE.Vector3();
    for (const s of ['L', 'R']) {
      const hand = j2['elbow' + s].getObjectByName('hand' + s);
      if (hand) mid.add(hand.getWorldPosition(p));
    }
    mid.multiplyScalar(0.5);
    j2.waist.worldToLocal(mid);
    const h = fig.userData.height;
    held.position.copy(mid);
    held.position.y += 0.035 * h;   // it RESTS on the hands
    held.position.z -= 0.020 * h;   // ... just past the fingertips
    j2.waist.add(held);
  }
  return fig;
}

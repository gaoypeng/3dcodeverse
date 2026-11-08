---
name: 3d-lang-urdf-blender
description: "Use when writing, refining or repairing an articulated_object (language urdf_blender): bpy link meshes in src/model.py plus a hand-written src/robot.urdf. The frame rule (joint origin in the parent frame, visual origin = minus the pivot), the FK check, limits and the motion-direction check, mimic couplings, the joint-sweep clearance thresholds, allowed/forbidden URDF, and the 3dcode loop."
license: Apache-2.0
compatibility: Blender 5.2 LTS headless for the links (the blender skill's sandbox and lint apply to model.py); native URDF for the joints; the 3dcode build exports meshes/<link>.glb, checks FK at q=0 and sweeps every joint.
metadata:
  verified: "2026-11-07"
---

# URDF + bpy — articulated objects

You model a MECHANISM, judged while it moves.  Links are bpy objects named exactly as the URDF
links; joints are native URDF: the origin in the PARENT link frame, the axis in the joint frame
that origin sets up.  The build owns the export and the pose sweep.

## Files
```
src/model.py    pure bpy (3d-lang-blender's sandbox and lint apply): per link, the object named exactly as the
                link plus anything parented under it, built in WORLD coordinates at the REST pose (= q = 0).
src/robot.urdf  links + joints; every link has ONE <visual> and ONE identical <collision>,
                geometry <mesh filename="meshes/<link>.glb"/>, origin = −(link frame).
```
The build runs `model.py` headless; takes the object named `<link>` plus everything parented
under it as that link's mesh (the wrapper joins them); exports `artifacts/meshes/<link>.glb`
with WORLD coordinates baked in; lints the URDF; checks that FK at q = 0 puts every mesh back
where you authored it within 1 mm (else the build FAILS as `FkInconsistent` and prints the
corrected `<origin>`); fails on a rest-pose penetration deeper than 5 mm (`RestPenetration`);
sweeps every joint (`3dcode joint-sweep`); renders q = 0 plus every joint at both limits for the
judge; builds `object.glb` with one node per link.  A link no object matches fails the build
(with a did-you-mean for a case / snake_case mismatch); a visible mesh no link claims fails it
too (parent it under a link's object or add a `<link>`).  `<collision>` missing or different from `<visual>` is a WARN: the VISUAL mesh is collided.
`references/interface.md` is one hinge, end to end.

## Frame rule (measured by the FK check)
* Z up, −Y front, +X right, metres, radians.  Object on z = 0 at q = 0 (`3dcode check-contract`
  compares every link's bbox with the plan's: max(10 mm, 10 % of the planned extent) per axis).
* **q = 0 = what you build = the plan's rest pose** (doors closed, drawers in, lids down unless
  the plan's `rest` says otherwise).  The judge sees q = 0 and both limits of every joint.
* Write every joint with `rpy="0 0 0"`: link frames are then world-aligned and the arithmetic
  below is subtraction.  A rotated origin is accepted (lint INFO) but the visual/collision
  origin must then be the full inverse of the link's FK transform — the FK check prints it.
* Root link frame = world origin.  Every other link frame = its joint's PIVOT: a world point ON
  the joint axis (hinge line, slide axis, axle); fixed joints: the attachment point.
* `<joint><origin xyz>` = `pivot_child − frame_parent` (a child of the root: just its pivot; a
  handle on a door: `pivot_handle − pivot_door`).
* `<visual>` AND `<collision>` `<origin xyz>` = `−pivot_link` (root: `0 0 0`), because the mesh
  holds world coordinates and the link frame sits at the pivot.  The build verifies this
  numerically and prints the numbers when it is wrong.
* `<axis xyz>` is a unit vector in the JOINT frame (world-aligned with `rpy="0 0 0"`): the world
  direction of the hinge line or slide (non-unit: normalised, WARN; missing: `1 0 0`, WARN).
  Sign: positive q must move the child the way the plan's `motion` word says — the
  `motion_direction` gate moves the joint positively and checks the child's centroid
  displacement within 60° of that word.  Wrong way: negate the axis; never swap lower/upper
  (q = 0 must stay the authored rest pose).
* Limits describe the mechanism's real travel (a lid 0…1.5 rad, a drawer 0…0.4 m), never
  `-3.14` / `3.14` placeholders: the sweep poses both ends, and a hinge that can swing through
  its own housing is a modelling error it will find.  `revolute` / `prismatic` need
  `<limit lower upper effort velocity/>` with
  `lower ≤ 0 ≤ upper` (`lower = plan.lower − rest`, `upper = plan.upper − rest`); a missing
  `<limit>` is a load ERROR.  `continuous`: `<limit effort velocity/>` only.  `fixed`: no
  `<limit>`, no `<axis>`.  Lint WARNs: `lower == upper`, a revolute range > 2π (use
  `continuous`), a `<limit>` without effort/velocity.
* Single-root tree: exactly one link that is no joint's child (the base); every other link the
  child of exactly one joint (ERROR otherwise); no cycles.  Anything riding on a moving link is part of that link's mesh or
  a fixed child of it — attached to the base it stays behind and the sweep reports the overlap.
* Names: link name == Blender object name == `meshes/<link>.glb` stem, case-sensitive, a plain
  identifier (`Door.001` means two objects shared a name); `world` is reserved (ERROR); name the
  part, not its state (`door`, not `door_open`: WARN).  snake_case of the plan parts
  (PascalCase accepted).  Copies of a plan part are separate links `<part>_1..n` with their own
  joints `<joint>_1..n`.  Joint names: the plan's, unique.

## Clearance and attachment (`3dcode joint-sweep`)
* Pose set: rest; every movable joint alone at lower, mid and upper (the others at 0;
  `continuous`: 0, ±π/2, π); 8 seeded random full-body combinations when more than one joint
  moves.  `<mimic>` followers follow their driver.
* In every sampled pose no two links overlap by more than 2 mm.  In a moved pose that is an
  ERROR at any depth — the normal failure of this track is an assembly clean at rest that jams
  at 40° of travel: give each moving link the clearance its WHOLE range needs, and make travel
  limits honest rather than letting links intersect.  At rest 2–5 mm is a WARN, > 5 mm fails
  the build.  Links joined only by `fixed` joints are one rigid group.  A non-watertight link
  gets an estimated depth (`[approx: non-watertight mesh]`).
* Attachment at rest: a `fixed` child within 2 mm of its parent (ERROR beyond); a hinged /
  sliding child's mesh within 10 mm (WARN beyond, ERROR beyond 30 mm; a prismatic child already
  inside its parent's bbox is exempt).
* `3dcode check-connectivity` reports a fragment inside a link smaller than 5 % of its largest
  extent as a stray island.

## Allowed / forbidden
`model.py`: the blender skill's lint, plus `exit()` / `quit()` ERROR, `input()` ERROR,
`time.sleep` / `while True` WARN; every link name must appear as a string literal in `model.py`
(WARN otherwise).  URDF: no `<gazebo>`, `<transmission>`, `<sensor>`, xacro, `package://`,
mesh scale (WARN), inline primitives (ERROR: visual geometry must be a `<mesh>`).  Inertials
optional.  `<mimic joint="..." multiplier="..." offset="..."/>` IS honoured — one per follower
of a one-input mechanism (a coupling naming no joint, itself, a fixed joint, or closing a loop
is a lint ERROR).  No triangle-budget gate; the build is killed at 300 s.

## The loop
`3dcode build` (lint, FK at q = 0, rest penetration) → `3dcode joint-sweep` → `3dcode
check-connectivity` + `3dcode check-contract` → fix → repeat → `3dcode render-sheet` → OPEN
the sheet and the pose sheet → `3dcode check` clean.  A sweep ERROR blocks the round whatever
the geometry looks like; `3dcode check-contract` compares every link's bbox with the plan.

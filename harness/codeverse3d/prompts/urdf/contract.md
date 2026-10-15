# URDF + bpy authoring contract — track `articulated_object`, language `urdf_blender`

## Files
```
src/model.py    pure bpy (the blender contract's sandbox and lint apply): per link, the object named
                exactly as the link (plus anything parented under it), built in WORLD coordinates
                at the REST pose (= URDF q=0).
src/robot.urdf  hand-written URDF: links + joints; every link has ONE <visual> and ONE identical
                <collision>, geometry <mesh filename="meshes/<link>.glb"/>, origin = −(link frame).
```
What the harness does with them: runs `model.py` headless; takes the object named exactly
`<link>` plus everything parented under it as that link's mesh (the wrapper joins them — you
need no join of your own; if you do join, set the active object first or the lint WARNs);
exports `artifacts/meshes/<link>.glb` with the WORLD coordinates baked in (nothing is shifted
for you); lints the URDF; checks that FK at q=0 puts every mesh back where you authored it
within 1 mm (otherwise the build FAILS as `FkInconsistent` and prints the corrected `<origin>`);
fails the build on a rest-pose penetration deeper than 5 mm (`RestPenetration`); sweeps every
joint over its range (the round's `joint_sweep` gate); renders q=0 plus every joint at its
lower/upper limit for the judge; builds `object.glb` with one node per link.  You never
export.  A link no object matches fails the build (with a did-you-mean hint for a case or
snake_case mismatch); a visible mesh object no link claims fails it too (parent it under a
link object or add a `<link>`).  `<collision>` missing or different from `<visual>` is a
lint WARN: the harness collides the VISUAL mesh.

## Frame rule (measured by the FK check)
* Z up, −Y front, +X right, meters, radians.  Object on z = 0 at q = 0 (`check_contract`
  compares every link's bbox with the plan's, whose bottom is z = 0; tolerance
  max(10 mm, 10 % of the planned extent) per axis).
* **q = 0 = what you build = the plan's rest pose**: the plan's bbox centres/extents and
  pivots describe this pose (doors closed, drawers in, lids down, arms at home, unless the
  plan's `rest` says otherwise).  The judge sees q=0 and every joint at both limits, so the
  motion is always shown; the geometry itself is the rest pose the plan's bboxes describe.
* The skeleton writes every joint with `rpy="0 0 0"`, so link frames are world-aligned and
  the origin arithmetic below is plain subtraction.  A rotated joint origin is accepted
  (lint INFO) but then the visual/collision origin must be the full inverse of the link's FK
  transform — the FK check tells you the numbers.
* Root link frame = world origin `(0,0,0)`.  Every other link frame = its joint's PIVOT: a
  world point ON the joint axis (hinge line / slide axis / wheel axle); fixed joints: the
  child's attachment point.
* `<joint><origin xyz>` = `pivot_child − frame_parent` (for a child of the root that is just
  the pivot; for a handle on a door: `pivot_handle − pivot_door`).
* `<visual>` AND `<collision>` `<origin xyz>` = `−pivot_link` (root: `0 0 0`), because the
  mesh file holds world coordinates and the link frame sits at the pivot.  This is the line
  everyone gets wrong; the build verifies it numerically (FK at q=0 reproduces the authored
  mesh within 1 mm) and prints the numbers when it does not.
* `<axis xyz>` is a unit vector in the JOINT frame (the frame the joint's `<origin rpy>` sets
  up).  With every `rpy="0 0 0"`, as the skeleton writes them, that frame is world-aligned and
  the axis is the world direction of the hinge line or slide (a non-unit axis is normalised
  with a lint WARN; a missing one defaults to `1 0 0`, WARN).  Sign: positive q
  must move the child the way the plan's `motion` sentence says — the harness
  (`motion_direction`) moves the joint positively and checks that the child's centroid
  displacement is within 60° of that word (front/back/up/down/left/right or ±x/±y/±z).  If
  it goes the other way, negate the axis; never swap lower/upper, because q = 0 must stay
  the authored rest pose.
* Limits: `revolute`/`prismatic`: `<limit lower upper effort velocity/>` with
  `lower ≤ 0 ≤ upper` (q=0 inside the range); from the plan: `lower = plan.lower − rest`,
  `upper = plan.upper − rest` — the plan's own numbers when `rest` is 0 (the skeleton already
  wrote them, with a comment where they are shifted).  `continuous`: `<limit effort velocity/>`
  only (lower/upper on a continuous joint: lint WARN, ignored).  `fixed`: no `<limit>`, no
  `<axis>`.  A revolute/prismatic joint without `<limit>` is a load ERROR; lint WARNs:
  `lower == upper`, a revolute range > 2π (use `continuous`), a `<limit>` without
  effort/velocity.
* Single-root tree: exactly one link that is no joint's child (the base); every other link
  is the child of exactly one joint (ERROR: a link with two parent joints, a link not
  connected to the root); no cycles.  Anything that rides on a moving link is part of that
  link's mesh or a fixed child of it; attached to the base it stays behind when the link
  moves and the sweep reports the overlap.
* Names: link name == Blender object name == `meshes/<link>.glb` stem, case-sensitive,
  identical in both files, a plain identifier (letters/digits/underscore; WARN otherwise —
  `Door.001` means two objects shared a name); `world` is reserved (ERROR); name the part,
  not its state (`door`, not `door_open`: WARN).  Keep the skeleton's names (snake_case of
  the plan parts: `door`, `handle_left`); the plan's PascalCase spelling is accepted too.
  Copies of a plan part are separate links `<part>_1..n` with their own joints
  `<joint>_1..n` (the skeleton places them in a row along +x as placeholders; put them where
  they belong).  Joint names: the plan's joint names; all unique.

## Clearance and attachment (measured by `joint_sweep`)
* Pose set: rest; every movable joint alone at lower, mid and upper (the others at 0;
  `continuous`: 0, ±π/2, π); 8 seeded random full-body combinations when more than one
  joint moves.
* In every sampled pose no two links overlap by more than 2 mm.  In a moved pose that is an
  ERROR at any depth.
  At rest 2–5 mm is a WARN and deeper than 5 mm fails the build (`RestPenetration`).
  Links joined only by `fixed` joints are one rigid group, judged once with the rest policy.
  A link that is not watertight only gets an estimated depth (`[approx: non-watertight mesh]`).
* Attachment at rest: a `fixed` child sits within 2 mm of its parent (touching, or sunk
  ≤ 2 mm; ERROR beyond).  A hinged / sliding child's mesh is within 10 mm of its parent's
  (WARN beyond, ERROR beyond 30 mm; a prismatic child already inside its parent's bbox is
  exempt).
* `check_connectivity` reports a disconnected fragment inside a link smaller than 5 % of the
  link's largest extent as a stray island; the sweep measures overlap depth exactly only on a
  watertight link mesh.

## Allowed / forbidden
`model.py` is linted by the blender contract's rules (imports, forbidden bpy calls, file IO)
plus: `exit()` / `quit()` ERROR (exits Blender before the links are exported), `input()` ERROR
(blocks headless Blender), `time.sleep` and `while True` WARN; every link name must appear as a
string literal in `model.py` (WARN otherwise — the wrapper looks the object up by that exact
name).
URDF: no `<gazebo>`, `<transmission>`, `<sensor>`, xacro, `package://`, mesh scale (WARN:
model in metres in `model.py`), inline primitives (ERROR: visual geometry must be a `<mesh>`).
Inertial blocks are optional.  `<mimic joint="..." multiplier="..." offset="..."/>` IS
honoured: the sweep drives the named joint and every follower follows it, so a one-input
mechanism (umbrella ribs, scissor arms, coupled folding legs) must declare it — one `<mimic>`
per follower, naming the joint that drives the mechanism (a coupling that names no joint,
names itself, mimics a fixed joint or closes a loop is a lint ERROR).
Budgets: the build is killed at `build_timeout_s` (300 s by default); this track has no
triangle-budget gate.

## The frame arithmetic, end to end
One hinge, verified by the build + FK check + sweep.  It shows the origins and where they come
from, not how to build anything: a 0.25 m bin body (root, on the ground) and a 10 mm lid
resting 1 mm above the rim, hinged along its back top edge; q = 0 = lid closed.
`src/model.py`
```python
import bpy, bmesh
from mathutils import Vector

for name, size, centre in (("body", (0.25, 0.25, 0.35), (0, 0, 0.175)),   # root link: frame = world origin
                           ("lid", (0.25, 0.25, 0.01), (0, 0, 0.356))):   # authored CLOSED (q = 0), 1 mm above the rim
    bm = bmesh.new(); bmesh.ops.create_cube(bm, size=1.0); bmesh.ops.scale(bm, vec=size, verts=bm.verts)
    me = bpy.data.meshes.new(name); bm.to_mesh(me); bm.free()
    o = bpy.data.objects.new(name, me); o.location = centre; bpy.context.scene.collection.objects.link(o)
PIVOT = {"body": Vector((0, 0, 0)), "lid": Vector((0, 0.125, 0.351))}  # link frame = a world point on the hinge line
print("lid: joint origin", tuple(PIVOT["lid"] - PIVOT["body"]), "visual/collision origin", tuple(-PIVOT["lid"]))
```
`src/robot.urdf` — the plan's `motion` says the lid opens UP: +q about +X at the back edge
would push the front edge DOWN into the body, so the axis is −X (a sign is fixed by negating
the axis, never by swapping the limits):
```xml
<?xml version="1.0"?>
<robot name="pedal_bin">
  <link name="body">
    <visual><origin xyz="0 0 0" rpy="0 0 0"/><geometry><mesh filename="meshes/body.glb"/></geometry></visual>
    <collision><origin xyz="0 0 0" rpy="0 0 0"/><geometry><mesh filename="meshes/body.glb"/></geometry></collision>
  </link>
  <link name="lid">   <!-- frame at the hinge pivot (0 0.125 0.351) → origin = its negation -->
    <visual><origin xyz="0 -0.125 -0.351" rpy="0 0 0"/><geometry><mesh filename="meshes/lid.glb"/></geometry></visual>
    <collision><origin xyz="0 -0.125 -0.351" rpy="0 0 0"/><geometry><mesh filename="meshes/lid.glb"/></geometry></collision>
  </link>
  <joint name="body_to_lid" type="revolute">
    <parent link="body"/><child link="lid"/>
    <origin xyz="0 0.125 0.351" rpy="0 0 0"/>   <!-- pivot_lid - frame_body -->
    <axis xyz="-1 0 0"/>                         <!-- +q lifts the front edge: the motion word is "up" -->
    <limit lower="0" upper="1.5" effort="5" velocity="2"/>
  </joint>
</robot>
```

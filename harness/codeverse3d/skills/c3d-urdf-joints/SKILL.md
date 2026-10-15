---
name: c3d-urdf-joints
description: "Use when authoring or repairing an articulated_object's src/robot.urdf - links, joints, axes, limits, clearances - or when joint_sweep fired. Also covers the rest pose in src/model.py, and applies on every repair round that reported joint_sweep or motion_direction. Explains which pose set the sweep gate tests, why a moved-pose overlap is always an ERROR, and the arithmetic that predicts which way positive q pushes a child before you write the axis."
license: Apache-2.0
compatibility: track articulated_object, language urdf_blender. Constants read from codeverse3d/languages/urdf/__init__.py, codeverse3d/spatial/joints_sweep.py, joints_poses.py and codeverse3d/tracks/articulated_object.py.
metadata:
  evidence: measured
  verified: "2026-08-25"
  corpus: "26 urdf_blender runs / 50 judged rounds under bench/out, mined 2026-08-25"
  owns: "joint_sweep, motion_direction"
  scope: "URDF physical structure only; SRDF semantics are out of scope and must not be invented"
  target_metric: "joint_sweep_errors"
  target_direction: "down"
  target_unit: "ERROR findings per run"
  target_measurable: "true"
  target_baseline: "6.91 mean / 0.0 median ERRORs per run; n=23 (bench/out, last gated round, 2026-08-25); 20 of 23 at zero, worst run 101"
---

# URDF joints: axes, limits and clearance across the sweep

**When:** any `articulated_object` round that writes or fixes `src/robot.urdf` (and the rest pose
in `src/model.py`), or that received a `joint_sweep` / `motion_direction` finding.

The frame rule (link frame at the pivot, joint origin = pivot of the child minus the frame of
the parent, visual and collision origin = minus the pivot) is in
`codeverse3d/prompts/urdf/contract.md`. This skill is what the gates measure, and how to
satisfy them before the first build.

## Two things fail the BUILD, not just a gate

* **FK check**, tolerance **1 mm** (`FK_TOL_M = 0.001` m). If FK at q=0 does not put every mesh back where
  `model.py` authored it, the build stops with `FkInconsistent` and prints the corrected
  `origin`. Cause is almost always a link's visual/collision `origin` left at `0 0 0` instead of
  `-pivot`, or a joint with a rotated origin (`rpy` not `0 0 0`) whose visual/collision origin
  was not inverted to match.
* **Rest penetration** over **5 mm** (`REST_PENETRATION_MAX_M = 0.005` m): the build stops with
  `RestPenetration`.

Everything else comes back as findings you still have to read.

## The sweep is not a rest-pose check

`joint_sweep` poses the robot at: **rest**, then **every movable joint alone at its lower, mid
and upper limit** (others at 0), then **8 seeded random full-body combinations** (seed 0) when
more than one joint moves. Continuous joints sample 0, +/-pi/2 and pi.

Severity, from `sweep_findings`:

| where the pair overlaps | depth | verdict |
|---|---|---|
| any pose | up to 2.0 mm | not reported (`tol_m = 0.002`) |
| rest, or a pair joined only by `fixed` joints | 2.0-5.0 mm | WARN |
| rest, or a `fixed`-joined pair | over 5.0 mm | ERROR (and fails the build) |
| **any moved pose** | over 2.0 mm | **ERROR, whatever the depth** |

So a 2.1 mm graze that only happens at one limit is scored exactly as badly as a 20 mm gouge.

Measured on the corpus: almost every finding is in a moved pose, a third of the colliding pairs
never touch at rest and some appear only in a random combination — posing joints one at a time
in your head is not enough: two arms that each clear the body can still meet each other.

Strong option: give every moving link more than the 2 mm the sweep tolerates, everywhere in its
envelope, and check the worst *combination* of joints, not the worst single joint. Fixed hardware
is different and cheap — a handle welded to a door may sink up to 2 mm with no finding at all,
because pairs joined only by `fixed` joints are measured once with the rest policy and never
re-judged as they move.

**Attachment** is checked at rest only: a `fixed` child must be within `CONTACT_GAP_M = 0.002` of
its parent (ERROR otherwise, always); a revolute / continuous / prismatic child must be within
**10 mm** of its parent (WARN, ERROR beyond 30 mm) — `link 'moving_jaw' slides in
'fixed_jaw_body' (prismatic joint) but the meshes are 42.5 mm apart at rest`. A prismatic child
already inside the parent's bounding box (a drawer in its cavity) is exempt.

A finding tagged `[approx: non-watertight mesh]` is an estimate: the depth is exact only on a
watertight link mesh.

## Strong options before the first build

**Keep the pivots in model.py and print the two origins.** Record each link's world pivot (the
point you chose on its hinge line or slide axis) in a dict while building; for every joint print
the child's pivot minus the parent's (the joint origin) and the negated child pivot (the visual
and collision origin), then copy both into `src/robot.urdf`. The FK check confirms them within
1 mm, so a wrong line is caught by the build, not by the judge.

```python
PIVOT = {"base": Vector((0, 0, 0))}        # world pivot = link frame, recorded as each link is built
PIVOT["door"] = Vector((-0.2, -0.25, 0.4))  # a point ON the hinge line
PIVOT["handle"] = Vector((0.15, -0.27, 0.45))
for child, parent in (("door", "base"), ("handle", "door")):
    print(child, "joint origin", tuple(PIVOT[child] - PIVOT[parent]), "visual/collision", tuple(-PIVOT[child]))
```

**Hardware is geometry, not joints.** A hinge barrel, a runner or a bracket lives in one of the
two links it connects; it is what lets the attachment check see the child within 10 mm of its
parent. A knob on a door is part of the door's mesh or a fixed child of the door — attached to
the base it stays behind when the door swings, and the sweep reports the overlap.

## Predict the direction before you write the axis

`motion_direction` does **not** read your axis. It moves the joint positively from rest and
measures where the **child link's centroid** went, then asks whether that displacement is within
60 degrees of the direction the plan's `motion` sentence names. Probe: the limit of larger
magnitude, clamped to 0.35 rad for a rotation; the full limit for a slide.

For a revolute joint that is pure geometry:

```python
import numpy as np
def child_moves(axis, pivot, centroid):        # revolute: d ~ axis x (centroid - pivot)
    d = np.cross(axis, np.subtract(centroid, pivot))
    return d / np.linalg.norm(d)               # prismatic: the direction IS the axis
# a lid hinged on its back top edge, centroid just above the rim
print(child_moves((-1, 0, 0), (0, 0.125, 0.351), (0, 0, 0.356)))
# [0.  0.03996804  0.99920096]  -> almost pure +Z: the lid lifts UP
```

Verified against `motion_direction_check` on 2026-08-25: it reported `up` for axis `-1 0 0` and
`down` for `+1 0 0` on that geometry, matching the cross product's sign both times.

Two independent ways to get this wrong, and the corpus has both:

1. **Axis sign.** Fix it by negating `axis`. The contract is explicit — *never* compensate by
   swapping `lower` and `upper`; q=0 must stay the plan's rest pose.
2. **Pivot off the axis line.** *"The hasp rotates about its center rather than its top edge,
   causing it to clip into the lid."* Put the pivot at the part's centre and `centroid - pivot`
   goes to zero: the centroid barely translates, the measured direction is noise, and the part
   sweeps *through* its neighbour instead of swinging clear of it. The pivot must be a point ON
   the hinge line.

`_DIRS` in `joints_sweep.py` maps the words onto the authoring frame `conventions.py` owns:
`up` = +Z, `down` = -Z, `front` = -Y, `back` = +Y, `right` = +X, `left` = -X. Signed-axis keys
(`+y`, `-x`, ...) name the same vectors.

## How the plan's sentence becomes the expected direction

`tracks/articulated_object.py::expected_direction` parses the plan's `motion` text with a fixed
precedence, and the gate is unforgiving about it:

* an explicit signed axis token anywhere in the text (`+y`, `-x`, ...) **wins immediately**;
* otherwise the first matching phrase wins ("pulls out" and "outward" mean `front`, "inward" and
  "slides in" mean `back`);
* text containing `around`, `rotates about`, `spins`, a negation, `either` or `both` is skipped —
  no finding either way;
* two different axes in one sentence with no dominant one is skipped.

Make positive q go the way the plan's FIRST direction word says, even when the prose reads
loosely ("from left to right" yields `left`). A `motion` like "rotates clockwise (+Y axis)" asks
for a translation along +Y that no revolute joint about +Y can give: confirm with the
`joint_sweep` pose renders that the part moves as the prose describes, then leave the axis alone.
**Do not flip a correct axis to chase an unsatisfiable finding.**

## Scope

URDF here owns physical structure only: links, joints, origins, axes, limits, visual and
collision geometry. Planning groups, end effectors, group states and disabled collision pairs
are SRDF concepts; this harness has no SRDF, so do not invent them. The URDF contract also
forbids `gazebo`, `transmission`, `sensor`, xacro, `package://` and inline primitives. Two of
these the linter measures directly: a `mesh scale` attribute is a WARN ("model in meters in
model.py instead"), and a `mimic` joint is checked, not dismissed: the sweep drives the
joint a `<mimic>` names and every follower follows it, so a one-input mechanism (umbrella
ribs, scissor arms, coupled folding legs) is posed the way it really moves. Declare one
`<mimic joint="<driver>" multiplier="<ratio>" offset="0"/>` per follower; a coupling that
names no joint, names itself, or closes a loop is a lint ERROR.

## Finish

A finish that catches everything: `build` (the FK check and the rest-pose check), `joint_sweep`
with a look at every joint's lower/mid/upper renders, `check_connectivity`, `check_contract`.

Worked repairs, the finding catalogue with real messages and the measured thresholds:
`references/sweep-and-motion.md`.

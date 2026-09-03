---
name: cv3d-urdf-joints
description: "Use when authoring or repairing an articulated_object's src/robot.urdf - links, joints, axes, limits, clearances - or when joint_sweep fired. Also covers the rest pose in src/model.py, and applies on every repair round that reported joint_sweep or motion_direction. Explains which pose set the sweep gate tests, why a moved-pose overlap is always an ERROR, and the arithmetic that predicts which way positive q pushes a child before you write the axis."
license: Apache-2.0
compatibility: track articulated_object, language urdf_blender. Constants read from codeverse/languages/urdf/runtime.py, codeverse/spatial/joints_sweep.py, joints_poses.py and codeverse/tracks/motion.py.
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

The frame recipe, the pivot rule and the visual/collision `origin = -pivot` line are in
`codeverse/prompts/urdf/contract.md` and its cookbook — follow them literally. This skill is the
part the contract does not tell you: **what the gates measure, and how to satisfy them before the
first build.**

## Two things fail the BUILD, not just a gate

* **FK check**, tolerance **1 mm** (`FK_TOL_M = 0.001` m). If FK at q=0 does not put every mesh back where
  `model.py` authored it, the build stops with `FkInconsistent` and prints the corrected
  `origin`. Cause is almost always a link's visual/collision `origin` left at `0 0 0` instead of
  `-pivot`, or a joint with `rpy` other than `0 0 0`.
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

**Measured** (26 urdf runs, 691 sweep findings, 2026-08-25): only **34 findings (4.9 %) were at
rest**, and every one of those was a WARN. **653 were in a moved pose** — 358 at a single joint's
limit, **295 in a random multi-joint combination**. Of 48 distinct link pairs that collided,
**29 never touch at rest** and **7 appear only in a combination**, so posing your joints one at a
time in your head is not enough: two arms that each clear the body can still meet each other.
Median depth 4.0 mm, max 20.0 mm; 510 of 687 were 5 mm or less.

Design rule: **1-3 mm of clearance all round, held over the whole envelope**, and think about the
worst *combination*, not the worst single joint. Fixed hardware is different and cheap — a
handle welded to a door may sink up to 2 mm with no finding at all, because pairs joined only by
`fixed` joints are measured once with the rest policy and never re-judged as they move.

**Attachment** is checked at rest only: a `fixed` child must be within `CONTACT_GAP_M = 0.002` of
its parent (ERROR otherwise, always); a revolute / continuous / prismatic child must be within
**10 mm** of its parent (WARN, ERROR beyond 30 mm) — `link 'moving_jaw' slides in
'fixed_jaw_body' (prismatic joint) but the meshes are 42.5 mm apart at rest`. A prismatic child
already inside the parent's bounding box (a drawer in its cavity) is exempt.

**159 of 687 findings carried `[approx: non-watertight mesh]`.** Depth is then an estimate. Make
each link one closed solid so the number you are given is the number you can trust.

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
# pedal-bin lid: pivot on the back top edge, lid centroid just above the rim
print(child_moves((-1, 0, 0), (0, 0.125, 0.351), (0, 0, 0.356)))
# [0.  0.03996804  0.99920096]  -> almost pure +Z: the lid lifts UP
```

Verified against `motion_direction_check` on 2026-08-25: it reported `up` for axis `-1 0 0` and
`down` for `+1 0 0` on that geometry, matching the cross product's sign both times.

Two independent ways to get this wrong, and the corpus has both:

1. **Axis sign.** Fix it by negating `axis`. The contract is explicit — *never* compensate by
   swapping `lower` and `upper`; q=0 must stay the plan's rest pose. (The gate's own fix hint
   offers both; take only the first half.)
2. **Pivot off the axis line.** *"The hasp rotates about its center rather than its top edge,
   causing it to clip into the lid."* Put the pivot at the part's centre and `centroid - pivot`
   goes to zero: the centroid barely translates, the measured direction is noise, and the part
   sweeps *through* its neighbour instead of swinging clear of it. The pivot must be a point ON
   the hinge line.

`_DIRS` in `joints_sweep.py` maps the words onto the authoring frame `conventions.py` owns:
`up` = +Z, `down` = -Z, `front` = -Y, `back` = +Y, `right` = +X, `left` = -X. Signed-axis keys
(`+y`, `-x`, ...) name the same vectors.

## How the plan's sentence becomes the expected direction

`tracks/motion.py::expected_direction` parses the plan's `motion` text with a fixed precedence,
and the gate is unforgiving about it:

* an explicit signed axis token anywhere in the text (`+y`, `-x`, ...) **wins immediately**;
* otherwise the first matching phrase wins ("pulls out" and "outward" mean `front`, "inward" and
  "slides in" mean `back`);
* text containing `around`, `rotates about`, `spins`, a negation, `either` or `both` is skipped —
  no finding either way;
* two different axes in one sentence with no dominant one is skipped.

So make **positive q move the child the way the plan's first direction word says**, even when the
prose is loose. Verified corpus consequences worth knowing before you argue with a finding:

* `"tray slides sideways from left side to right side"` yields `left` (first word wins), and the
  build that slid it rightward was flagged. The judge agreed the motion "fulfils the brief's
  intent" and scored it `minor` — but it is still an ERROR-severity gate finding.
* `"rear-right leg hinges..."` yields `right`, from the part name.
* `"rotates clockwise (+Y axis)"` yields `+y` as a **translation** direction. A revolute joint
  about +Y can never move its child along +Y, so no axis satisfies it. Confirm with the
  `joint_sweep` pose renders that the part moves the way the prose describes, then leave it.
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

`build` (runs the FK check and the sweep) then `joint_sweep` and look at the lower/mid/upper
renders for every joint, then `check_connectivity`, then `check_contract`.

Worked repairs, the full finding catalogue with real messages, and clearance numbers per
mechanism: `references/sweep-and-motion.md`.

# joint_sweep and motion_direction — depth

Read out of the live harness on 2026-08-25: `codeverse3d/spatial/joints_sweep.py`,
`joints_poses.py`, `joints_model.py`, `codeverse3d/languages/urdf/runtime.py`,
`codeverse3d/tracks/motion.py`, `codeverse3d/tracks/articulated_object.py`,
`codeverse3d/conventions.py`; plus every `joint_sweep` and `motion_direction` report under
`bench/out` (26 `urdf_blender` runs, 50 judged rounds).

## 1. Constants, with the module each comes from

| constant | value | module | what it decides |
|---|---|---|---|
| `FK_TOL_M` | 0.001 m | `languages/urdf/runtime.py` | build fails `FkInconsistent` above this |
| `REST_PENETRATION_MAX_M` | 0.005 m | `languages/urdf/runtime.py` | build fails `RestPenetration` above this |
| `tol_m` | 0.002 m | `joints_sweep.sweep_collisions` | overlaps shallower than this are not reported |
| `rest_max_m` | 0.005 m | `joints_sweep.sweep_findings` | rest / rigid overlap: WARN below, ERROR above |
| `contact_gap_m` | 0.002 m (`CONTACT_GAP_M`) | `conventions.py` | a `fixed` child must be this close to its parent |
| `hinge_clearance_m` | 0.010 m | `joints_sweep` | a moving child must be this close to its parent |
| ERROR on attachment | gap over 0.030 m | `joints_sweep.sweep_findings` | `hinge_clearance_m * 3` |
| depth rounding | 0.1 mm | `joints_sweep` | float32 mesh coordinates carry no finer meaning |
| random poses | 8, seed 0 | `languages/urdf/runtime.py` | only when more than one joint moves |
| motion probe | limit of larger magnitude, capped at 0.35 rad | `joints_sweep.motion_direction_check` | prismatic uses the full limit |
| motion pass test | `cos(observed, expected) > 0.5` | same | a 60 degree cone |

## 2. The pose set, exactly

`joints_poses.pose_samples(robot, n_random=8, seed=0)` returns:

1. `{}` — the rest pose, labelled `rest`.
2. For each movable joint, each distinct non-zero value of `lower`, `(lower+upper)/2`, `upper`,
   **with every other joint at 0**. Labels: `hinge@lower`, `hinge@mid`, `hinge@upper`.
   `continuous` joints use `-pi/2`, `+pi/2`, `pi` instead.
3. Eight seeded uniform draws over every joint at once (only if more than one joint is movable).
   Labels list every active joint: `door_hinge=1,handle_turn=0.188,latch_retract=0.000615`.

Two structural facts follow:

* **Attachment (`floating`) is only evaluated in the `rest` pose.** A hinge that flies apart at
  the upper limit is not reported as unattached; it is reported as an overlap somewhere else, or
  not at all. Look at the pose renders.
* **Links joined only by `fixed` joints form a rigid group.** Their mutual overlap is measured
  once (at `rest` if there is one) and judged with the rest policy in every pose. This is why a
  welded handle costs nothing while a hinged one is expensive.

## 3. Real messages, and what each one means

```
links 'table_top' and 'left_leg_frame' overlap by 20.0 mm at pose left_leg_joint@upper   [error]
links 'turret' and 'lower_arm' overlap by 2.9 mm at pose shoulder_joint@lower            [error]
links 'window_frame' and 'window_sash' overlap by 11.9 mm at pose crank_rotation=-1.45,
  latch_pivot=1.43,operator_base_pivot=0.0574,... [approx: non-watertight mesh]           [error]
links 'door_leaf' and 'lever_handle' overlap by 3.0 mm at pose rest                        [warn]
link 'moving_jaw' slides in 'fixed_jaw_body' (prismatic joint) but the meshes are
  42.5 mm apart at rest - nothing physically connects them                                [error]
link 'seat' hinges on 'main_frame' (revolute joint) but the meshes are 11.0 mm apart
  at rest - nothing physically connects them                                              [warn]
```

The fix hints differ by pose and are worth reading rather than guessing:

* at rest — "shrink/move one of 'a', 'b' so they touch (2 mm) instead of overlapping";
* in a moved pose — "Moving joint(s) [...] drives 'a' into 'b'. Either move the pivot/axis in
  robot.urdf so the part swings/slides clear, shrink the limits, or carve the clearance in
  model.py." Three genuinely different repairs; pick the one the mechanism justifies. Shrinking
  a limit is legitimate when the plan's range was optimistic (a drawer at 0.8 x depth), and wrong
  when the range is the point of the object.

## 4. Corpus (bench/out, mined 2026-08-25)

Denominator: 26 `urdf_blender` run records; 50 judged rounds.

| observation | value |
|---|---|
| runs where `joint_sweep` fired | 10 of 26 (38 %) |
| of those, runs with an ERROR | 9 |
| runs where `motion_direction` fired | 9 of 26 (35 %) |
| of those, runs with an ERROR | 8 |
| total sweep WARN/ERROR findings | 691 (687 penetration, 4 unattached) |
| findings at the `rest` pose | 34 (4.9 %), **all WARN** |
| findings at a single-joint limit or mid pose | 358 |
| findings in a random multi-joint combination | 295 |
| distinct (run, link-pair) collisions | 48 |
| pairs that never collide at rest | 29 of 48 |
| pairs seen **only** in a random combination | 7 of 48 |
| median overlap depth | 4.0 mm |
| max overlap depth | 20.0 mm |
| findings with depth 5 mm or less | 510 of 687 |
| findings marked `[approx: non-watertight mesh]` | 159 of 687 |

Judge `articulation` issues in `urdf_blender`: **34** — 11 critical, 19 major, 4 minor.
`articulation_plausibility` (0.732) and `joint_placement_and_range` (0.736) are the 2nd and 3rd
lowest of the eight urdf criteria over 50 rounds; only `geometry_detail` (0.699) is lower.

Corpus-verbatim, grouped by root cause:

**Direction (about 13 of 34).**
"Leg frames fold outward and upward into the air instead of inward under the table." (critical)
"The door swings in the wrong direction (inward instead of outward), causing massive clipping
with the frame." (critical)
"The inner tray slides forward out of the chest body instead of sideways." (critical)
"The joint axis is flipped; the lower limit points the arm up instead of folding it down." (major)
"The steering joint rotates the fork assembly around the Z-axis (roll) instead of the Y-axis
(yaw), causing the wheel to tilt sideways." (critical)

**Pivot placement.**
"The hasp rotates about its center rather than its top edge, causing it to clip into the lid."
"Because the front leg frame is detached, it pivots around a point in empty space rather than a
physical crossbar."

**Sweep clearance.**
"The right rear spreader collides with the front frame during the folding sweep."
"The moving jaw heavily interpenetrates the fixed jaw body at its upper travel limit."
"Seat overlaps with the main frame by 6.0 mm when fully raised."

## 5. The motion phrase table, verbatim from the code

`_DIRS` in `joints_sweep.py` (URDF frame: Z up, -Y front, +X right):

```
+x / right                (1, 0, 0)      -x / left                (-1, 0, 0)
+y / back / in            (0, 1, 0)      -y / front / out / open_front  (0, -1, 0)
+z / up / open_up         (0, 0, 1)      -z / down / open_down    (0, 0, -1)
```

`tracks/motion.py::expected_direction` maps the plan's prose onto those keys. Precedence:

1. Any of `-y +y -z +z -x +x` found as a whole token returns immediately.
2. Otherwise the longest matching phrase, in the module's fixed order. Phrases that mean
   `front`: "pulls out", "slides out", "swings out", "outward", "forward", "towards the viewer",
   "opens out", "opens to the front". Phrases that mean `back`: "pushes in", "slides in",
   "inward", "backward", "rearward", "towards the back". Then `up` / `down` / `left` / `right`.
   Bare "out" is `front`; bare "in" is not in the phrase list, only "slides in" / "pushes in".
3. Skipped entirely (`None`, no finding) if the text contains `not `, `n't`, `either`, `both`,
   `around`, `rotates about` or `spins`, or if it names two different axes with no dominant one.

Checked against real plan strings on 2026-08-25:

| plan `motion` text | expected |
|---|---|
| "lower arm pitches up and down from 20 to 80 degrees elevation" | `up` |
| "drawer pulls out 0.4 m" | `front` |
| "door leaf swings 90 degrees outward toward the front (-Y)" | `-y` |
| "tray slides sideways from left side to right side along internal ledges" | `left` |
| "rear-right leg hinges from vertical (-25 deg) to deployed splay (0 deg)" | `right` |
| "Left leg frame rotates clockwise (+Y axis) folding inward toward table center" | `+y` |
| "wheel spins around its axle" | skipped |
| "lid rotates about the back edge" | skipped |

The last three rows are the ones to recognise on a repair round. `"rotates clockwise (+Y axis)"`
asks for a *translation* along +Y from a joint that rotates about +Y — unsatisfiable. The
`"sideways from left to right"` and `"rear-right"` rows are satisfiable but only by making +q go
the way the first word says, which may read backwards in prose; the judge scored one of these
`minor` and noted "the visual sideways motion fulfills the brief's intent".

## 6. Clearance numbers that pass the sweep

| pair | rest gap | why |
|---|---|---|
| moving part vs its housing (drawer in cabinet, door in frame) | 1-3 mm all round | must stay positive over the whole range, including combinations |
| a closed door / lid against the carcass face | 0-1 mm, flush | must not sink in; 2-5 mm is a rest WARN, over 5 mm fails the build |
| `fixed` hardware (handle, knob, foot) on its parent | touching, or sunk up to 2 mm | rigid pairs are judged once with the rest policy, so this never becomes a moved-pose ERROR |
| hinge barrel / drawer runner to its partner | touching (2 mm) | otherwise the attachment check calls the child unattached |
| revolute or prismatic child to its parent at rest | 10 mm or less | over 10 mm is a WARN, over 30 mm an ERROR |

Sanity procedure that costs nothing: for every moving link, take its bounding box at `lower`,
`mid` and `upper`, plus the corners of the combinations you expect, and check the swept envelope
against each neighbour. If two joints can move a pair towards each other, test them together —
7 of 48 collisions in the corpus were visible in no other pose.

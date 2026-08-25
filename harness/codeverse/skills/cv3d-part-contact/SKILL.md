---
name: cv3d-part-contact
description: "Use when an object has two or more parts, or when a connectivity or joint_sweep finding fired: how parts must touch without penetrating. For static_object and articulated_object in any language, before the first build and on every repair round. Gives the contact, weld-overlap, penetration and stray-island numbers the connectivity gate actually measures, and how to add detail without creating new contact pairs."
license: Apache-2.0
compatibility: Object tracks only (static_object, articulated_object). Needs the connectivity gate, i.e. an exported object.glb; scene and graphics tracks have no part graph.
metadata:
  evidence: measured
  verified: "2026-08-25"
  corpus: "142 graded runs with gate artefacts (102 blender, 23 urdf_blender), mined 2026-08-25"
  target_metric: "interpenetrating_pairs"
  target_direction: "down"
  target_unit: "findings per run"
  target_measurable: "true"
  target_baseline: "3.35 mean / 2.0 median findings per run; n=164 (bench/out, last gated round, 2026-08-25); 5.07 on the first gated round"
---

# Parts that touch

**Read this if** your plan has two or more parts and you are about to write, move or resize
any of them. It is the one defect class that costs the most score here and the one that
repair rounds are worst at removing.

## Why this is the first thing to get right

Measured on the 102 graded blender runs that have a connectivity report (mined 2026-08-25):

| | first gated round | last gated round |
|---|---|---|
| runs with interpenetrating parts | 88 (86%) | **67 (66%)** |
| runs with a floating part | 10 (10%) | 3 (3%) |
| runs with tiny stray islands | 56 (55%) | 52 (51%) |

Floating parts get fixed, because they are ERRORs. Interpenetration and crumbs mostly do
not: three quarters of the runs that started buried shipped buried. And it is expensive —
`docs/COMPLEXITY.md` section 6, over 160 recorded rounds with both a score and a
connectivity report, mean `assembly_fit` falls 0.709 (nothing buried) to 0.505 (a part more
than half buried), and the plain **count of penetrating pairs** is the strongest single
predictor of that criterion (r = -0.258). `assembly_fit` is already the lowest-scoring
criterion in blender (mean 0.60 over 246 judged rounds, 102 runs).

So: fewer pairs in contact, and each contact within the band below.

## The band — every number is a live constant

`codeverse/spatial/connectivity.py` + `codeverse/conventions.py`:

| what | constant | value | consequence |
|---|---|---|---|
| two parts count as touching | `CONTACT_GAP_M` | surface distance ≤ **2 mm** | further apart = no edge in the contact graph |
| overlap that is still a weld | `PENETRATION_WARN_M` | depth ≤ **2 mm** | above it: WARN "interpenetrate by ~N mm" |
| overlap that is a defect | `PENETRATION_ERROR_M` | depth > **10 mm** | ERROR, and the judge rubric caps the run at **0.70** |
| how much has to be inside | `PENETRATION_MIN_FRACTION` | **2%** of surface samples | below it nothing is reported at any depth |
| samples per part | `N_SAMPLES` | 600 | the depth you see is the deepest sampled point |
| a part too small to check | `MIN_PART_SIZE_M` | largest extent < **10 mm** | skipped for contact, penetration and islands (INFO only) |
| a crumb inside one part | `TINY_ISLAND_FRACTION` | island extent < **5%** of the part's largest extent | WARN "stray geometry" |

**The whole legal window for a joint is 2 mm of gap to 2 mm of overlap.** Aim for
**0.5-1.5 mm of overlap** and you are inside it with margin on both sides. Two traps follow
directly:

* Do not "fix" an interpenetration WARN by pulling the parts apart. Past 2 mm of gap the
  contact edge disappears and the part can drop out of the support component — a WARN
  becomes an ERROR that caps the run at 0.60.
* Do not use a fat weld. Measured by running the gate's own `penetration_depth` on the
  stool geometry the blender contract's example builds — a rod driven into a seat disc:

  | weld overlap | reported depth | fraction inside | gate |
  |---|---|---|---|
  | 4.0 mm | 4.00 mm | 2.8% | **WARN** |
  | 2.0 mm | 2.00 mm | 2.7% | **WARN** (right on the line) |
  | 1.0 mm | 1.00 mm | 2.5% | clean |
  | 0.5 mm | 0.50 mm | 2.3% | clean |

## Floating parts: never guess the fix

A part outside the *support component* (the ground-touching component with the most faces)
is always an ERROR, and the finding already contains the answer:

```
ERROR part 'door_leaf' is floating: nearest supported part is 'frame' at 4.0 mm
      fix: translate 'door_leaf' by (0.0000, -0.0040, 0.0000) m -- or extend it by
           4.0 mm towards 'frame' -- so the surfaces touch
```

The vector is converted into **your** authoring frame before it is printed, and the
alternative ("extend it by N mm") is usually the better one: translating a leg leaves a gap
at its other end. Apply one of the two literally. Do not eyeball a new position.

## Plan the contacts before you write code

For every part with `attach_to` set, write one line first:

    <part>  meets  <neighbour>  on <which face/axis>, at <coordinate>, overlap 1 mm

Then derive the part's span **from the neighbour's plan bbox**, never from a re-typed
number: a leg's top is `seat.bbox.min.z + 0.001`, not `0.451`. Any hand-typed span is a
future contract finding as soon as the neighbour moves. (`cv3d-bbox-contract` owns the
tolerance arithmetic; this skill only says where the number comes from.)

## Detail belongs INSIDE a part, not as more parts

N top-level parts create N(N-1)/2 candidate contact pairs, and the count of penetrating
pairs is what predicts the score. Sub-shapes inside one part create **none**.

`PartPlan.children` is exactly this device — the docstring says sub-parts of an assembly
part are "still ONE named object in the export" — and `spatial/measure.py::part_meshes`
backs it up: a part is an effective top-level node and *its whole subtree is merged* into
one mesh before any pairwise test runs. Verified on Blender 5.0.1: three disjoint sub-shapes built
into one mesh named `Housing` export as a single part.

**But fusing is not optional.** Islands are split by *face connectivity*, not by overlap —
verified against the gate: a rivet placed so it geometrically overlaps a panel, without shared
topology, is still a separate island. So a sub-shape must be either

* **welded into the body** — one boolean UNION, or an array / mirror / screw result (those
  are one object by construction), or built into the same `bmesh`; or
* **at least 5% of the part's largest extent**, which is what `TINY_ISLAND_FRACTION` asks.
  On a 1.0 m panel that is 50 mm: a 15 mm rivet dropped in loose is a stray-island WARN.

## Where crumbs actually come from

* **A cutter object left in the scene.** Verified on Blender 5.0.1: the exporter selects every
  visible mesh, so an un-hidden boolean cutter exports as its own part — with its own
  interpenetration and its own floating ERROR. Apply the boolean and delete the cutter, or
  set `hide_render` / `hide_viewport` / `hide_set(True)` on it; all three keep it out of the
  export and the boolean still evaluates.
* Boolean leftovers and slivers — use the EXACT or MANIFOLD solver, never FLOAT/FAST.
* A mirrored half that was never merged, and zero-thickness or coincident faces.

## Verify with the gate, do not re-implement it

Call `check_connectivity` (no arguments — it reads your workspace and answers in your
authoring frame). It runs an fcl exact-distance contact graph, a per-island parity
containment test and the island split. A hand-written centroid or bounding-box check in
your own build script is strictly weaker and will disagree with the gate that scores you.
Then `check_contract`, then rebuild. Do not finish on a run whose last
`check_connectivity` still lists an ERROR.

Worked recipes (bpy, CadQuery, URDF) and the full finding-to-fix table are in
`references/contact-recipes.md`.

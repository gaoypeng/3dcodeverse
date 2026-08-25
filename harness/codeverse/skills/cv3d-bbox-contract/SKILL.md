---
name: cv3d-bbox-contract
description: "Use when a session owns part sizes, placement or ground contact, or when the contract gate fired: hit the plan's dimensions on the first build. Applies to baseline, part, detail, refine, rebuild and zone sessions on static_object, articulated_object and scene. Covers the tolerance the gate really uses (relative, not a flat millimetre figure), how instanced parts are measured, ground contact and footprint, part-name matching, and how to prove the numbers with measure / check_contract instead of by eye."
license: Apache-2.0
metadata:
  evidence: measured
  verified: "2026-08-25"
  corpus: bench/out, 142 graded records with gate artefacts, recomputed 2026-08-25
  target_metric: "contract_findings"
  target_direction: "down"
  target_unit: "findings per run"
  target_measurable: "true"
  target_baseline: "1.51 mean / 1.0 median findings per run; n=167 (bench/out, last gated round, 2026-08-25); 1.23 on the first gated round"
---

# The plan's numbers are the contract

Read this before you type a dimension. Every number in `plan.json` is a promise the
`contract` gate measures against the exported GLB, in the plan's own authoring frame —
which is why every fix hint it writes is already labelled with that frame and is safe to
paste straight into your code.

## What the gate actually allows

Per axis, `tol = max(1 cm, 0.10 x the planned extent on that axis)`
(`codeverse/spatial/contract.py`, `codeverse/conventions.py`). A deviation past `tol` is a
WARN; past **3x** `tol` it is an ERROR.

| planned extent | tolerance on that axis | in relative terms |
|---|---|---|
| 3 cm | 1 cm | 33 % |
| 10 cm | 1 cm | 10 % |
| 50 cm | 5 cm | 10 % |
| 2 m | 20 cm | 10 % |

The absolute floor rules small parts and the relative term rules large ones. So a 4 cm
bracket may wander a full centimetre, and a 2 m beam that comes out 25 cm short is an
ERROR, not a rounding difference.

## What actually goes wrong (recomputed from `bench/out` 2026-08-25, 142 graded records with gate artefacts)

* part bbox deviates — **75 runs (52.8 %)**; overall bbox — 44 (31.0 %); per-instance —
  29 (20.4 %); footprint off the up axis — 21 (14.8 %).
* Of 978 bbox findings, **673 name only the size**, 133 only the centre, 172 both. This is
  overwhelmingly a *size* defect, not a placement defect.
* Out-of-tolerance axes run **713 undersized to 394 oversized**. The usual story is a part
  built as its core shape while the plan's extent included the trim, flange or overhang.
* Median worst deviation is **1.47x tolerance**, p90 2.86x. Most failures are one axis
  missing by a hair — arithmetic you could have checked before building, not a redesign.

## Rules

1. **Type every plan number once.** Each dimension becomes one named constant at the top of
   the file; everything else is derived from those constants. A number typed twice is a
   number that will disagree with itself.
2. **Derive a spanning dimension from its neighbours' bounds, never re-type it.** A shelf
   that spans two uprights is `inner_span = pitch - upright_thickness + 2 * weld`, computed
   from the constants the uprights already used. Re-typing "0.58" is how the 1.47x median
   miss is made.
3. **Budget for what the modifiers do to the bounding box.** The gate measures the built
   AABB, after everything. A bevel, chamfer or fillet *shrinks* the silhouette; a solidify,
   skin or subdivision surface pulls a cage *inward*; an outline offset grows it. If the
   plan says 88 mm and your bevel eats 9 mm, you shipped 79 mm and the gate says so.
4. **Instanced parts are measured on extents only.** For a part with `instances > 1` the
   gate reads the plan box two ways — as the size of ONE copy, and as the union of all
   copies — and keeps whichever fits better, so building either reading is accepted. It
   does **not** check the centre of an instanced part (it cannot know which copy is which).
   A wrong *count* is a separate WARN: name the copies `Name_0 .. Name_N-1`.
5. **Sit on the ground and centre the footprint.** Lowest point at zero: a gap beyond 1 cm
   is a WARN and beyond 3 cm an ERROR, with the exact translation in the hint. The
   footprint centre must be within `max(1 cm, 10 % of the larger horizontal extent)` of the
   up axis — that one is always a WARN and always fixable by one translate.
6. **Names are the join key.** Parts match by their snake-cased name; the exact name is
   claimed first and `Name_0..Name_N` afterwards. A plan part with no matching node is an
   ERROR ("missing from the GLB"); an extra node the plan never mentioned is only INFO. So
   a spare helper node is cheap and a misspelt part name is expensive.

## Prove it, do not eyeball it

`build` -> `measure` -> `check_contract`. `measure` gives you per-part size in cm and the
ground gap; `check_contract` gives you, per failing part, the built size against the planned
size, the per-axis delta, the planned centre and the frame those numbers are written in.
Fix the largest ratio first — the message states it ("worst 3.4x tolerance"), so you always
know which finding is the ERROR.

`isolate <PartName>` renders one part alone with its measurement row when a delta does not
make sense.

Worked example of turning a real fix hint into an edit: `references/reading-a-fix-hint.md`.

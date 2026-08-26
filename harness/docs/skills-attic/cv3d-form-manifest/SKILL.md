<!--
RETIRED 2026-08-25 (curate wave).  Not loaded; kept for the record.

WHY IT WAS CUT — read rate, not prose quality.
  api-agent `read_skill` ground truth, both arms of the description A/B:
    baseline   1 open / 10 listings
    revised    1 open /  9 listings   (first sentence rewritten to lead with the trigger)
    pooled     2 opens / 19 listings = 11%, 95% CI [3%, 31%]
  That interval excludes every other bundle's.  The next-lowest, cv3d-repeats-and-mirrors,
  pooled 3/8 = 38%; the most-listed bundle, cv3d-bbox-contract, pooled 13/19 = 68%.
  It was listed in 19 of the 27 sessions that were actually offered the read tool — more
  than any bundle except bbox-contract — and cost ~1,917 body tokens plus an index line
  every one of those times.

  The standing rule is: a bundle that is unread AFTER a revision is cut.  It was revised
  once, deliberately, on the cut that api-agent actually sees (`prompting.index_summary`
  takes the first sentence; `api_skills.SPEC_SUMMARY_CHARS` takes the first 150 chars),
  and it did not move.

  Plausible mechanism, recorded so the next attempt does not repeat it: the bundle asks
  the agent to do planning work BEFORE the geometry it was just told to write.  That is
  the one instruction an eager coder skips, and no wording makes it cheaper to accept.

WHAT ITS TARGET METRIC SAID — a second, independent reason.
  target: feature_density (spatial/complexity, recomputed from object.glb), direction UP
  corpus baseline: 2561.7 median / 8564.4 mean, n=175 graded object runs (2026-08-25)
  A/A floor on identical arms: delta +3904.7 (2095.7 -> 6000.5, a 2.9x swing),
    paired sd 3057.6, 2 SE +/-3530.6 — a noise band 1.4x the corpus median it measures.
  the one skills-on datapoint moved it the WRONG WAY: -1001.8 (direction is up).
  n needed to resolve a 25% move: ~92 paired prompts.
  And the metric is only a LEADING indicator: r = +0.19 against the judged
  geometry_detail (docs/COMPLEXITY.md 2.2, n=44).  It was on ledger probation for that.

WHAT WOULD BRING IT BACK.
  Either a read channel that makes pre-geometry planning cheap for an agent to accept, or
  evidence that feature_density tracks geometry_detail well enough to be worth 1,917
  tokens a session.  Neither is a rewrite of the text below, which is why the text below
  is unchanged.

Its FORM vocabulary outlived it: cv3d-blender-forms and cv3d-threejs-forms still key
their technique tables on the same words, and those tables are self-contained.
-->

---
name: cv3d-form-manifest
description: "Use before calling any geometry API on a baseline, part, detail or rebuild session: name every shape, its dimensions and placement in FORM words. For the static_object and articulated_object tracks - whenever you are about to write geometry for a part you have not built yet. Turns the ENGINEERING BRIEF and the plan's part rows into a written element list the language skill's table then implements one line at a time, each with one countable refinement. Skip it on a repair round: a repair is not re-planning."
license: Apache-2.0
compatibility: "static_object and articulated_object tracks, all four object languages (blender, urdf_blender, cadquery, threejs). Needs the plan and, when the run enabled it, the ENGINEERING BRIEF block in your prompt."
metadata:
  evidence: measured
  verified: "2026-08-25"
  corpus: "bench/out/*/runs/* graded records, recomputed 2026-08-25"
  pairs_with: "cv3d-blender-forms, cv3d-cadquery-forms, cv3d-threejs-forms, cv3d-part-contact"
  target_metric: "feature_density"
  target_direction: "up"
  target_unit: "small sharp edges per unit surface (spatial/complexity)"
  target_measurable: "true"
  target_baseline: "2551 median / 7946 mean; n=164 (bench/out, last gated round, 2026-08-25); right-skewed, read the median"
---

# Form manifest — name the shapes before you name an API

## When to use

You are about to write geometry for a part you have not built yet. Spend three
minutes writing what each element **is** as a shape. Then implement it.
On a repair round, skip this and fix the finding.

## Why this pays (measured on this harness)

`geometry_detail` is the second-lowest judge criterion in both object tracks —
**0.622** over 47 graded blender runs and **0.699** over 23 graded urdf runs
(`bench/out/*/runs/*`, recomputed 2026-08-25). It does not improve by adding parts.
Over 44 static runs (`docs/COMPLEXITY.md` §2.2, 2026-08-24):

| what you add | correlation with `geometry_detail` | correlation with gate ERRORs |
|---|--:|--:|
| more plan parts | **−0.25** | −0.13 |
| more built top-level parts | +0.01 | **+0.55** |
| more triangles | −0.05 | +0.06 |
| more `feature_density` (small sharp edges per unit surface) | **+0.19** | +0.03 |

One axis out of four moves the criterion, and it is the one that measures modelled
features, not part count. Adding parts instead is actively expensive: when one
recorded toaster was rebuilt with more top-level parts its interpenetrating pairs
went 4 → 8, max depth 3.5 mm → 9.0 mm, and overall 0.953 → 0.658
(`docs/COMPLEXITY.md` §6).

So: **fidelity, not count.** A drawer is five panels because a real one has five
panels. Invented rivets bolted on as new parts are how floaters and
interpenetration get made.

## You are not starting from nothing

Read these three before writing a word of the manifest — they are already in your
prompt or your workspace:

| source | what it hands you |
|---|---|
| the ENGINEERING BRIEF block | the real reference instance, real dimensions in metres, the sub-assemblies a fitter would name, `visible from outside`, `inside, NOT visible (do not model)`, `it does NOT have`, and 3–6 SIGNATURE FEATURES |
| your part's row in the plan | `name` (PascalCase), `role`, `description`, `bbox`, `material`, `attach_to`, `symmetry`, `instances`, `children`, `detail_hint` |
| the DETAIL BUDGET block | the triangle target for this object and per built object |

Every SIGNATURE FEATURE must end up as an element in some part's manifest.
Nothing on the `hidden inside` or `does NOT have` lists may appear at all.

## Write it to `src/design/manifest_<snake>.md`

`src/` and `public/` are the only directories you may create files in, and the
language gates only read `.py` / `.js` / `.urdf`, so a Markdown note under
`src/design/` is inert. One element per line, in this order:

```
Element — FORM — size (m) — placement — refinement — source
```

* **size** taken from the plan's bbox or the brief's dimensions, in the units
  the contract states; never a round number you liked the look of.
* **placement** relative to your part's own bbox or to a named neighbour part
  ("top face of `Housing`", "20 mm in from the +X wall"), never an absolute
  guess.
* **source** — which signature feature, `detail_hint` phrase or `children` row
  this element discharges. An element with no source is an element to cut.

Stop when every signature feature and every `children` row is discharged. Six to
twelve lines is a normal part. If your list is longer than the part's `children`
list plus its `detail_hint`, you are padding.

## The FORM words

Language-neutral on purpose: the same word keys the table in
`cv3d-blender-forms`, `cv3d-cadquery-forms` and `cv3d-threejs-forms`, so the
manifest survives a language change and you look up exactly one row.

| form | it means |
|---|---|
| revolved profile | a 2-D outline spun about an axis — bottles, knobs, feet, turned legs |
| extruded outline | a closed 2-D outline given thickness — plates, brackets, panels |
| tube swept along a path | a circular section carried along a curve — cables, handles, rails |
| slab | a rectangular block whose thickness is much smaller than its face |
| shaft | a long constant section — a rod, a bar, a post |
| tapered solid | a section that changes size along its length — a draft, a cone, a wedge |
| ring or band | an annulus or a torus — rims, ferrules, gaskets, trim |
| carved cavity | a recess, pocket, slot, hole or notch taken out of a mass |
| shell with wall thickness | a hollow body with a visible wall — a cup, a housing, a bin |
| mirror pair | one element and its reflection across a plane |
| radial array | N copies about an axis, angle derived from N |
| layered or inset | a face broken into a proud or sunk sub-panel |
| faceted solid | a flat-sided body no primitive covers — a wedge, a prism, a hull |
| lofted transition | one section blending into a different section — a horn, a fairing |

Write the form word, not the API call. Picking the API is the language skill's
job and it changes per language; picking the shape is yours and it does not.

## Every element carries at least one refinement kind

`geometry_detail` is scored by counting **refinement kinds** visible in the
renders — this is the rubric's own countable unit
(`codeverse/judges/rubrics/static_object_v1.yaml`), not a heuristic:

1. bevel / chamfer / fillet on an edge
2. taper or draft along a part
3. curve, sweep or compound surface where a primitive would be flat
4. moulding or profile — a shaped edge, a cornice, a bead
5. cut-out, hole, slot or notch
6. visible wall thickness or hollow section — a rim, a lip, an open tube end
7. surface relief — flutes, ribs, grooves, panelling, treads, weave, bark
8. separately modelled hardware — hinge, knob, screw, bracket, strap, spoke

Colour, material and shading are **not** refinement kinds and never raise this
criterion. The anchors the judge actually uses:

* 3–4 kinds on at least half the nameable parts → **0.85**
* 5+ kinds with at least two thirds of parts carrying one, finest features at
  real hardware scale (a few mm) → **1.0**
* many parts but no refinement on any of them → **0.25–0.4**

Two or three kinds spread across the object is the difference between 0.55 and
0.85. Pick them at manifest time so they are budgeted, not bolted on later.

## Where a new element goes

An element is a **new top-level part** only if a human would unbolt it as a
separate object AND the plan already lists it. Otherwise it goes *inside* the
part you are building.

The plan's `children` are a planning device, not export nodes: the parent stays
exactly **one** named object in the export, the contract gate measures the
parent's bbox, and the connectivity gate loads one mesh per top-level node — so
shapes inside a part are never pair-tested against each other, while still
counting for `geometry_detail`. That is the cheapest detail on this harness.

One caveat, so this does not become a licence to scatter: a sub-shape that
floats free inside its parent is still caught, as a tiny-island warning on that
part. Every element in your manifest has to touch something. Contact between
parts, and its numbers, belong to `cv3d-part-contact`.

## Before you write code

1. Every signature feature appears in some manifest line.
2. Nothing from `inside, NOT visible` or `does NOT have` appears in any line.
3. At least three distinct refinement kinds across the object, and no element in
   your part is an unmodified primitive.
4. Every line carries a real dimension and a placement anchored to a bbox or a
   named neighbour.

Then implement the manifest top to bottom, one line at a time, looking up each
FORM word in your language's table.

## Depth

`references/worked_manifest.md` — a full worked manifest for one part of a
hand-crank coffee grinder, from brief fields to element lines, plus the two
failure shapes (the padded manifest and the manifest with no refinements).

# Complexity — measuring how much artifact the harness can actually build

> **Paths in this document.** `bench/…` is relative to `eval/` (this folder's parent); `codeverse3d/…`, `tests/…`,
> `runtime_js/…` and the other `docs/…` files are relative to `harness/`.  Recorded battery output (`bench/out/…`)
> is run data and is not in git.

Every other number in this repo answers *how good is it?*  This one answers
*how much of it is there?* — objectively, from the built artifact, with no VLM,
no render and no model call.  The pair is the point: a 0.9 on a five-box stool
and a 0.6 on a twenty-five-part beam engine are not the same result, and until
the second number existed the harness could not tell them apart.

Reconciled against the code and the recorded corpus on **2026-08-24**.

* the vector: `codeverse3d/spatial/complexity.py`
* where it is stored: `Measurement.extra["complexity"]` (every round's own: `record.record.round_complexity`),
  `record.extra["complexity"]`, the gallery card + detail page
* the study: `bench/complexity_report.py`
* the ladder battery: `bench/prompts/complexity_v3.yaml`

---

## 1. The complexity vector

Eight measured axes plus one 0–1 `index`.  All of it comes out of the canonical
GLB, in ~50–200 ms, deterministically (the silhouette sampler is seeded — law 10).

| axis | what it measures | how |
|---|---|---|
| `part_count` | how many nameable parts have geometry | top-level nodes of the canonical GLB |
| `assembly_depth` | sub-assemblies of sub-assemblies | deepest node chain under the effective top level (1 = flat) |
| `tri_count` | raw geometry budget spent | triangles in the whole artifact |
| `materials` | distinct materials bound to geometry | unique material objects in the scene |
| `silhouette` | outline complexity | mean isoperimetric quotient `P²/4πA` of the mask in the three axis projections |
| `feature_density` | small-scale modelled detail | small **and** sharp edges per unit surface: `n · diag² / area`, edge sharp above 20°, short below 2 % of the bbox diagonal |
| `symmetry_groups` | organised repetition | families of geometrically identical parts (same tri count, same sorted extents to 1 mm) |
| `hollowness` | tubes, shells, C-frames, bent bars | mean per-part `1 − volume / convex_hull_volume` over watertight parts |

Reference points on the silhouette scale (measured, not asserted): a solid box
**1.30**, a sphere **1.63**, a toaster **1.7**, a dining chair **14.3**, a spiral
stair **22.3**, a birdcage **52.3**.  On the feature-density scale: a plain shed
**110**, a toaster **474**, a dining chair **778**, a machinist's vise **989**,
a telescope on a tripod **10 700**, a wrought-iron garden arch **96 700**.

### Why silhouette is computed from geometry, not from a render

The brief said "from the silhouette render".  It is computed from the mesh
instead: surface points are sampled with faces weighted by their **projected**
area and rasterised into a 128² mask, which is then 3×3 closed.  Three reasons —
it needs no GPU and no render round, it is identical on every machine, and it
works for a round that failed to render.  The projected-area weighting is
load-bearing: sampling the raw surface spends a third of its points on faces
that project to a line, and the resulting pinholes read as perimeter — a solid
box measured **89** instead of 1.3 before that fix.

`P` is the raw 4-connected crack boundary, uncorrected: a correction that is
right for curves is wrong for axis-aligned rectangles, and this axis is
comparative.

### The index

```
index = Σ  weight[axis] · norm[axis](value)
```

| axis | weight | normaliser (value that scores 0 → value that saturates at 1) |
|---|--:|---|
| `feature_density` | 0.22 | log, 50 → 8 000 |
| `part_count` | 0.20 | log, 1 → 30 |
| `silhouette` | 0.15 | log, 1 → 40 |
| `tri_count` | 0.13 | log, 300 → 60 000 |
| `hollowness` | 0.09 | linear, 0 → 1 |
| `materials` | 0.08 | log, 1 → 8 |
| `assembly_depth` | 0.07 | linear, 1 → 4 |
| `symmetry_groups` | 0.06 | log, 1 → 9 |

The weighting is a deliberate opinion: **modelled detail and part count carry
44 % of it**, because those are what the harness is actually being asked to
raise; triangles carry only 13 % so that one subdivided blob cannot buy an
intricate rating; symmetry and depth are small tie-breakers that separate an
organised 20-part machine from 20 random lumps.  Bands: `trivial` < 0.25,
`simple` < 0.40, `moderate` < 0.55, `complex` < 0.70, `intricate` ≥ 0.70.
`COMPLEXITY_VERSION` is stamped on every vector; bump it when a weight moves.

**The index is difficulty, never quality.**  A rich broken model and a rich good
model score the same index.  Nothing in the scoring path reads it, and the judge
is never told it — otherwise it would immediately become a target.

---

## 2. The corpus baseline (67 object runs, 2026-08-24)

`python bench/complexity_report.py bench/out --recursive`.  67 runs carried both
a judgment and geometry: 44 `static_object` (static_v1/v2), 23
`articulated_object` (articulated_v1/v2).  23 runs were skipped: 16 graphics and 4 scene runs
(no `object.glb` — the vector does not apply to them) and 3 articulated runs
whose best round carries no usable verdict.

### 2.1 Score vs complexity

| complexity band | n | mean index | mean overall | pass % | detail | struct | fit | craft | $/run | min |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| trivial 0.00–0.30 | 5 | 0.23 | **0.89** | 80 | 0.78 | 0.94 | 0.95 | 0.95 | 0.63 | 23 |
| simple 0.30–0.40 | 7 | 0.35 | 0.79 | 71 | 0.79 | 0.91 | 0.83 | 0.78 | 1.18 | 42 |
| moderate 0.40–0.50 | 15 | 0.46 | 0.71 | 53 | 0.71 | 0.87 | 0.77 | 0.84 | 1.51 | 29 |
| complex 0.50–0.60 | 21 | 0.55 | 0.71 | 52 | 0.68 | 0.86 | 0.77 | 0.83 | 2.16 | 30 |
| complex 0.60–0.70 | 12 | 0.65 | **0.55** | **8** | 0.56 | 0.64 | 0.59 | 0.72 | 2.40 | 37 |
| intricate 0.70–1.00 | 7 | 0.77 | 0.65 | 43 | 0.72 | 0.79 | 0.69 | 0.81 | 1.63 | 28 |

The ceiling is visible: everything above index 0.60 loses ~0.2 of overall and
almost all of its pass rate.  The 0.70+ row recovers partly because it is
dominated by *thin repetitive* artifacts (garden arch, palm, penny-farthing)
that are geometrically rich but structurally simple — nothing there has to fit
together.

### 2.2 Where the score falls, and why (static, n = 44)

Pearson r of each judge target against each complexity axis:

| target | index | part_count | tri_count | n_materials | silhouette | feature_density | hollowness | plan_parts |
|---|--:|--:|--:|--:|--:|--:|--:|--:|
| overall | −0.18 | −0.08 | +0.03 | −0.19 | −0.02 | +0.19 | −0.25 | −0.15 |
| intent_fidelity | +0.03 | +0.02 | +0.06 | −0.10 | +0.16 | +0.22 | −0.10 | −0.12 |
| structure_plausibility | −0.18 | −0.04 | +0.01 | −0.27 | +0.03 | +0.13 | **−0.33** | −0.18 |
| **geometry_detail** | **+0.01** | **+0.01** | **−0.05** | −0.27 | +0.15 | **+0.19** | −0.13 | **−0.25** |
| proportions_scale | −0.19 | −0.06 | −0.02 | −0.13 | −0.00 | +0.18 | −0.07 | −0.05 |
| assembly_fit | −0.19 | −0.15 | −0.09 | −0.21 | +0.03 | +0.12 | −0.26 | −0.17 |
| craftsmanship | −0.09 | −0.03 | +0.01 | −0.15 | −0.00 | +0.11 | −0.28 | −0.26 |
| *weighted mean before caps* | −0.09 | −0.04 | +0.00 | −0.23 | +0.11 | +0.20 | −0.24 | −0.22 |
| *loss to caps + penalties* | +0.23 | +0.10 | −0.06 | +0.06 | +0.20 | −0.11 | +0.16 | −0.02 |
| **gate_errors** | **+0.39** | **+0.55** | +0.06 | −0.10 | +0.21 | +0.03 | +0.20 | −0.13 |
| cost_usd | +0.12 | −0.03 | −0.11 | +0.19 | −0.03 | −0.16 | +0.10 | **+0.42** |

And the articulated track (n = 23), where the same wall is a cliff:

| target | index | part_count | silhouette | feature_density | plan_parts |
|---|--:|--:|--:|--:|--:|
| overall | **−0.70** | −0.65 | −0.65 | −0.56 | −0.65 |
| structure_plausibility | −0.60 | −0.53 | −0.61 | −0.64 | −0.53 |
| geometry_detail | −0.43 | −0.50 | −0.56 | −0.43 | −0.50 |
| proportions_scale | −0.67 | −0.53 | −0.72 | −0.47 | −0.53 |
| assembly_fit | −0.41 | −0.56 | −0.38 | −0.42 | −0.56 |
| gate_errors | +0.23 | +0.53 | +0.21 | +0.30 | **+0.53** |
| cost_usd | +0.44 | +0.22 | +0.44 | +0.23 | +0.22 |

### 2.3 The four findings this wave is judged against

**F1 — Complexity is punished through the gates, not through the criteria.**
On static objects the weighted mean *before* caps barely moves with complexity
(r = −0.09); what moves is the cap-and-penalty loss (r = +0.23) and its cause,
gate errors (r = +0.39 with the index, **+0.55 with part count**).  The
bucket table says it in dollars: at index ≥ 0.65 the mean run carries **5.3 gate
errors** against 0.3–0.5 everywhere else, and 0.14 of overall is taken by caps.
The physical-plausibility gates are doing exactly what they were built to do —
more parts means more contacts means more chances to float or interpenetrate.
Raising the ceiling therefore means **making complex assemblies fit**, not
loosening the caps.

**F2 — `geometry_detail` is the weakest criterion AND the one blindest to
geometry.**  Static mean **0.667**, the lowest of the seven; 2 runs of 44 reached
0.9, exactly one reached 1.0 — and that one was `toy_easy_blocks`, a bin of
literal boxes.  Its correlation with objective detail is noise: r = **+0.01**
with the index, **+0.01** with part count, **−0.05** with triangle count,
**+0.19** with feature density.  The only strong relation it has is with
`plan_parts` — and it is **negative** (r = −0.25, ρ = −0.30): *asking the planner
for more parts lowered the detail score.*  The old anchors asked for a vibe
("multiple refinements … reads as a designed product"), gave no way to count, and
scored the object against the simplest version of its subject, so a clean
5-part stack collected 1.0 while a 33-part spiral stair collected 0.85.

**F3 — Hollow, thin and open structures are where plausibility dies.**
`hollowness` is the axis most negatively correlated with the *criteria*
themselves: structure −0.33, craftsmanship −0.28, assembly fit −0.26.  Thin
frames, tubes and open sections are what the builder gets wrong — they are also
exactly what "more complex" looks like geometrically.

**F4 — Complexity in the plan buys cost, not score.**  `plan_parts` is the best
predictor of spend (r = +0.42 static, minutes +0.43 articulated) and a *negative*
predictor of every quality criterion.  A prompt that demands 20 parts today buys
a bigger bill and a worse verdict.

### 2.4 What a complexity point costs

| band | n | $ / complexity point | min / point | mean $/run |
|---|--:|--:|--:|--:|
| index < 0.45 | 17 | 0.0339 | 0.92 | 1.15 |
| 0.45–0.55 | 20 | 0.0401 | 0.69 | 2.01 |
| 0.55–0.65 | 16 | 0.0333 | 0.44 | 1.96 |
| ≥ 0.65 | 14 | 0.0283 | 0.45 | 2.04 |
| **all 67** | 67 | **0.0333** | 0.58 | 1.79 |
| passed runs only | 32 | 0.0198 | — | — |

A complexity point (index × 100) costs about **3.3 ¢** and 35 s.  Complexity is
*not* the expensive part — complexity is roughly free per point, and gets
slightly cheaper at the top because rich runs converge or die early.  What is
expensive is failure: a point on a run that passes costs 2.0 ¢, one on a run
that does not costs 5.7 ¢.

---

## 3. What changed because of the study

Only the measurement and the rubric anchors are in this wave's scope; the
generation-side levers belong to the other two owners of the complexity wave.

1. **The vector exists and is recorded** — additive in `Measurement.extra`, a
   `complexity` block on every record (with `plan_parts`, `parts_per_plan_part`
   and the per-round trail), a `cx 0.65` chip on every gallery card, a
   complexity panel and a complexity sort in the gallery.
2. **`geometry_detail` was made countable** (F2) in `static_object_v1`,
   `articulated_v1`, `asset_v1` and `reference_v1`:
   * a named list of **eight refinement kinds** (bevel/fillet, taper, curve,
     moulding, cut-out, wall thickness, surface relief, modelled hardware) in the
     rubric notes, so "detail" is a count and not a mood;
   * a seven-rung anchor ladder — 1.0 = **five or more kinds on two thirds of the
     parts at hardware scale**, 0.85 = three or four kinds on half, 0.7 = two,
     0.55 = one, 0.4 = a box stack with one token detail, 0.25 = *every part an
     unmodified primitive*, 0.1 = blobs;
   * "colour, material and shading are NOT refinement kinds — read the criterion
     off the geometry-only montage first" (kills the material confound);
   * a **DETAIL AND SIZE** rule: score the object *built*, not the simplest
     version of the subject — a 20-part model with mouldings must outscore a
     clean 5-part box-stack of the same subject, and its defects belong to
     structure/fit/craftsmanship, not here.
3. **`primitive_only` became a majority rule** ("two thirds or more of the
   nameable parts are unmodified …, and at most one part carries any bevel,
   taper, profile, curve or cut-out"), so one token chamfer no longer exempts a
   box stack.
4. **`craftsmanship` 1.0 was moved to detail scale** (no cracks at joins, no
   coplanar flicker, no coarse facets on a surface that should read as curved)
   with a new 0.85 rung underneath it, so a rich artifact is not handed a 1.0 for
   looking clean at thumbnail size.
5. **One countable binary defect was added** — `detail_below_part_count`
   ("ten or more nameable parts but fewer than THREE distinct refinement kinds
   across all of them", penalty 0.05; eight or more *links* on the articulated
   rubric).  Binary checklist items are the mechanism that gives a compressed
   judge range (ARCHITECTURE §6), and this is the one statement of F2 that a
   judge cannot answer with a vibe: it forces the kinds to be counted.
6. **Nothing in the physical-plausibility path was touched by this wave.**  Every
   cap FLOOR and defect cap is unchanged — `build_error` 0.0, `floating_part` 0.6,
   `penetration_error` 0.7, `contract_violation` 0.75,
   `missing_must_acceptance` 0.6, `wrong_object` 0.25.  One of them has since
   changed shape, deliberately and outside this wave: since 2026-08-30 the
   acceptance cap is GRADED on the object rubrics — `0.6 + 0.4 · verified/total`
   over the must items, so 0 of n still floors at 0.6 while 9 of 10 caps at 0.96
   (`CapRule.graded`; DECISIONS D46 b, EVAL §6).  The guard
   `tests/judges/test_detail_anchors.py::test_physical_plausibility_caps_are_untouched`
   pinned `.cap` alone and so let that through; its companion
   `test_acceptance_cap_is_graded_with_the_floor_pinned` now pins both the
   `graded` flag and the 0.6 floor, so a later wave can neither soften a floor
   nor quietly fall back to the flat cap.

### 3.1 Did the rubric change measurably work?  Not yet — and here is the number

The clean experiment is a re-judge A/B: the **same recorded artifacts, the same
images, the same judge** (`gemini-3.1-pro-preview`, n_samples = 2), scored once
with the pre-wave rubric and once with the new one.  Ten runs picked to span the
detail axis, from a bin of literal blocks to a wrought-iron garden arch:

| run | index | feature density | detail: recorded → old → new |
|---|--:|--:|---|
| toy_easy_blocks | 0.49 | 3 335 | 1.00 → 0.90 → **0.95** |
| mus_easy_drum | 0.50 | 350 | 0.40 → 0.40 → 0.33 |
| veh_med_pickup | 0.53 | 906 | 0.40 → 0.10 → 0.30 |
| ctrl_med_toaster | 0.42 | 474 | 0.90 → 0.90 → 0.90 |
| ctrl_med_dining_chair | 0.55 | 778 | 0.80 → 0.65 → 0.68 |
| furn_med_dining_chair | 0.58 | 2 572 | 0.80 → 0.70 → 0.65 |
| comp_hard_telescope_tripod | 0.66 | 10 743 | 0.85 → 0.72 → **0.85** |
| plant_easy_cactus | 0.61 | 9 164 | 0.85 → 0.85 → 0.85 |
| arch_hard_spiral_stair | 0.83 | 8 330 | 0.85 → 0.82 → 0.85 |
| thin_hard_garden_arch | 0.86 | 96 747 | 0.85 → 0.85 → 0.75 |

| | mean detail | σ | r with log(feature density) | r with index | ≥ 0.9 |
|---|--:|--:|--:|--:|--:|
| old rubric | 0.690 | 0.243 | +0.48 | +0.27 | 2 / 10 |
| new rubric | 0.710 | 0.218 | +0.46 | +0.22 | 2 / 10 |

**The anchor rewrite is inside the noise on n = 10.**  Mean detail +0.020, mean
overall −0.017 (pro's measured σ on overall is 0.030), the correlation with
objective detail unmoved, the same two runs at ≥ 0.9 — and those two are still
low-complexity artifacts.  The rewrite is kept because it is strictly more
specific, auditable and test-guarded, and because it moved the two runs it was
aimed at (the telescope +0.13, the pickup +0.20); but it is **not** a
demonstrated fix, and this document should not be read as claiming one.

The honest next step is outside this owner's files: the judge currently *thinks*
about refinement kinds and reports a number.  Making it **name the kinds it
counted** in the wire schema (`judges/rubrics.py`, `build_wire_model`) would turn the ladder
from an instruction into an auditable observation — the same move that gave the
defect checklist its range.

---

## 4. Tracking the ceiling: `complexity_v3`

`bench/prompts/complexity_v3.yaml` is 12 prompts on one deliberate ladder, from a
3-part milking stool to a 25-part Watt beam engine with a visible mechanism, with
thin features, repetition, hollow sections, mouldings and mechanism switched on
in a documented order.  Every prompt declares the band its **built** artifact is
expected to reach:

```
python -m bench.run_bench bench/prompts/complexity_v3.yaml --out bench/out/complexity_v3 \
     --generator gemini-cli:gemini-3.6-flash --judge gemini:gemini-3.1-pro-preview
python bench/complexity_report.py bench/out/complexity_v3 --battery bench/prompts/complexity_v3.yaml
```

The report prints the score-vs-complexity table, the correlation matrix, the
`$ per complexity point`, and — the row that matters — every prompt whose built
artifact landed **LOW**: a run that scored well by quietly building the easy
version of the brief.  That is the failure the battery exists to catch, and the
number the next wave should move.

---

## 5. Live check: three `static_objects_v2` prompts re-run (2026-08-24)

Re-run on the current tree with the same generator and judge as their recorded
baselines (`gemini-cli:gemini-3.6-flash` / `gemini-3.1-pro-preview`), and
scored against the recorded run of the same prompt:

| run / arm | index | parts | tris | silhouette | feature density | hollow | overall | detail | fit | materials | $ | min |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| dining chair — recorded | 0.554 | 12 | 4 160 | 14.3 | 778 | 0.13 | 0.60 | 0.80 | 0.90 | 0.70 | 2.21 | 15 |
| dining chair — new | **0.686** | 14 | 19 480 | 13.8 | **3 663** | 0.14 | **0.93** | 0.85 | 0.85 | 0.70 | 1.45 | 150* |
| birdcage — recorded | 0.746 | 14 | 17 492 | 52.3 | 6 229 | 0.79 | 0.67 | 0.60 | 0.60 | 0.80 | 1.93 | 37 |
| birdcage — new | **0.825** | 15 | 33 804 | 55.2 | **13 568** | 0.90 | **0.85** | 0.85 | 0.85 | 0.85 | 1.76 | 131* |
| toaster — recorded | 0.420 | 11 | 4 722 | 1.7 | 474 | 0.13 | **0.95** | 0.90 | 0.90 | 0.95 | 0.61 | 8 |
| toaster — new | **0.517** | 11 | 15 986 | 1.7 | **2 375** | 0.10 | 0.66 | 0.75 | **0.60** | 0.75 | 0.90 | 84* |

\* the wall clocks are not comparable: this window sat inside a sustained
`gemini-3.7-flash` 503 storm and each run was restarted at least once.  Cost is
comparable and did not rise.

**Read with the eyes, not only the index.**  The chair really is richer: the
recorded one is a flat square seat slab with straight rails; the new one has a
dished seat with a shaped front edge, a curved crest rail with turned
terminals, tapered legs, side/rear aprons and corner blocks — 14 parts, 4.7× the
feature density, and the score went 0.60 → 0.93.  The birdcage gained a wire
floor grid, a framed door, more dome ribs and a trapeze — but it also lost its
brass colour and now reads near-white, which the index cannot see and the judge
did not punish.  The toaster is the finding of this document acted out: 5× the
feature density, and the score **fell** 0.95 → 0.66 because the new detail
interpenetrates (`assembly_fit` 0.90 → 0.60).  More artifact, worse verdict —
F1, live.

**What this does and does not prove.**  It proves the vector is produced,
recorded and readable in a real run (`record.extra["complexity"]`, the per-round
trail, the gallery chip).  It does **not** isolate this owner's changes: the
same tree carries a parallel wave's generation work (scoped part generation, a
`detail_budget` gate, planner-brief changes), which is the likelier cause of the
extra geometry.  The rubric A/B in §3.1 is the isolated experiment.

---

## 6. Why the toaster regressed: interpenetration is the price of detail

The one loss in §5 (`ctrl_med_toaster`, overall **0.95 → 0.66**, `assembly_fit`
0.90 → 0.60) has a measured, deterministic cause.  Replaying the recorded
connectivity gate of the baseline run against the new one:

| run | penetrating pairs | max depth | connectivity gate | overall |
|---|---|---|---|---|
| `static_v2_flash` (baseline) | 4 | 3.5 mm | **passed** | 0.953 |
| `complexity_e2e2` (this tree) | 8 | **9.0 mm** | **passed** | 0.658 |

Detail doubled the number of interpenetrating part pairs and nearly tripled the
depth — and **the gate reported both as warnings**, because `PENETRATION_ERROR_M`
is an absolute 10 mm and 9 mm squeaks under it.  The judge was not fooled; the
deterministic gate was.

Interpenetration predicts the judge, across 160 recorded rounds that have both an
`assembly_fit` score and a connectivity report:

| max fraction of a part buried | n | mean `assembly_fit` |
|---|---|---|
| none | 74 | **0.709** |
| 0.1 – 10 % | 19 | 0.647 |
| 10 – 25 % | 31 | 0.542 |
| 25 – 50 % | 26 | 0.573 |
| > 50 % | 10 | 0.505 |

`corr(max fraction inside, assembly_fit) = -0.243`, `corr(max depth, …) = -0.247`,
`corr(number of penetrating pairs, …) = -0.258`.  Two things follow, and the second
is a negative result worth recording:

1. **Any** interpenetration costs roughly 0.06–0.20 of `assembly_fit`, and the gate
   currently passes runs with up to **89 %** of a part buried inside another.  The
   ERROR bar is too lax — the harness owns a signal that predicts the judge and
   throws it away as a warning.
2. Scaling the threshold by *fraction buried* instead of absolute depth sounded
   better — 9 mm is 3.2 % of a 0.28 m toaster but nothing on a 2 m wardrobe — but the
   corpus does **not** support it: fraction (-0.243) is no better a predictor than
   depth (-0.247), and the plain count of penetrating pairs (-0.258) beats both.

NOT CHANGED YET, deliberately.  Raising the bar makes the gate fail more runs, which
changes how many repair rounds fire and what they cost, and that cannot be evaluated
from recorded data — it needs an A/B on live runs.  The queued experiment is: current
thresholds vs `error when a part is >25 % buried OR depth > 10 mm`, on
`static_objects_v2`, reading out `assembly_fit`, overall score, rounds and $/run.
Same discipline as the storm gate in `docs/COST.md` §21, which was built, measured and
shipped **off** because it lost.

## 7. Caveats

* Object tracks only.  A shader or a scene has no `object.glb`; the report counts
  those runs as skipped rather than pretending they scored 0 complexity.
* `feature_density` cannot tell a deliberate bevel from a finely tessellated
  cylinder.  It measures *modelled small-scale variation*, which is the honest
  claim; a 64-segment cylinder does read as more refined than an 8-segment one.
* `hollowness` reads "not a solid convex primitive": a bent bar scores like a
  tube.  That is intended — both are things a box-stack builder does not make.
* `symmetry_groups` matches on (tri count, sorted extents): two different parts
  that happen to share both are one family.  Rare, and it only moves 6 % of the
  index.
* Runs recorded before the vector existed have no block; the report recomputes
  them from the GLB, the gallery does not (it would put a mesh parse in the page
  request).  Re-measuring a run backfills it.
* The corpus is 67 runs from one generator (`gemini-3.7-flash`) and two judges.
  Every r above should be read as "the shape of the relation", not as a
  calibrated coefficient.

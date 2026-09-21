# Metrics — definitions, formulas, pitfalls

Everything `score.py` / `metrics.py` compute, with the exact convention used, and the reason. Numbers in
`finetune/docs/REPORT.md` and `llm_finetune_exps.md` were produced with the same definitions (§1–§3), so they
remain comparable; §4–§6 are additions.

## 1. Execution-level

| metric | definition | notes |
|---|---|---|
| **exec_rate** | `#OK / n` | OK is *strict*: Blender = ≥1 evaluated MESH object with ≥1 vertex **and** a successful GLB export in an emptied factory scene; CadQuery = a shape with volume > 1e-9 or faces, STL written; OpenSCAD = rc 0 and STL > 100 bytes; GLSL = glslang compiles the `mainImage` body under the Shadertoy prelude; three.js = page loads, zero fatal JS/import/request errors, a `<canvas>` exists, screenshot is non-uniform (`distinct colours > 6`, `std > 4`) |
| statuses | OK / EMPTY / FAIL / CRASH / TIMEOUT / MISSING | EMPTY = ran but produced nothing; CRASH = host died; MISSING = no generation for the task (counts in the denominator) |
| **render_rate** (GLSL only) | `#(compiled AND rendered non-uniform image) / n` | rendered in WebGL2 (headless Chromium) at t=0 and t=1.7 s: `distinct colours > 3` and `std > 2`; `STATIC` = compiles but paints a constant colour. Stricter than compile rate; the historical GLSL numbers are compile-only |
| truncated | `#(finish_reason == length)` | every reasoning/long-output failure shows up here first — report it next to exec_rate |
| extraction diagnostics | `no_fence`, `multi_block`, `chosen_not_last`, `from_think_fallback`, `syntax_valid`, `no_dialect_marker` | from `extract.py`; separates *format* failures from *capability* failures (REPORT §11.2) |

## 2. Geometry (suites with a reference mesh)

Both meshes are normalised: translate to the bounding-box centre, scale so the largest bbox extent is 1.
`n_points` surface samples (10 000 for 3DCodeBench, 5 000 for the held-out sets), `seed=0`.

| metric | formula | notes |
|---|---|---|
| **Chamfer** | `CD = mean_{a∈G} min_{b∈R} ‖a−b‖₂ + mean_{b∈R} min_{a∈G} ‖a−b‖₂` | **sum** of the two directional means, unsquared L2 (the report's convention). Many CAD papers report `½·CD` or squared distances ×1000 — convert before comparing to them. |
| **F@τ** | `P = frac(d_{G→R} < τ)`, `R = frac(d_{R→G} < τ)`, `F = 2PR/(P+R)`; τ ∈ {0.05, 0.1} of the unit bbox | F@0.05 is the headline geometry metric; F@0.1 is lenient |
| rotation search | generated cloud rotated by k·90° (k=0..3) about the up axis (Y for GLB, Z for STL); keep the rotation with minimal CD | objects may face any cardinal direction; no continuous ICP on purpose (would reward wrong shapes) |
| **IoU_vox** | solid-voxel IoU on a 64³ grid of the normalised bbox | needs closed meshes; `None` when a mesh cannot be voxelised (open shells) — reported as mean over scored tasks and with fail = 0 |
| `*_mean_scored` | mean over tasks with `exec == OK` and a reference mesh | quality *given* execution |
| `*_mean_all` | fail counted as 0, denominator = tasks whose **reference** executed (`n_ref_ok`) | the headline; the old held-out evals divided by `n` and so charged reference failures to the model (bug #3 in `inventory_finetune_eval.md`) |

Sanity anchor: re-executing the 3DCodeBench reference scripts in the same executor and scoring against the
shipped GT gives CD ≈ 0.016, F@0.05 ≈ 1.0 (REPORT §5). `run_eval --backend reference` reproduces this
self-test for every suite.

## 3. Sampling metrics

| metric | formula |
|---|---|
| **pass@k** | unbiased estimator (Chen et al. 2021): `1 − C(n−c, k)/C(n, k)` for n samples with c OK, averaged over tasks |
| best-of-N F@0.05 (all) | mean over tasks of `max_i F@0.05_i` with failures = 0 |

Sampling at T = 0.7, top-p 0.95, seeds 0..N−1 (`run_eval --samples N`).

## 4. Rubric-judged suites (harness batteries, no GT)

Execution rate is always reported. Quality needs a judge: the harness's calibrated VLM judge
(`harness/codeverse/judges`, Gemini pro, 14-view rig, `static_object_v1` rubric, threshold 0.72, σ ≈ 0.03 at
n=3) is the reference implementation — see `docs/inventory_harness_bench.md` §2 for how to call it on a
GLB. No judge is bundled here yet; if one is added it must be pairwise with swapped order and report κ against a
human-labelled subset (LLM judges have position/length bias — survey §2e).

## 4b. Structural integrity (any executed mesh; no GT needed) — `structural.py`, vendored from the official repo

What Chamfer, F and the image encoders are blind to: is the output *one coherent object* or a pile of parts?
Mesh normalised to a unit sphere, vertices welded, connected components on the vertex graph; contact tolerance
ε = 1 % of the bbox diagonal.

| field | meaning |
|---|---|
| `n_components`, `abs_log2_component_ratio` | component count and |log₂(gen / ref)| when a reference exists |
| **`floating_part_rate`**, `floating_area_fraction` | components not within ε of any other component (share of count / of surface area) |
| `largest_component_volume_fraction` | fragmentation: volume of the largest component / total |
| `nonmanifold_edge_rate`, `boundary_edge_rate`, `is_watertight` | topology health |
| `ground_contact`, **`com_over_support`**, `com_support_margin` | has a real base; centre of mass over the support polygon (static stability) |

Reported conditional (executed meshes) and penalized (failures at the worst value, per the official
`PENALTY` table). Read most fields as a *ratio to the reference*: only 38 % of the 3DCodeBench GT meshes are
watertight and 25 % are single-component (a fern legitimately has thousands of parts). On this machine the
CadQuery reference set scores floating 0.027 / watertight 0.93 / stable 0.93.

## 4c. VLM judge (`judge.py`) — the quality layer when there is no GT

Absolute mode: 1–5 on identity / structure / detail (+ reference faithfulness for image tasks) and an overall
grade, from the 4 canonical renders; reported conditional and penalized (unrendered = 0). Pairwise mode: the
official 3DCodeBench arena prompt, judged in both orders; a winner counts only when both orders agree, else
`tie` (order disagreements are reported — they measure the judge's position bias). Any OpenAI-compatible VLM
endpoint or Claude. Treat absolute grades as triage; rank models with pairwise win-rates.

## 5. Image-conditioned (VLM) suites

Same executor and geometry metrics as the text suites (same GT), so `3dcodebench_img4` vs `3dcodebench_text`
is directly comparable. Reference views are the official 4 GT renders (`Image_005/015/025/035`), sent as
768-px JPEG data-URLs, image parts before the text.

## 6. Noise floor and reporting rules (from REPORT §12.7, §13.4, §13.7)

- Within one session with one engine config, greedy runs repeat to 0.0 pp on Blender suites, 0.5 pp CadQuery,
  1.5 pp GLSL, 2.0 pp OpenSCAD, 5.0 pp three.js (n = 40). Across sessions / engine configs drift can reach
  6–8 pp on OpenSCAD. **Compare models only within one batch.**
- OpenSCAD / GLSL / three.js: greedy decoding degenerates into runaway loops on boilerplate-heavy dialects;
  report **T = 0.7 as primary** and greedy as secondary (suite defaults do this).
- Fix `max_new_tokens` per suite (8192 text/CAD, 12288 three.js) and always report `truncated`.
- Report `exec_rate` and `F@0.05 (all)` as the two headline numbers; `F@0.1 (ok)`, `CD (ok)`, `IoU (ok)`
  describe quality given execution.
- 212-task binomial 95 % CI at 50 % is ±6.7 pp; at n = 40 it is ±15 pp — do not read third decimals.

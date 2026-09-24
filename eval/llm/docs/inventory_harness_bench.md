# Inventory: the `harness/bench` evaluation layer (what is reusable for LLM/VLM eval)

Read-only audit of `/home/yipeng/3dcodeverse/harness/` (2026-09-08). Paths relative to `harness/`.

## 1. Prompt batteries (`bench/prompts/*.yaml`)

Schema (`bench/run_bench.py:49-71`): `Battery{name, track, language, description, prompts[]}`;
`BenchPrompt{id, prompt, tier (easy|medium|hard), category, must_have[], dimensions_m{}?, tags[], language?, references[]}`.

| battery | track / language | n | tiers | notes |
|---|---|---|---|---|
| `static_objects_v1` | static_object / blender | 24 | 8/8/8, 8 categories | must_have + dimensions_m |
| `static_objects_v2` | blender | 20 | 4 med + 16 hard | tags `axis:decomposition/curved_organic/thin_features/arch_detail/multi_material` |
| `compare_v1..v3` | blender | 8/8/12 | | |
| `compare_v4` | blender | 40 | 8/8/24 | union of v1+v2, paired-CI sized |
| `complexity_v3` | blender | 12 | 3/3/6 | `expected_complexity: [lo,hi]` |
| `fancy_v1_blender` | blender | 10 | hard | long product-designer briefs |
| `fancy_v1_cadquery` | cadquery | 3 | hard | |
| `fancy_v1_threejs` | threejs | 3 | hard | |
| `teaser_v1_static` | mixed | 9 | | 3 cadquery, 3 threejs rows |
| `h2h_brilliana_v1_*` | static_object | 5/4/3 | medium | must_have empty on purpose |
| `articulated_v1/v2` | urdf_blender | 12/14 | | ≥3 DoF in v2 |
| `scenes_v1/v2`, `teaser_v1_scene`, `h2h_scene_v1` | scene_threejs | 12/10/6/5 | | **needs the agentic harness** (multi-file) |
| `graphics_v1/v2`, `teaser_v1_graphics` | glsl_shader (some opengl_python) | 6/10/5 | | |
| `refs_v1/v2_graphics` | glsl_shader | 6 each | | `ref`/`noref` arms with photos in `bench/refs/aurora_ref_*` |

**One-shot appropriate** (single LLM call → code): every static_object battery (blender/cadquery/threejs), every graphics battery, articulated (two-file contract). **Not one-shot**: scene batteries.

## 2. Judging methodology

- `codeverse3d/judges/vlm_judge.py` `VlmJudge(rubric, model_id, n_samples, temperature=0.2, thinking="low")`; default judge `gemini:gemini-3.1-pro-preview`. Variants `ReferenceJudge` (reference images + silhouette IoU), `LikenessJudge`, `PairwiseJudge` (`judges/pairwise.py`: both orderings, winner only when they agree).
- Prompt (`judges/prompt_builder.py`): blind judge, observe-then-score, defect checklist; BRIEF + CONSTRAINTS + MEASUREMENTS + GATE FINDINGS; ≤5 labelled 2×2 montages (512 px tiles) + ≤2 detail crops; cross-section slices only on connectivity ERROR (D48).
- Score (`judges/rubrics.py`, SCORING_VERSION=2): weighted criteria → majority-voted defects with penalties/caps → gate caps → `passed = overall ≥ threshold ∧ no floor ∧ no unverified must item`.
- Rubrics (`judges/rubrics/*.yaml`): `static_object_v1` (threshold 0.72; intent_fidelity .22, structure .18, geometry_detail .16, proportions .12, assembly_fit .12, materials .10, craftsmanship .10; 11 defects), `articulated_v1` (0.70), `scene_v1` (0.70), `shader_v2` (0.70; shader_v1 had Spearman 0.16 vs human), `asset_v1`, `reference_v1` (measured silhouette IoU criterion, weight .25).
- View rig (`codeverse3d/conventions.py:87-102`): 14 views (high ring ×4 @ +30°, eye-level ×4, low ring ×4 @ −30°, top, bottom), 768² studio background; clay montage ×4. Graphics: frames at t = 0, 1, 2.5, 4, 6 s.
- Calibration (`docs/EVAL.md` §6): pro σ(overall, n=3) **0.030**, flash σ **0.083**; fixed-order re-judge σ 0.035; paired A/A on identical arms: paired sd 0.16–0.20 → generation variance dominates; ±0.02 needs ~500 pairs. ~$0.15–0.20 per pro verdict.
- Deterministic gates: per-language lint; build (`ok` requires non-empty GLB); `measure_glb` (bbox, tri_count, n_meshes, n_islands, per-part volume/watertight, ground_gap, materials, complexity vector); `check_connectivity` (floating part = ERROR, penetration WARN > 2 mm, ERROR > 10 mm); graphics `gl_frames` (NaN, black/blown, static, flicker, low_detail); `reference_silhouette` IoU; `must_have` → VLM acceptance items.
- **No CLIP/DINO/Chamfer metric exists in the harness**; silhouette IoU is the only image similarity; geometry is compared to the plan's boxes only.

## 3. One-shot path (`bench/_oneshot.py`)
- `oneshot_prompt(spec)` = `"Model this object in raw bpy: <prompt>"` + dimensions + MUST HAVE/NOT + minimal contract (Blender 5.x, emptied scene, Z-up, −Y front, metres, on z=0, PascalCase names, material per mesh, no render/export/import/network, <500k tris, <120 s, call `main()`) + "reply with the COMPLETE file as ONE ```python block".
- Backends: `claude-code[:model]`, `codex[:model]`, `gemini:|anthropic:|openai:<model>` (ApiOneShot: temperature 0.5, thinking medium, max_output 32000). **Local open model works** via `openai:<model>` + `C3D_OPENAI_BASE_URL=http://host:8000/v1` (vLLM OpenAI server).
- Contracts per language to reuse verbatim: `codeverse3d/prompts/{blender,cadquery,threejs,glsl_shader,opengl_python,scene_threejs,urdf}/{system.md,contract.md,cookbook.md}`.
- Evaluate a workspace: `bench/_fixed_eval.FixedEvaluator(judge_model, n_samples, track, language).evaluate(ws, spec) -> EvalOutcome` (build → gates → 14-view render → judge).

## 4. Renderers / executors (`codeverse3d/languages/<lang>`)
| language | build | outputs |
|---|---|---|
| blender | `blender -b --factory-startup --python wrappers/run_bpy.py -- --script src/model.py --out artifacts --rlimit-gb 12 --seed 0 --stl` | `object.glb` (Y-up), `object.stl`, `build.json`, `census.json` |
| cadquery | `wrappers/run_cq.py --script … --tolerance 0.001` | `object.glb/.step/.stl` |
| threejs | `node runtime_js/export_glb.mjs` (three@0.182) | `object.glb` |
| urdf_blender | `run_bpy_links.py` + FK check + fcl sweep | `robot.urdf`, `meshes/*.glb`, pose renders |
| scene_threejs | puppeteer probe/render over http | renders, `metrics.json` |
| glsl_shader / opengl_python | moderngl `GlHost` | `frames/*.png`, `preview.gif`, `metrics.json` |

GLB rendering for judging: `codeverse3d.spatial.render.render_glb(glb, out_dir, views=OBJECT_VIEWS, mode=shaded|clay|…)` via `runtime_js/render_glb.mjs` in headless Chrome.

## 5. Result schema
`BenchItemResult`: `id, tier, category, score_baseline, score_final, passed, rounds, cost_usd, minutes, status, errors, workspace, generator, judge`. `compare_backends.py` → `CellResult{prompt_id, arm, status (scored|build_failed|no_code|judge_error|infra_failed|budget_exhausted), score, score_std, passed, build_ok, gate_errors[], tris, gen_cost_usd, judge_cost_usd, criteria{}, …}` + `paired.md` (paired Δ, t-CI, sign test).

## 6. Reuse recommendation for `3dcodeverse_eval/`
1. Batteries are data: copy the YAMLs (`static_objects_v2`, `compare_v4`, `fancy_v1_*`, `graphics_v2`, `articulated_v2`) as a **rubric-judged / no-GT** prompt set.
2. Judge stack as-is: `codeverse3d.judges.{rubrics, prompt_builder, vlm_judge, pairwise}` + rubric YAMLs; keep Gemini pro for comparability; n = 1 or 3.
3. Gates/renderers: `codeverse3d.spatial.{measure.measure_glb, connectivity.check_connectivity, render.render_glb, complexity.complexity_of_glb, silhouette.best_view_match, frame_stats}`; `codeverse3d.languages.get_runtime(lang).build`.
4. Gaps: one-shot contracts exist only for blender/urdf (derive others from `prompts/<lang>/contract.md`); no plan-free `dimensions_m` gate (write one over `measure_glb().extents`); scene batteries need the full harness.

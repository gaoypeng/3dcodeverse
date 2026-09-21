# Evaluation protocol — what to run, how to decode, how to report

Distilled from `finetune/docs/REPORT.md` (§5, §11–§13), the 3DCodeBench paper protocol
(`~/nips2026_paper/3dcodebench`, arXiv:2606.01057) and the literature survey (`survey_synthesis.md` §4).

## 1. The standard battery for one model

| tier | suites | why |
|---|---|---|
| headline | `3dcodebench_text` | the report's headline; 212 tasks, GT mesh, greedy |
| dialect coverage | `heldout_blender`, `heldout_cadquery`, `heldout_openscad`, `heldout_glsl`, `heldout_threejs` | never-trained-on prompts per dialect; catches "one-dialect" models |
| VLM | `3dcodebench_img4` (+ `img1`, `img4text` for the ablation) | image→code with the same GT as text, so the text/image gap is a direct read |
| paper placement | `3dcodebench_official_text`, `3dcodebench_official_img4` | the official prompts + SigLIP-2/DINOv3 + official Chamfer, comparable to the frontier table |
| rubric | `harness_static_objects_v2`, `harness_compare_v4`, `harness_graphics_v2` … | brief-style prompts with must-have lists; needs the harness VLM judge for quality |

`run_eval --suites headline heldout vlm official` runs the first four tiers.

## 2. Decoding

| dialect / suite | primary | secondary | max_new_tokens |
|---|---|---|---|
| Blender (3dcodebench_*, heldout_blender), CadQuery | greedy (T=0), thinking off | T=0.7 × 4 samples (pass@k) | 8192 |
| OpenSCAD, GLSL, three.js | **T=0.7, top-p 0.95** | greedy (diagnostic: report the runaway/truncation rate) | 8192 / 8192 / 12288 |
| official suites | T=0.7 (paper default), thinking "medium" for API models | — | 16384 |

Rationale: greedy decoding on boilerplate-heavy dialects collapses into repetition loops that hit the token
cap (REPORT §13; ShaderMatch's `incomplete` bucket; survey §2g). Thinking modes raise execution rates on most
dialects but exhaust the budget on GLSL (REPORT §11.2) — evaluate them as a separate row with a stated budget,
never mixed with no-think rows. `--think` keeps the model's reasoning on; the default sets
`enable_thinking=False` through the chat template.

## 3. Sampling and pass@k

`--samples N` (seeds 0..N-1, T forced to 0.7 if the suite default is greedy) → `report.py` computes unbiased
pass@1/2/4/N and best-of-N F@0.05. Use N = 4 for cost, N = 10 when a 3-point gap matters. pass@1 from N
samples is the *sampling* pass@1; the greedy row is a different quantity — label both.

## 4. Execution budgets (identical across suites of a dialect)

Blender 300 s, CadQuery 90 s, OpenSCAD 120 s, GLSL 60 s (compile + WebGL render), three.js 60 s page load +
4 s settle. Official renderer 240 s per GLB (Cycles 64 spp, 512 px, GPU if present).

## 5. What to report (one table row per (model, suite))

`exec_rate` · `F@0.05 (all)` · `F@0.1 (ok)` · `CD (ok)` · `IoU (ok)` · `render_rate` (GLSL) · `truncated` ·
`mean_new_tokens` · `no_fence` / `multi_block` — `report.py` emits exactly this. For official suites add
`siglip2 paired (penalized / conditional)`, `dinov3 paired`, `cd_official_yawmin (cond / pen)`.

Always give: model id + revision, backend (vllm / server / API), temperature, max_new_tokens, thinking on/off,
tool versions (Blender 5.0.1, OpenSCAD build, glslang, Chromium), and the date — API drift (Blender 4→5) was
the largest single confound in the paper.

## 6. Noise, comparisons, contamination

- Within one batch and engine config, greedy repeats are exact on Blender suites; OpenSCAD / GLSL / three.js
  vary 1.5–5 pp; across sessions up to 6–8 pp (REPORT §13.7). **Run the models you compare in one batch**,
  same `--limit`, same engine.
- Binomial 95 % CI at n = 212 is ±6.7 pp around 50 %; n = 40 (three.js) ±15 pp. Report per-suite n.
- Do not read a metric computed only on executed tasks (`*_scored`) as a quality ranking: it rewards models
  that fail often (survivor bias, survey §2a). The `*_all` / penalized numbers are the headline.
- Contamination: the 212 test factories and all their seed instances are excluded from every 3DCodeVerse
  training mix (`build_multidialect.py`, `build_md_max.py` rules); the held-out parquets were built with
  `random.seed(7)` splits before training. A model trained on the open `ilabai/3dcodeverse` corpus **has
  seen** DeepCAD / Shadertoy / Thingiverse neighbours of the held-out prompts (same sources, different items) —
  say so when reporting.

## 7. Extending

- New prompt set: write `data/prompts/<suite>.jsonl` in the row schema (README) and add a `SuiteSpec`.
- New dialect: add `executors/<dialect>.py` with `run(code, workdir, timeout) -> report`, register in
  `executors/__init__.py` (`TIMEOUTS`, `CODE_EXT`, `RUNNERS`), add dialect markers to `extract.DIALECTS`.
- New metric: `metrics.py` primitive + one block in `score.py` gated by the suite's `metrics` tuple + a row in
  `docs/metrics.md`.
- Deterministic property tests per prompt (survey §3.3: CADTestBench-style must-have checks, human agreement
  F1 0.94 vs 0.66 for Chamfer) are the highest-value next addition; the harness batteries already carry
  `must_have` lists in `meta` to seed them.

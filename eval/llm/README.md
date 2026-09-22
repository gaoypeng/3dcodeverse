# eval/llm — one place to evaluate an LLM / VLM on 3D coding (was `finetune/3dcodeverse_eval`)

Self-contained, machine-independent evaluation of *text → 3D code* and *image → 3D code* across the
3DCodeVerse dialects (Blender-Python, CadQuery, OpenSCAD, GLSL, three.js). It packages the prompt sets,
the reference assets, the executors, the metrics and the reporting that were previously spread over
`finetune/eval`, `finetune/scripts`, `harness/bench` and the official `3dcodebench` repo, with every
path resolved from the environment (`config.py`) instead of `/wekafs/...`.

```
llm/
  config.py            paths + tool discovery (C3D_EVAL_DATA, C3D_TOOLS, C3D_BLENDER, …); `python -m llm.config` = doctor
  download.py          pull the assets from the Hub (needs a token with access to ilabai/*)
  build_prompts.py     Hub assets → data/prompts/<suite>.jsonl   (one schema for every suite)
  build_refs.py        execute the reference programs → $C3D_EVAL_DATA/refs/<suite>/<id>/ref.{glb,stl}
  suites.py            suite registry: dialect, decoding defaults, which metrics apply, tags
  generate.py          backends: vllm (local, text+image) · openai-compatible (vLLM server / API) · anthropic · reference
  extract.py           dialect-aware code extraction from free-form answers (think blocks, multiple fences)
  executors/           blender · cadquery · openscad · glsl (compile + WebGL render) · threejs (Playwright)
  execute.py           run a gen dir → exec_results.jsonl (uniform OK/EMPTY/FAIL/CRASH/TIMEOUT/MISSING)
  render_views.py      official 4-view turntable render of a GLB (Cycles, 512 px)
  metrics.py           Chamfer / F@τ / voxel IoU (report protocol) · official squared unit-sphere Chamfer · pass@k
  image_sim.py         SigLIP-2 / DINOv3 view-paired + best-assignment similarity (official image metric)
  structural.py        official structural-integrity metric (floating parts, fragmentation, watertight, stability), vendored
  judge.py             VLM judge: absolute 1-5 grades and pairwise A/B (both orders) from the 4 renders — quality without GT
  rescore_logs.py      replay the paper's frontier-model outputs through this pipeline
  setup.sh             installs the conda env + Blender / OpenSCAD / glslang / Chromium (no sudo)
  score.py             metrics.jsonl + summary.json per (run, suite)
  report.py            report.md / report.json over an output root (+ pass@k over sampled runs)
  run_eval.py          gen → exec → render → score → report in one command
  data/prompts/        the prompt sets (built by build_prompts.py; NOT in git)    harness YAML batteries: ../bench/prompts (config.HARNESS_BATTERIES)
  data/official_prompts/  the official 3DCodeBench system prompts
  docs/                metrics.md · protocol.md · survey_synthesis.md · survey_records.md · inventory_*.md · results/
  tests/               offline smoke tests (`pytest llm/tests`)
```

## Suites (`python -c "from llm.suites import all_suites; ..."` or `suites.py`)

| suite | n | input → dialect | reference | metrics | decoding default |
|---|---|---|---|---|---|
| `3dcodebench_text` | 212 | structured instruction → Blender | GT mesh (re-executed factory, + canonical GLB) | exec, Chamfer, F@0.05/0.1, IoU | greedy, 8k |
| `3dcodebench_desc` | 212 | short description → Blender | same | same | greedy, 8k |
| `3dcodebench_img1` / `img4` / `img4text` | 212 | 1 / 4 GT views (+ description) → Blender | same | same (VLM) | greedy, 8k |
| `3dcodebench_official_text` / `official_img4` | 212 | official system prompt (raw python, no fence) | same | exec, official Chamfer, **SigLIP-2 / DINOv3** view similarity, F/IoU | T=0.7, 16k |
| `heldout_blender` | 103 | bioinspired3d + non-test factories → Blender | executed reference (96 OK) | exec, geometry | greedy, 8k |
| `heldout_cadquery` | 200 | DeepCAD / Articraft → CadQuery | executed reference (200 OK) | exec, geometry | greedy, 8k |
| `heldout_openscad` | 50 | Thingiverse → OpenSCAD | executed reference (50 OK) | exec, geometry | **T=0.7**, 8k |
| `heldout_glsl` | 200 | Shadertoy captions → GLSL | compile (178/200 refs) | compile rate, WebGL render rate | **T=0.7**, 8k |
| `heldout_threejs` | 40 | scene briefs → single-file three.js | execution (40/40 refs) | exec | **T=0.7**, 12k |
| `harness_*` (10) | 3–40 | harness batteries (must_have, dimensions) → blender / cadquery / threejs / glsl | none | exec (+ VLM judge via harness) | greedy / T=0.7 |

Tags: `headline` (3dcodebench_text), `text`, `vlm`, `official`, `heldout`, `long-output`, `rubric`, `all` (= everything but `rubric`).

## Quickstart (a new machine: three commands to a report)

```bash
# 0. environment: conda env cv3d-eval + Blender 5.0.1 + OpenSCAD + glslang + Chromium, no sudo (~15 min, re-runnable)
bash llm/setup.sh
export C3D_EVAL_DATA=~/3dcodeverse_data/3dcodeverse_eval C3D_TOOLS=~/3dcodeverse_data/tools   # defaults
python -m llm.config                     # doctor: every tool and asset it can / cannot find

# 1. assets (token with access to ilabai/*: `hf auth login`)
python -m llm.download --core --views --logs      # ~1 GB; --gt-tar adds the canonical GLBs (2.1 GB)
python -m llm.build_prompts
SEED=0 python -m llm.build_refs                    # executes every reference program (~20 min)

# 2. sanity: the references through the whole pipeline (exec ≈ 100 %, F@0.05 ≈ 1)
python -m llm.run_eval --backend reference --run reference --suites all

# 3. a local open model (vLLM in-process; 8B bf16 fits a 24 GB card)
python -m llm.run_eval --backend vllm --model ~/3dcodeverse_data/models/Qwen3-8B --run qwen3_8b --suites headline heldout
python -m llm.run_eval --backend vllm --model ... --run qwen3_8b_t07 --suites long-output --samples 4   # pass@k

# 4. any OpenAI-compatible endpoint (vLLM server, OpenRouter, GPT…) or Claude
vllm serve ~/3dcodeverse_data/models/Qwen3-8B --port 8000 &
python -m llm.run_eval --backend openai --base-url http://localhost:8000/v1 --model Qwen3-8B --run q8_server --suites all
ANTHROPIC_API_KEY=… python -m llm.run_eval --backend anthropic --model claude-sonnet-4-6 --run sonnet --suites headline

# 5. quality without ground truth: VLM judge (any OpenAI-compatible VLM endpoint, or Claude)
python -m llm.run_eval ... --judge-model gpt-5.4 --judge-base-url https://…/v1      # absolute 1-5 after scoring
python -m llm.judge pairwise --run-a qwen3_8b --run-b official_gpt-5.5 --suite 3dcodebench_official_text --judge-model …

# 6. tables
python -m llm.report --root $C3D_EVAL_DATA/out
```

What is measured, per suite: execution → geometry vs GT (F@0.05, Chamfer, IoU) → structural integrity of the
mesh itself (floating parts, fragmentation, watertight, stability) → official render similarity (SigLIP-2 /
DINOv3) → optional VLM judge. `docs/metrics.md` defines each; the answer to "is the object good" is F@0.05 /
SigLIP-2 when a GT exists, and the structural fields + judge when it does not.

Outputs: `$C3D_EVAL_OUT/<run>/<suite>[/sN]/{<id>/{code.*, raw.txt, exec/…}, gens.jsonl, gen_stats.json,
exec_results.jsonl, metrics.jsonl, summary.json}` and `$C3D_EVAL_OUT/report.md`.

## Prompt row schema (`data/prompts/*.jsonl`)

```json
{"id": "AgaveMonocot_seed0", "suite": "3dcodebench_text", "dialect": "blender",
 "messages": [{"role": "system", "content": "…"}, {"role": "user", "content": "…"}],
 "images": ["hub/3DCode/3DCodeBench_ModelLogs/inputs/AgaveMonocot_seed0/images/Image_005.png", …],   // VLM suites
 "reference": {"code": "…", "mesh": "refs/3dcodebench/AgaveMonocot_seed0/ref.glb", "renders": ["…Image_005.png", …]},
 "meta": {"factory": "AgaveMonocot", "caption_type": "instruction", "source": "YipengGao/3DCode/3DCodeBench"}}
```
Paths are relative to `$C3D_EVAL_DATA`. Adding a suite = adding a JSONL in this schema (+ one line in
`suites.SUITES` for decoding defaults / metrics).

## What the numbers mean

See `docs/metrics.md` (definitions, formulas, pitfalls) and `docs/protocol.md` (the recommended protocol:
decoding, budgets, noise floor, reporting). Two protocols coexist on purpose:

* **report protocol** (`3dcodebench_text`, `heldout_*`): the exact prompts, executors and metrics of
  `finetune/docs/REPORT.md`, so every historical number in `llm_finetune_exps.md` stays comparable.
* **official protocol** (`3dcodebench_official_*`): the prompts, 4-view renderer, SigLIP-2 / DINOv3 and
  squared unit-sphere Chamfer of the 3DCodeBench paper (arXiv:2606.01057), so a local model can be placed
  in the paper's frontier-model table. The paper's raw frontier outputs (`download.py --logs`) can be
  re-scored here to calibrate.

Reference self-test on this machine (2026-09-08, Blender 5.0.1 + scipy 1.17.1 + shapely, OpenSCAD 2026.09.07,
glslang 16.5, Chromium 151): 3DCodeBench refs 210/212 (ElkhornCoral empty, FanCoral scipy ValueError),
held-out Blender 96/103, CadQuery 200/200, OpenSCAD 50/50, GLSL 178/200 compile (133 render non-uniform),
three.js 40/40 — identical to the report's reference counts.

## Reusing the harness judge

The harness batteries (`../bench/prompts/*.yaml`, the ten in `config.HARNESS_BATTERIES`) have no ground truth; their quality score is the calibrated
Gemini VLM judge in `harness/codeverse3d/judges` (14-view rig, rubric `static_object_v1`, σ ≈ 0.03 at n=3).
`docs/inventory_harness_bench.md` §7 shows the minimal call sequence (`FixedEvaluator`, `h2h_glb.py`) to
judge a GLB produced here without the agentic pipeline.

## Provenance

* prompts/executors/metrics of the report protocol: `docs/inventory_finetune_eval.md`
* harness batteries and judge: `docs/inventory_harness_bench.md`
* public benchmarks and metrics literature (130 records): `docs/survey_records.md`, synthesis in `docs/survey_synthesis.md`

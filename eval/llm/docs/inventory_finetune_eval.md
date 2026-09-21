# Inventory: the existing `finetune/eval` + `scripts` evaluation code

Read-only audit of `/home/yipeng/3dcodeverse/finetune/` (2026-09-08). This is the reference for what
`3dcodeverse_eval/` re-implements portably. All paths relative to `finetune/` unless absolute.

## 1. Evaluation suites

### 1.1 Shared conventions

- Prompt jsonl row (all text suites): `{"task": <str>, "messages": [{"role":"system",...},{"role":"user",...}]}`. Image suites add `"images": [abs paths]` and one `<image>` per image in the user text (`eval/generate_vllm_img.py:3-4`).
- Task id for held-out sets: `r["id"].replace("/", "__")`, messages truncated to `[:2]` (system + user; the assistant reference is dropped) — `eval/eval_all.sh:47-50`, `eval/run_dialect_eval.sh:11-13`, `scripts/threejs_check.sh:19-21`.
- Every held-out set row in `data/multidialect/<dialect>/test.jsonl` is `{"messages":[system,user,assistant("```<fence>\n<code>\n```")], "dialect", "subset", "id"}` (`scripts/build_multidialect.py:66-70`).
- Reference code is recovered from the assistant turn with the **legacy** longest-fenced-block rule (`eval/dialect_eval.py:13-14`, `eval/blender_dialect_eval.py:9-10`).
- `scripts/build_hf_dataset.py:119-129` exports every `test.jsonl` to `test/<dialect>.parquet` with columns `id, system, prompt, reference_code` — 593 rows total (103+200+50+200+40). This is the cleanest portable source of the five held-out sets (now on the Hub: `ilabai/3dcodeverse-llamafactory/test/*.parquet`).

### 1.2 3DCodeBench (212 tasks, text → Blender-Python)

| item | value | ref |
|---|---|---|
| Prompt file | `data/sft_v1/bench_prompts.jsonl` | `eval/run_eval.sh:8-9`, `eval/eval_all.sh:43` |
| Built by | `for d in sorted(os.listdir(BENCH)): prompt = open(f"{BENCH}/{d}/prompt_instruction.txt").read().strip()` | `scripts/prep_data.py:176-180` |
| BENCH dir | `/wekafs/ict/hx_624/data/3dcodebench/data/<task>/` with `prompt_instruction.txt`, `prompt_description.txt`, `glb/<task>.glb` (GT), `images/*.png` (GT renders, ≥4 views) | `prep_data.py:69`, `metrics.py:8,47` |
| Task name | directory name, e.g. `AgaveMonocot_seed0`; factory name = name minus `_seed0` | `prep_data.py:70` |
| System prompt (exact) | `"You are an expert in procedural 3D modeling with Blender Python (bpy). Given a description of an object, write a complete, standalone Blender 5 Python script that builds it from scratch (clear the default scene first, create geometry with bpy/bmesh, assign simple materials when relevant, leave the finished mesh objects in the scene). Output ONLY the code in one ```python block."` | `prep_data.py:14-17` |
| User prompt | verbatim `prompt_instruction.txt`. Alternate suite uses `prompt_description.txt` | `prep_data.py:178` |
| Size | 212 = number of dirs in BENCH; `metrics.py:43` iterates the BENCH listing, so tasks with no generation become `exec: "MISSING"` and count in the denominator | `metrics.py:43-46,65` |
| Leakage control | training drops factories whose `name.replace("Factory","")` is in the 212 test names (`prep_data.py:85-86`, `build_multidialect.py:33,36`); `3dcodebench/instances_*` excluded entirely (`build_md_max.py:12`) | |
| Sanity | reference scripts through the harness: Chamfer ≈ 0.016, F@0.05 ≈ 1.0 (REPORT §5) | |

### 1.3 Blender held-out (103)

- Source: all `bioinspired3d/*/metadata.parquet` plus `3dcodebench/*/metadata.parquet` filtered to non-test factories (`build_multidialect.py:34-36`).
- Prompt: `caps.instruction` if present, else `"Create the 3D object described below using Python Blender code: " + caps.detailed` (line 25).
- System: `"You are an expert in procedural 3D modeling with Blender Python (bpy). Given a description of an object, write a complete, standalone Blender 5 Python script that builds it from scratch (clear the default scene first). Output ONLY the code in one ```python block."` (line 9).
- QC (`build_multidialect.py:48-56`): drop empty code, `<3` newlines, control chars, prompt `<20` chars, sha1-duplicate code. Seed `random.seed(7)`; `n_test = min(200, max(50, len(keep)//50))`. Reference exec: 96/103 OK.

### 1.4 CadQuery (200)

- Source: `deepcad/*` + `articraft/cadquery_single_tex*`. Prompt: `"Write a CadQuery (Python) script that builds the following CAD model: " + (instruction or detailed)`.
- System: `"You are an expert CAD programmer. Given a description of a part, write a complete, standalone CadQuery (Python) script that builds it and leaves the final solid in a variable named `result`. Output ONLY the code in one ```python block."` Reference exec 199/200.

### 1.5 OpenSCAD (50)

- Source: `thingiverse/openscad`. Prompt: `detailed or instruction` verbatim. System: `"You are an expert OpenSCAD programmer. Given a request, write a complete, standalone OpenSCAD (.scad) program that produces the described model. Output ONLY the code in one ```openscad block."` Reference exec 50/50.

### 1.6 GLSL (200)

- Source: `shadertoy/*` main image pass only. Prompt: `instruction` if it starts with write/create/implement/make, else `"Write a GLSL fragment shader for Shadertoy that renders: " + (instruction or detailed)`.
- System: `"You are an expert shader programmer. Given a description, write a complete Shadertoy-style GLSL ES 3.00 fragment shader defining `void mainImage(out vec4 fragColor, in vec2 fragCoord)`. Output ONLY the code in one ```glsl block."` Reference compile 178/200. Metric is compile-only.

### 1.7 three.js (40)

- `data/multidialect/threejs/test.jsonl` (280/20/40 split of `threejs_distill`; the split builder is not in the repo). Fence ```html. Reference exec 40/40. Historical budgets 12288 → 32768.

### 1.8 Image-conditioned suites (3DCodeBench renders → Blender-Python)

| file | images | system | user |
|---|---|---|---|
| `_img_bench_prompts.jsonl` | first render | `"...Given reference renders of an object, write a complete, standalone Blender 5 Python script that reproduces it (clear the default scene first). Output ONLY the code in one ```python block."` | `"<image>\nReproduce this 3D object with Blender Python code."` |
| `_img4_bench_prompts.jsonl` | 4 renders | same | `"<image>"*4 + "\nReproduce this 3D object with Blender Python code."` |
| `_img4text_bench_prompts.jsonl` | 4 renders + caption | (builder missing; inferred from `build_mm_dataset.py:33,48`) | `"<image>"*4 + "\nThese are reference renders of one object. Write the Blender Python code that builds it.\n\nThe object: " + caption` |

Scoring identical to the text bench, so image and text numbers are directly comparable.

## 2. Executors

### 2.1 Blender (`eval/run_bench.py` + `eval/blender_runner.py`)
- `BLENDER -b --factory-startup -noaudio --python blender_runner.py -- --script code.py --out out.glb --report exec.json`; env `HOME=/tmp`, `XDG_CONFIG_HOME=/tmp/.bcfg`; workers 16–32, timeout 300 s.
- Statuses: OK / EMPTY / FAIL (from report) ; CRASH (no report) ; TIMEOUT.
- Inside Blender: delete every object, purge orphan data (so the default cube cannot pass), `runpy.run_path`, then count depsgraph-evaluated MESH objects → EMPTY if none / no verts; export GLB (`export_yup=True`, `export_apply=True`).
- Pitfalls: scipy must be installed in Blender's bundled python (15/212 refs import it); only MESH objects count; no sandbox; `blender_dialect_eval.py` uses timeout 180 and a different env.

### 2.2 CadQuery (`eval/dialect_runners.py:6-60`)
- Subprocess with a runner that monkeypatches `cq.exporters.export`, injects `show_object`/`debug`, finds the result object (captured export → named globals `result, final, model, part, ...` → any), `Assembly→toCompound`, STL export with `tolerance=0.01`. `CQ_REPORT {json}` line in stdout. Timeout 90 s. EMPTY when volume ≤ 1e-9 and no faces.

### 2.3 OpenSCAD — `openscad -o out.stl --export-format binstl model.scad`, `QT_QPA_PLATFORM=offscreen`, timeout 120 s; OK iff rc==0 and STL > 100 bytes.

### 2.4 GLSL compile — strip `#version`/`precision`/uniform/`out vec4` lines, wrap with a Shadertoy prelude (`#version 300 es`, uniforms, `void main`), `glslangValidator -S frag`. Prelude lacks `iFrameRate` (render harness has it).

### 2.5 GLSL render (`eval/glsl_render.py`) — WebGL2 in headless Chromium (swiftshader), 320×240, frames at t=0 and 1.7 s; OK if distinct colours > 3 and std > 2, else STATIC. Not part of any suite historically.

### 2.6 three.js (`eval/threejs_runner.py`) — Playwright Chromium (angle/swiftshader), 640×480, `file://` load, 4 s settle, collect pageerror/console.error/requestfailed; blank decision from a screenshot (`distinct > 6 and std > 4`). Depends on CDN import of three.js (network).

## 3. Metrics (`eval/metrics.py`)
- Normalise: centre at bbox centre, scale so max extent = 1; `sample_surface(n)` (bench 10 000, held-out 5 000), `np.random.seed(0)`.
- `chamfer = mean(d_a→b) + mean(d_b→a)` (sum, L2, not squared); `F@τ = 2PR/(P+R)` with `P=frac(d_a→b<τ)`, `R=frac(d_b→a<τ)`, τ ∈ {0.05, 0.1}.
- Rotation search: gen points rotated by k·90° about Y (glTF up), keep min-Chamfer.
- `summary.json`: `n_tasks, exec_ok, exec_ok_rate, n_scored, chamfer_mean_scored, chamfer_median_scored, f@0.05_mean_scored, f@0.1_mean_scored, f@0.05_mean_all(fail=0), f@0.1_mean_all(fail=0)`.
- pass@N / best-of-N (`pass_at_n.py`): pass@1 = mean OK over samples; pass@N = tasks with ≥1 OK; best-of-N F = mean over tasks of max_i F (fail = 0).
- Extraction stats (`extract.summarize`): `n, with_think, no_fence, from_think_fallback, multi_block, chosen_not_last, syntax_valid, no_dialect_marker, empty`.

## 4. Code extraction (`eval/extract.py`)
- Strip closed `<think>` blocks; unclosed → truncate at tag, mine code from the think text as fallback.
- Candidates: every fence + one trailing unterminated fence; no fence → from first code-like line.
- Score: +3 per dialect marker (max 2), +2 fence-lang match, +2 syntax OK; penalties for shell/pip lines, JSON, tracebacks, < 40 chars. Tie → later block, then longer.
- Known bug: the shell-prompt penalty `^\s*[$#>]\s+\w` also fires on a python script whose first line is `# comment`.

## 5. Generation layer
- `generate_vllm.py`: `LLM(dtype=bf16, max_model_len 12288, enable_prefix_caching)`, `llm.chat(messages, chat_template_kwargs={"enable_thinking": False})`, `SamplingParams(temperature, top_p=0.95 if T>0 else 1.0, max_tokens, seed)`; output `<out>/<task>/code.py`, `raw.txt`, `gens_shard0.jsonl` (`task, n_new_tokens, finished, has_code_block, extract`), `extract_stats.json`.
- `generate_multi.py`: one engine for many suites (`--spec` list of `{name,prompts,out,dialect}`), adds `truncated`.
- `generate_vllm_img.py`: `limit_mm_per_prompt={"image":4}`, images as JPEG data-URLs (thumbnail 768), thinking always off.

## 6. Zero-shot baselines to reproduce against

| model | suite | decode | exec | notes |
|---|---|---|---|---|
| Qwen3.5-9B | 3DCodeBench 212 | greedy, no-think, 6144 | **0/212 = 0.0%** | 165/212 start with hallucinated `bpy.ops.scene.clear()`; lenient patch → 4/212 |
| Qwen3.5-9B | 3DCodeBench | greedy, thinking | 1/212 | |
| Qwen3.5-9B | Blender 103 | greedy nt / think | 1% / 14.6% | |
| Qwen3.5-9B | CadQuery 200 | greedy nt / think | 10% (F@0.05 all 0.023) / 33% | |
| Qwen3.5-9B | OpenSCAD 50 | greedy nt / think | 42–46% / 84% | |
| Qwen3.5-9B | GLSL 200 | greedy nt / think | 39% / 35% | compile only |
| Qwen3.5-9B | three.js 40 | greedy nt / T0.7 / think | 77.5% / 67.5% / 87.5% | |
| Qwen3.8-27B | 3DCodeBench | greedy nt | 61.8%, F@0.05(all) 0.257 | thinking@12k 33.5% (truncation) |
| Qwen2.5-Coder-7B-Instruct | 3DCodeBench | greedy, HF, 6144 | 8.5%, CD 0.312, F@0.05(ok) 0.231 | |
| Qwen3-8B | any suite zero-shot | — | **not measured** | LoRA-v1: 76.4% |

Protocol notes: budgets drifted 6144 → 8192 → 12288 → 32768; REPORT §13.4 mandates reporting greedy **and** T=0.7 for OpenSCAD/GLSL/three.js; within-session noise floor: bench/Blender 0.0 pp, CadQuery 0.5, GLSL 1.5, OpenSCAD 2.0, three.js 5.0 pp; across sessions up to 6–8 pp on OpenSCAD.

## 7. Hardcoded `/wekafs/ict/hx_624/...` (parameterised in the new package)
Blender binary, OpenSCAD AppRun, glslangValidator, llmft python, ms-playwright cache, 3DCodeBench GT dir, `eval/out`, `runs`, conda activation, secrets, model dirs.

## 8. Bugs / inconsistencies found
1. Held-out Blender timeout 180 vs bench 300; held-out CRASH has no error text.
2. `metrics.py:63` comment describes an unimplemented Chamfer penalty.
3. `f@0.05_mean_all` in the held-out evals counts reference failures against the model.
4. `GLSL_PRELUDE` lacks `iFrameRate`.
5. `reextract.py`/`generate.py` use the legacy longest-block extractor.
6. `metrics.py` derives the task list from the GT dir listing, so `--limit` runs report MISSING.
7. Blender EMPTY ignores non-MESH objects that evaluate to geometry.
8. three.js TIMEOUT detected by substring; CDN dependence.
9. Decoding budget drift; image generator cannot enable thinking.
10. Three different Blender system prompts across suites.
11. Missing builders: three.js split, `_img4text` prompts.
12. Pins: Blender 5.0.1 (+scipy), OpenSCAD ≥ 2026.08 (`--export-format binstl`), cadquery 2.8.0, glslang 16.5, playwright + chromium, trimesh, scipy.

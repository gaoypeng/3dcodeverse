# LLM finetuning experiments — 3DCodeVerse text→3D-code

Full log of every experiment run on dgx03 (4×H100 80GB, GPUs 0–3), 2026-08-21 → 2026-08-25.
Narrative report with the reasoning behind each step: [`docs/REPORT.md`](docs/REPORT.md). Environment: [`docs/SETUP.md`](docs/SETUP.md).

**Task** — given a text description, generate code that builds a 3D asset, in six dialects: Blender-Python (bpy), CadQuery, OpenSCAD, GLSL (Shadertoy), three.js, and animated web pages.
**Evaluation is execution-based** — every generated program is actually run (Blender 5.0.1 headless / CadQuery / OpenSCAD AppImage / glslangValidator / headless Chromium), and the resulting mesh is compared to the ground-truth mesh with Chamfer distance and F-score at 5% / 10% of the object's largest dimension (`eval/metrics.py`). "exec rate" = fraction of tasks whose code runs and produces a non-empty result; `F@0.05 (all)` counts failures as 0.
**Training** — unmodified LLaMA-Factory (upstream `c4e09c7`, 0.9.6.dev0); every run is one YAML in `configs/lf/`.

## 0. Headline results

| model | training | 3DCodeBench exec / F@0.05(all) | Blender held-out exec / F@0.05(ok) / Chamfer | CadQuery | OpenSCAD (T=0.7) | GLSL (T=0.7) | three.js |
|---|---|---|---|---|---|---|---|
| Qwen3.5-9B | zero-shot | 0% / 0.000 | 1% / – | 10% | 46% | 39% | 77.5% |
| Qwen3.8-27B | zero-shot | 61.8% / 0.257 | 77.7% / 0.624 / 0.120 | 62.5% | 88% | 62% | 82.5% |
| Qwen3.5-9B | LoRA, 5.2k Blender samples (`v1`) | 75–82% / 0.28–0.41 | – | – | – | – | – |
| Qwen3.5-9B | `v1` + execution-feedback DPO ×2 | **96.2%** / 0.365 | – | – | – | – | – |
| Qwen3.5-9B | 6-dialect LoRA, 270k pairs (`md_xl`) | 90.1% / 0.346 | 93.2% / 0.776 / 0.080 | **97.5%** | 90% | 60% | 37.5% |
| Qwen3.5-9B | `md_xl` + geometry-feedback DPO | 88.2% / 0.351 | 93.2% / **0.800** / 0.070 | 97.0% | – | – | – |
| **Qwen3.8-27B** | **6-dialect LoRA, 104k pairs (`v2`)** | **93.4%** / **0.380** | 92.2% / **0.890** / **0.049** | 86.5% | 80% | 69% | 92.5% |
| Qwen3.8-27B | `v2` + execution-feedback DPO (756 pairs) | 91.5% / 0.358 | **93.2%** / 0.881 / 0.051 | 84.5% | **82%** | **71%** | **100%** |

Best per objective: **executability on the main benchmark** → 9B + execution-feedback DPO (96.2%) or 27B v2 (93.4%); **geometric fidelity** → 27B v2 (F@0.05 0.890, Chamfer 0.049); **CadQuery** → 9B `md_xl` (97.5%); **three.js** → 27B (92–95%).

## 1. What moved the needle (and what did not)

| lever | effect | evidence |
|---|---|---|
| **Any finetuning at all** | 0% → 75–82% on 3DCodeBench | `v1` vs base; the base model hallucinates `bpy.ops.scene.clear()` in 165/212 tasks |
| **Execution-feedback DPO** (sample → run → (OK, FAIL) pairs → DPO) | 75% → **93.9%** → **96.2%** (2 rounds) on a weak base; on a strong base it only helps where the pairs are: 27B v2 → +2 pt OpenSCAD, +2 pt GLSL, three.js 92.5%→**100%**, 3DCodeBench −1.9 pt | `dpo_exec_v1/v2`, `q27b_dpo` (only 756 pairs could be built: 20 Blender / 26 CadQuery, because v2 already passes 94%/99% of its own samples) |
| **More *distinct* code** | 67% (26k) → 84.9% (102k) → 90.1% (270k) | `md_mixed` → `md_big` → `md_xl`; the curve flattens after ~0.5 epoch of 270k |
| **Bigger base model** | zero-shot 0% → 61.8%; geometric fidelity of correct answers beats every finetuned 9B | 9B vs 27B zero-shot |
| **Dialect share inside the mix** | 27B: 57.6% → **93.4%** by raising Blender 15%→40% and adding 15.7k execution-verified bootstrapped samples | `27b v1` → `27b v2` |
| **Sampling instead of greedy** for boilerplate-heavy dialects | OpenSCAD 20%→80%, GLSL 30%→69% (27B v2); 0%→66% (9B md_openscad) | every `*_T07` row in §C |
| **Geometry-feedback DPO** (F-score ranked pairs) | first thing that moved *fidelity*: F@0.05(ok) 0.387→0.403, Blender held-out Chamfer −12% | `dpo_geo_md_xl` |
| ❌ **Caption augmentation** (3 prompts per code sample) | 3DCodeBench **90.1% → 34.4%** at equal token budget | `md_xl` vs `md_max_9b`; control `md_v3` (1 caption + bootstrap) recovers to 86.8% |
| ❌ **Upsampling / rebalancing without new code** | 84.9% → 82.5% | `md_big` → `md_bal` (Blender ×4, boot ×2) |
| ❌ **Full-parameter finetuning** | equal to LoRA at both 5k and 116k samples (71–73% vs 75–82%; 83.5% vs 82.5%), 3× the memory | `full_v1/v2/v3`, `full_md_bal` |
| ❌ **More epochs** | 2 ep 75% → 4 ep 69.3% | `v7_ep4` |
| ❌ **Small-sample SFT on a dialect the base already knows** | three.js 77.5% (base) → 30% (280-sample LoRA) | `md_threejs` |

## 2. Data

Corpus audit (`scripts/build_md_max.py`): **768,596 prompt→code pairs / 891M tokens** from every usable 3DCodeVerse source — CadQuery 390k (deepcad + articraft), GLSL 353k (shadertoy), Blender 19.6k (bioinspired3d + 3dcodebench factories + blender_distill), OpenSCAD 3.3k (thingiverse), web 1.6k (animation2code), three.js 549 — each sample expanded over its 3 caption variants.
Excluded on purpose: `3dcodebench/instances_*` (3,904 rows — 100% re-seeded instances of the 212 benchmark factories = test leakage), `articraft/urdf_*` (code column empty in the metadata), `threejs_repos` (multi-file projects), and every id in the held-out test sets.
Training mixes are sampled from it with `scripts/sample_mix.py` (per-dialect token budget, captions-per-sample cap). The most valuable single subset is the **15.7k execution-verified bootstrapped Blender samples** (`scripts/self_train_round*.sh`): removing them costs ~50 points on 3DCodeBench, and adding them back is what took the 27B from 57.6% to 93.4%.

## 3. Throughput (4×H100, LoRA r64, cutoff 8192 + packing unless noted)

| setup | tokens/s | note |
|---|---|---|
| 9B LoRA, 4 GPUs DDP | ~17,000 | 270k samples / 293M tok = 4h46m |
| 9B full FT, 4 GPUs ZeRO-3 | ~14,000 | |
| 27B LoRA, 4 GPUs DDP, cutoff 4096 | ~6,200 | 104k pairs / 75M tok = 3h36m |
| 27B LoRA, 4 GPUs ZeRO-3, cutoff 8192 | ~460 | 7.8× slower than DDP — LoRA's optimizer state is tiny, so sharding parameters only buys all-gather traffic |
| 27B inference, vLLM TP=2 | 2,500–4,100 | engine load 3.5–12 min; one engine per evaluation, not per suite (`eval/generate_multi.py`) |

## 4. Evaluating models whose native output is not just code

Reasoning/instruct models answer with prose + one or more code blocks. `eval/extract.py` strips `<think>` blocks, collects every fenced block (plus an unterminated trailing one, plus a no-fence fallback), and scores candidates by dialect markers (`import bpy`, `cq.Workplane`, `void mainImage`, `THREE.`, `module …`), fence language, and syntax validity — ties go to the *last* high-scoring block. On the 27B with thinking enabled this matters a lot: 52/212 answers had no fence at all, 103/212 had multiple blocks, and in 74/212 the best block was not the last one. On finetuned models it is a no-op (verified identical to the old rule on 702 stored generations), so no historical number changed.
Thinking mode costs 10× the tokens: at a 12k budget 204/212 of the 27B's answers were truncated (33.5% exec); at 32k it recovers to 60.8% with *better* geometry than greedy (F@0.1 0.718 vs 0.646) — i.e. thinking buys fidelity, not executability, and only if you pay for the budget.

---

## A. Training runs (from `runs/*/train_results.json` + the config that produced each)

| run | base | stage | method | data | cutoff | ep | lr | par | train loss | eval loss | wall | config |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `lf_qwen35_9b_dpo_exec_v1` | merged | dpo | lora r16 | `dpo_exec_pairs` | 8192 | 2.0 | 5e-06 | ddp | 0.46 | – | 56m | `dpo_exec_v1.yaml` |
| `lf_qwen35_9b_dpo_exec_v2` | merged_r2 | dpo | lora r16 | `dpo_exec_pairs_r2` | 8192 | 2.0 | 5e-06 | ddp | 0.622 | – | 21m | `dpo_exec_v2.yaml` |
| `lf_qwen35_9b_dpo_geo_md_xl` | merged_geo | dpo | lora r16 | `dpo_geo_pairs` | 8192 | 2.0 | 5e-06 | ddp | 0.634 | – | 1h31m | `dpo_geo_md_xl.yaml` |
| `lf_qwen35_9b_dpo_md_big` | merged | dpo | lora r16 | `dpo_md_big_pairs` | 8192 | 2.0 | 5e-06 | ddp | 0.579 | – | 30m | `dpo_md_big.yaml` |
| `lf_qwen35_9b_dpo_md_mixed` | merged | dpo | lora r16 | `dpo_md_pairs` | 8192 | 2.0 | 5e-06 | ddp | 0.618 | – | 20m | `dpo_md_mixed.yaml` |
| `lf_qwen35_9b_dpo_md_xl` | merged | dpo | lora r16 | `dpo_md_xl_pairs` | 8192 | 2.0 | 5e-06 | ddp | 0.634 | – | 26m | `dpo_md_xl.yaml` |
| `lf_qwen35_9b_full_md_bal` | Qwen3.5-9B | sft | full | `md_bal_train` | 8192 | 1.0 | 1e-05 | zero3 | 0.334 | – | 2h30m | `full_md_bal.yaml` |
| `lf_qwen35_9b_full_v2_3gpu` | Qwen3.5-9B | sft | full | `blender3d_v1_train` | 8192 | 2.0 | 1e-05 | zero3 | 0.206 | – | 51m | `qwen35_9b_full_sft_3gpu.yaml` |
| `lf_qwen35_9b_full_v3_4gpu` | Qwen3.5-9B | sft | full | `blender3d_v1_train` | 8192 | 2.0 | 1e-05 | zero3 | 0.204 | – | 11m | `qwen35_9b_full_sft_4gpu.yaml` |
| `lf_qwen35_9b_lora_md_bal` | Qwen3.5-9B | sft | lora r64 | `md_bal_train` | 8192 | 1.0 | 0.0001 | ddp | 0.364 | – | 2h26m | `lora_md_bal.yaml` |
| `lf_qwen35_9b_lora_md_big` | Qwen3.5-9B | sft | lora r64 | `md_big_train` | 8192 | 2.0 | 0.0001 | ddp | 0.433 | – | 3h25m | `lora_md_big.yaml` |
| `lf_qwen35_9b_lora_md_blender` | Qwen3.5-9B | sft | lora r16 | `md_blender_train` | 8192 | 2.0 | 0.0001 | ddp | 0.281 | – | 24m | `lora_md_blender.yaml` |
| `lf_qwen35_9b_lora_md_cadquery` | Qwen3.5-9B | sft | lora r16 | `md_cadquery_train` | 8192 | 2.0 | 0.0001 | ddp | 0.155 | – | 1h06m | `lora_md_cadquery.yaml` |
| `lf_qwen35_9b_lora_md_glsl` | Qwen3.5-9B | sft | lora r16 | `md_glsl_train` | 8192 | 2.0 | 0.0001 | ddp | 0.841 | – | 53m | `lora_md_glsl.yaml` |
| `lf_qwen35_9b_lora_md_max` | Qwen3.5-9B | sft | lora r64 | `md_max_9b_train` | 8192 | 1.0 | 0.0001 | ddp | 0.404 | – | 6h43m | `lora_md_max_9b.yaml` |
| `lf_qwen35_9b_lora_md_mixed` | Qwen3.5-9B | sft | lora r16 | `md_mixed_train` | 8192 | 2.0 | 0.0001 | ddp | 0.538 | – | 1h46m | `lora_md_mixed.yaml` |
| `lf_qwen35_9b_lora_md_openscad` | Qwen3.5-9B | sft | lora r16 | `md_openscad_train` | 8192 | 2.0 | 0.0001 | ddp | 0.88 | – | 13m | `lora_md_openscad.yaml` |
| `lf_qwen35_9b_lora_md_threejs` | Qwen3.5-9B | sft | lora r16 | `md_threejs_train` | 16384 | 3.0 | 0.0001 | ddp | 0.487 | – | 20m | `lora_md_threejs.yaml` |
| `lf_qwen35_9b_lora_md_v3` | Qwen3.5-9B | sft | lora r64 | `md_v3_train` | 8192 | 1.0 | 0.0001 | ddp | 0.457 | – | 4h50m | `lora_md_v3.yaml` |
| `lf_qwen35_9b_lora_md_xl` | Qwen3.5-9B | sft | lora r64 | `md_xl_train` | 8192 | 1.0 | 0.0001 | ddp | 0.445 | – | 4h46m | `lora_md_xl.yaml` |
| `lf_qwen35_9b_lora_v1` | Qwen3.5-9B | sft | lora r64 | `blender3d_v1_train` | 8192 | 2.0 | 0.0001 | ddp | 0.264 | – | 30m | `lora_v1.yaml` |
| `lf_qwen35_9b_lora_v10_4b` | Qwen3.5-4B | sft | lora r64 | `blender3d_v1_train` | 8192 | 2.0 | 0.0001 | ddp | 0.27 | – | 24m | `lora_v10_4b.yaml` |
| `lf_qwen35_9b_lora_v11_selftrain` | Qwen3.5-9B | sft | lora r64 | `v11_selftrain_train` | 8192 | 2.0 | 0.0001 | ddp | 0.263 | – | 15m | `lora_v11_selftrain.yaml` |
| `lf_qwen35_9b_lora_v11b_selfprompts` | Qwen3.5-9B | sft | lora r64 | `v11b_selfprompts_train` | 8192 | 2.0 | 0.0001 | ddp | 0.163 | – | 34m | `lora_v11b_selfprompts.yaml` |
| `lf_qwen35_9b_lora_v12_qwen3_8b` | Qwen3-8B | sft | lora r64 | `blender3d_v1_train` | 8192 | 2.0 | 0.0001 | ddp | 0.362 | – | 27m | `lora_v12_qwen3_8b.yaml` |
| `lf_qwen35_9b_lora_v13_boot` | Qwen3.5-9B | sft | lora r64 | `v13_boot_train` | 8192 | 2.0 | 0.0001 | ddp | 0.137 | – | 1h03m | `lora_v13_boot.yaml` |
| `lf_qwen35_9b_lora_v1_rep` | Qwen3.5-9B | sft | lora r64 | `blender3d_v1_train` | 8192 | 2.0 | 0.0001 | ddp | 0.265 | – | 17m | `lora_v1_rep.yaml` |
| `lf_qwen35_9b_lora_v2_indist` | Qwen3.5-9B | sft | lora r64 | `v2_indist_train` | 8192 | 2.0 | 0.0001 | ddp | 0.295 | – | 37m | `lora_v2_indist.yaml` |
| `lf_qwen35_9b_lora_v2b_indist16k` | Qwen3.5-9B | sft | lora r64 | `v2b_indist16k_train` | 16384 | 2.0 | 0.0001 | ddp | 0.342 | – | 58m | `lora_v2b_indist16k.yaml` |
| `lf_qwen35_9b_lora_v3_bio` | Qwen3.5-9B | sft | lora r64 | `v3_bio_train` | 8192 | 2.0 | 0.0001 | ddp | 0.203 | – | 24m | `lora_v3_bio.yaml` |
| `lf_qwen35_9b_lora_v4_cq` | Qwen3.5-9B | sft | lora r64 | `v4_cq_train` | 8192 | 2.0 | 0.0001 | ddp | 0.186 | – | 1h07m | `lora_v4_cq.yaml` |
| `lf_qwen35_9b_lora_v5_instr` | Qwen3.5-9B | sft | lora r64 | `v5_instr_train` | 8192 | 2.0 | 0.0001 | ddp | 0.264 | – | 31m | `lora_v5_instr.yaml` |
| `lf_qwen35_9b_lora_v6_detail` | Qwen3.5-9B | sft | lora r64 | `v6_detail_train` | 8192 | 2.0 | 0.0001 | ddp | 0.262 | – | 33m | `lora_v6_detail.yaml` |
| `lf_qwen35_9b_lora_v7_ep4` | Qwen3.5-9B | sft | lora r64 | `blender3d_v1_train` | 8192 | 4.0 | 0.0001 | ddp | 0.202 | – | 1h01m | `lora_v7_ep4.yaml` |
| `lf_qwen35_9b_lora_v8_r16` | Qwen3.5-9B | sft | lora r16 | `blender3d_v1_train` | 8192 | 2.0 | 0.0001 | ddp | 0.313 | – | 31m | `lora_v8_r16.yaml` |
| `lf_qwen35_9b_lora_v9_lr2e4` | Qwen3.5-9B | sft | lora r64 | `blender3d_v1_train` | 8192 | 2.0 | 0.0002 | ddp | 0.241 | – | 31m | `lora_v9_lr2e4.yaml` |
| `lf_qwen38_27b_dpo` | merged | dpo | lora r16 | `dpo_27b_pairs` | 8192 | 2.0 | 5e-06 | ddp | 0.667 | – | 34m | `dpo_27b.yaml` |
| `lf_qwen38_27b_lora_md_max` | Qwen3.8-27B | sft | lora r64 | `md_27b_max4k_train` | 4096 | 1.0 | 0.0001 | ddp | 0.383 | – | 3h51m | `lora_27b_ddp.yaml` |
| `lf_qwen38_27b_lora_v2` | Qwen3.8-27B | sft | lora r64 | `md_27b_v2s_train` | 4096 | 1.0 | 0.0001 | ddp | 0.318 | – | 3h35m | `lora_27b_v2.yaml` |

## B. 3DCodeBench (212 tasks: generate → run in Blender → export GLB → Chamfer/F vs GT)

| eval run | exec | rate | F@0.05 (all, fail=0) | F@0.05 (ok) | F@0.1 (ok) | Chamfer (ok) | mean out tok |
|---|---|---|---|---|---|---|---|
| `qwen35_9b_dpo_exec_v2` | 204/212 | 96.2% | 0.365 | 0.383 | 0.607 | 0.200 | – |
| `qwen35_9b_dpo_exec_v1` | 199/212 | 93.9% | 0.357 | 0.384 | 0.611 | 0.201 | – |
| `q27b_v2_bench` | 198/212 | 93.4% | 0.380 | 0.413 | 0.634 | 0.191 | 472 |
| `qwen35_9b_dpo_exec_v1_T07_s2` | 198/212 | 93.4% | 0.337 | 0.363 | 0.594 | 0.204 | – |
| `q27b_dpo_bench` | 194/212 | 91.5% | 0.358 | 0.395 | 0.623 | 0.195 | 499 |
| `qwen35_9b_lora_md_xl_ck2250` | 193/212 | 91.0% | 0.343 | 0.381 | 0.596 | 0.202 | – |
| `qwen35_9b_dpo_exec_v1_T07_s1` | 192/212 | 90.6% | 0.344 | 0.382 | 0.607 | 0.201 | – |
| `qwen35_9b_lora_md_xl` | 191/212 | 90.1% | 0.346 | 0.387 | 0.602 | 0.203 | – |
| `qwen35_9b_dpo_md_xl` | 190/212 | 89.6% | 0.348 | 0.388 | 0.599 | 0.204 | – |
| `dpo_geo_md_xl_T07_s1` | 187/212 | 88.2% | 0.342 | 0.392 | 0.620 | 0.195 | – |
| `qwen35_9b_dpo_exec_v1_T07_s4` | 187/212 | 88.2% | 0.345 | 0.396 | 0.610 | 0.200 | – |
| `qwen35_9b_dpo_geo_md_xl` | 187/212 | 88.2% | 0.351 | 0.403 | 0.627 | 0.195 | – |
| `qwen35_9b_lora_md_xl_ck2700` | 187/212 | 88.2% | 0.333 | 0.380 | 0.605 | 0.197 | – |
| `qwen35_9b_lora_md_xl_ck3150` | 187/212 | 88.2% | 0.342 | 0.390 | 0.607 | 0.198 | – |
| `qwen35_9b_lora_md_big_ck2420` | 186/212 | 87.7% | 0.341 | 0.391 | 0.616 | 0.199 | – |
| `md_big_T07_s3` | 185/212 | 87.3% | 0.343 | 0.396 | 0.615 | 0.199 | – |
| `qwen35_9b_dpo_md_big` | 185/212 | 87.3% | 0.325 | 0.375 | 0.601 | 0.202 | – |
| `dpo_geo_md_xl_T07_s4` | 184/212 | 86.8% | 0.325 | 0.378 | 0.610 | 0.194 | – |
| `q9b_v3_bench` | 184/212 | 86.8% | 0.326 | 0.382 | 0.600 | 0.204 | 629 |
| `qwen35_9b_dpo_exec_v1_T07_s3` | 183/212 | 86.3% | 0.336 | 0.392 | 0.616 | 0.194 | – |
| `qwen35_9b_lora_md_big_ck3080` | 183/212 | 86.3% | 0.331 | 0.388 | 0.602 | 0.204 | – |
| `qwen35_9b_lora_md_big_ck2200` | 182/212 | 85.9% | 0.330 | 0.387 | 0.613 | 0.198 | – |
| `qwen35_9b_lora_md_big_ck2640` | 182/212 | 85.9% | 0.335 | 0.392 | 0.619 | 0.195 | – |
| `qwen35_9b_lora_md_xl_ck1800` | 182/212 | 85.9% | 0.329 | 0.388 | 0.602 | 0.200 | – |
| `md_xl_T07_s3` | 181/212 | 85.4% | 0.308 | 0.364 | 0.583 | 0.209 | – |
| `qwen35_9b_lora_md_big` | 180/212 | 84.9% | 0.329 | 0.389 | 0.611 | 0.194 | – |
| `qwen35_9b_lora_md_big_ck3292` | 180/212 | 84.9% | 0.326 | 0.384 | 0.604 | 0.201 | – |
| `md_big_T07_s1` | 179/212 | 84.4% | 0.329 | 0.392 | 0.601 | 0.203 | – |
| `md_xl_T07_s1` | 179/212 | 84.4% | 0.316 | 0.377 | 0.595 | 0.201 | – |
| `qwen35_9b_lora_md_big_ck2860` | 178/212 | 84.0% | 0.315 | 0.377 | 0.596 | 0.204 | – |
| `dpo_geo_md_xl_T07_s2` | 177/212 | 83.5% | 0.320 | 0.388 | 0.605 | 0.202 | – |
| `qwen35_9b_full_md_bal` | 177/212 | 83.5% | 0.327 | 0.392 | 0.623 | 0.191 | – |
| `md_big_T07_s4` | 176/212 | 83.0% | 0.323 | 0.394 | 0.603 | 0.201 | – |
| `md_xl_T07_s4` | 176/212 | 83.0% | 0.309 | 0.379 | 0.599 | 0.199 | – |
| `qwen35_9b_lora_md_xl_ck1350` | 176/212 | 83.0% | 0.321 | 0.389 | 0.618 | 0.199 | – |
| `qwen35_9b_lora_md_bal` | 175/212 | 82.5% | 0.331 | 0.406 | 0.626 | 0.192 | – |
| `dpo_geo_md_xl_T07_s3` | 174/212 | 82.1% | 0.308 | 0.379 | 0.601 | 0.202 | – |
| `qwen35_9b_lora_v11_selftrain` | 174/212 | 82.1% | 0.331 | 0.408 | 0.622 | 0.192 | – |
| `md_big_T07_s2` | 173/212 | 81.6% | 0.324 | 0.404 | 0.633 | 0.191 | – |
| `qwen35_9b_lora_v11b_selfprompts` | 171/212 | 80.7% | 0.318 | 0.394 | 0.611 | 0.196 | – |
| `qwen35_9b_lora_v1_T07_s6` | 168/212 | 79.2% | 0.315 | 0.400 | 0.616 | 0.195 | – |
| `qwen35_9b_lora_v1_ckpt100` | 168/212 | 79.2% | 0.301 | 0.385 | 0.600 | 0.198 | – |
| `md_xl_T07_s2` | 166/212 | 78.3% | 0.303 | 0.390 | 0.607 | 0.199 | – |
| `qwen35_9b_lora_v1_rep` | 166/212 | 78.3% | 0.302 | 0.393 | 0.607 | 0.196 | – |
| `qwen35_9b_lora_v13_boot` | 163/212 | 76.9% | 0.308 | 0.403 | 0.619 | 0.200 | – |
| `qwen35_9b_lora_v12_qwen3_8b` | 162/212 | 76.4% | 0.277 | 0.364 | 0.560 | 0.220 | – |
| `qwen35_9b_lora_v1_T07_s1` | 162/212 | 76.4% | 0.279 | 0.372 | 0.593 | 0.201 | – |
| `qwen35_9b_lora_v8_r16` | 162/212 | 76.4% | 0.284 | 0.372 | 0.590 | 0.210 | – |
| `desc_qwen35_9b_lora_v1` | 161/212 | 75.9% | 0.294 | 0.387 | 0.607 | 0.197 | – |
| `qwen35_9b_lora_v9_lr2e4` | 161/212 | 75.9% | 0.286 | 0.377 | 0.589 | 0.204 | – |
| `qwen35_9b_lora_md_xl_ck450` | 159/212 | 75.0% | 0.290 | 0.386 | 0.601 | 0.197 | – |
| `qwen35_9b_lora_md_xl_ck900` | 159/212 | 75.0% | 0.286 | 0.384 | 0.599 | 0.202 | – |
| `qwen35_9b_lora_v1` | 159/212 | 75.0% | 0.285 | 0.382 | 0.598 | 0.201 | – |
| `qwen35_9b_lora_v1_T07_s4` | 159/212 | 75.0% | 0.286 | 0.386 | 0.612 | 0.199 | – |
| `qwen35_9b_lora_v4_cq` | 157/212 | 74.1% | 0.274 | 0.375 | 0.596 | 0.201 | – |
| `qwen35_9b_lora_v3_bio` | 156/212 | 73.6% | 0.278 | 0.380 | 0.597 | 0.205 | – |
| `qwen35_9b_full_v3_4gpu` | 154/212 | 72.6% | 0.272 | 0.377 | 0.585 | 0.207 | – |
| `qwen35_9b_lora_v3_bio_ckpt100` | 154/212 | 72.6% | 0.264 | 0.368 | 0.588 | 0.215 | – |
| `qwen35_9b_lora_v3_bio_ckpt50` | 152/212 | 71.7% | 0.267 | 0.375 | 0.586 | 0.211 | – |
| `qwen35_9b_full_v1_ckpt60` | 151/212 | 71.2% | 0.269 | 0.381 | 0.604 | 0.200 | – |
| `qwen35_9b_full_v2_3gpu` | 151/212 | 71.2% | 0.258 | 0.365 | 0.589 | 0.205 | – |
| `qwen35_9b_lora_v10_4b` | 148/212 | 69.8% | 0.262 | 0.378 | 0.614 | 0.202 | – |
| `qwen35_9b_lora_v7_ep4` | 147/212 | 69.3% | 0.262 | 0.381 | 0.605 | 0.205 | – |
| `desc_qwen35_9b_lora_v6_detail` | 146/212 | 68.9% | 0.262 | 0.381 | 0.588 | 0.207 | – |
| `qwen35_9b_lora_v5_instr` | 143/212 | 67.5% | 0.270 | 0.403 | 0.609 | 0.199 | – |
| `qwen35_9b_lora_md_mixed` | 142/212 | 67.0% | 0.232 | 0.349 | 0.569 | 0.215 | – |
| `qwen35_9b_lora_v1_T07_s2` | 141/212 | 66.5% | 0.252 | 0.382 | 0.603 | 0.197 | – |
| `qwen35_9b_lora_v1_T07_s5` | 140/212 | 66.0% | 0.281 | 0.426 | 0.646 | 0.185 | – |
| `qwen35_9b_dpo_md_mixed` | 139/212 | 65.6% | 0.220 | 0.336 | 0.563 | 0.218 | – |
| `qwen35_9b_lora_v6_detail` | 139/212 | 65.6% | 0.239 | 0.368 | 0.585 | 0.209 | – |
| `qwen35_9b_lora_v1_T07_s3` | 137/212 | 64.6% | 0.259 | 0.401 | 0.619 | 0.195 | – |
| `qwen35_9b_lora_md_blender` | 135/212 | 63.7% | 0.230 | 0.364 | 0.561 | 0.218 | – |
| `q27b_nothink_bench` | 131/212 | 61.8% | 0.257 | 0.429 | 0.638 | 0.194 | 2619 |
| `qwen35_9b_lora_v2_indist` | 131/212 | 61.8% | 0.252 | 0.411 | 0.642 | 0.186 | – |
| `q27b_think32k_bench` | 129/212 | 60.9% | 0.300 | 0.496 | 0.718 | 0.152 | 25699 |
| `qwen35_4b_lora_T07_s1` | 128/212 | 60.4% | 0.209 | 0.354 | 0.566 | 0.222 | – |
| `qwen35_9b_lora_v1_T07_s7` | 126/212 | 59.4% | 0.227 | 0.382 | 0.598 | 0.203 | – |
| `q27b_ft_bench` | 122/212 | 57.6% | 0.249 | 0.440 | 0.646 | 0.186 | 1984 |
| `qwen35_4b_lora_T07_s2` | 121/212 | 57.1% | 0.198 | 0.350 | 0.571 | 0.206 | – |
| `qwen35_9b_lora_v2b_indist16k` | 114/212 | 53.8% | 0.223 | 0.414 | 0.622 | 0.197 | – |
| `q9b_md_max_bench` | 73/212 | 34.4% | 0.121 | 0.352 | 0.539 | 0.231 | 3983 |
| `q27b_think_bench` | 71/212 | 33.5% | 0.167 | 0.506 | 0.738 | 0.149 | 12168 |
| `qwen25coder7b_base` | 18/212 | 8.5% | 0.014 | 0.231 | 0.394 | 0.312 | – |
| `qwen35_9b_lora_md_openscad` | 8/212 | 3.8% | 0.012 | 0.308 | 0.483 | 0.316 | – |
| `qwen35_9b_base_nothink_lenient` | 4/212 | 1.9% | 0.004 | 0.239 | 0.486 | 0.265 | – |
| `_vllm_smoke` | 1/212 | 0.5% | 0.002 | 0.353 | 0.481 | 0.251 | – |
| `q9b_think_bench` | 1/212 | 0.5% | 0.001 | 0.210 | 0.336 | 0.334 | 5340 |
| `desc_qwen35_9b_base` | 0/212 | 0.0% | 0.000 | – | – | – | – |
| `q9b_nothink_bench` | 0/212 | 0.0% | 0.000 | – | – | – | 3844 |
| `qwen35_4b_lora_T07_s3` | 0/212 | 0.0% | 0.000 | – | – | – | – |
| `qwen35_4b_lora_T07_s4` | 0/212 | 0.0% | 0.000 | – | – | – | – |
| `qwen35_9b_base_nothink` | 0/212 | 0.0% | 0.000 | – | – | – | – |
| `qwen35_9b_lora_md_cadquery` | 0/212 | 0.0% | 0.000 | – | – | – | – |
| `qwen35_9b_lora_md_glsl` | 0/212 | 0.0% | 0.000 | – | – | – | – |
| `qwen35_9b_lora_v1_T07_s8` | 0/212 | 0.0% | 0.000 | – | – | – | – |

## C. Held-out dialect sets (per-dialect executors; reference code is executed the same way)


### blender_heldout (n=103, reference exec OK=96)

| eval run | exec | rate | F@0.05 (all) | F@0.05 (ok) | Chamfer (ok) |
|---|---|---|---|---|---|
| `md_dpo_geo_md_xl_blender` | 96/103 | 93.2% | 0.746 | 0.800 | 0.070 |
| `md_dpo_md_big_blender` | 96/103 | 93.2% | 0.730 | 0.783 | 0.077 |
| `md_dpo_md_xl_blender` | 96/103 | 93.2% | 0.725 | 0.778 | 0.078 |
| `md_full_md_bal_blender` | 96/103 | 93.2% | 0.856 | 0.919 | 0.045 |
| `md_md_big_ck3292_blender` | 96/103 | 93.2% | 0.737 | 0.790 | 0.074 |
| `md_md_xl_blender` | 96/103 | 93.2% | 0.723 | 0.776 | 0.080 |
| `md_md_xl_ck2700_blender` | 96/103 | 93.2% | 0.711 | 0.763 | 0.083 |
| `md_md_xl_ck3150_blender` | 96/103 | 93.2% | 0.702 | 0.753 | 0.079 |
| `md_v1_blender` | 96/103 | 93.2% | 0.725 | 0.778 | 0.075 |
| `q27b_dpo_blender` | 96/103 | 93.2% | 0.813 | 0.872 | 0.052 |
| `md_md_big_ck2200_blender` | 95/103 | 92.2% | 0.752 | 0.815 | 0.068 |
| `md_md_big_ck2420_blender` | 95/103 | 92.2% | 0.726 | 0.787 | 0.073 |
| `md_md_big_ck3080_blender` | 95/103 | 92.2% | 0.728 | 0.790 | 0.074 |
| `md_md_xl_ck1350_blender` | 95/103 | 92.2% | 0.635 | 0.711 | 0.097 |
| `md_md_xl_ck2250_blender` | 95/103 | 92.2% | 0.692 | 0.751 | 0.081 |
| `q27b_v2_blender` | 95/103 | 92.2% | 0.821 | 0.890 | 0.049 |
| `q9b_md_max_blender` | 95/103 | 92.2% | 0.751 | 0.814 | 0.066 |
| `md_md_big_ck2640_blender` | 94/103 | 91.3% | 0.717 | 0.785 | 0.073 |
| `md_md_big_ck2860_blender` | 94/103 | 91.3% | 0.733 | 0.803 | 0.073 |
| `md_md_mixed_blender` | 94/103 | 91.3% | 0.708 | 0.775 | 0.081 |
| `md_md_xl_ck900_blender` | 94/103 | 91.3% | 0.648 | 0.710 | 0.094 |
| `md_dpo_md_mixed_blender` | 93/103 | 90.3% | 0.694 | 0.769 | 0.084 |
| `md_md_big_blender` | 93/103 | 90.3% | 0.705 | 0.781 | 0.077 |
| `q27b_ft_blender` | 93/103 | 90.3% | 0.790 | 0.876 | 0.054 |
| `md_md_bal_blender` | 92/103 | 89.3% | 0.729 | 0.816 | 0.071 |
| `md_md_xl_ck1800_blender` | 92/103 | 89.3% | 0.614 | 0.688 | 0.110 |
| `md_md_blender_blender` | 91/103 | 88.3% | 0.647 | 0.732 | 0.089 |
| `md_md_xl_ck450_blender` | 90/103 | 87.4% | 0.545 | 0.630 | 0.106 |
| `q9b_v3_blender` | 90/103 | 87.4% | 0.639 | 0.732 | 0.090 |
| `q27b_nothink_blender` | 80/103 | 77.7% | 0.472 | 0.624 | 0.120 |
| `q27b_think_blender` | 78/103 | 75.7% | 0.500 | 0.687 | 0.092 |
| `md_md_openscad_blender` | 17/103 | 16.5% | 0.075 | 0.454 | 0.202 |
| `q9b_think_blender` | 15/103 | 14.6% | 0.056 | 0.387 | 0.236 |
| `md_md_glsl_blender` | 3/103 | 2.9% | 0.017 | 0.579 | 0.222 |
| `md_base_blender` | 1/103 | 1.0% | 0.003 | 0.256 | 0.336 |
| `q9b_nothink_blender` | 1/103 | 1.0% | 0.003 | 0.256 | 0.337 |
| `md_md_cadquery_blender` | 0/103 | 0.0% | 0.000 | – | – |

### cadquery (n=200, reference exec OK=199)

| eval run | exec | rate | F@0.05 (all) | F@0.05 (ok) | Chamfer (ok) |
|---|---|---|---|---|---|
| `md_md_xl_ck3150_T07_cadquery` | 199/200 | 99.5% | 0.284 | 0.286 | 0.263 |
| `md_dpo_md_big_T07_cadquery` | 197/200 | 98.5% | 0.267 | 0.271 | 0.270 |
| `md_dpo_md_xl_T07_cadquery` | 197/200 | 98.5% | 0.313 | 0.320 | 0.252 |
| `md_md_big_ck2420_T07_cadquery` | 197/200 | 98.5% | 0.273 | 0.279 | 0.265 |
| `md_md_big_ck3080_T07_cadquery` | 197/200 | 98.5% | 0.267 | 0.272 | 0.270 |
| `md_md_big_ck3292_T07_cadquery` | 197/200 | 98.5% | 0.271 | 0.275 | 0.269 |
| `md_md_xl_ck1800_T07_cadquery` | 197/200 | 98.5% | 0.286 | 0.290 | 0.270 |
| `md_dpo_md_mixed_T07_cadquery` | 196/200 | 98.0% | 0.282 | 0.288 | 0.262 |
| `md_md_big_ck2860_T07_cadquery` | 196/200 | 98.0% | 0.263 | 0.269 | 0.269 |
| `md_md_mixed_T07_cadquery` | 196/200 | 98.0% | 0.273 | 0.279 | 0.266 |
| `md_md_xl_ck450_T07_cadquery` | 196/200 | 98.0% | 0.262 | 0.268 | 0.277 |
| `md_dpo_md_xl_cadquery` | 195/200 | 97.5% | 0.323 | 0.331 | 0.254 |
| `md_md_big_ck2200_T07_cadquery` | 195/200 | 97.5% | 0.298 | 0.305 | 0.254 |
| `md_md_xl_cadquery` | 195/200 | 97.5% | 0.321 | 0.330 | 0.257 |
| `md_md_xl_ck2700_T07_cadquery` | 195/200 | 97.5% | 0.276 | 0.283 | 0.267 |
| `md_md_xl_ck900_T07_cadquery` | 195/200 | 97.5% | 0.254 | 0.261 | 0.279 |
| `q9b_md_max_cadquery` | 195/200 | 97.5% | 0.299 | 0.307 | 0.264 |
| `md_dpo_geo_md_xl_cadquery` | 194/200 | 97.0% | 0.325 | 0.335 | 0.253 |
| `q9b_v3_cadquery` | 193/200 | 96.5% | 0.276 | 0.286 | 0.288 |
| `md_md_big_ck2640_T07_cadquery` | 192/200 | 96.0% | 0.248 | 0.260 | 0.268 |
| `md_md_xl_ck2250_T07_cadquery` | 192/200 | 96.0% | 0.303 | 0.315 | 0.249 |
| `md_md_xl_ck1350_T07_cadquery` | 191/200 | 95.5% | 0.249 | 0.260 | 0.276 |
| `md_md_big_cadquery` | 190/200 | 95.0% | 0.295 | 0.311 | 0.267 |
| `md_dpo_md_big_cadquery` | 188/200 | 94.0% | 0.279 | 0.297 | 0.272 |
| `md_full_md_bal_cadquery` | 183/200 | 91.5% | 0.302 | 0.330 | 0.260 |
| `md_md_bal_cadquery` | 183/200 | 91.5% | 0.279 | 0.304 | 0.268 |
| `q27b_v2_cadquery` | 173/200 | 86.5% | 0.218 | 0.252 | 0.298 |
| `q27b_dpo_cadquery` | 169/200 | 84.5% | 0.228 | 0.269 | 0.287 |
| `q27b_ft_cadquery` | 162/200 | 81.0% | 0.233 | 0.288 | 0.271 |
| `md_dpo_md_mixed_cadquery` | 160/200 | 80.0% | 0.246 | 0.308 | 0.266 |
| `md_md_mixed_cadquery` | 157/200 | 78.5% | 0.239 | 0.305 | 0.274 |
| `md_md_cadquery_cadquery` | 146/200 | 73.0% | 0.223 | 0.306 | 0.267 |
| `q27b_nothink_cadquery` | 125/200 | 62.5% | 0.173 | 0.279 | 0.242 |
| `md_md_blender_cadquery` | 84/200 | 42.0% | 0.109 | 0.259 | 0.242 |
| `q27b_think_cadquery` | 70/200 | 35.0% | 0.089 | 0.255 | 0.248 |
| `q9b_think_cadquery` | 66/200 | 33.0% | 0.089 | 0.271 | 0.269 |
| `md_v1_cadquery` | 58/200 | 29.0% | 0.081 | 0.280 | 0.237 |
| `md_md_openscad_cadquery` | 53/200 | 26.5% | 0.064 | 0.241 | 0.275 |
| `md_base_cadquery` | 20/200 | 10.0% | 0.023 | 0.234 | 0.231 |
| `q9b_nothink_cadquery` | 20/200 | 10.0% | 0.023 | 0.234 | 0.231 |
| `md_md_glsl_cadquery` | 14/200 | 7.0% | 0.020 | 0.291 | 0.242 |

### openscad (n=50, reference exec OK=50)

| eval run | exec | rate | F@0.05 (all) | F@0.05 (ok) | Chamfer (ok) |
|---|---|---|---|---|---|
| `md_md_xl_ck3150_T07_openscad` | 46/50 | 92.0% | 0.377 | 0.410 | 0.202 |
| `md_md_xl_ck2700_T07_openscad` | 45/50 | 90.0% | 0.340 | 0.378 | 0.213 |
| `q27b_nothink_openscad` | 44/50 | 88.0% | 0.446 | 0.506 | 0.165 |
| `md_dpo_md_xl_T07_openscad` | 43/50 | 86.0% | 0.337 | 0.392 | 0.200 |
| `q9b_think_openscad` | 42/50 | 84.0% | 0.370 | 0.440 | 0.179 |
| `md_dpo_md_big_T07_openscad` | 41/50 | 82.0% | 0.321 | 0.391 | 0.225 |
| `q27b_v2_T07_openscad` | 40/50 | 80.0% | 0.321 | 0.402 | 0.194 |
| `md_md_xl_ck2250_T07_openscad` | 39/50 | 78.0% | 0.290 | 0.372 | 0.215 |
| `md_full_md_bal_openscad` | 38/50 | 76.0% | 0.367 | 0.483 | 0.169 |
| `md_dpo_md_mixed_T07_openscad` | 36/50 | 72.0% | 0.266 | 0.370 | 0.217 |
| `md_md_big_ck2640_T07_openscad` | 35/50 | 70.0% | 0.305 | 0.436 | 0.197 |
| `md_md_mixed_T07_openscad` | 35/50 | 70.0% | 0.261 | 0.373 | 0.204 |
| `md_md_big_ck3292_T07_openscad` | 34/50 | 68.0% | 0.291 | 0.428 | 0.198 |
| `md_md_xl_ck1800_T07_openscad` | 34/50 | 68.0% | 0.297 | 0.437 | 0.181 |
| `md_md_xl_ck450_T07_openscad` | 34/50 | 68.0% | 0.241 | 0.354 | 0.225 |
| `md_md_openscad_T07_openscad` | 33/50 | 66.0% | 0.210 | 0.319 | 0.241 |
| `md_md_xl_ck1350_T07_openscad` | 33/50 | 66.0% | 0.241 | 0.365 | 0.223 |
| `md_md_big_ck2200_T07_openscad` | 32/50 | 64.0% | 0.308 | 0.481 | 0.169 |
| `md_md_big_ck2420_T07_openscad` | 31/50 | 62.0% | 0.241 | 0.389 | 0.223 |
| `md_md_big_ck3080_T07_openscad` | 29/50 | 58.0% | 0.242 | 0.418 | 0.187 |
| `md_md_bal_openscad` | 28/50 | 56.0% | 0.277 | 0.494 | 0.152 |
| `md_md_xl_ck900_T07_openscad` | 28/50 | 56.0% | 0.194 | 0.346 | 0.210 |
| `q9b_v3_T07_openscad` | 27/50 | 54.0% | 0.185 | 0.344 | 0.228 |
| `q27b_think_openscad` | 26/50 | 52.0% | 0.294 | 0.565 | 0.168 |
| `md_dpo_md_xl_openscad` | 25/50 | 50.0% | 0.263 | 0.526 | 0.161 |
| `md_md_big_ck2860_T07_openscad` | 24/50 | 48.0% | 0.190 | 0.395 | 0.192 |
| `q9b_nothink_openscad` | 23/50 | 46.0% | 0.222 | 0.483 | 0.183 |
| `md_base_openscad` | 21/50 | 42.0% | 0.196 | 0.467 | 0.176 |
| `md_md_xl_openscad` | 21/50 | 42.0% | 0.210 | 0.499 | 0.171 |
| `q27b_dpo_openscad` | 18/50 | 36.0% | 0.167 | 0.463 | 0.168 |
| `q27b_v2_openscad` | 17/50 | 34.0% | 0.164 | 0.483 | 0.169 |
| `md_v1_openscad` | 14/50 | 28.0% | 0.109 | 0.389 | 0.200 |
| `q27b_ft_openscad` | 10/50 | 20.0% | 0.118 | 0.593 | 0.146 |
| `md_dpo_md_mixed_openscad` | 7/50 | 14.0% | 0.063 | 0.448 | 0.168 |
| `md_md_cadquery_openscad` | 7/50 | 14.0% | 0.064 | 0.456 | 0.159 |
| `md_dpo_md_big_openscad` | 5/50 | 10.0% | 0.049 | 0.493 | 0.180 |
| `md_md_blender_openscad` | 5/50 | 10.0% | 0.024 | 0.244 | 0.257 |
| `md_md_mixed_openscad` | 3/50 | 6.0% | 0.035 | 0.576 | 0.135 |
| `md_md_big_openscad` | 2/50 | 4.0% | 0.021 | 0.512 | 0.186 |
| `md_md_glsl_openscad` | 2/50 | 4.0% | 0.022 | 0.548 | 0.217 |
| `q9b_md_max_openscad` | 2/50 | 4.0% | 0.026 | 0.639 | 0.188 |
| `q9b_v3_openscad` | 1/50 | 2.0% | 0.003 | 0.159 | 0.468 |
| `md_md_openscad_openscad` | 0/50 | 0.0% | 0.000 | – | – |

### glsl (n=200, reference exec OK=178)

| eval run | exec | rate | F@0.05 (all) | F@0.05 (ok) | Chamfer (ok) |
|---|---|---|---|---|---|
| `md_dpo_md_big_T07_glsl` | 145/200 | 72.5% | 0.000 | – | – |
| `q27b_v2_T07_glsl` | 138/200 | 69.0% | 0.000 | – | – |
| `md_md_big_ck2640_T07_glsl` | 134/200 | 67.0% | 0.000 | – | – |
| `md_md_big_ck3080_T07_glsl` | 128/200 | 64.0% | 0.000 | – | – |
| `md_dpo_md_xl_T07_glsl` | 127/200 | 63.5% | 0.000 | – | – |
| `md_md_big_ck3292_T07_glsl` | 127/200 | 63.5% | 0.000 | – | – |
| `q27b_nothink_glsl` | 124/200 | 62.0% | 0.000 | – | – |
| `md_md_big_ck2860_T07_glsl` | 121/200 | 60.5% | 0.000 | – | – |
| `md_md_big_ck2200_T07_glsl` | 119/200 | 59.5% | 0.000 | – | – |
| `md_md_xl_ck2250_T07_glsl` | 119/200 | 59.5% | 0.000 | – | – |
| `q9b_v3_T07_glsl` | 118/200 | 59.0% | 0.000 | – | – |
| `md_md_xl_ck3150_T07_glsl` | 117/200 | 58.5% | 0.000 | – | – |
| `md_md_big_ck2420_T07_glsl` | 116/200 | 58.0% | 0.000 | – | – |
| `md_md_xl_ck2700_T07_glsl` | 115/200 | 57.5% | 0.000 | – | – |
| `md_md_xl_ck900_T07_glsl` | 113/200 | 56.5% | 0.000 | – | – |
| `md_md_xl_ck1350_T07_glsl` | 107/200 | 53.5% | 0.000 | – | – |
| `md_dpo_md_big_glsl` | 96/200 | 48.0% | 0.000 | – | – |
| `md_dpo_md_mixed_T07_glsl` | 94/200 | 47.0% | 0.000 | – | – |
| `md_md_xl_ck450_T07_glsl` | 89/200 | 44.5% | 0.000 | – | – |
| `md_md_mixed_T07_glsl` | 85/200 | 42.5% | 0.000 | – | – |
| `md_md_big_glsl` | 84/200 | 42.0% | 0.000 | – | – |
| `md_md_glsl_T07_glsl` | 84/200 | 42.0% | 0.000 | – | – |
| `md_dpo_md_xl_glsl` | 81/200 | 40.5% | 0.000 | – | – |
| `md_base_glsl` | 78/200 | 39.0% | 0.000 | – | – |
| `q9b_nothink_glsl` | 78/200 | 39.0% | 0.000 | – | – |
| `md_md_xl_glsl` | 75/200 | 37.5% | 0.000 | – | – |
| `md_md_blender_glsl` | 73/200 | 36.5% | 0.000 | – | – |
| `md_md_xl_ck1800_T07_glsl` | 73/200 | 36.5% | 0.000 | – | – |
| `q9b_think_glsl` | 70/200 | 35.0% | 0.000 | – | – |
| `q27b_dpo_glsl` | 69/200 | 34.5% | 0.000 | – | – |
| `q27b_ft_glsl` | 69/200 | 34.5% | 0.000 | – | – |
| `q9b_md_max_glsl` | 67/200 | 33.5% | 0.000 | – | – |
| `md_full_md_bal_glsl` | 66/200 | 33.0% | 0.000 | – | – |
| `q9b_v3_glsl` | 66/200 | 33.0% | 0.000 | – | – |
| `q27b_v2_glsl` | 60/200 | 30.0% | 0.000 | – | – |
| `md_md_glsl_glsl` | 59/200 | 29.5% | 0.000 | – | – |
| `md_v1_glsl` | 57/200 | 28.5% | 0.000 | – | – |
| `md_md_glsl_8k_glsl` | 56/200 | 28.0% | 0.000 | – | – |
| `md_dpo_md_mixed_glsl` | 49/200 | 24.5% | 0.000 | – | – |
| `md_md_bal_glsl` | 48/200 | 24.0% | 0.000 | – | – |
| `q27b_think_glsl` | 39/200 | 19.5% | 0.000 | – | – |
| `md_md_mixed_glsl` | 37/200 | 18.5% | 0.000 | – | – |
| `md_md_cadquery_glsl` | 6/200 | 3.0% | 0.000 | – | – |
| `md_md_openscad_glsl` | 2/200 | 1.0% | 0.000 | – | – |

### threejs (n=40, reference exec OK=–)

| eval run | exec | rate | F@0.05 (all) | F@0.05 (ok) | Chamfer (ok) |
|---|---|---|---|---|---|
| `q27b_dpo_threejs` | 40/40 | 100.0% | – | – | – |
| `q27b_ft_threejs` | 38/40 | 95.0% | – | – | – |
| `q27b_v2_threejs` | 37/40 | 92.5% | – | – | – |
| `q9b_think_threejs` | 35/40 | 87.5% | – | – | – |
| `q27b_nothink_threejs` | 33/40 | 82.5% | – | – | – |
| `md_base_threejs` | 31/40 | 77.5% | – | – | – |
| `q9b_nothink_threejs` | 31/40 | 77.5% | – | – | – |
| `md_base_T07_threejs` | 27/40 | 67.5% | – | – | – |
| `md_md_xl_T07_threejs` | 21/40 | 52.5% | – | – | – |
| `md_md_threejs_T07_threejs` | 20/40 | 50.0% | – | – | – |
| `q9b_md_max_threejs` | 17/40 | 42.5% | – | – | – |
| `md_md_xl_threejs` | 15/40 | 37.5% | – | – | – |
| `q9b_v3_threejs` | 13/40 | 32.5% | – | – | – |
| `md_md_threejs_threejs` | 12/40 | 30.0% | – | – | – |
| `md_md_threejs_threejs_32k` | 12/40 | 30.0% | – | – | – |
| `q27b_think_threejs` | 0/40 | 0.0% | – | – | – |

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
| Qwen3.8-27B | `v3` — CadQuery doubled, rank 32 / cutoff 3072 | 91.5% / 0.363 | 93.2% / 0.768 / 0.081 | 85.0% | 62% | 65.5% | 90.0% |

Best per objective: **executability on the main benchmark** → 9B + execution-feedback DPO (96.2%) or 27B v2 (93.4%); **geometric fidelity** → 27B v2 (F@0.05 0.890, Chamfer 0.049); **CadQuery** → 9B `md_xl` (97.5%); **three.js** → 27B (92–95%).

## 1. What moved the needle (and what did not)

| lever | effect | evidence |
|---|---|---|
| **Any finetuning at all** | 0% → 75–82% on 3DCodeBench | `v1` vs base; the base model hallucinates `bpy.ops.scene.clear()` in 165/212 tasks |
| **Execution-feedback DPO** (sample → run → (OK, FAIL) pairs → DPO) | 75% → **93.9%** → **96.2%** (2 rounds) on a weak base; on a strong base it only helps where the pairs are: 27B v2 → +2 pt OpenSCAD, +2 pt GLSL, three.js 92.5%→**100%**, 3DCodeBench −1.9 pt | `dpo_exec_v1/v2`, `q27b_dpo` (only 756 pairs could be built: 20 Blender / 26 CadQuery, because v2 already passes 94%/99% of its own samples) |
| **More *distinct* code** | 67% (26k) → 84.9% (102k) → 90.1% (270k) | `md_mixed` → `md_big` → `md_xl`; the curve flattens after ~0.5 epoch of 270k |
| **Bigger base model** | zero-shot 0% → 61.8%; geometric fidelity of correct answers beats every finetuned 9B | 9B vs 27B zero-shot |
| **Dialect share inside the mix** | 27B: 57.6% → **93.4%** by raising Blender 15%→40% and adding 15.7k execution-verified bootstrapped samples. The reverse does *not* hold: doubling CadQuery (36k→65k pairs) left CadQuery flat (86.5%→85.0%) and cost every other suite | `27b v1` → `v2` → `v3` |
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


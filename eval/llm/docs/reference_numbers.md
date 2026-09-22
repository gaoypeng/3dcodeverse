# Reference numbers — what a new model should be compared against

Three sources, three protocols. Never mix columns across sections without converting (see `metrics.md`).

## A. 3DCodeBench paper, Table 1 (arXiv:2606.01057, `~/paper_refs/3dcodebench/tables_main/main_results.tex`)

Official protocol: official system prompt (raw python), T=0.7, thinking at each model's best level, Blender 5.0,
4-view Cycles renders. **Each value is the mean over the text-to-3D and image-to-3D tracks; SigLIP-2 / DINOv3 /
Chamfer are conditional (executed instances only).** Chamfer = squared, unit-sphere, 8 192 pts.

| model | Exec ↑ | SigLIP-2 ↑ | DINOv3 ↑ | Chamfer ↓ | Uni3D 3D–3D ↑ | Elo |
|---|---|---|---|---|---|---|
| Gemini 3 Flash | 0.481 | 0.810 | 0.528 | 0.067 | 0.543 | 1039 |
| Gemini 3.1 Flash Lite | 0.575 | 0.778 | 0.496 | 0.077 | 0.445 | 877 |
| Gemini 3.1 Pro | 0.725 | 0.824 | 0.569 | 0.069 | 0.567 | 1147 |
| Gemini 3.5 Flash | 0.464 | 0.824 | 0.563 | 0.068 | 0.519 | 1119 |
| Gemma 4 26B | 0.517 | 0.786 | 0.483 | 0.077 | 0.435 | 859 |
| Gemma 4 31B | 0.582 | 0.801 | 0.518 | 0.076 | 0.494 | 952 |
| Claude Haiku 4.5 | 0.502 | 0.761 | 0.413 | 0.095 | 0.363 | 799 |
| Claude Sonnet 4.6 | 0.804 | 0.813 | 0.551 | 0.068 | 0.525 | 1015 |
| Claude Opus 4.7 | 0.910 | 0.814 | 0.545 | 0.067 | 0.490 | 1006 |
| GPT-5.4 mini | 0.731 | 0.803 | 0.526 | 0.070 | 0.506 | 951 |
| GPT-5.4 | 0.866 | 0.817 | 0.560 | 0.064 | 0.552 | 1074 |
| GPT-5.5 | 0.906 | 0.834 | 0.576 | 0.059 | 0.562 | 1163 |

Single-shot (no thinking-level search) appendix table differs by ≤ 0.07 exec (Gemini 3 Flash 0.547, 3.1 Pro 0.698,
3.5 Flash 0.479; the rest identical). Gemini 2.5 Pro and GPT-5.4 nano were excluded (exec < 10 %).

Paper findings to keep in mind: SigLIP-2 view-paired (cond.) has Pearson r = 0.964 and DINOv3 Spearman ρ = 0.972
against the human Elo; multi-turn traceback feedback lifts exec from 0.70 to 0.97 without changing conditional
quality; coding-agent harnesses do the same (0.747 → 0.973, conditional SigLIP-2 −0.010).

The raw outputs behind this table are on the Hub (`3DCodeBench_ModelLogs/data/text_to_3D.parquet` etc.);
`rescore_logs.py` replays them through this package — see §C for the local re-score.

## B. finetune report protocol (`finetune/docs/REPORT.md`, `llm_finetune_exps.md`)

Fenced-block system prompt, greedy, no-think, 6 144–8 192 new tokens, Blender 5.0.1, exec = non-empty mesh + GLB
export, F@τ on unit-bbox point clouds (10 k), reported as F(all) with failures = 0.

| model | 3DCodeBench exec | F@0.05 all | Blender 103 | CadQuery 200 | OpenSCAD 50 | GLSL 200 | three.js 40 |
|---|---|---|---|---|---|---|---|
| Qwen3.5-9B zero-shot (no-think) | 0.0 % | 0.000 | 1 % | 10 % | 42–46 % | 39 % | 77.5 % |
| Qwen3.5-9B zero-shot (thinking) | 0.5 % | – | 14.6 % | 33 % | 84 % | 35 % | 87.5 % |
| Qwen2.5-Coder-7B-Instruct zero-shot | 8.5 % | 0.014 | – | – | – | – | – |
| Qwen3.8-27B zero-shot (no-think) | 61.8 % | 0.257 | 77.7 % | 62.5 % | 88 % | 62 % | 82.5 % |
| Qwen3.5-9B LoRA v1 (5.2 k Blender) | 75.0 % | 0.285 | – | – | – | – | – |
| Qwen3-8B LoRA v1 | 76.4 % | 0.277 | – | – | – | – | – |
| Qwen3.5-9B + exec-feedback DPO r2 | 96.2 % | – | – | – | – | – | – |
| Qwen3.5-9B md_xl (270 k, 4 dialects) | 90.1 % | – | – | – | – | – | – |
| Qwen3.8-27B v2 SFT | 93.4 % | best geometry | – | – | – | – | – |

Reference programs through the same executors: 3DCodeBench 210/212 (this machine), Blender held-out 96/103,
CadQuery 200/200 (report: 199/200), OpenSCAD 50/50, GLSL 178/200 compile, three.js 40/40.

## C. Local re-scores (this machine, `docs/results/`)

Filled in by the runs under `$C3D_EVAL_OUT` (`report.py`): frontier logs replayed under the official protocol,
the reference self-test, and the open 8B/9B runs. See `docs/results/README.md`.

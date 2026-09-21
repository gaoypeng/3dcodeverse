# 3DCodeVerse

An ecosystem for LLMs that write **raw, executable 3D code** — Blender `bpy`,
CadQuery, Three.js, URDF, GLSL / OpenGL — covering the full loop: a generation
harness that plans, builds, measures, renders and judges what models write; a
data toolchain that curates the results (and external sources) into verified
training corpora; and a finetuning study that trains open models on that data.

```
 harness/                    toolkits/                        finetune/
 API models write 3D code ─▶ curate + verify + package ─────▶ train & evaluate open models
 plan→generate→gate→render    corpus → LLaMA-Factory sets      LoRA / full SFT / DPO
 →judge→refine, per-run       (real compilers & renderers      3DCodeBench + per-dialect
 flywheel records             check every sample)               executors
        ▲                                                            │
        └──────────── better generators / judges ◀───────────────────┘
```

## [`harness/`](harness/) — the generation harness

Python dist `3dcodeverse` (import `codeverse`, CLI `3dcodeverse` / `3dcode`).
LLMs write raw 3D code across four tracks — `static_object` ·
`articulated_object` · `scene` · `graphics` — in seven languages; the harness
owns everything around the code: typed plans, deterministic gates
(connectivity, contract, joint sweeps, frame metrics), a 19-tool spatial
toolbox served to coding agents over MCP, labelled multi-view renders, a
rubric VLM judge with binary defect checklists and code-computed caps, an
optional text-to-image texture pass, and a git-versioned record of every round
for the data flywheel.  Generation backends: the vendor coding CLIs
(`gemini-cli`, `claude-code`, `codex`, `agy`) or single-shot chat calls on
Gemini / Anthropic / OpenAI models.

```bash
bash harness/setup.sh        # python deps + node runtime + doctor (Linux, python 3.13, node 20.6+)
3dcode make "a mid-century wooden dining chair" --track static_object --language blender
3dcode gallery serve                   # browse every run in the browser
```

Start at [`harness/README.md`](harness/README.md); design and operation live in
`harness/docs/` (ARCHITECTURE, INSTALL, RUNBOOK, DECISIONS, COST).

## [`eval/`](eval/) — evaluating the harness, and evaluating a bare LLM

Two evaluations kept apart from what they evaluate.  [`eval/bench/`](eval/bench/) measures **the
harness**: prompt batteries run through it and through one-shot / bare-agent arms under one fixed
judge, paired A/B rigs for harness switches, offline re-judging and reports.  [`eval/llm/`](eval/llm/)
measures **a bare LLM / VLM** on text → 3D code and image → 3D code (3DCodeBench, held-out dialect
sets, ten harness batteries): every answer is executed and scored against references.  Protocols and
every recorded comparison: `eval/docs/` (EVAL, COMPLEXITY, PAPER_WRITING).  Nothing under `harness/`
imports anything from `eval/`.

## [`toolkits/`](toolkits/) — raw 3D projects → trainable data

Everything that operates on data rather than models: the installable `3dcode-data`
contributor CLI (validate / dedupe / execute / render / push, with per-dialect
modules for Blender-Python, CadQuery, build123d, FreeCAD, OpenSCAD), per-source
curation pipelines (convert → execute → render → ground truth → dedupe →
caption → pack → upload), and the LLaMA-Factory dataset builders that turn
every corpus subdirectory into verified ShareGPT sets — checked with the real
compilers and headless renderers, because *compiling is not drawing*.  The
resulting corpus is published as
[`ilabai/3dcodeverse`](https://huggingface.co/datasets/ilabai/3dcodeverse) on
the Hub.  See [`toolkits/README.md`](toolkits/README.md).

## [`finetune/`](finetune/) — training open models on 3DCodeVerse

Reproducible finetuning recipes on **unpatched LLaMA-Factory** (LoRA and
ZeRO-3 full-parameter SFT, execution- and geometry-feedback DPO) plus the
execution-based evaluation stack: 3DCodeBench (generate → run in Blender →
Chamfer / F-score against ground truth) and held-out per-dialect executors.
Headline (Qwen3.5-9B, 3DCodeBench execution rate): zero-shot **0%** → LoRA on
5k Blender samples **75–82%** → + execution-feedback DPO **96.2%**; the full
270k-sample multi-dialect mix reaches **90.1%**, and Qwen3.5-27B v2 scores
**93.4%** with the best geometry.  Training sets ship as
[`ilabai/3dcodeverse-llamafactory`](https://huggingface.co/datasets/ilabai/3dcodeverse-llamafactory).
Results and conclusions: [`finetune/docs/REPORT.md`](finetune/docs/REPORT.md);
full experiment log: [`finetune/llm_finetune_exps.md`](finetune/llm_finetune_exps.md).

## License

Code is licensed under [Apache-2.0](LICENSE).  The published datasets carry
their own terms (CC-BY-NC-SA, non-commercial) — see each dataset card.

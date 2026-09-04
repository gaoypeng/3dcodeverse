# 3DCodeVerse evaluation

Scores a model by **executing what it generates**, not by comparing text. A 3D program either runs and
produces the geometry or it does not, so every number here is a pass rate from a real interpreter.

```bash
source env.sh                       # optional on this box; all vars already default to these values
GPUS=0 TP=1 ./run_eval.sh /path/to/model my_run
GPUS=0 TP=1 ./run_eval.sh /path/to/model my_run --temp 0.7 --seed 1     # sampled decoding
GPUS=0 TP=1 ./run_eval.sh /path/to/model my_run --suites blender threejs
```

Results: `$EVAL_OUT/<name>_<suite>/summary.json`, aggregated into `$EVAL_OUT/<name>_report.json`.

## Suites

| suite | what runs the code | pass = |
|---|---|---|
| `bench` | Blender 5.0.1 (+scipy) | script executes **and** geometry matches ground truth |
| `blender` | Blender 5.0.1 | script executes, scene is non-empty |
| `cadquery` | CadQuery | solid builds and exports |
| `openscad` | OpenSCAD CLI | compiles to a non-empty mesh |
| `glsl` | `glslangValidator` | shader compiles |
| `threejs` | headless Chromium, WebGL2 | page renders without a JS error and draws something |

## Layout

`run_eval.sh` is the only entry point. It generates once with **one vLLM engine for all suites** (loading a
large model per suite costs more than the generation itself), then executes and scores each suite.

- generation — `generate_multi.py` (all suites, one engine), `generate_vllm.py` (single suite),
  `generate_vllm_img.py` (image → code), `generate.py` (HF fallback, no vLLM)
- extraction — `extract.py`, dialect-aware: what counts as the answer differs per dialect
- execution — `dialect_runners.py`, `blender_runner.py`, `threejs_runner.py`, `glsl_render.py`, `render_glb.py`
- scoring — `run_bench.py` + `metrics.py` (bench), `dialect_eval.py`, `blender_dialect_eval.py`,
  `threejs_eval.py`, aggregation in `results_all.py`
- `free_gpus.sh` — lists cards with enough free memory (the box is shared)

## Two guards that are there for a reason

**It refuses to score an empty generation directory.** An engine that never started leaves no generations,
and every scorer reports that as 0%. A real 0% and a failed launch then look identical — which is how a
rescore of two healthy models once came back as 0/200 on every suite.

**It re-checks the GPU right before launching.** On a shared box a card that was free when it was picked can
be full by the time vLLM starts; the harness switches to whichever card is free now, or waits.

## Reading the numbers

Report long-output dialects under **both** greedy and sampled decoding. A fine-tuned model that scores 6% on
OpenSCAD greedy and 44% sampled has not forgotten the dialect — it is in a repetition loop and never emits
the closing token. Preference pairs on termination fix it, and afterwards greedy beats sampling again.

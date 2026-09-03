# Tool locations for 3DCodeVerse evaluation. `source env.sh` before run_eval.sh, or export your own.
# Every variable already defaults to the value below, so on this box sourcing it is optional.
export PROJECT_ROOT=${PROJECT_ROOT:-/wekafs/ict/hx_624/llm-ft}          # holds data/multidialect, data/sft_v1
export EVAL_OUT=${EVAL_OUT:-$PROJECT_ROOT/eval/out}                     # generations + summaries land here
export BENCH_DATA=${BENCH_DATA:-/wekafs/ict/hx_624/data/3dcodebench/data}
export BLENDER_BIN=${BLENDER_BIN:-/wekafs/ict/hx_624/tools/blender-5.0.1-linux-x64/blender}
export OPENSCAD_BIN=${OPENSCAD_BIN:-/wekafs/ict/hx_624/tools/openscad/squashfs-root/AppRun}
export GLSLANG_BIN=${GLSLANG_BIN:-/wekafs/ict/hx_624/anaconda3/envs/llmft/bin/glslangValidator}
export EVAL_PYTHON=${EVAL_PYTHON:-/wekafs/ict/hx_624/anaconda3/envs/llmft/bin/python}
export PLAYWRIGHT_BROWSERS_PATH=${PLAYWRIGHT_BROWSERS_PATH:-/wekafs/ict/hx_624/cache/ms-playwright}
export VLLM_ENV=${VLLM_ENV:-vllm}      # conda env with vLLM (generation)
export EXEC_ENV=${EXEC_ENV:-llmft}     # conda env with the runners (execution + scoring)

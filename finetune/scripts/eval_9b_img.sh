#!/bin/bash
# Evaluate the image-conditioned model on 3DCodeBench by feeding each task's own reference render instead of the
# text prompt. Same executor and geometry metrics, so the score is directly comparable to the text-only runs.
set -uo pipefail; cd /wekafs/ict/hx_624/llm-ft
G=$(scripts/free_gpus.sh | cut -d, -f1)
RUN=runs/lf_qwen35_9b_img4; OUT=eval/out/q9b_img_bench; mkdir -p $OUT
python - <<'PY'
import json, glob, os
rows=[]
for d in sorted(glob.glob("/wekafs/ict/hx_624/data/3dcodebench/data/*_seed0")):
    imgs=sorted(glob.glob(os.path.join(d,"images","*.png")))[:1]
    if not imgs: continue
    rows.append({"task": os.path.basename(d), "images": imgs,
                 "messages":[{"role":"system","content":"You are an expert in procedural 3D modeling with Blender Python (bpy). Given reference renders of an object, write a complete, standalone Blender 5 Python script that reproduces it (clear the default scene first). Output ONLY the code in one ```python block."},
                             {"role":"user","content":"<image>\nReproduce this 3D object with Blender Python code."}]})
open("eval/out/_img_bench_prompts.jsonl","w").write("\n".join(json.dumps(r) for r in rows))
print("image prompts:", len(rows))
PY
source /wekafs/ict/hx_624/anaconda3/etc/profile.d/conda.sh; conda activate vllm
export VLLM_CACHE_ROOT=/wekafs/ict/hx_624/cache/vllm HF_HOME=/wekafs/ict/hx_624/cache/huggingface VLLM_LOGGING_LEVEL=WARNING CUDA_VISIBLE_DEVICES=$G
python eval/generate_vllm_img.py --model $RUN/merged --prompts eval/out/_img_bench_prompts.jsonl --out $OUT --max_new_tokens 6144 2>&1 | tail -2
conda activate llmft
python eval/run_bench.py --gen_dir $OUT --workers 24 --timeout 300 2>&1 | grep "status counts" | tail -1
python eval/metrics.py --gen_dir $OUT 2>&1 | tail -1
echo "[imgeval] DONE"

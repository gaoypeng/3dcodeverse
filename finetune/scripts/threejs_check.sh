#!/bin/bash
# three.js pipeline: wait Playwright -> reference runner check -> base eval (GPU) -> LoRA (300 samples, cutoff 16k) -> eval -> open the md_big gate
set -uo pipefail; GPU=${GPUS:-2}; cd /wekafs/ict/hx_624/llm-ft; source /wekafs/ict/hx_624/llm-ft/env.sh; export PLAYWRIGHT_BROWSERS_PATH=/wekafs/ict/hx_624/cache/ms-playwright
until grep -q PW_INSTALL_DONE /wekafs/ict/hx_624/tools/playwright_install.log 2>/dev/null; do sleep 15; done
echo "[3js] $(date) runner check on 6 reference samples"
python - <<'PY'
import json, sys, re; sys.path.insert(0,"eval"); from threejs_runner import run_threejs
rows=[json.loads(l) for l in open("data/multidialect/threejs/test.jsonl")][:6]
ok=0
for i,r in enumerate(rows):
    code=re.findall(r"```html\n(.*?)```", r["messages"][-1]["content"], flags=re.S)[0]
    rep=run_threejs(code, f"eval/out/_3js_refcheck/{i}"); ok+=rep["status"]=="OK"; print(i, rep["status"], (rep["error"] or "")[:120], rep["latency_s"], "s")
print("REFCHECK_OK", ok, "/", len(rows))
PY
echo "[3js] $(date) base eval"
source /wekafs/ict/hx_624/anaconda3/etc/profile.d/conda.sh; conda activate vllm; export VLLM_CACHE_ROOT=/wekafs/ict/hx_624/cache/vllm HF_HOME=/wekafs/ict/hx_624/cache/huggingface VLLM_LOGGING_LEVEL=WARNING CUDA_VISIBLE_DEVICES=$GPU
python - <<'PY'
import json
with open("eval/out/_3js_prompts.jsonl","w") as f:
    for l in open("data/multidialect/threejs/test.jsonl"):
        r=json.loads(l); f.write(json.dumps({"task": r["id"].replace("/","__"), "messages": r["messages"][:2]})+"\n")
PY
mkdir -p eval/out/md_base_threejs; python eval/generate_vllm.py --model /wekafs/ict/hx_624/models/Qwen3.5-9B --prompts eval/out/_3js_prompts.jsonl --out eval/out/md_base_threejs --tp 1 --no_think --max_new_tokens 12288 2>&1 | grep -E "gen-vllm|Error" | tail -1
conda activate llmft; python eval/threejs_eval.py --gen_dir eval/out/md_base_threejs 2>&1 | tail -2
echo "[3js] $(date) LoRA train (300 samples, cutoff 16k, 3 epochs) on GPU $GPU"
GPUS=$GPU scripts/lf_train.sh configs/lf/lora_md_threejs.yaml > logs/train_md_threejs.log 2>&1
RUN=runs/lf_qwen35_9b_lora_md_threejs; [ -f $RUN/train_results.json ] || { echo "[3js] TRAIN FAILED"; touch logs/.gate_threejs; exit 1; }
echo "[3js] $(date) train done: $(tr -d '\n' < $RUN/train_results.json)"
sed -e "s|adapter_name_or_path:.*|adapter_name_or_path: $(readlink -f $RUN)|" -e "s|export_dir:.*|export_dir: $(readlink -f $RUN)/merged|" configs/lf/export_lora_template.yaml > configs/lf/export_md_threejs.yaml
scripts/lf_export.sh configs/lf/export_md_threejs.yaml > logs/export_md_threejs.log 2>&1 || { echo "[3js] EXPORT FAILED"; touch logs/.gate_threejs; exit 1; }
conda activate vllm; mkdir -p eval/out/md_md_threejs_threejs; python eval/generate_vllm.py --model $RUN/merged --prompts eval/out/_3js_prompts.jsonl --out eval/out/md_md_threejs_threejs --tp 1 --no_think --max_new_tokens 12288 2>&1 | grep -E "gen-vllm|Error" | tail -1
touch logs/.gate_threejs; echo "[3js] gate opened for md_big"
conda activate llmft; python eval/threejs_eval.py --gen_dir eval/out/md_md_threejs_threejs 2>&1 | tail -2
rm -rf $RUN/merged; echo "[3js] DONE"

#!/bin/bash
# One round of execution-verified self-training:
#  LoRA-v1 merged model -> sample K completions (T) for every training prompt -> run all in Blender -> keep OK -> v1 data + verified samples -> LoRA -> eval
# usage: GPUS=0 scripts/self_train_round.sh <K> <T>   (default K=2, T=0.8)
set -uo pipefail
K=${1:-2}; T=${2:-0.8}; GPU=${GPUS:-0}
cd /wekafs/ict/hx_624/llm-ft
NAME=v11_selftrain; SRC=data/sft_v1; WORK=data/selftrain_round1; mkdir -p $WORK
echo "[self] $(date) start (K=$K T=$T GPU=$GPU)"
# 0) prompts file from train.jsonl (system+user only)
source /wekafs/ict/hx_624/llm-ft/env.sh
python - <<PY
import json
rows=[json.loads(l) for l in open("$SRC/train.jsonl")]
with open("$WORK/prompts.jsonl","w") as f:
    for i,r in enumerate(rows):
        f.write(json.dumps({"task": f"t{i:05d}", "messages": r["messages"][:2], "source": r.get("source")})+"\n")
print("prompts:", len(rows))
PY
# 1) merged LoRA-v1
[ -d runs/lf_qwen35_9b_lora_v1/merged ] || scripts/lf_export.sh configs/lf/export_v1.yaml > logs/export_v1_self.log 2>&1 || { echo "[self] EXPORT FAILED"; exit 1; }
# 2) sample K completions
source /wekafs/ict/hx_624/anaconda3/etc/profile.d/conda.sh; conda activate vllm
export VLLM_CACHE_ROOT=/wekafs/ict/hx_624/cache/vllm HF_HOME=/wekafs/ict/hx_624/cache/huggingface VLLM_LOGGING_LEVEL=WARNING CUDA_VISIBLE_DEVICES=$GPU
for i in $(seq 1 $K); do
  python eval/generate_vllm.py --model runs/lf_qwen35_9b_lora_v1/merged --prompts $WORK/prompts.jsonl --out $WORK/gen_s$i --tp 1 --no_think --max_new_tokens 4096 --temperature $T --seed $((100+i)) 2>&1 | grep -E "gen-vllm|Error|Traceback" | tail -3
done
rm -rf runs/lf_qwen35_9b_lora_v1/merged
echo "[self] $(date) sampling done"
# 3) execute everything in Blender (CPU)
conda activate llmft
for i in $(seq 1 $K); do python eval/run_bench.py --gen_dir $WORK/gen_s$i --workers 48 --timeout 120 > $WORK/gen_s$i/exec.log 2>&1; grep "status counts" $WORK/gen_s$i/exec.log; done
echo "[self] $(date) exec done"
# 4) build v11 = v1 + verified samples (dedup by code hash); val = v1 val
python - <<PY
import json, hashlib, os, random
random.seed(0)
W="$WORK"; K=$K
prompts={json.loads(l)["task"]: json.loads(l) for l in open(f"{W}/prompts.jsonl")}
base=[json.loads(l) for l in open("$SRC/train.jsonl")]
seen={hashlib.sha1(r["messages"][-1]["content"].encode()).hexdigest() for r in base}
new=[]; n_ok=0
for i in range(1,K+1):
    for l in open(f"{W}/gen_s{i}/exec_results.jsonl"):
        r=json.loads(l)
        if r["status"]!="OK": continue
        n_ok+=1
        code=open(f"{W}/gen_s{i}/{r['task']}/code.py").read().strip()
        if code.count("\n")<4: continue
        h=hashlib.sha1(("```python\n"+code+"\n```").encode()).hexdigest()
        if h in seen: continue
        seen.add(h)
        p=prompts[r["task"]]
        new.append({"messages": p["messages"]+[{"role":"assistant","content":"```python\n"+code+"\n```"}], "source":"selftrain", "id":r["task"], "prompt_kind":"self"})
print(f"verified OK generations: {n_ok}, new unique samples: {len(new)}")
out="data/sft_v11_selftrain"; os.makedirs(out, exist_ok=True)
allrows=base+new; random.shuffle(allrows)
with open(f"{out}/train.jsonl","w") as f:
    for r in allrows: f.write(json.dumps(r, ensure_ascii=False)+"\n")
import shutil; shutil.copy("$SRC/val.jsonl", f"{out}/val.jsonl")
json.dump({"n_train":len(allrows),"n_new":len(new),"n_ok_generations":n_ok}, open(f"{out}/stats.json","w"), indent=1)
PY
python scripts/to_llamafactory.py --data_dir data/sft_v11_selftrain --name v11_selftrain >/dev/null && python scripts/make_lora_cfg.py $NAME v11_selftrain >/dev/null
cat data/sft_v11_selftrain/stats.json
echo "[self] $(date) data built -> train"
# 5) train + eval (run_exp)
scripts/run_exp.sh $GPU $NAME 2>&1 | tee -a logs/exp_$NAME.log
echo "[self] DONE"

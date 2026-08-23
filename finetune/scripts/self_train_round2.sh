#!/bin/bash
# Round 2 (novel prompts): LoRA-v1 samples K completions for 9.9k articraft/deepcad-derived Blender prompts on 4 GPUs -> Blender filter -> v13 = v1 + verified -> LoRA (4-GPU DDP) -> eval
set -uo pipefail; K=${1:-2}; T=${2:-0.8}; W=data/selftrain_round2; NAME=v13_boot; cd /wekafs/ict/hx_624/llm-ft
echo "[r2] $(date) start K=$K T=$T"
[ -d runs/lf_qwen35_9b_lora_v1/merged ] || scripts/lf_export.sh configs/lf/export_v1.yaml > logs/export_v1_r2.log 2>&1 || { echo "[r2] EXPORT FAILED"; exit 1; }
source /wekafs/ict/hx_624/llm-ft/env.sh
python - <<PY
import json
rows=[json.loads(l) for l in open("$W/prompts.jsonl")]
for g in range(4):
    with open(f"$W/prompts_g{g}.jsonl","w") as f:
        for r in rows[g::4]: f.write(json.dumps(r)+"\n")
print("sharded", len(rows))
PY
source /wekafs/ict/hx_624/anaconda3/etc/profile.d/conda.sh; conda activate vllm
export VLLM_CACHE_ROOT=/wekafs/ict/hx_624/cache/vllm HF_HOME=/wekafs/ict/hx_624/cache/huggingface VLLM_LOGGING_LEVEL=WARNING
for i in $(seq 1 $K); do
  for g in 0 1 2 3; do
    CUDA_VISIBLE_DEVICES=$g python eval/generate_vllm.py --model runs/lf_qwen35_9b_lora_v1/merged --prompts $W/prompts_g$g.jsonl --out $W/gen_s${i}_g$g --tp 1 --no_think --max_new_tokens 4096 --temperature $T --seed $((200+i*10+g)) > $W/gen_s${i}_g$g.log 2>&1 &
  done; wait
  for g in 0 1 2 3; do grep -E "gen-vllm|Error" $W/gen_s${i}_g$g.log | tail -1; done
done
rm -rf runs/lf_qwen35_9b_lora_v1/merged; echo "[r2] $(date) sampling done"
conda activate llmft
for d in $W/gen_s*_g*/; do python eval/run_bench.py --gen_dir $d --workers 56 --timeout 120 > $d/exec.log 2>&1; grep "status counts" $d/exec.log; done
echo "[r2] $(date) exec done"
python - <<PY
import json, hashlib, os, random, shutil, glob
random.seed(0); W="$W"; SRC="data/sft_v1"
prompts={}
for f in glob.glob(f"{W}/prompts_g*.jsonl"):
    for l in open(f): r=json.loads(l); prompts[r["task"]]=r
base=[json.loads(l) for l in open(f"{SRC}/train.jsonl")]
seen={hashlib.sha1(r["messages"][-1]["content"].encode()).hexdigest() for r in base}
new=[]; n_ok=0; per_src={}
for d in sorted(glob.glob(f"{W}/gen_s*_g*/")):
    if not os.path.exists(d+"exec_results.jsonl"): continue
    for l in open(d+"exec_results.jsonl"):
        r=json.loads(l)
        if r["status"]!="OK": continue
        n_ok+=1
        code=open(f"{d}/{r['task']}/code.py").read().strip()
        if code.count("\n")<4: continue
        h=hashlib.sha1(("```python\n"+code+"\n```").encode()).hexdigest()
        if h in seen: continue
        seen.add(h); p=prompts[r["task"]]; per_src[p["src"]]=per_src.get(p["src"],0)+1
        new.append({"messages": p["messages"]+[{"role":"assistant","content":"```python\n"+code+"\n```"}], "source":"boot_"+p["src"], "id":r["task"], "prompt_kind":"boot"})
print(f"verified OK generations: {n_ok}, new unique samples: {len(new)}, by source: {per_src}")
out="data/sft_v13_boot"; os.makedirs(out, exist_ok=True)
allrows=base+new; random.shuffle(allrows)
with open(f"{out}/train.jsonl","w") as f:
    for r in allrows: f.write(json.dumps(r, ensure_ascii=False)+"\n")
shutil.copy(f"{SRC}/val.jsonl", f"{out}/val.jsonl")
json.dump({"n_train":len(allrows),"n_new":len(new),"n_ok_generations":n_ok,"by_source":per_src}, open(f"{out}/stats.json","w"), indent=1)
PY
python scripts/to_llamafactory.py --data_dir data/sft_v13_boot --name v13_boot >/dev/null && python scripts/make_lora_cfg.py $NAME v13_boot >/dev/null
cat data/sft_v13_boot/stats.json; echo "[r2] $(date) data built -> train on 4 GPUs"
scripts/run_exp.sh 0,1,2,3 $NAME 2>&1 | tee -a logs/exp_$NAME.log
echo "[r2] DONE"

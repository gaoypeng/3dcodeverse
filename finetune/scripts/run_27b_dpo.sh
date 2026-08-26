#!/bin/bash
# 27B v2 + multi-dialect execution-feedback DPO: sample K=2 (T=0.8) on 1200 train prompts per dialect ->
# execute with each dialect's executor -> (OK, FAIL) pairs -> DPO (cutoff 2048, DDP) -> full eval.
set -uo pipefail; cd /wekafs/ict/hx_624/llm-ft; W=data/dpo_27b_v2; mkdir -p $W
BASE=runs/lf_qwen38_27b_lora_v2/merged
# opportunistic: take whatever GPUs are fully idle (>=2), confirmed 3 times in a row
STABLE=0; GPUS_USE=""
while [ $STABLE -lt 3 ]; do
  F=$(scripts/free_gpus.sh); N=$(echo "$F" | awk -F, 'NF&&$1!=""{print NF}')
  if [ -n "$F" ] && [ "${N:-0}" -ge 2 ]; then
    if [ "$F" = "$GPUS_USE" ]; then STABLE=$((STABLE+1)); else GPUS_USE="$F"; STABLE=1; fi
  else STABLE=0; GPUS_USE=""; fi
  sleep 60
done
NGPU=$(echo "$GPUS_USE" | awk -F, '{print NF}')
echo "[sched] using GPUs $GPUS_USE ($NGPU cards)"
echo "[27bdpo] $(date) sampling with the tuned 27B (TP=2)"
source /wekafs/ict/hx_624/llm-ft/env.sh
python - <<'PY'
import json, random; random.seed(9)
rows=[]
for d in ["blender","cadquery","openscad","glsl"]:
    rs=[json.loads(l) for l in open(f"data/multidialect/{d}/train.jsonl")]; random.shuffle(rs)
    for i,r in enumerate(rs[:1200]): rows.append({"task": f"{d}_{i:05d}", "dialect": d, "messages": r["messages"][:2]})
random.shuffle(rows)
with open("data/dpo_27b_v2/prompts.jsonl","w") as f:
    for r in rows: f.write(json.dumps(r)+"\n")
print("prompts", len(rows))
PY
source /wekafs/ict/hx_624/anaconda3/etc/profile.d/conda.sh; conda activate vllm
export VLLM_CACHE_ROOT=/wekafs/ict/hx_624/cache/vllm HF_HOME=/wekafs/ict/hx_624/cache/huggingface VLLM_LOGGING_LEVEL=WARNING CUDA_VISIBLE_DEVICES=$GPUS_USE
for i in 1 2; do
  python eval/generate_vllm.py --model $BASE --prompts $W/prompts.jsonl --out $W/gen_s$i --tp $NGPU --no_think \
    --max_new_tokens 4096 --temperature 0.8 --seed $((700+i)) 2>&1 | grep -E "gen-vllm|Error" | tail -1
done
echo "[27bdpo] $(date) executing + building pairs"
conda activate llmft
python - <<'PY'
import json, os, glob, random, subprocess, sys
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, "eval"); from dialect_runners import RUNNERS
BL="/wekafs/ict/hx_624/tools/blender-5.0.1-linux-x64/blender"; RUNNER=os.path.abspath("eval/blender_runner.py")
def run_blender(code, d):
    d=os.path.abspath(d); os.makedirs(d, exist_ok=True); sp=os.path.join(d,"code.py"); open(sp,"w").write(code)
    try:
        subprocess.run([BL,"-b","--factory-startup","-noaudio","--python",RUNNER,"--","--script",sp,"--out",os.path.join(d,"out.glb"),"--report",os.path.join(d,"exec.json")],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120, env={**os.environ,"HOME":"/tmp"})
        return json.load(open(os.path.join(d,"exec.json"))) if os.path.exists(os.path.join(d,"exec.json")) else {"status":"CRASH"}
    except subprocess.TimeoutExpired: return {"status":"TIMEOUT"}
W="data/dpo_27b_v2"; prompts={json.loads(l)["task"]: json.loads(l) for l in open(f"{W}/prompts.jsonl")}
jobs=[]
for d in sorted(glob.glob(f"{W}/gen_s*/")):
    for t in os.listdir(d):
        if os.path.isfile(os.path.join(d,t,"code.py")): jobs.append((d,t))
def job(x):
    d,t=x; code=open(os.path.join(d,t,"code.py")).read(); dia=prompts[t]["dialect"]; wd=os.path.join(d,t,"exec")
    r = run_blender(code, wd) if dia=="blender" else RUNNERS[dia](code, wd)
    return (d,t,dia,r.get("status"))
with ThreadPoolExecutor(40) as ex: res=list(ex.map(job, jobs))
by={}
for d,t,dia,st in res:
    code=open(os.path.join(d,t,"code.py")).read().strip()
    if code.count("\n")<3: continue
    by.setdefault(t,{"dia":dia,"ok":[],"bad":[]})["ok" if st=="OK" else "bad"].append(code)
from collections import Counter
print("per-dialect pass rate:", {dia: round(sum(1 for _,_,di,st in res if di==dia and st=="OK")/max(1,sum(1 for _,_,di,_ in res if di==dia)),3) for dia in ["blender","cadquery","openscad","glsl"]})
random.seed(3); pairs=[]; per=Counter()
for t,v in by.items():
    if v["ok"] and v["bad"]:
        ok=random.choice(v["ok"]); bad=random.choice(v["bad"])
        if ok==bad: continue
        p=prompts[t]; per[v["dia"]]+=1
        pairs.append({"conversations":[{"from":"human","value":p["messages"][1]["content"]}],"system":p["messages"][0]["content"],
                      "chosen":{"from":"gpt","value":ok if ok.startswith("```") else "```\n"+ok+"\n```"},
                      "rejected":{"from":"gpt","value":bad if bad.startswith("```") else "```\n"+bad+"\n```"}})
random.shuffle(pairs); json.dump(pairs, open("data/lf/dpo_27b_pairs.json","w"), ensure_ascii=False)
info=json.load(open("data/lf/dataset_info.json")); info["dpo_27b_pairs"]={"file_name":"dpo_27b_pairs.json","ranking":True,"formatting":"sharegpt","columns":{"messages":"conversations","system":"system","chosen":"chosen","rejected":"rejected"}}
json.dump(info, open("data/lf/dataset_info.json","w"), indent=2); print("27B pairs:", len(pairs), dict(per))
PY
sed -e "s|model_name_or_path:.*|model_name_or_path: $(readlink -f $BASE)|" -e "s|dataset: dpo_exec_pairs|dataset: dpo_27b_pairs|" \
    -e "s|output_dir:.*|output_dir: /wekafs/ict/hx_624/llm-ft/runs/lf_qwen38_27b_dpo|" configs/lf/dpo_exec_v1.yaml > configs/lf/dpo_27b.yaml
echo "[27bdpo] $(date) DPO train (cutoff 2048, 4 GPUs)"
GPUS=$GPUS_USE scripts/lf_train.sh configs/lf/dpo_27b.yaml cutoff_len=2048 gradient_accumulation_steps=4 > logs/train_27b_dpo.log 2>&1
RUN=runs/lf_qwen38_27b_dpo
[ -f $RUN/train_results.json ] || { echo "[27bdpo] TRAIN FAILED: $(grep -E 'OutOfMemory|Error' logs/train_27b_dpo.log | grep -v errors | tail -1 | cut -c1-160)"; exit 1; }
echo "[27bdpo] $(date) train done: $(tr -d '\n' < $RUN/train_results.json | cut -c1-160)"
sed -e "s|model_name_or_path:.*|model_name_or_path: $(readlink -f $BASE)|" -e "s|adapter_name_or_path:.*|adapter_name_or_path: $(readlink -f $RUN)|" -e "s|export_dir:.*|export_dir: $(readlink -f $RUN)/merged|" configs/lf/export_lora_template.yaml > configs/lf/export_27b_dpo.yaml
scripts/lf_export.sh configs/lf/export_27b_dpo.yaml > logs/export_27b_dpo.log 2>&1 || { echo "[27bdpo] EXPORT FAILED"; exit 1; }
GPUS=$GPUS_USE TP=$NGPU eval/eval_all_run2.sh $RUN/merged q27b_dpo --max_new 8192 2>&1 | grep -E "gen-multi|status counts|dialect|DONE" | tail -20
GPUS=$GPUS_USE TP=$NGPU eval/eval_all_run2.sh $RUN/merged q27b_dpo_T07 --temp 0.7 --seed 1 --max_new 8192 --suites openscad glsl 2>&1 | grep -E "dialect|DONE" | tail -4
echo "[27bdpo] ALL DONE"

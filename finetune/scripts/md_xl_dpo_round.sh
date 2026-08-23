#!/bin/bash
# Multi-dialect execution-feedback DPO on md_xl: sample K=2 (T=0.8) on 1500 train prompts per dialect -> execute with each dialect's executor -> (OK, FAIL) pairs -> DPO -> eval (sampling for cq/scad/glsl, greedy for blender & 3DCodeBench)
set -uo pipefail; GPU=${GPUS:-2,3}; G1=$(echo $GPU | cut -d, -f1); G2=$(echo $GPU | cut -d, -f2); cd /wekafs/ict/hx_624/llm-ft; W=data/md_xl_dpo_round; mkdir -p $W
echo "[xldpo] $(date) start"
source /wekafs/ict/hx_624/llm-ft/env.sh
python - <<'PY'
import json, random; random.seed(5)
rows=[]
for d in ["cadquery","openscad","glsl","blender"]:
    rs=[json.loads(l) for l in open(f"data/md_{d}/train.jsonl")]; random.shuffle(rs)
    for i,r in enumerate(rs[:1500]): rows.append({"task": f"{d}_{i:05d}", "dialect": d, "messages": r["messages"][:2]})
random.shuffle(rows)
for g in range(2):
    with open(f"data/md_xl_dpo_round/prompts_g{g}.jsonl","w") as f:
        for r in rows[g::2]: f.write(json.dumps(r)+"\n")
print("prompts", len(rows))
PY
BASE=runs/lf_qwen35_9b_lora_md_xl/merged
[ -d $BASE ] || scripts/lf_export.sh configs/lf/export_md_xl.yaml > logs/export_md_xl_dpo.log 2>&1 || { echo "[xldpo] EXPORT FAILED"; exit 1; }
source /wekafs/ict/hx_624/anaconda3/etc/profile.d/conda.sh; conda activate vllm
export VLLM_CACHE_ROOT=/wekafs/ict/hx_624/cache/vllm HF_HOME=/wekafs/ict/hx_624/cache/huggingface VLLM_LOGGING_LEVEL=WARNING
for i in 1 2; do
  CUDA_VISIBLE_DEVICES=$G1 python eval/generate_vllm.py --model $BASE --prompts $W/prompts_g0.jsonl --out $W/gen_s${i}_g0 --tp 1 --no_think --max_new_tokens 6144 --temperature 0.8 --seed $((400+i)) > $W/gen_s${i}_g0.log 2>&1 &
  CUDA_VISIBLE_DEVICES=$G2 python eval/generate_vllm.py --model $BASE --prompts $W/prompts_g1.jsonl --out $W/gen_s${i}_g1 --tp 1 --no_think --max_new_tokens 6144 --temperature 0.8 --seed $((410+i)) > $W/gen_s${i}_g1.log 2>&1 &
  wait; grep -h gen-vllm $W/gen_s${i}_g0.log $W/gen_s${i}_g1.log
done
echo "[xldpo] $(date) sampling done"
conda activate llmft
python - <<'PY'
# execute every generation with its dialect's executor (parallel), then build pairs
import json, glob, os, sys, random
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, "eval"); from dialect_runners import RUNNERS
import subprocess
BL="/wekafs/ict/hx_624/tools/blender-5.0.1-linux-x64/blender"; RUNNER=os.path.abspath("eval/blender_runner.py")
def run_blender(code, d):
    d=os.path.abspath(d); os.makedirs(d, exist_ok=True); sp=os.path.join(d,"code.py"); open(sp,"w").write(code)
    try:
        subprocess.run([BL,"-b","--factory-startup","-noaudio","--python",RUNNER,"--","--script",sp,"--out",os.path.join(d,"out.glb"),"--report",os.path.join(d,"exec.json")],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=120,env={**os.environ,"HOME":"/tmp"})
        r=json.load(open(os.path.join(d,"exec.json"))) if os.path.exists(os.path.join(d,"exec.json")) else {"status":"CRASH"}
    except subprocess.TimeoutExpired: r={"status":"TIMEOUT"}
    return r
W="data/md_xl_dpo_round"; prompts={}
for f in glob.glob(f"{W}/prompts_g*.jsonl"):
    for l in open(f): r=json.loads(l); prompts[r["task"]]=r
jobs=[]
for d in sorted(glob.glob(f"{W}/gen_s*_g*/")):
    for t in os.listdir(d):
        cp=os.path.join(d,t,"code.py")
        if os.path.isfile(cp): jobs.append((d,t))
def job(x):
    d,t=x; code=open(os.path.join(d,t,"code.py")).read(); dia=prompts[t]["dialect"]; wd=os.path.join(d,t,"exec")
    r = run_blender(code, wd) if dia=="blender" else RUNNERS[dia](code, wd)
    return (d,t,dia,r.get("status"))
with ThreadPoolExecutor(48) as ex: res=list(ex.map(job, jobs))
by={}
for d,t,dia,st in res:
    code=open(os.path.join(d,t,"code.py")).read().strip()
    if code.count("\n")<3: continue
    by.setdefault(t,{"dia":dia,"ok":[],"bad":[]})["ok" if st=="OK" else "bad"].append(code)
from collections import Counter
print("per-dialect pass rate:", {dia: round(sum(1 for d_,t_,di,st in res if di==dia and st=="OK")/max(1,sum(1 for d_,t_,di,st in res if di==dia)),3) for dia in ["cadquery","openscad","glsl","blender"]})
random.seed(2); pairs=[]; per=Counter()
for t,v in by.items():
    if v["ok"] and v["bad"]:
        ok=random.choice(v["ok"]); bad=random.choice(v["bad"])
        if ok==bad: continue
        p=prompts[t]; per[v["dia"]]+=1
        pairs.append({"conversations":[{"from":"human","value":p["messages"][1]["content"]}],"system":p["messages"][0]["content"],"chosen":{"from":"gpt","value":ok if ok.startswith("```") else "```\n"+ok+"\n```"},"rejected":{"from":"gpt","value":bad if bad.startswith("```") else "```\n"+bad+"\n```"}})
random.shuffle(pairs); json.dump(pairs, open("data/lf/dpo_md_xl_pairs.json","w"), ensure_ascii=False)
info=json.load(open("data/lf/dataset_info.json")); info["dpo_md_xl_pairs"]={"file_name":"dpo_md_xl_pairs.json","ranking":True,"formatting":"sharegpt","columns":{"messages":"conversations","system":"system","chosen":"chosen","rejected":"rejected"}}
json.dump(info, open("data/lf/dataset_info.json","w"), indent=2); print("md pairs:", len(pairs), dict(per))
PY
echo "[xldpo] $(date) pairs built"
sed -e "s|model_name_or_path:.*|model_name_or_path: $(readlink -f $BASE)|" -e "s|dataset: dpo_exec_pairs|dataset: dpo_md_xl_pairs|" -e "s|output_dir:.*|output_dir: /wekafs/ict/hx_624/llm-ft/runs/lf_qwen35_9b_dpo_md_xl|" configs/lf/dpo_exec_v1.yaml > configs/lf/dpo_md_xl.yaml
GPUS=$GPU scripts/lf_train.sh configs/lf/dpo_md_xl.yaml gradient_accumulation_steps=8 cutoff_len=4096 > logs/train_dpo_md_xl.log 2>&1
RUN=runs/lf_qwen35_9b_dpo_md_xl; [ -f $RUN/train_results.json ] || { echo "[xldpo] TRAIN FAILED"; exit 1; }
echo "[xldpo] $(date) train done: $(tr -d '\n' < $RUN/train_results.json)"
sed -e "s|model_name_or_path:.*|model_name_or_path: $(readlink -f $BASE)|" -e "s|adapter_name_or_path:.*|adapter_name_or_path: $(readlink -f $RUN)|" -e "s|export_dir:.*|export_dir: $(readlink -f $RUN)/merged|" configs/lf/export_lora_template.yaml > configs/lf/export_dpo_md_xl.yaml
scripts/lf_export.sh configs/lf/export_dpo_md_xl.yaml > logs/export_dpo_md_xl.log 2>&1 || { echo "[xldpo] EXPORT2 FAILED"; exit 1; }
GPUS=$G1 eval/run_eval_vllm.sh $RUN/merged eval/out/qwen35_9b_dpo_md_xl --no_think --max_new_tokens 6144 > logs/eval_dpo_md_xl.log 2>&1
echo "[xldpo] 3DCodeBench: $(python3 -c "import json; s=json.load(open('eval/out/qwen35_9b_dpo_md_xl/summary.json')); print(s['exec_ok'], round(s['f@0.05_mean_all(fail=0)'],3))")"
GPUS=$G1 eval/run_dialect_eval.sh $RUN/merged dpo_md_xl > logs/mdeval_dpo_md_xl.log 2>&1
# sampling eval for cq/scad/glsl
conda activate vllm; export CUDA_VISIBLE_DEVICES=$G1
for D in glsl openscad cadquery; do OUT=eval/out/md_dpo_md_xl_T07_$D; mkdir -p $OUT; cp eval/out/md_dpo_md_xl_$D/prompts.jsonl $OUT/; python eval/generate_vllm.py --model $RUN/merged --prompts $OUT/prompts.jsonl --out $OUT --tp 1 --no_think --max_new_tokens 8192 --temperature 0.7 --seed 1 2>&1 | grep -E "gen-vllm" | tail -1; done
conda activate llmft
for D in glsl openscad cadquery; do python eval/dialect_eval.py --dialect $D --test data/multidialect/$D/test.jsonl --gen_dir eval/out/md_dpo_md_xl_T07_$D --workers 32 2>&1 | grep -E "dialect"; done
for D in cadquery openscad glsl blender; do echo "[xldpo] greedy $D: $(cat eval/out/md_dpo_md_xl_$D/summary.json | tr -d '\n ' | cut -c1-160)"; done
rm -rf $RUN/merged $BASE; echo "[xldpo] DONE"

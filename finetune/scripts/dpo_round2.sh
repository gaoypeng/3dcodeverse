#!/bin/bash
# Iterative DPO round 2: DPO_v1 model samples K=2 on the 9.9k novel prompts -> Blender filter -> new (OK, FAIL) pairs -> DPO on top of DPO_v1 -> eval
set -uo pipefail; GPU=${GPUS:-1}; cd /wekafs/ict/hx_624/llm-ft; W=data/dpo_round2; mkdir -p $W
echo "[dpo2] $(date) start"
[ -d runs/lf_qwen35_9b_lora_v1/merged ] || scripts/lf_export.sh configs/lf/export_v1.yaml > logs/export_v1_dpo2.log 2>&1
BASE=runs/lf_qwen35_9b_dpo_exec_v1/merged_r2
sed -e "s|export_dir:.*|export_dir: $(readlink -f runs/lf_qwen35_9b_dpo_exec_v1)/merged_r2|" configs/lf/export_dpo_exec_v1.yaml > configs/lf/export_dpo_exec_v1_r2.yaml
[ -d $BASE ] || scripts/lf_export.sh configs/lf/export_dpo_exec_v1_r2.yaml > logs/export_dpo_r2.log 2>&1 || { echo "[dpo2] EXPORT FAILED"; exit 1; }
source /wekafs/ict/hx_624/anaconda3/etc/profile.d/conda.sh; conda activate vllm
export VLLM_CACHE_ROOT=/wekafs/ict/hx_624/cache/vllm HF_HOME=/wekafs/ict/hx_624/cache/huggingface VLLM_LOGGING_LEVEL=WARNING CUDA_VISIBLE_DEVICES=$GPU
for i in 1 2; do python eval/generate_vllm.py --model $BASE --prompts data/selftrain_round2/prompts.jsonl --out $W/gen_s$i --tp 1 --no_think --max_new_tokens 4096 --temperature 0.8 --seed $((300+i)) 2>&1 | grep -E "gen-vllm|Error" | tail -1; done
echo "[dpo2] $(date) sampling done"
conda activate llmft
for i in 1 2; do python eval/run_bench.py --gen_dir $W/gen_s$i --workers 56 --timeout 120 > $W/gen_s$i/exec.log 2>&1; grep "status counts" $W/gen_s$i/exec.log; done
python - <<'PY'
import json, glob, os, random
random.seed(1); W="data/dpo_round2"
prompts={}
for l in open("data/selftrain_round2/prompts.jsonl"): r=json.loads(l); prompts[r["task"]]=r
by={}
for d in sorted(glob.glob(f"{W}/gen_s*/")):
    for l in open(d+"exec_results.jsonl"):
        r=json.loads(l); code=open(f"{d}/{r['task']}/code.py").read().strip()
        if code.count("\n")<3: continue
        by.setdefault(r["task"],{"ok":[],"bad":[]})["ok" if r["status"]=="OK" else "bad"].append(code)
pairs=[]
for t,v in by.items():
    if v["ok"] and v["bad"]:
        ok=random.choice(v["ok"]); bad=random.choice(v["bad"])
        if ok==bad: continue
        p=prompts[t]; pairs.append({"conversations":[{"from":"human","value":p["messages"][1]["content"]}],"system":p["messages"][0]["content"],"chosen":{"from":"gpt","value":"```python\n"+ok+"\n```"},"rejected":{"from":"gpt","value":"```python\n"+bad+"\n```"}})
random.shuffle(pairs); json.dump(pairs, open("data/lf/dpo_exec_pairs_r2.json","w"), ensure_ascii=False)
info=json.load(open("data/lf/dataset_info.json")); info["dpo_exec_pairs_r2"]={"file_name":"dpo_exec_pairs_r2.json","ranking":True,"formatting":"sharegpt","columns":{"messages":"conversations","system":"system","chosen":"chosen","rejected":"rejected"}}
json.dump(info, open("data/lf/dataset_info.json","w"), indent=2); print("round2 pairs:", len(pairs), "prompts with any OK:", sum(1 for v in by.values() if v["ok"]), "/", len(by))
PY
sed -e "s|model_name_or_path:.*|model_name_or_path: $(readlink -f $BASE)|" -e "s|dataset: dpo_exec_pairs|dataset: dpo_exec_pairs_r2|" -e "s|output_dir:.*|output_dir: /wekafs/ict/hx_624/llm-ft/runs/lf_qwen35_9b_dpo_exec_v2|" configs/lf/dpo_exec_v1.yaml > configs/lf/dpo_exec_v2.yaml
echo "[dpo2] $(date) train"; GPUS=$GPU scripts/lf_train.sh configs/lf/dpo_exec_v2.yaml gradient_accumulation_steps=16 > logs/train_dpo_exec_v2.log 2>&1
RUN=runs/lf_qwen35_9b_dpo_exec_v2; [ -f $RUN/train_results.json ] || { echo "[dpo2] TRAIN FAILED"; exit 1; }
echo "[dpo2] $(date) train done: $(tr -d '\n' < $RUN/train_results.json)"
sed -e "s|model_name_or_path:.*|model_name_or_path: $(readlink -f $BASE)|" -e "s|adapter_name_or_path:.*|adapter_name_or_path: $(readlink -f $RUN)|" -e "s|export_dir:.*|export_dir: $(readlink -f $RUN)/merged|" configs/lf/export_lora_template.yaml > configs/lf/export_dpo_exec_v2.yaml
scripts/lf_export.sh configs/lf/export_dpo_exec_v2.yaml > logs/export_dpo_exec_v2.log 2>&1 || { echo "[dpo2] EXPORT2 FAILED"; exit 1; }
GPUS=$GPU eval/run_eval_vllm.sh $RUN/merged eval/out/qwen35_9b_dpo_exec_v2 --no_think --max_new_tokens 6144 > logs/eval_dpo_exec_v2.log 2>&1
echo "[dpo2] $(date) eval done"; cat eval/out/qwen35_9b_dpo_exec_v2/summary.json; rm -rf $RUN/merged $BASE; echo "[dpo2] DONE"

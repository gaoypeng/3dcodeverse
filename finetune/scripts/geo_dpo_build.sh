#!/bin/bash
# Geometry-feedback DPO data: sample md_xl (merged_geo) K times on train prompts that have reference code -> execute -> F@0.05 vs reference mesh -> (high-F, low-F/FAIL) pairs
set -uo pipefail; cd /wekafs/ict/hx_624/llm-ft; G=${GPUS:-0}; W=data/geo_dpo; M=runs/lf_qwen35_9b_lora_md_xl/merged_geo
until [ -f $M/config.json ] && [ -n "$(ls $M/*.safetensors 2>/dev/null)" ] && [ $(( $(date +%s) - $(stat -c %Y logs/export_md_xl_geo.log) )) -gt 90 ]; do sleep 30; done
until grep -q "\[xlextra\] DONE" logs/md_xl_extra_eval.log 2>/dev/null; do sleep 30; done
echo "[geo] $(date) sampling on GPU $G"
source /wekafs/ict/hx_624/anaconda3/etc/profile.d/conda.sh; conda activate vllm
export VLLM_CACHE_ROOT=/wekafs/ict/hx_624/cache/vllm HF_HOME=/wekafs/ict/hx_624/cache/huggingface VLLM_LOGGING_LEVEL=WARNING CUDA_VISIBLE_DEVICES=$G
for i in 1 2 3 4; do python eval/generate_vllm.py --model $M --prompts $W/blender_vllm_prompts.jsonl --out $W/gen_blender_s$i --tp 1 --no_think --max_new_tokens 4096 --temperature 0.8 --seed $((500+i)) 2>&1 | grep -E "gen-vllm|Error" | tail -1; done
for i in 1 2; do python eval/generate_vllm.py --model $M --prompts $W/cadquery_vllm_prompts.jsonl --out $W/gen_cadquery_s$i --tp 1 --no_think --max_new_tokens 4096 --temperature 0.8 --seed $((600+i)) 2>&1 | grep -E "gen-vllm|Error" | tail -1; done
echo "[geo] $(date) sampling done; executing + scoring"
conda activate llmft
for i in 1 2 3 4; do python eval/blender_dialect_eval.py --test $W/blender_prompts.jsonl --gen_dir $W/gen_blender_s$i --workers 40 2>&1 | tail -1 | cut -c1-200; done
for i in 1 2; do python eval/dialect_eval.py --dialect cadquery --test $W/cadquery_prompts.jsonl --gen_dir $W/gen_cadquery_s$i --workers 32 2>&1 | tail -1 | cut -c1-200; done
python - <<'PY'
import json, os, re, random, glob
from collections import Counter, defaultdict
W="data/geo_dpo"; random.seed(7)
def code_of(d, tid):
    p=f"{d}/{tid}/code.py"; return open(p).read().strip() if os.path.exists(p) else None
prompts={}
for dia in ["blender","cadquery"]:
    for l in open(f"{W}/{dia}_prompts.jsonl"):
        r=json.loads(l); prompts[r["id"].replace("/","__")]=(dia, r["messages"][0]["content"], r["messages"][1]["content"])
by=defaultdict(list)  # tid -> [(F or None, status, code)]
for d in sorted(glob.glob(f"{W}/gen_*_s*")):
    mp=f"{d}/dialect_metrics.jsonl"
    if not os.path.exists(mp): continue
    for l in open(mp):
        r=json.loads(l); tid=r["id"]; code=code_of(d, tid)
        if not code or code.count("\n")<3: continue
        by[tid].append((r.get("f@0.05"), r.get("gen"), code))
pairs=[]; kinds=Counter(); per=Counter()
for tid, lst in by.items():
    dia, sys_, user=prompts[tid]; ok=[x for x in lst if x[1]=="OK" and x[0] is not None]; bad=[x for x in lst if x[1]!="OK"]
    if not ok: continue
    best=max(ok, key=lambda x:x[0]); worst=min(ok, key=lambda x:x[0])
    def mk(ch, rj, kind):
        if ch==rj: return
        pairs.append({"conversations":[{"from":"human","value":user}],"system":sys_,"chosen":{"from":"gpt","value":ch if ch.startswith("```") else "```\n"+ch+"\n```"},"rejected":{"from":"gpt","value":rj if rj.startswith("```") else "```\n"+rj+"\n```"}}); kinds[kind]+=1; per[dia]+=1
    if best[0]>=0.25 and len(ok)>=2 and best[0]-worst[0]>=0.15: mk(best[2], worst[2], "geo")
    if best[0]>=0.25 and bad: mk(best[2], random.choice(bad)[2], "exec")
random.shuffle(pairs); json.dump(pairs, open("data/lf/dpo_geo_pairs.json","w"), ensure_ascii=False)
info=json.load(open("data/lf/dataset_info.json")); info["dpo_geo_pairs"]={"file_name":"dpo_geo_pairs.json","ranking":True,"formatting":"sharegpt","columns":{"messages":"conversations","system":"system","chosen":"chosen","rejected":"rejected"}}
json.dump(info, open("data/lf/dataset_info.json","w"), indent=2)
okF=[x[0] for l in by.values() for x in l if x[1]=="OK" and x[0] is not None]
print("geo pairs:", len(pairs), dict(kinds), dict(per), "| prompts with samples:", len(by), "| mean F of OK samples %.3f" % (sum(okF)/max(1,len(okF))))
PY
sed -e "s|model_name_or_path:.*|model_name_or_path: $(readlink -f $M)|" -e "s|dataset: dpo_exec_pairs|dataset: dpo_geo_pairs|" -e "s|output_dir:.*|output_dir: /wekafs/ict/hx_624/llm-ft/runs/lf_qwen35_9b_dpo_geo_md_xl|" -e "s|overwrite_output_dir:.*|overwrite_output_dir: true|" configs/lf/dpo_exec_v1.yaml > configs/lf/dpo_geo_md_xl.yaml
echo "GEO_PAIRS_READY"

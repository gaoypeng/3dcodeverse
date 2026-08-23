#!/bin/bash
# Finish the self-training round after its script was stopped post-sampling: exec (CPU) -> build data -> (after 3-GPU full FT) train on GPUs 0,3 -> eval. Then re-run Qwen3-8B on GPU 1.
cd /wekafs/ict/hx_624/llm-ft; W=data/selftrain_round1; K=2
until grep -q "self-train script stopped" logs/exp_full_v2_3gpu.log 2>/dev/null; do sleep 30; done
source /wekafs/ict/hx_624/llm-ft/env.sh
for i in $(seq 1 $K); do [ -f $W/gen_s$i/exec_results.jsonl ] || python eval/run_bench.py --gen_dir $W/gen_s$i --workers 48 --timeout 120 > $W/gen_s$i/exec.log 2>&1; grep "status counts" $W/gen_s$i/exec.log; done
echo "[self] $(date) exec done"
python - <<PY
import json, hashlib, os, random, shutil
random.seed(0); W="$W"; K=$K; SRC="data/sft_v1"
prompts={json.loads(l)["task"]: json.loads(l) for l in open(f"{W}/prompts.jsonl")}
base=[json.loads(l) for l in open(f"{SRC}/train.jsonl")]
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
        seen.add(h); p=prompts[r["task"]]
        new.append({"messages": p["messages"]+[{"role":"assistant","content":"```python\n"+code+"\n```"}], "source":"selftrain", "id":r["task"], "prompt_kind":"self"})
print(f"verified OK generations: {n_ok}, new unique samples: {len(new)}")
out="data/sft_v11_selftrain"; os.makedirs(out, exist_ok=True)
allrows=base+new; random.shuffle(allrows)
with open(f"{out}/train.jsonl","w") as f:
    for r in allrows: f.write(json.dumps(r, ensure_ascii=False)+"\n")
shutil.copy(f"{SRC}/val.jsonl", f"{out}/val.jsonl")
json.dump({"n_train":len(allrows),"n_new":len(new),"n_ok_generations":n_ok}, open(f"{out}/stats.json","w"), indent=1)
PY
python scripts/to_llamafactory.py --data_dir data/sft_v11_selftrain --name v11_selftrain >/dev/null && python scripts/make_lora_cfg.py v11_selftrain v11_selftrain >/dev/null
cat data/sft_v11_selftrain/stats.json; echo "[self] $(date) data built"
until grep -q "FULL3_DONE\|TRAIN FAILED" logs/exp_full_v2_3gpu.log 2>/dev/null; do sleep 30; done
until [ "$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits -i 0,3 | sort -n | tail -1)" -lt 10000 ]; do sleep 15; done
scripts/run_exp.sh 0,3 v11_selftrain 2>&1 | tee -a logs/exp_v11_selftrain.log
echo "[self] DONE"

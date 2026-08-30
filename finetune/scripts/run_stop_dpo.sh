#!/bin/bash
# The 9B does not fail OpenSCAD because it forgot the dialect -- greedy walks it into a repetition loop. The
# proof: 6.0% greedy vs 44.0% sampled on the same weights (base is 46%), with 30,166 mean tokens greedy against
# 884 in its training answers, and the longest output is 576 lines of "holder holder holder" comments with no
# geometry at all. Sampling is a workaround; this trains the behaviour out. The model generates its own negatives
# (its runaway greedy output) and its own positives (a sampled generation that finishes AND executes), on
# TRAINING prompts only -- the dialect suites are held-out and must not be trained on.
set -uo pipefail; cd /wekafs/ict/hx_624/llm-ft
W=data/stop_dpo; BASE=runs/lf_qwen35_9b_mmmix2/merged
[ -d "$BASE" ] || { echo "[stopdpo] no base model"; exit 0; }
G=""; for _ in $(seq 1 60); do G=${GPUS:-$(scripts/free_gpus.sh 40000 | cut -d, -f1)}; [ -n "$G" ] && break; sleep 30; done
[ -z "$G" ] && { echo "[stopdpo] no GPU with 40 GB free"; exit 0; }
source /wekafs/ict/hx_624/anaconda3/etc/profile.d/conda.sh; conda activate vllm
export VLLM_CACHE_ROOT=/wekafs/ict/hx_624/cache/vllm HF_HOME=/wekafs/ict/hx_624/cache/huggingface VLLM_LOGGING_LEVEL=WARNING CUDA_VISIBLE_DEVICES=$G
# greedy produces the negatives, sampling the positives -- exactly the two behaviours we want to separate
[ -d $W/gen_greedy/. ] || python eval/generate_vllm.py --model $BASE --prompts $W/prompts.jsonl --out $W/gen_greedy \
  --tp 1 --no_think --max_new_tokens 16384 --max_model_len 20480 2>&1 | tail -1
for i in 1 2; do
  [ -d $W/gen_s$i/. ] || python eval/generate_vllm.py --model $BASE --prompts $W/prompts.jsonl --out $W/gen_s$i \
    --tp 1 --no_think --max_new_tokens 16384 --max_model_len 20480 --temperature 0.8 --seed $((700+i)) 2>&1 | tail -1
done
conda activate llmft
python - <<'PY'
import json, os, glob, random, collections
random.seed(5); W = "data/stop_dpo"
prompts = {json.loads(l)["task"]: json.loads(l) for l in open(f"{W}/prompts.jsonl")}
def load(d):
    out = {}
    p = f"{d}/gens_shard0.jsonl"
    if not os.path.exists(p): return out
    for l in open(p):
        r = json.loads(l); t = r["task"]
        cp = f"{d}/{t}/code.py"
        if not os.path.exists(cp): continue
        out[t] = {"finished": r.get("finished", True), "ntok": r.get("n_new_tokens", 0),
                  "code": open(cp, encoding="utf-8", errors="replace").read().strip()}
    return out
greedy = load(f"{W}/gen_greedy")
samples = [load(f"{W}/gen_s{i}") for i in (1, 2)]
pairs, why = [], collections.Counter()
for t, g in greedy.items():
    # a negative must actually be a runaway: unfinished, or wildly longer than anything in the training data
    bad = (not g["finished"]) or g["ntok"] > 8192
    if not bad: why["greedy_was_fine"] += 1; continue
    cands = [s[t] for s in samples if t in s and s[t]["finished"] and s[t]["ntok"] <= 8192 and len(s[t]["code"]) > 60]
    if not cands: why["no_clean_sample"] += 1; continue
    good = min(cands, key=lambda c: c["ntok"])
    if good["code"] == g["code"]: why["identical"] += 1; continue
    p = prompts[t]
    pairs.append({"conversations": [{"from": "human", "value": p["messages"][1]["content"]}],
                  "system": p["messages"][0]["content"],
                  "chosen": {"from": "gpt", "value": good["code"]},
                  "rejected": {"from": "gpt", "value": g["code"][:60000]},
                  "dialect": p["dialect"]})
    why["paired"] += 1
random.shuffle(pairs)
json.dump(pairs, open("data/lf/stop_dpo_pairs.json", "w"), ensure_ascii=False)
info = json.load(open("data/lf/dataset_info.json"))
info["stop_dpo_pairs"] = {"file_name": "stop_dpo_pairs.json", "ranking": True, "formatting": "sharegpt",
                          "columns": {"messages": "conversations", "system": "system", "chosen": "chosen", "rejected": "rejected"}}
json.dump(info, open("data/lf/dataset_info.json", "w"), indent=2)
print("[stopdpo] pairs:", len(pairs), dict(why))
print("[stopdpo] by dialect:", dict(collections.Counter(p["dialect"] for p in pairs)))
PY
echo "[stopdpo] DATA DONE"

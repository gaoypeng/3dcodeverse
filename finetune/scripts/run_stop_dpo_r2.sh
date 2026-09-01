#!/bin/bash
# Round 2 of the same idea, but generated from the round-1 model. The first attempt at this reused round 1's
# directories because of a "skip if it exists" guard and rebuilt an identical pair set in nine seconds while
# reporting success -- a real round has to see what the CURRENT model still gets wrong.
set -uo pipefail; cd /wekafs/ict/hx_624/llm-ft
W=data/stop_dpo_r2; BASE=runs/lf_qwen35_9b_stopdpo/merged
[ -d "$BASE" ] || { echo "[stop2] round-1 model missing"; exit 0; }
mkdir -p $W; cp data/stop_dpo/prompts.jsonl $W/prompts.jsonl
G=""; for _ in $(seq 1 60); do G=${GPUS:-$(scripts/free_gpus.sh 40000 | cut -d, -f1)}; [ -n "$G" ] && break; sleep 30; done
[ -z "$G" ] && { echo "[stop2] no GPU with 40 GB free"; exit 0; }
source /wekafs/ict/hx_624/anaconda3/etc/profile.d/conda.sh; conda activate vllm
export VLLM_CACHE_ROOT=/wekafs/ict/hx_624/cache/vllm HF_HOME=/wekafs/ict/hx_624/cache/huggingface VLLM_LOGGING_LEVEL=WARNING CUDA_VISIBLE_DEVICES=$G
echo "[stop2] $(date) sampling round-1 model on GPU $G"
python eval/generate_vllm.py --model $BASE --prompts $W/prompts.jsonl --out $W/gen_greedy --tp 1 --no_think \
  --max_new_tokens 16384 --max_model_len 20480 2>&1 | tail -1
for i in 1 2; do
  python eval/generate_vllm.py --model $BASE --prompts $W/prompts.jsonl --out $W/gen_s$i --tp 1 --no_think \
    --max_new_tokens 16384 --max_model_len 20480 --temperature 0.8 --seed $((900+i)) 2>&1 | tail -1
done
conda activate llmft
python - "$W" <<'PY'
import json, os, sys, random, collections
random.seed(9); W = sys.argv[1]
prompts = {json.loads(l)["task"]: json.loads(l) for l in open(f"{W}/prompts.jsonl")}
def load(d):
    out = {}; p = f"{d}/gens_shard0.jsonl"
    if not os.path.exists(p): return out
    for l in open(p):
        r = json.loads(l); t = r["task"]; cp = f"{d}/{t}/code.py"
        if os.path.exists(cp):
            out[t] = {"finished": r.get("finished", True), "ntok": r.get("n_new_tokens", 0),
                      "code": open(cp, encoding="utf-8", errors="replace").read().strip()}
    return out
greedy = load(f"{W}/gen_greedy"); samples = [load(f"{W}/gen_s{i}") for i in (1, 2)]
pairs, why = [], collections.Counter()
for t, g in greedy.items():
    if not ((not g["finished"]) or g["ntok"] > 8192): why["greedy_was_fine"] += 1; continue
    cands = [s[t] for s in samples if t in s and s[t]["finished"] and s[t]["ntok"] <= 8192 and len(s[t]["code"]) > 60]
    if not cands: why["no_clean_sample"] += 1; continue
    good = min(cands, key=lambda c: c["ntok"])
    if good["code"] == g["code"]: why["identical"] += 1; continue
    p = prompts[t]
    pairs.append({"conversations": [{"from": "human", "value": p["messages"][1]["content"]}],
                  "system": p["messages"][0]["content"],
                  "chosen": {"from": "gpt", "value": good["code"]},
                  "rejected": {"from": "gpt", "value": g["code"][:60000]}, "dialect": p["dialect"]})
    why["paired"] += 1
random.shuffle(pairs)
json.dump(pairs, open("data/lf/stop_dpo_pairs_r2.json", "w"), ensure_ascii=False)
info = json.load(open("data/lf/dataset_info.json"))
info["stop_dpo_pairs_r2"] = {"file_name": "stop_dpo_pairs_r2.json", "ranking": True, "formatting": "sharegpt",
                             "columns": {"messages": "conversations", "system": "system", "chosen": "chosen", "rejected": "rejected"}}
json.dump(info, open("data/lf/dataset_info.json", "w"), indent=2)
print("[stop2] pairs:", len(pairs), dict(why))
print("[stop2] by dialect:", dict(collections.Counter(p["dialect"] for p in pairs)))
PY
N=$(python3 -c "import json;print(len(json.load(open('data/lf/stop_dpo_pairs_r2.json'))))" 2>/dev/null || echo 0)
# round 1 fixed most of the runaways; if few remain there is nothing left to train on, and that is the result
[ "${N:-0}" -lt 100 ] && { echo "[stop2] only $N pairs left after round 1 — the behaviour is largely gone, stopping here"; exit 0; }
sed -e "s|model_name_or_path:.*|model_name_or_path: $(readlink -f $BASE)|" -e "s|dataset: stop_dpo_pairs|dataset: stop_dpo_pairs_r2|" \
    -e "s|^output_dir:.*|output_dir: /wekafs/ict/hx_624/llm-ft/runs/lf_qwen35_9b_stopdpo_r2|" configs/lf/dpo_stop.yaml > configs/lf/dpo_stop_r2.yaml
RUN=runs/lf_qwen35_9b_stopdpo_r2
GPUS=$G scripts/lf_train.sh configs/lf/dpo_stop_r2.yaml > logs/train_stopdpo_r2.log 2>&1
[ -f $RUN/train_results.json ] || { echo "[stop2] TRAIN FAILED: $(grep -iE 'out of memory|Error' logs/train_stopdpo_r2.log | grep -v errors | tail -1 | cut -c1-180)"; exit 0; }
sed -e "s|model_name_or_path:.*|model_name_or_path: $(readlink -f $BASE)|" -e "s|adapter_name_or_path:.*|adapter_name_or_path: $(readlink -f $RUN)|" -e "s|export_dir:.*|export_dir: $(readlink -f $RUN)/merged|" configs/lf/export_lora_template.yaml > configs/lf/export_stopdpo_r2.yaml
scripts/lf_export.sh configs/lf/export_stopdpo_r2.yaml > logs/export_stopdpo_r2.log 2>&1 || { echo "[stop2] EXPORT FAILED"; exit 0; }
GPUS=$G TP=1 bash eval/eval_all_run2.sh $RUN/merged q9b_stopdpo_r2_greedy 2>&1 | tail -9
GPUS=$G TP=1 bash eval/eval_all_run2.sh $RUN/merged q9b_stopdpo_r2_t07 --temp 0.7 --seed 1 2>&1 | tail -9
echo "[stop2] DONE"

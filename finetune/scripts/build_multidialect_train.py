"""Subsample per-dialect train sets (token-filtered ≤ 8192), build LF datasets md_<dialect> and md_mixed."""
import json, random, os, subprocess
from transformers import AutoTokenizer
random.seed(3); tok = AutoTokenizer.from_pretrained("/wekafs/ict/hx_624/models/Qwen3.5-9B")
MD = "/wekafs/ict/hx_624/llm-ft/data/multidialect"; CAP = {"blender": 6000, "cadquery": 10000, "openscad": 2000, "glsl": 10000}
mixed = []
for d, cap in CAP.items():
    rows = [json.loads(l) for l in open(f"{MD}/{d}/train.jsonl")]; random.shuffle(rows)
    keep = []
    for r in rows:
        if len(keep) >= cap: break
        n = len(tok(tok.apply_chat_template(r["messages"], tokenize=False)).input_ids)
        if n <= 8192: keep.append(r)
    out = f"/wekafs/ict/hx_624/llm-ft/data/md_{d}"; os.makedirs(out, exist_ok=True)
    nv = max(20, len(keep) // 50); val, train = keep[:nv], keep[nv:]
    for split, rs in [("train", train), ("val", val)]:
        with open(f"{out}/{split}.jsonl", "w") as f:
            for r in rs: f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(d, "train", len(train), "val", len(val)); mixed += train
random.shuffle(mixed); out = "/wekafs/ict/hx_624/llm-ft/data/md_mixed"; os.makedirs(out, exist_ok=True)
with open(f"{out}/train.jsonl", "w") as f:
    for r in mixed: f.write(json.dumps(r, ensure_ascii=False) + "\n")
# mixed val = concat of per-dialect vals
with open(f"{out}/val.jsonl", "w") as f:
    for d in CAP:
        for l in open(f"/wekafs/ict/hx_624/llm-ft/data/md_{d}/val.jsonl"): f.write(l)
print("mixed train", len(mixed))

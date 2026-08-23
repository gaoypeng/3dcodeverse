"""md_big: larger multi-dialect SFT set (token-filtered <=8192): blender (multidialect train + v13 bootstrapped), cadquery 40k, glsl 40k, openscad all."""
import json, random, os, sys
sys.path.insert(0, "/wekafs/ict/hx_624/llm-ft/scripts"); from ntok_cache import load_ntok
from transformers import AutoTokenizer
random.seed(11); tok = AutoTokenizer.from_pretrained("/wekafs/ict/hx_624/models/Qwen3.5-9B")
MD = "/wekafs/ict/hx_624/llm-ft/data/multidialect"; CAP = {"cadquery": 40000, "glsl": 40000, "openscad": 5000, "blender": 10000}
def ntok(r): return len(tok(tok.apply_chat_template(r["messages"], tokenize=False)).input_ids)
out_rows = []; stats = {}
for d, cap in CAP.items():
    rows = [json.loads(l) for l in open(f"{MD}/{d}/train.jsonl")]; NT = load_ntok(f"{MD}/{d}/train.jsonl", tok)
    for r, n_ in zip(rows, NT): r["_ntok"] = n_
    random.shuffle(rows); keep = []; toks = 0
    for r in rows:
        if len(keep) >= cap: break
        n = r.pop("_ntok")
        if n <= 8192: keep.append(r); toks += n
    stats[d] = (len(keep), toks); out_rows += keep
# bootstrapped blender samples (execution-verified, novel prompts)
BOOT = "/wekafs/ict/hx_624/llm-ft/data/sft_v13_boot/train.jsonl"; bl = open(BOOT).read().splitlines(); bn = load_ntok(BOOT, tok)
boot = [(json.loads(l), n) for l, n in zip(bl, bn) if '"source": "boot_' in l]
bt = 0; kb = []
for r, n in boot:
    if n <= 8192: kb.append({"messages": r["messages"], "dialect": "blender", "subset": "boot"}); bt += n
stats["blender_boot"] = (len(kb), bt); out_rows += kb
random.shuffle(out_rows)
out = "/wekafs/ict/hx_624/llm-ft/data/md_big"; os.makedirs(out, exist_ok=True)
with open(f"{out}/train.jsonl", "w") as f:
    for r in out_rows: f.write(json.dumps(r, ensure_ascii=False) + "\n")
os.system(f"cp /wekafs/ict/hx_624/llm-ft/data/md_mixed/val.jsonl {out}/val.jsonl")
tot = sum(v[1] for v in stats.values())
print({k: (v[0], round(v[1]/1e6, 1)) for k, v in stats.items()}, "| total samples", len(out_rows), "| tokens/epoch %.1fM" % (tot/1e6))

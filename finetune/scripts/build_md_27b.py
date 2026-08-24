"""md_27b: 5-dialect mix sized for a 27B LoRA run (~50M tok/epoch): CadQuery 15k, GLSL 12k, Blender all + boot 8k, OpenSCAD x4, three.js."""
import json, random, os, sys
sys.path.insert(0, "/wekafs/ict/hx_624/llm-ft/scripts"); from ntok_cache import load_ntok
from transformers import AutoTokenizer
random.seed(14); tok = AutoTokenizer.from_pretrained("/wekafs/ict/hx_624/models/Qwen3.5-9B")
MD = "/wekafs/ict/hx_624/llm-ft/data/multidialect"; CAP = {"cadquery": 15000, "glsl": 12000, "openscad": 10**9, "blender": 10**9, "threejs": 10**9}; REP = {"openscad": 4}; BOOT_REP = 1; BOOT_CAP = 8000
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
    rep = REP.get(d, 1); stats[d] = (len(keep) * rep, toks * rep); out_rows += keep * rep
BOOT = "/wekafs/ict/hx_624/llm-ft/data/sft_v13_boot/train.jsonl"; bl = open(BOOT).read().splitlines(); bn = load_ntok(BOOT, tok)
boot = [(json.loads(l), n) for l, n in zip(bl, bn) if '"source": "boot_' in l]
bt = 0; kb = []
for r, n in boot:
    if n <= 8192: kb.append({"messages": r["messages"], "dialect": "blender", "subset": "boot"}); bt += n
import random as _r; _r.shuffle(kb); kb = kb[:BOOT_CAP]; bt = sum(n for _, n in kb_ntok[:BOOT_CAP]) if False else int(bt * BOOT_CAP / max(1, len(boot)))
stats["blender_boot"] = (len(kb), bt); out_rows += kb
# LLaMA-Factory's qwen3_5 (multimodal) template treats literal <image>/<video>/<audio> in text as media placeholders -> escape them
def _san(t): return t.replace("<image>", "<image >").replace("<video>", "<video >").replace("<audio>", "<audio >")
for r in out_rows:
    for m in r["messages"]: m["content"] = _san(m["content"])
random.shuffle(out_rows)
out = "/wekafs/ict/hx_624/llm-ft/data/md_27b"; os.makedirs(out, exist_ok=True)
with open(f"{out}/train.jsonl", "w") as f:
    for r in out_rows: f.write(json.dumps(r, ensure_ascii=False) + "\n")
os.system(f"cp /wekafs/ict/hx_624/llm-ft/data/md_mixed/val.jsonl {out}/val.jsonl")
tot = sum(v[1] for v in stats.values())
print({k: (v[0], round(v[1]/1e6, 1)) for k, v in stats.items()}, "| total samples", len(out_rows), "| tokens/epoch %.1fM" % (tot/1e6))

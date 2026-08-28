"""Helper for build scripts: load cached token counts for a jsonl (scripts/cache_ntok.py), falling back to tokenizing."""
import os
def load_ntok(path, tok=None):
    c = path + ".ntok"
    if os.path.exists(c):
        ns = [int(x) for x in open(c).read().split()]
        if ns and sum(1 for _ in open(path)) == len(ns): return ns
    if tok is None:
        from transformers import AutoTokenizer; tok = AutoTokenizer.from_pretrained("/wekafs/ict/hx_624/models/Qwen3.5-9B")
    import json
    ns = [len(tok(tok.apply_chat_template(json.loads(l)["messages"], tokenize=False)).input_ids) for l in open(path)]
    open(c, "w").write("\n".join(map(str, ns)) + "\n"); return ns

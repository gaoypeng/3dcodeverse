"""One-off: cache per-sample token counts (Qwen3.5 chat template) for every multidialect train/test file and the bootstrapped set.
Writes <file>.ntok (one int per line, same order). Build scripts read this instead of re-tokenizing."""
import json, os, sys, glob
from multiprocessing import Pool
FILES = sorted(glob.glob("/wekafs/ict/hx_624/llm-ft/data/multidialect/*/train.jsonl") + glob.glob("/wekafs/ict/hx_624/llm-ft/data/multidialect/*/test.jsonl")) + ["/wekafs/ict/hx_624/llm-ft/data/sft_v13_boot/train.jsonl"]
tok = None
def init():
    global tok
    from transformers import AutoTokenizer
    tok = AutoTokenizer.from_pretrained("/wekafs/ict/hx_624/models/Qwen3.5-9B")
def count(line):
    r = json.loads(line); return len(tok(tok.apply_chat_template(r["messages"], tokenize=False)).input_ids)
if __name__ == "__main__":
    with Pool(12, initializer=init) as pool:
        for f in FILES:
            out = f + ".ntok"
            if os.path.exists(out) and sum(1 for _ in open(out)) == sum(1 for _ in open(f)): print("cached", f); continue
            lines = open(f).read().splitlines()
            ns = pool.map(count, lines, chunksize=256)
            open(out, "w").write("\n".join(map(str, ns)) + "\n"); print(f, len(ns), "done", flush=True)

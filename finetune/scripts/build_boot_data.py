"""Build bootstrapped SFT sets from execution-verified generations (proper python file; the earlier bash-heredoc version mangled the ```python fence via backtick substitution).
usage: build_boot_data.py <round_dir> <out_name> [--base data/sft_v1]"""
import argparse, glob, hashlib, json, os, random, shutil
ap = argparse.ArgumentParser(); ap.add_argument("round_dir"); ap.add_argument("out_name"); ap.add_argument("--base", default="data/sft_v1"); ap.add_argument("--max_per_prompt", type=int, default=2)
a = ap.parse_args(); random.seed(0)
W = a.round_dir
prompts = {}
for f in glob.glob(f"{W}/prompts*.jsonl"):
    for l in open(f):
        r = json.loads(l); prompts[r["task"]] = r
base = [json.loads(l) for l in open(f"{a.base}/train.jsonl")]
def h_of(code): return hashlib.sha1(("```python\n" + code + "\n```").encode()).hexdigest()
base_hashes = {hashlib.sha1(r["messages"][-1]["content"].encode()).hexdigest() for r in base}
seen = set(base_hashes); new = []; n_ok = 0; dup_base = 0; dup_self = 0; per_prompt = {}
for d in sorted(glob.glob(f"{W}/gen_s*/")) + sorted(glob.glob(f"{W}/gen_s*_g*/")):
    if not os.path.exists(os.path.join(d, "exec_results.jsonl")): continue
    for l in open(os.path.join(d, "exec_results.jsonl")):
        r = json.loads(l)
        if r["status"] != "OK": continue
        n_ok += 1
        code = open(os.path.join(d, r["task"], "code.py")).read().strip()
        if code.count("\n") < 4: continue
        h = h_of(code)
        if h in base_hashes: dup_base += 1; continue
        if h in seen: dup_self += 1; continue
        if per_prompt.get(r["task"], 0) >= a.max_per_prompt: continue
        seen.add(h); per_prompt[r["task"]] = per_prompt.get(r["task"], 0) + 1
        p = prompts[r["task"]]
        new.append({"messages": p["messages"][:2] + [{"role": "assistant", "content": "```python\n" + code + "\n```"}],
                    "source": "boot_" + str(p.get("src", p.get("source", "train"))), "id": r["task"], "prompt_kind": "boot"})
out = f"data/{a.out_name}"; os.makedirs(out, exist_ok=True)
allrows = base + new; random.shuffle(allrows)
with open(f"{out}/train.jsonl", "w") as f:
    for r in allrows: f.write(json.dumps(r, ensure_ascii=False) + "\n")
shutil.copy(f"{a.base}/val.jsonl", f"{out}/val.jsonl")
stats = {"n_train": len(allrows), "n_base": len(base), "n_new": len(new), "n_ok_generations": n_ok, "dup_of_training_code": dup_base, "dup_within_generations": dup_self, "prompts_with_new": len(per_prompt)}
json.dump(stats, open(f"{out}/stats.json", "w"), indent=1); print(json.dumps(stats))

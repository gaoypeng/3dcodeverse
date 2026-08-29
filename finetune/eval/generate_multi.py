"""Run several evaluation suites through ONE vLLM engine (loading a 27B twice costs more than generating).

  python eval/generate_multi.py --model M --tp 2 --spec '[{"name":"bench","prompts":"...","out":"...","dialect":"blender"}, ...]'
        [--no_think] [--temperature 0.7] [--seed 1] [--max_new_tokens 12288] [--max_model_len 16384] [--limit N]

Writes, per job: <out>/<task>/{code.py,raw.txt}, <out>/gens_shard0.jsonl, <out>/extract_stats.json — identical to
generate_vllm.py, so every downstream executor/metric script works unchanged.
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from extract import extract, summarize



def auto_gpu_mem(requested, tp=1, headroom_gib=4.0):
    """`gpu_memory_utilization` is a fraction of the card's TOTAL memory, but this box is shared — asking for
    0.88 of a card that another user already half fills makes vLLM refuse to start. Derive the fraction from what
    is actually free, keeping a little headroom, and never exceed what the caller asked for."""
    try:
        import subprocess
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.total,memory.used", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=30).stdout.strip().splitlines()
        vis = os.environ.get("CUDA_VISIBLE_DEVICES", "")
        idx = [int(x) for x in vis.split(",") if x.strip().isdigit()] or list(range(len(out)))
        worst = 1.0
        for i in idx[:max(1, tp)]:
            tot, used = (int(v) for v in out[i].split(","))
            free_gib = (tot - used) / 1024.0
            worst = min(worst, max(0.10, (free_gib - headroom_gib) / (tot / 1024.0)))
        if worst < requested:
            print(f"[gen] gpu_memory_utilization {requested} -> {worst:.2f} (the card is shared)", flush=True)
            return round(worst, 2)
    except Exception as e:
        print(f"[gen] auto_gpu_mem failed ({e}); keeping {requested}", flush=True)
    return requested


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--spec", required=True, help="JSON list (or @file) of {name,prompts,out,dialect}")
    ap.add_argument("--max_new_tokens", type=int, default=8192)
    ap.add_argument("--max_model_len", type=int, default=12288)
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--no_think", action="store_true")
    ap.add_argument("--tp", type=int, default=1)
    ap.add_argument("--gpu_mem", type=float, default=0.88)
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()
    spec = json.load(open(a.spec[1:])) if a.spec.startswith("@") else json.loads(a.spec)

    from vllm import LLM, SamplingParams
    t_load = time.time()
    llm = LLM(model=a.model, dtype="bfloat16", tensor_parallel_size=a.tp, max_model_len=a.max_model_len,
              gpu_memory_utilization=auto_gpu_mem(a.gpu_mem, getattr(a, 'tp', 1)), enable_prefix_caching=True,
              limit_mm_per_prompt={"image": 0, "video": 0}, trust_remote_code=True)
    print(f"[gen-multi] engine up in {time.time()-t_load:.0f}s | jobs: {[j['name'] for j in spec]}", flush=True)
    sp = SamplingParams(temperature=a.temperature, top_p=0.95 if a.temperature > 0 else 1.0,
                        max_tokens=a.max_new_tokens, seed=a.seed)
    kw = {"chat_template_kwargs": {"enable_thinking": False}} if a.no_think else {}

    for job in spec:
        rows = [json.loads(l) for l in open(job["prompts"])]
        if a.limit:
            rows = rows[: a.limit]
        os.makedirs(job["out"], exist_ok=True)
        t0 = time.time()
        outs = llm.chat([r["messages"] for r in rows], sp, use_tqdm=True, **kw)
        el = time.time() - t0
        ntok, metas = 0, []
        with open(f"{job['out']}/gens_shard0.jsonl", "w") as f:
            for r, o in zip(rows, outs):
                text = o.outputs[0].text
                n = len(o.outputs[0].token_ids)
                ntok += n
                d = f"{job['out']}/{r['task']}"
                os.makedirs(d, exist_ok=True)
                code, meta = extract(text, job.get("dialect", "auto"))
                metas.append(meta)
                open(f"{d}/code.py", "w").write(code + "\n")
                open(f"{d}/raw.txt", "w").write(text)
                f.write(json.dumps({"task": r["task"], "n_new_tokens": n,
                                    "finished": o.outputs[0].finish_reason == "stop",
                                    "has_code_block": "```" in text, "extract": meta}) + "\n")
        stats = summarize(metas)
        stats.update({"model": a.model, "dialect": job.get("dialect"), "temperature": a.temperature,
                      "no_think": a.no_think, "max_new_tokens": a.max_new_tokens,
                      "tok_per_s": round(ntok / max(el, 1e-6), 1), "mean_new_tokens": round(ntok / max(1, len(rows)), 1),
                      "truncated": sum(1 for r, o in zip(rows, outs) if o.outputs[0].finish_reason != "stop")})
        json.dump(stats, open(f"{job['out']}/extract_stats.json", "w"), indent=2)
        print(f"[gen-multi] {job['name']}: {len(rows)} prompts | {ntok} tok | {ntok/el:.0f} tok/s | {el/60:.1f} min", flush=True)
        print("[extract] " + json.dumps({k: stats[k] for k in ("with_think", "no_fence", "from_think_fallback",
                                                               "multi_block", "chosen_not_last", "syntax_valid",
                                                               "no_dialect_marker", "truncated")}), flush=True)
    print("[gen-multi] ALL GENERATION DONE", flush=True)


if __name__ == "__main__":
    main()

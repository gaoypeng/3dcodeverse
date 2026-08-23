"""vLLM version of generate.py: same outputs (<out>/<task>/code.py + raw.txt, <out>/gens_shard0.jsonl)."""
import argparse, json, os, re, time, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from generate import extract_code

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True); ap.add_argument("--prompts", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--max_new_tokens", type=int, default=6144); ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--max_model_len", type=int, default=12288); ap.add_argument("--no_think", action="store_true")
    ap.add_argument("--tp", type=int, default=1); ap.add_argument("--gpu_mem", type=float, default=0.88); ap.add_argument("--limit", type=int, default=None); ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    from vllm import LLM, SamplingParams
    os.makedirs(a.out, exist_ok=True)
    rows = [json.loads(l) for l in open(a.prompts)]
    if a.limit: rows = rows[:a.limit]
    llm = LLM(model=a.model, dtype="bfloat16", tensor_parallel_size=a.tp, max_model_len=a.max_model_len, gpu_memory_utilization=a.gpu_mem,
              enable_prefix_caching=True, limit_mm_per_prompt={"image": 0, "video": 0}, trust_remote_code=True)
    sp = SamplingParams(temperature=a.temperature, top_p=0.95 if a.temperature > 0 else 1.0, max_tokens=a.max_new_tokens, seed=a.seed)
    kw = {"chat_template_kwargs": {"enable_thinking": False}} if a.no_think else {}
    t0 = time.time()
    outs = llm.chat([r["messages"] for r in rows], sp, use_tqdm=True, **kw)
    el = time.time() - t0; ntok = 0
    with open(f"{a.out}/gens_shard0.jsonl", "w") as f:
        for r, o in zip(rows, outs):
            text = o.outputs[0].text; n = len(o.outputs[0].token_ids); ntok += n
            d = f"{a.out}/{r['task']}"; os.makedirs(d, exist_ok=True)
            open(f"{d}/code.py", "w").write(extract_code(text) + "\n"); open(f"{d}/raw.txt", "w").write(text)
            f.write(json.dumps({"task": r["task"], "n_new_tokens": n, "finished": o.outputs[0].finish_reason == "stop", "has_code_block": "```" in text}) + "\n")
    print(f"[gen-vllm] {len(rows)} prompts | {ntok} tok | {ntok/el:.0f} tok/s | {el/60:.1f} min", flush=True)

if __name__ == "__main__":
    main()

"""Batched generation for the 3DCodeBench prompts with a HF causal LM. Writes <out>/<task>/code.py + gens.jsonl"""
import argparse, json, os, re, time, torch
from transformers import AutoTokenizer, AutoModelForCausalLM

def extract_code(text):
    m = re.findall(r"```[a-zA-Z0-9_+.-]*[ \t]*\n(.*?)```", text, flags=re.S)   # any language fence (python/glsl/openscad/...)
    if m:
        return max(m, key=len).strip("\n")
    m = re.search(r"```[a-zA-Z0-9_+.-]*[ \t]*\n(.*)$", text, flags=re.S)  # unterminated block
    if m: return m.group(1).strip("\n")
    return text.strip()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--prompts", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--batch_size", type=int, default=16)
    ap.add_argument("--max_new_tokens", type=int, default=6144)
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--top_p", type=float, default=0.95)
    ap.add_argument("--shard", type=int, default=0); ap.add_argument("--num_shards", type=int, default=1)
    ap.add_argument("--attn", default="auto")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--no_think", action="store_true", help="pass enable_thinking=False to the chat template (Qwen3/3.5)")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    attn = a.attn
    if attn == "auto":
        try: import flash_attn; attn = "flash_attention_2"  # noqa
        except Exception: attn = "sdpa"
    tok = AutoTokenizer.from_pretrained(a.model, padding_side="left")
    if tok.pad_token is None: tok.pad_token = tok.eos_token
    try:
        model = AutoModelForCausalLM.from_pretrained(a.model, dtype=torch.bfloat16, attn_implementation=attn, device_map="cuda")
    except Exception as e:  # multimodal wrappers (e.g. Qwen3.5 = Qwen3_5ForConditionalGeneration)
        print("[gen] AutoModelForCausalLM failed (%s); falling back to AutoModelForImageTextToText" % str(e)[:200], flush=True)
        from transformers import AutoModelForImageTextToText
        model = AutoModelForImageTextToText.from_pretrained(a.model, dtype=torch.bfloat16, attn_implementation=attn, device_map="cuda")
    model.eval()
    rows = [json.loads(l) for l in open(a.prompts)]
    rows = rows[a.shard::a.num_shards]
    if a.limit: rows = rows[:a.limit]
    # skip already-done
    done = set()
    outf = f"{a.out}/gens_shard{a.shard}.jsonl"
    if os.path.exists(outf):
        done = {json.loads(l)["task"] for l in open(outf)}
    rows = [r for r in rows if r["task"] not in done]
    print(f"[gen] {len(rows)} prompts to do (shard {a.shard}/{a.num_shards}) attn={attn}", flush=True)
    # sort by prompt length for efficient batching
    tmpl_kwargs = {"enable_thinking": False} if a.no_think else {}
    texts = [tok.apply_chat_template(r["messages"], tokenize=False, add_generation_prompt=True, **tmpl_kwargs) for r in rows]
    if texts: print("[gen] prompt tail example:", repr(texts[0][-200:]), flush=True)
    order = sorted(range(len(rows)), key=lambda i: len(texts[i]))
    gen_kwargs = dict(max_new_tokens=a.max_new_tokens, pad_token_id=tok.pad_token_id)
    if a.temperature > 0: gen_kwargs.update(do_sample=True, temperature=a.temperature, top_p=a.top_p)
    else: gen_kwargs.update(do_sample=False)
    t0 = time.time(); ntok = 0
    with open(outf, "a") as f:
        for b in range(0, len(order), a.batch_size):
            idx = order[b:b + a.batch_size]
            enc = tok([texts[i] for i in idx], return_tensors="pt", padding=True).to("cuda")
            with torch.no_grad():
                out = model.generate(**enc, **gen_kwargs)
            gen = out[:, enc["input_ids"].shape[1]:]
            for j, i in enumerate(idx):
                ids = gen[j]
                n = int((ids != tok.pad_token_id).sum())
                text = tok.decode(ids, skip_special_tokens=True)
                code = extract_code(text)
                finished = bool((ids == tok.eos_token_id).any()) or any((ids == t).any() for t in tok.all_special_ids if t != tok.pad_token_id) if n < a.max_new_tokens else False
                task = rows[i]["task"]; d = f"{a.out}/{task}"; os.makedirs(d, exist_ok=True)
                open(f"{d}/code.py", "w").write(code + "\n"); open(f"{d}/raw.txt", "w").write(text)
                f.write(json.dumps({"task": task, "n_new_tokens": n, "finished": finished, "has_code_block": "```" in text}) + "\n"); f.flush()
                ntok += n
            el = time.time() - t0
            print(f"[gen] {min(b + a.batch_size, len(order))}/{len(order)} done | {ntok} tok | {ntok/el:.0f} tok/s | {el/60:.1f} min", flush=True)

if __name__ == "__main__":
    main()

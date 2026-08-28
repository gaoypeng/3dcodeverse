"""vLLM generation with image inputs (multimodal prompts), same outputs as generate_vllm.py.

Prompt file rows: {"task", "images": [paths], "messages": [{role, content}]} where the user content contains one
`<image>` per image. The images are attached as multi_modal_data so the model actually sees them.
"""
import argparse, json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from extract import extract, summarize

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True); ap.add_argument("--prompts", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--max_new_tokens", type=int, default=6144); ap.add_argument("--max_model_len", type=int, default=12288)
    ap.add_argument("--temperature", type=float, default=0.0); ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--tp", type=int, default=1); ap.add_argument("--gpu_mem", type=float, default=0.88)
    ap.add_argument("--dialect", default="blender"); ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()
    from vllm import LLM, SamplingParams
    from PIL import Image
    rows = [json.loads(l) for l in open(a.prompts) if l.strip()]
    if a.limit: rows = rows[:a.limit]
    os.makedirs(a.out, exist_ok=True)
    llm = LLM(model=a.model, dtype="bfloat16", tensor_parallel_size=a.tp, max_model_len=a.max_model_len,
              gpu_memory_utilization=a.gpu_mem, limit_mm_per_prompt={"image": 4, "video": 0}, trust_remote_code=True)
    sp = SamplingParams(temperature=a.temperature, top_p=0.95 if a.temperature > 0 else 1.0,
                        max_tokens=a.max_new_tokens, seed=a.seed)
    import base64, io
    def data_url(path):
        im = Image.open(path).convert("RGB")
        im.thumbnail((768, 768))
        b = io.BytesIO(); im.save(b, "JPEG", quality=88)
        return "data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode()
    convs = []
    for r in rows:
        msgs = []
        for m in r["messages"]:
            if m["role"] == "user" and r.get("images"):
                parts = [{"type": "image_url", "image_url": {"url": data_url(p)}} for p in r["images"]]
                parts.append({"type": "text", "text": m["content"].replace("<image>", "").strip()})
                msgs.append({"role": "user", "content": parts})
            else:
                msgs.append(m)
        convs.append(msgs)
    t0 = time.time()
    outs = llm.chat(convs, sp, use_tqdm=True, chat_template_kwargs={"enable_thinking": False})
    el = time.time() - t0; ntok = 0; metas = []
    with open(f"{a.out}/gens_shard0.jsonl", "w") as f:
        for r, o in zip(rows, outs):
            text = o.outputs[0].text; n = len(o.outputs[0].token_ids); ntok += n
            d = f"{a.out}/{r['task']}"; os.makedirs(d, exist_ok=True)
            code, meta = extract(text, a.dialect); metas.append(meta)
            open(f"{d}/code.py", "w").write(code + "\n"); open(f"{d}/raw.txt", "w").write(text)
            f.write(json.dumps({"task": r["task"], "n_new_tokens": n,
                                "finished": o.outputs[0].finish_reason == "stop",
                                "has_code_block": "```" in text, "extract": meta}) + "\n")
    st = summarize(metas); st.update({"model": a.model, "images": True, "tok_per_s": round(ntok/max(el,1e-6), 1),
                                      "mean_new_tokens": round(ntok/max(1, len(rows)), 1)})
    json.dump(st, open(f"{a.out}/extract_stats.json", "w"), indent=2)
    print(f"[gen-vllm-img] {len(rows)} prompts | {ntok} tok | {ntok/el:.0f} tok/s | {el/60:.1f} min", flush=True)

if __name__ == "__main__":
    main()

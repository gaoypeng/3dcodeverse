"""Build SFT data (chat 'messages' jsonl) for text -> Blender-Python from the 3DCodeVerse-family sources.

Sources (all Blender Python):
  * bioinspired3d geo/tex metadata.parquet   (ilabai/3dcodeverse)       ~6.3k, short scripts
  * 3dcodebench factories geo/tex parquet    (ilabai/3dcodeverse)       243*2, Infinigen style, LONG scripts
        -> by default ONLY the 31 factories that are NOT in the 3DCodeBench test set (avoid contamination)
  * blender_distill (Claude-generated)       (ilabai/3dcodeverse)       ~60-100
  * thingiverse-openscad-blender-5-0          (ilabai/...)               ~1.2k, keep native-CSG only (no mesh fallback)
Outputs: <out>/train.jsonl, <out>/val.jsonl, <out>/stats.json, <out>/bench_prompts.jsonl
"""
import argparse, glob, hashlib, json, os, random, re
import pandas as pd

SYSTEM = ("You are an expert in procedural 3D modeling with Blender Python (bpy). "
          "Given a description of an object, write a complete, standalone Blender 5 Python script that builds it "
          "from scratch (clear the default scene first, create geometry with bpy/bmesh, assign simple materials when "
          "relevant, leave the finished mesh objects in the scene). Output ONLY the code in one ```python block.")

def ans(code):
    return "```python\n" + code.strip("\n") + "\n```"

def wrap_desc(desc):
    starters = ["Write a Blender Python script that builds the following object: ",
                "Create this 3D object with Blender Python (bpy): ",
                "Using Python Blender code, procedurally model: ",
                "Generate a Blender Python (bpy) script for a 3D model matching this description: "]
    return random.choice(starters) + desc.strip()

PROMPT_W = {"instruction": 6, "detailed": 3, "name": 1}
def pick_prompt(name, caps):
    """caps: dict with keys detailed / instruction / factory (some may be missing)."""
    ins = (caps or {}).get("instruction") or ""
    det = (caps or {}).get("detailed") or ""
    opts = []
    if ins and len(ins) > 20: opts += [("instruction", ins)] * PROMPT_W.get("instruction", 0)
    if det and len(det) > 20: opts += [("detailed", wrap_desc(det))] * PROMPT_W.get("detailed", 0)
    if name and len(name) > 15 and not name.lower().startswith("deepcad"): opts += [("name", name)] * PROMPT_W.get("name", 0)
    if not opts: return None, None
    return random.choice(opts)

def code_ok(code, min_lines=4):
    if not code or code.count("\n") < min_lines: return False
    if "import bpy" not in code and "bpy." not in code and "import cadquery" not in code: return False
    if "reference_mesh.json" in code or "MESH_REFERENCE_ASSET" in code: return False   # mesh fallback
    return True

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="/wekafs/ict/hx_624/data")
    ap.add_argument("--out", default="/wekafs/ict/hx_624/llm-ft/data/sft_v1")
    ap.add_argument("--val_frac", type=float, default=0.02)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--include_test_factories", action="store_true", help="(contaminated) also include the 212 factories that overlap the benchmark")
    ap.add_argument("--max_chars", type=int, default=60000, help="drop samples whose code is longer than this (~15k tokens)")
    ap.add_argument("--tokenizer", default="/wekafs/ict/hx_624/models/Qwen2.5-Coder-7B-Instruct")
    ap.add_argument("--max_tokens", type=int, default=8192, help="drop samples whose full chat exceeds this many tokens")
    ap.add_argument("--sources", default="bio3d,fac,distill,thingi50", help="comma list of sources to include: bio3d,fac,distill,thingi50,deepcad")
    ap.add_argument("--prompt_fields", default="instruction:6,detailed:3,name:1", help="weighted prompt field choice, e.g. 'instruction:1' for instruction-only")
    ap.add_argument("--deepcad_n", type=int, default=20000, help="number of deepcad CadQuery samples when 'deepcad' in --sources")
    args = ap.parse_args()
    random.seed(args.seed)
    os.makedirs(args.out, exist_ok=True)
    SRC = set(args.sources.split(","))
    PROMPT_W.clear(); PROMPT_W.update({k: int(v) for k, v in (x.split(":") for x in args.prompt_fields.split(","))})
    R = args.root
    samples = []  # dict(source, id, prompt_kind, messages)

    # ---- test-set names (for contamination control)
    bench_dir = f"{R}/3dcodebench/data"
    test_names = {d.replace("_seed0", "") for d in os.listdir(bench_dir)}

    # ---- bioinspired3d
    for sub in (["bpy_structures_geo", "bpy_structures_tex"] if "bio3d" in SRC else []):
        df = pd.read_parquet(f"{R}/3dcodeverse/bioinspired3d_{sub}_metadata.parquet")
        for r in df.itertuples():
            if not code_ok(r.code): continue
            kind, p = pick_prompt(r.name, dict(r.captions))
            if not p: continue
            samples.append(dict(source=f"bio3d_{sub[-3:]}", id=r.id, prompt_kind=kind, code=r.code, prompt=p))

    # ---- 3dcodebench factories (Infinigen style)
    for sub in (["factories_geo", "factories_tex"] if "fac" in SRC else []):
        df = pd.read_parquet(f"{R}/3dcodeverse/3dcodebench_{sub}_metadata.parquet")
        for r in df.itertuples():
            fac = r.name.replace("Factory", "")
            if fac in test_names and not args.include_test_factories: continue
            if not code_ok(r.code): continue
            kind, p = pick_prompt(None, dict(r.captions))
            if not p: continue
            samples.append(dict(source=f"fac_{sub[-3:]}" + ("_TEST" if fac in test_names else ""), id=r.id, prompt_kind=kind, code=r.code, prompt=p))

    # ---- blender_distill
    for cp in (glob.glob(f"{R}/3dcodeverse/blender_distill/**/code.py", recursive=True) if "distill" in SRC else []):
        d = os.path.dirname(cp); code = open(cp).read()
        if not code_ok(code): continue
        caps = json.load(open(os.path.join(d, "captions.json"))) if os.path.exists(os.path.join(d, "captions.json")) else {}
        p = caps.get("user_instruction") or (caps.get("instruction") and wrap_desc(caps["instruction"]))
        if not p: continue
        # distill scripts define build() but may not call it -> make sure the script executes standalone
        if re.search(r"^def build\(\):", code, re.M) and not re.search(r"^\s*build\(\)\s*$", code, re.M) and "__main__" not in code:
            code = code.rstrip("\n") + "\n\n\nif __name__ == \"__main__\":\n    build()\n"
        samples.append(dict(source="distill", id=os.path.relpath(d, R), prompt_kind="user_instruction", code=code, prompt=p))

    # ---- deepcad (CadQuery) -- cross-dialect data; the code is NOT Blender, system prompt is adapted
    if "deepcad" in SRC:
        dfs = [pd.read_parquet(f) for f in sorted(glob.glob(f"{R}/3dcodeverse_meta/deepcad/*/metadata.parquet"))]
        dd = pd.concat(dfs, ignore_index=True).sample(n=min(args.deepcad_n, sum(len(d) for d in dfs)), random_state=args.seed)
        for r in dd.itertuples():
            code = r.code or ""
            if "import cadquery" not in code or code.count("\n") < 3: continue
            caps = dict(r.captions); ins = caps.get("instruction") or caps.get("detailed") or ""
            if len(ins) < 20: continue
            p = "Write a CadQuery (Python) script that builds the following CAD model: " + ins.strip()
            samples.append(dict(source="deepcad", id=r.id, prompt_kind="instruction", code=code, prompt=p, dialect="cadquery"))
    # ---- thingiverse-openscad-blender-5-0 (native CSG only)
    for cp in (glob.glob(f"{R}/thingiverse_5_0/*/code.py") if "thingi50" in SRC else []):
        d = os.path.dirname(cp); code = open(cp).read()
        rep_p = os.path.join(d, "reports", "translation_report.json")
        status = json.load(open(rep_p)).get("status", "") if os.path.exists(rep_p) else ""
        if "mesh_fallback" in status or not code_ok(code): continue
        caps = json.load(open(os.path.join(d, "captions.json"))) if os.path.exists(os.path.join(d, "captions.json")) else {}
        desc = caps.get("synthetic_prompt") or caps.get("detailed") or ""
        if len(desc) < 40: continue
        p = ("The following request was originally written for OpenSCAD. Implement the same design in Blender Python (bpy) "
             "instead, as a standalone script that builds the geometry with native Blender primitives/booleans.\n\n" + desc.strip())
        samples.append(dict(source="thingi50", id=os.path.basename(d), prompt_kind="synthetic_prompt", code=code, prompt=p))

    # ---- dedup by code hash, length filter
    seen, uniq = set(), []
    for s in samples:
        h = hashlib.sha1(s["code"].strip().encode()).hexdigest()
        if h in seen or len(s["code"]) > args.max_chars: continue
        seen.add(h); uniq.append(s)
    # ---- token length filter (drop too-long samples instead of truncating the completion)
    tok = None
    try:
        from transformers import AutoTokenizer
        tok = AutoTokenizer.from_pretrained(args.tokenizer)
    except Exception as e:
        print("tokenizer unavailable:", e)
    if tok is not None:
        before = len(uniq)
        def ntok(s):
            msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": s["prompt"]}, {"role": "assistant", "content": ans(s["code"])}]
            return len(tok(tok.apply_chat_template(msgs, tokenize=False)).input_ids)
        uniq = [s for s in uniq if ntok(s) <= args.max_tokens]
        print(f"dropped {before - len(uniq)} samples longer than {args.max_tokens} tokens")
    random.shuffle(uniq)
    n_val = max(1, int(len(uniq) * args.val_frac))
    val, train = uniq[:n_val], uniq[n_val:]

    SYSTEM_CQ = ("You are an expert CAD programmer. Given a description of a part, write a complete, standalone CadQuery (Python) "
                 "script that builds it. Output ONLY the code in one ```python block.")
    def to_msgs(s):
        return {"messages": [{"role": "system", "content": SYSTEM_CQ if s.get("dialect") == "cadquery" else SYSTEM},
                             {"role": "user", "content": s["prompt"]},
                             {"role": "assistant", "content": ans(s["code"])}],
                "source": s["source"], "id": s["id"], "prompt_kind": s["prompt_kind"]}

    stats = {"n_train": len(train), "n_val": len(val), "by_source": {}, "token_len": {}}
    for split, rows in [("train", train), ("val", val)]:
        lens = []
        with open(f"{args.out}/{split}.jsonl", "w") as f:
            for s in rows:
                m = to_msgs(s)
                f.write(json.dumps(m, ensure_ascii=False) + "\n")
                stats["by_source"].setdefault(split, {}); stats["by_source"][split][s["source"]] = stats["by_source"][split].get(s["source"], 0) + 1
                if tok is not None:
                    lens.append(len(tok(tok.apply_chat_template(m["messages"], tokenize=False)).input_ids))
        if lens:
            import numpy as np
            lens = np.array(lens)
            stats["token_len"][split] = {"p50": int(np.percentile(lens, 50)), "p90": int(np.percentile(lens, 90)), "p99": int(np.percentile(lens, 99)), "max": int(lens.max()), "total": int(lens.sum()), "n_gt_8192": int((lens > 8192).sum()), "n_gt_16384": int((lens > 16384).sum())}

    # ---- benchmark prompts
    with open(f"{args.out}/bench_prompts.jsonl", "w") as f:
        for d in sorted(os.listdir(bench_dir)):
            p = open(f"{bench_dir}/{d}/prompt_instruction.txt").read().strip()
            f.write(json.dumps({"task": d, "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": p}]}) + "\n")
    json.dump(stats, open(f"{args.out}/stats.json", "w"), indent=2)
    print(json.dumps(stats, indent=2))

if __name__ == "__main__":
    main()

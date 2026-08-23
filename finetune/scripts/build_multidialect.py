"""Build per-dialect SFT sets from ALL 3DCodeVerse metadata parquets + data-quality report.
Dialects: blender (bioinspired3d+factories non-test), cadquery_deepcad, cadquery_articraft, openscad (thingiverse), glsl (shadertoy).
Outputs data/multidialect/<dialect>/{train,test}.jsonl (+ all.jsonl) and data/multidialect/report.json"""
import glob, hashlib, json, os, random, re, sys, unicodedata
import pandas as pd, numpy as np
random.seed(7)
META = "/wekafs/ict/hx_624/data/3dcodeverse_meta"; OUT = "/wekafs/ict/hx_624/llm-ft/data/multidialect"; os.makedirs(OUT, exist_ok=True)
SYS = {
 "blender": "You are an expert in procedural 3D modeling with Blender Python (bpy). Given a description of an object, write a complete, standalone Blender 5 Python script that builds it from scratch (clear the default scene first). Output ONLY the code in one ```python block.",
 "cadquery": "You are an expert CAD programmer. Given a description of a part, write a complete, standalone CadQuery (Python) script that builds it and leaves the final solid in a variable named `result`. Output ONLY the code in one ```python block.",
 "openscad": "You are an expert OpenSCAD programmer. Given a request, write a complete, standalone OpenSCAD (.scad) program that produces the described model. Output ONLY the code in one ```openscad block.",
 "glsl": "You are an expert shader programmer. Given a description, write a complete Shadertoy-style GLSL ES 3.00 fragment shader defining `void mainImage(out vec4 fragColor, in vec2 fragCoord)`. Output ONLY the code in one ```glsl block.",
}
FENCE = {"blender": "python", "cadquery": "python", "openscad": "openscad", "glsl": "glsl"}
def bad_chars(s):
    return sum(1 for ch in s if unicodedata.category(ch) in ("Cc",) and ch not in "\n\t\r") + s.count("�")
def wrap(dialect, caps, name):
    ins = (caps or {}).get("instruction") or ""; det = (caps or {}).get("detailed") or ""
    if dialect == "cadquery":
        return "Write a CadQuery (Python) script that builds the following CAD model: " + (ins or det)
    if dialect == "glsl":
        return ins if ins.lower().startswith(("write", "create", "implement", "make")) else "Write a GLSL fragment shader for Shadertoy that renders: " + (ins or det)
    if dialect == "openscad":
        return det or ins   # thingiverse captions are already long OpenSCAD requests
    return ins or ("Create the 3D object described below using Python Blender code: " + det)
sets = {}
def add(dialect, sub, df, code_col="code"):
    rows = sets.setdefault(dialect, [])
    for r in df.itertuples():
        code = getattr(r, code_col) or ""; caps = dict(r.captions) if isinstance(r.captions, dict) else {}
        rows.append(dict(dialect=dialect, subset=sub, id=r.id, code=code, prompt=wrap(dialect, caps, getattr(r, "name", None)), caps=caps))
# --- sources
test_names = {d.replace("_seed0", "") for d in os.listdir("/wekafs/ict/hx_624/data/3dcodebench/data")}
for f in glob.glob(f"{META}/bioinspired3d/*/metadata.parquet"): add("blender", "bioinspired3d", pd.read_parquet(f))
for f in glob.glob(f"{META}/3dcodebench/*/metadata.parquet"):
    df = pd.read_parquet(f); df = df[~df["name"].str.replace("Factory", "", regex=False).isin(test_names)]; add("blender", "factories_nontest", df)
for f in glob.glob(f"{META}/deepcad/*/metadata.parquet"): add("cadquery", "deepcad", pd.read_parquet(f))
for f in glob.glob(f"{META}/articraft/cadquery_single_tex*/metadata.parquet"): add("cadquery", "articraft", pd.read_parquet(f))
add("openscad", "thingiverse", pd.read_parquet(f"{META}/thingiverse/openscad/metadata.parquet"))
for f in glob.glob(f"{META}/shadertoy/*/metadata.parquet"): add("glsl", "shadertoy", pd.read_parquet(f))
# --- QC + build
from transformers import AutoTokenizer
tok = AutoTokenizer.from_pretrained("/wekafs/ict/hx_624/models/Qwen3.5-9B")
report = {}
for dialect, rows in sets.items():
    n0 = len(rows); issues = dict(empty_code=0, short_code=0, bad_chars=0, no_prompt=0, dup_code=0, too_long=0)
    seen = set(); keep = []
    for s in rows:
        code = s["code"].strip()
        if not code: issues["empty_code"] += 1; continue
        if code.count("\n") < 3: issues["short_code"] += 1; continue
        if bad_chars(code) > 0: issues["bad_chars"] += 1; continue
        if len(s["prompt"]) < 20: issues["no_prompt"] += 1; continue
        h = hashlib.sha1(code.encode()).hexdigest()
        if h in seen: issues["dup_code"] += 1; continue
        seen.add(h); s["code"] = code; keep.append(s)
    # token lengths on a sample
    samp = random.sample(keep, min(500, len(keep)))
    lens = [len(tok(s["prompt"] + "\n" + s["code"]).input_ids) for s in samp]
    p50, p90, p99 = np.percentile(lens, [50, 90, 99]); frac_gt8k = float(np.mean([l > 8192 for l in lens])); frac_gt16k = float(np.mean([l > 16384 for l in lens]))
    random.shuffle(keep)
    n_test = min(200, max(50, len(keep) // 50))
    test, train = keep[:n_test], keep[n_test:]
    d = f"{OUT}/{dialect}"; os.makedirs(d, exist_ok=True)
    for split, rs in [("train", train), ("test", test)]:
        with open(f"{d}/{split}.jsonl", "w") as f:
            for s in rs:
                f.write(json.dumps({"messages": [{"role": "system", "content": SYS[dialect]}, {"role": "user", "content": s["prompt"]},
                                                 {"role": "assistant", "content": f"```{FENCE[dialect]}\n{s['code']}\n```"}],
                                    "dialect": dialect, "subset": s["subset"], "id": s["id"]}, ensure_ascii=False) + "\n")
    by_sub = {}
    for s in keep: by_sub[s["subset"]] = by_sub.get(s["subset"], 0) + 1
    report[dialect] = dict(raw=n0, kept=len(keep), train=len(train), test=len(test), issues=issues, by_subset=by_sub,
                           tok_p50=int(p50), tok_p90=int(p90), tok_p99=int(p99), frac_gt8k=round(frac_gt8k, 3), frac_gt16k=round(frac_gt16k, 3),
                           est_train_tokens_M=round(float(np.mean(lens)) * len(train) / 1e6, 1))
    print(dialect, json.dumps(report[dialect]))
json.dump(report, open(f"{OUT}/report.json", "w"), indent=1)

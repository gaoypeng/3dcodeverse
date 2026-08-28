"""md_max: use EVERY usable 3DCodeVerse sample, with multi-caption prompt expansion (~0.5M prompt→code pairs).

Sources (all parquet metadata + the distilled file trees):
  blender    bioinspired3d geo+tex, 3dcodebench factories (test factories removed), blender_distill (*.py)
  cadquery   deepcad v1..v1_4, articraft cadquery_single_tex*
  openscad   thingiverse/openscad
  glsl       shadertoy (112 shards)
  threejs    threejs_distill (*.html)
  web        animation2code (*.html)  — new: animated web/3D pages

Deliberately excluded and why:
  3dcodebench/instances_*      (3904)  100% of them are re-seeded instances of the 212 benchmark factories → test leakage
  articraft/urdf_*             (6146)  `code` column is empty in the metadata (URDF lives inside the 51 GB tars) and we have no URDF executor
  threejs_repos                        multi-file projects, not single-file prompt→code
  every id in data/multidialect/*/test.jsonl and the 212 3DCodeBench task names

Each sample yields up to 3 training pairs (captions: instruction / detailed / factory), deduped by (prompt, code).
Token length is estimated as tokens(code) + tokens(prompt) + template overhead, tokenizing each unique code/prompt once.

usage: python scripts/build_md_max.py [--max_tokens 8192] [--captions 3] [--out data/md_max]
"""
import argparse
import glob
import hashlib
import json
import os
import random
import re
import sys
import unicodedata
from concurrent.futures import ProcessPoolExecutor

import pandas as pd

sys.path.insert(0, "/wekafs/ict/hx_624/llm-ft/scripts")
META = "/wekafs/ict/hx_624/data/3dcodeverse_meta"
FILES = "/wekafs/ict/hx_624/data/3dcodeverse_files"
MD = "/wekafs/ict/hx_624/llm-ft/data/multidialect"
BENCH_DIR = "/wekafs/ict/hx_624/data/3dcodebench/data"

SYS = {
    "blender": "You are an expert in procedural 3D modeling with Blender Python (bpy). Given a description of an object, write a complete, standalone Blender 5 Python script that builds it from scratch (clear the default scene first). Output ONLY the code in one ```python block.",
    "cadquery": "You are an expert CAD programmer. Given a description of a part, write a complete, standalone CadQuery (Python) script that builds it and leaves the final solid in a variable named `result`. Output ONLY the code in one ```python block.",
    "openscad": "You are an expert OpenSCAD programmer. Given a request, write a complete, standalone OpenSCAD (.scad) program that produces the described model. Output ONLY the code in one ```openscad block.",
    "glsl": "You are an expert shader programmer. Given a description, write a complete Shadertoy-style GLSL ES 3.00 fragment shader defining `void mainImage(out vec4 fragColor, in vec2 fragCoord)`. Output ONLY the code in one ```glsl block.",
    "threejs": "You are an expert three.js developer. Given a description, write a complete, standalone HTML page that renders the described 3D scene with three.js (import three from a CDN-style module URL, create a renderer/camera/lights, and animate). Output ONLY the code in one ```html block.",
    "web": "You are an expert creative web developer. Given a description of an animation, write a complete, standalone HTML page (inline CSS/JS, no local assets) that reproduces it. Output ONLY the code in one ```html block.",
}
FENCE = {"blender": "python", "cadquery": "python", "openscad": "openscad", "glsl": "glsl", "threejs": "html", "web": "html"}
LEAD = {  # how to phrase a bare description as a request, per dialect
    "blender": "Create the 3D object described below using Python Blender code: ",
    "cadquery": "Write a CadQuery (Python) script that builds the following CAD model: ",
    "openscad": "Write an OpenSCAD program for the following: ",
    "glsl": "Write a GLSL fragment shader for Shadertoy that renders: ",
    "threejs": "Write a standalone three.js page that renders: ",
    "web": "Write a standalone animated web page that reproduces: ",
}
IMPERATIVE = ("write", "create", "implement", "make", "build", "generate", "design", "produce")


def bad_chars(s):
    return sum(1 for ch in s if unicodedata.category(ch) == "Cc" and ch not in "\n\t\r") + s.count("�")


def phrase(dialect, kind, text):
    t = (text or "").strip()
    if not t:
        return None
    if kind == "instruction" and t.lower().startswith(IMPERATIVE):
        return t
    return LEAD[dialect] + t


def load_parquet_rows(dialect, sub, drop_names=frozenset()):
    p = f"{META}/{sub}/metadata.parquet"
    if not os.path.exists(p):
        return []
    df = pd.read_parquet(p)
    out = []
    for r in df.itertuples():
        name = str(getattr(r, "name", "") or "")
        norm = name.replace("Factory", "").replace("_geo", "").replace("_tex", "")
        if name in drop_names or name.rsplit("_", 1)[0] in drop_names or norm in drop_names:
            continue
        code = getattr(r, "code", None) or ""
        if not isinstance(code, str) or len(code.strip()) < 40:
            continue
        caps = r.captions if isinstance(r.captions, dict) else (json.loads(r.captions) if isinstance(r.captions, str) else {})
        out.append({"dialect": dialect, "subset": sub, "id": str(r.id), "name": name, "code": code, "caps": dict(caps)})
    return out


def load_tree_rows(dialect, root, code_glob):
    """distill / animation2code trees: <sample>/{code.py|code.html|index.html} + captions.json"""
    rows = []
    for cp in glob.glob(f"{FILES}/{root}/{code_glob}", recursive=True):
        d = os.path.dirname(cp)
        capp = os.path.join(d, "captions.json")
        try:
            code = open(cp, encoding="utf-8", errors="replace").read()
            caps = json.load(open(capp)) if os.path.exists(capp) else {}
        except Exception:
            continue
        if len(code.strip()) < 40:
            continue
        rid = f"{root}/{os.path.relpath(d, f'{FILES}/{root}')}"
        caps = {k: v for k, v in caps.items() if isinstance(v, str) and v.strip()}
        # tree sources use their own caption keys -> map onto instruction/detailed
        norm = {}
        if caps.get("user_instruction"): norm["instruction"] = caps["user_instruction"]      # imperative request
        if caps.get("instruction"): norm["detailed"] = caps["instruction"]                    # description
        if caps.get("brief"):                                                                 # animation2code
            extra = caps.get("function", "")
            norm["instruction"] = (caps["brief"] + ((" — " + extra) if extra and extra not in caps["brief"] else "")).strip()
        if caps.get("source_title") and "detailed" not in norm: norm["detailed"] = caps["source_title"]
        if caps.get("style") and norm.get("detailed"): norm["detailed"] += f" (style: {caps['style']})"
        rows.append({"dialect": dialect, "subset": root, "id": rid, "name": os.path.basename(d), "code": code, "caps": norm or caps})
    return rows


_TOK = None


def _init_tok():
    global _TOK
    from transformers import AutoTokenizer
    _TOK = AutoTokenizer.from_pretrained("/wekafs/ict/hx_624/models/Qwen3.5-9B")


def _ntok(s):
    return len(_TOK(s).input_ids)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max_tokens", type=int, default=8192)
    ap.add_argument("--captions", type=int, default=3, help="max prompt variants per sample")
    ap.add_argument("--out", default="/wekafs/ict/hx_624/llm-ft/data/md_max")
    ap.add_argument("--workers", type=int, default=16)
    a = ap.parse_args()
    random.seed(17)

    bench_factories = {os.path.basename(p).replace("_seed0", "") for p in glob.glob(f"{BENCH_DIR}/*_seed0")}
    held_out = set()
    for f in glob.glob(f"{MD}/*/test.jsonl"):
        for l in open(f):
            held_out.add(json.loads(l)["id"])
    print(f"[md_max] excluding {len(bench_factories)} benchmark factories and {len(held_out)} held-out test ids", flush=True)

    samples = []
    for sub in ["bioinspired3d/bpy_structures_geo", "bioinspired3d/bpy_structures_tex", "3dcodebench/factories_geo", "3dcodebench/factories_tex"]:
        samples += load_parquet_rows("blender", sub, bench_factories)
    for sub in sorted(glob.glob(f"{META}/deepcad/*/metadata.parquet")) + sorted(glob.glob(f"{META}/articraft/cadquery_single_tex*/metadata.parquet")):
        samples += load_parquet_rows("cadquery", "/".join(sub.split("/")[-3:-1]))
    samples += load_parquet_rows("openscad", "thingiverse/openscad")
    for sub in sorted(glob.glob(f"{META}/shadertoy/*/metadata.parquet")):
        samples += load_parquet_rows("glsl", "/".join(sub.split("/")[-3:-1]))
    samples += load_tree_rows("blender", "blender_distill", "*/*/code.py")
    samples += load_tree_rows("threejs", "threejs_distill", "*/*/code.html")
    samples += load_tree_rows("web", "animation2code", "*/*/index.html")
    samples = [s for s in samples if s["id"] not in held_out]
    print(f"[md_max] samples after source load: {len(samples)}", flush=True)

    # build (prompt, code) pairs from up to N caption variants
    pairs, seen = [], set()
    for s in samples:
        code = s["code"]
        if bad_chars(code) > 5:
            continue
        used = 0
        for kind in ("instruction", "detailed", "factory"):
            if used >= a.captions:
                break
            p = phrase(s["dialect"], kind, s["caps"].get(kind))
            if not p or len(p) < 20:
                continue
            h = hashlib.md5((p[:400] + "|" + code[:400]).encode()).hexdigest()
            if h in seen:
                continue
            seen.add(h)
            pairs.append({"dialect": s["dialect"], "subset": s["subset"], "id": s["id"], "caption": kind, "prompt": p, "code": code})
            used += 1
    print(f"[md_max] prompt-code pairs before length filter: {len(pairs)}", flush=True)

    # length: tokenize each unique code and prompt once
    ucode = {}
    uprompt = {}
    for p in pairs:
        ucode.setdefault(p["code"], None)
        uprompt.setdefault(p["prompt"], None)
    cache_path = "/wekafs/ict/hx_624/llm-ft/data/md_max/.ntok_cache.json"
    cache = {}
    if os.path.exists(cache_path):
        try: cache = json.load(open(cache_path))
        except Exception: cache = {}
    def _key(x): return hashlib.md5(x.encode()).hexdigest()
    for store in (ucode, uprompt):
        for k in list(store):
            v = cache.get(_key(k))
            if v is not None: store[k] = v
    codes = [k for k, v in ucode.items() if v is None]; prompts = [k for k, v in uprompt.items() if v is None]
    print(f"[md_max] tokenizing {len(codes)} unique codes + {len(prompts)} unique prompts on {a.workers} procs", flush=True)
    with ProcessPoolExecutor(a.workers, initializer=_init_tok) as ex:
        for k, n in zip(codes, ex.map(_ntok, codes, chunksize=64)):
            ucode[k] = n
        for k, n in zip(prompts, ex.map(_ntok, prompts, chunksize=256)):
            uprompt[k] = n
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    json.dump({_key(k): v for store in (ucode, uprompt) for k, v in store.items()}, open(cache_path, "w"))

    OVERHEAD = 60  # chat template + system prompt + fence
    keep, stats = [], {}
    for p in pairs:
        n = ucode[p["code"]] + uprompt[p["prompt"]] + OVERHEAD
        if n > a.max_tokens:
            continue
        p["ntok"] = n
        keep.append(p)
        d = stats.setdefault(p["dialect"], {"n": 0, "tok": 0, "by_caption": {}})
        d["n"] += 1
        d["tok"] += n
        d["by_caption"][p["caption"]] = d["by_caption"].get(p["caption"], 0) + 1
    def _san(t): return t.replace("<image>", "<image >").replace("<video>", "<video >").replace("<audio>", "<audio >")
    for p_ in keep:
        p_["code"] = _san(p_["code"]); p_["prompt"] = _san(p_["prompt"])
    random.shuffle(keep)

    os.makedirs(a.out, exist_ok=True)
    val = keep[:600]
    train = keep[600:]
    for split, rows in (("train", train), ("val", val)):
        with open(f"{a.out}/{split}.jsonl", "w") as f:
            for p in rows:
                msgs = [{"role": "system", "content": SYS[p["dialect"]]},
                        {"role": "user", "content": p["prompt"]},
                        {"role": "assistant", "content": f"```{FENCE[p['dialect']]}\n{p['code'].strip()}\n```"}]
                f.write(json.dumps({"id": p["id"], "dialect": p["dialect"], "subset": p["subset"], "caption": p["caption"], "messages": msgs}, ensure_ascii=False) + "\n")
    tot_tok = sum(v["tok"] for v in stats.values())
    report = {"pairs_kept": len(keep), "tokens": tot_tok, "by_dialect": {k: {"n": v["n"], "Mtok": round(v["tok"] / 1e6, 1), "by_caption": v["by_caption"]} for k, v in sorted(stats.items(), key=lambda x: -x[1]["n"])}}
    json.dump(report, open(f"{a.out}/report.json", "w"), indent=2)
    print(json.dumps(report["by_dialect"], indent=1))
    print(f"[md_max] total samples {len(keep)} | tokens/epoch {tot_tok/1e6:.1f}M | train {len(train)} val {len(val)}", flush=True)


if __name__ == "__main__":
    main()

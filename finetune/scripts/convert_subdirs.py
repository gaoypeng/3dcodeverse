"""Convert EVERY 3DCodeVerse subdirectory into its own LLaMA-Factory dataset + a data-quality report.

One output folder per source subdir, named `<subdir>_llamafactory/`, so it can sit next to the source on the
Hub (e.g. `3dcodebench/factories_geo/` → `3dcodebench/factories_geo_llamafactory/`):

    <subdir>_llamafactory/
      train.json          ShareGPT: [{system, conversations:[human, gpt]}]  — one row per caption variant
      dataset_info.json   drop-in LLaMA-Factory registry entry for this folder
      qc.json             per-subdir quality counters + token stats
      README.md           what it is, how many rows, what was dropped and why

Quality checks applied per sample (a sample can fail several):
  empty_code / short_code (<40 chars) / bad_chars (control chars or U+FFFD) / no_caption /
  duplicate_code (same code hash inside the subdir) / syntax_bad (AST for Python dialects, brace balance for
  scad-glsl, tag check for html) / too_long (>8192 tokens, still exported but flagged) / media_placeholder
  (literal <image>/<video>/<audio> — breaks the qwen3_5 template unless escaped; we escape it and flag it)

usage: python scripts/convert_subdirs.py [--out DIR] [--workers 16] [--limit_subdirs N]
"""
import argparse
import ast
import collections
import glob
import hashlib
import json
import os
import re
import sys
import unicodedata
from concurrent.futures import ProcessPoolExecutor

import pandas as pd

sys.path.insert(0, "/wekafs/ict/hx_624/llm-ft/scripts")
META = "/wekafs/ict/hx_624/data/3dcodeverse_meta"
FILES = "/wekafs/ict/hx_624/data/3dcodeverse_files"
BENCH_DIR = "/wekafs/ict/hx_624/data/3dcodebench/data"

# subdir prefix -> dialect
DIALECT_OF = {
    "3dcodebench": "blender", "bioinspired3d": "blender", "blender_distill": "blender",
    "deepcad": "cadquery", "articraft": "cadquery", "shadertoy": "glsl",
    "thingiverse": "openscad", "threejs_distill": "threejs", "animation2code": "web",
}
SYS = {
    "blender": "You are an expert in procedural 3D modeling with Blender Python (bpy). Given a description of an object, write a complete, standalone Blender 5 Python script that builds it from scratch (clear the default scene first). Output ONLY the code in one ```python block.",
    "cadquery": "You are an expert CAD programmer. Given a description of a part, write a complete, standalone CadQuery (Python) script that builds it and leaves the final solid in a variable named `result`. Output ONLY the code in one ```python block.",
    "openscad": "You are an expert OpenSCAD programmer. Given a request, write a complete, standalone OpenSCAD (.scad) program that produces the described model. Output ONLY the code in one ```openscad block.",
    "glsl": "You are an expert shader programmer. Given a description, write a complete Shadertoy-style GLSL ES 3.00 fragment shader defining `void mainImage(out vec4 fragColor, in vec2 fragCoord)`. Output ONLY the code in one ```glsl block.",
    "threejs": "You are an expert three.js developer. Given a description, write a complete, standalone HTML page that renders the described 3D scene with three.js. Output ONLY the code in one ```html block.",
    "web": "You are an expert creative web developer. Given a description of an animation, write a complete, standalone HTML page (inline CSS/JS, no local assets) that reproduces it. Output ONLY the code in one ```html block.",
    "urdf": "You are an expert in articulated 3D assets. Given a description, write a complete, self-contained URDF file for it. Output ONLY the code in one ```xml block.",
}
FENCE = {"blender": "python", "cadquery": "python", "openscad": "openscad", "glsl": "glsl",
         "threejs": "html", "web": "html", "urdf": "xml"}
LEAD = {
    "blender": "Create the 3D object described below using Python Blender code: ",
    "cadquery": "Write a CadQuery (Python) script that builds the following CAD model: ",
    "openscad": "Write an OpenSCAD program for the following: ",
    "glsl": "Write a GLSL fragment shader for Shadertoy that renders: ",
    "threejs": "Write a standalone three.js page that renders: ",
    "web": "Write a standalone animated web page that reproduces: ",
    "urdf": "Write a URDF description of: ",
}
IMPERATIVE = ("write", "create", "implement", "make", "build", "generate", "design", "produce")
MEDIA = re.compile(r"<(image|video|audio)>")


def bad_char_count(s):
    return sum(1 for ch in s if unicodedata.category(ch) == "Cc" and ch not in "\n\t\r") + s.count("�")


def syntax_ok(code, dialect):
    if dialect in ("blender", "cadquery"):
        try:
            ast.parse(code)
            return True
        except Exception:
            return False
    if dialect == "openscad":
        return code.count("{") == code.count("}") and code.count("(") == code.count(")")
    if dialect == "glsl":
        return code.count("{") == code.count("}") and "void" in code
    if dialect in ("threejs", "web"):
        return "<" in code and ">" in code
    if dialect == "urdf":
        return "<robot" in code or "<link" in code
    return True


def phrase(dialect, kind, text):
    t = (text or "").strip()
    if not t:
        return None
    if kind == "instruction" and t.lower().startswith(IMPERATIVE):
        return t
    return LEAD[dialect] + t


def normalize_caps(caps):
    """tree sources use their own caption keys"""
    caps = {k: v for k, v in caps.items() if isinstance(v, str) and v.strip()}
    if any(k in caps for k in ("instruction", "detailed", "factory")) and "user_instruction" not in caps and "brief" not in caps:
        return caps
    norm = {}
    if caps.get("user_instruction"):
        norm["instruction"] = caps["user_instruction"]
    if caps.get("instruction"):
        norm["detailed"] = caps["instruction"]
    if caps.get("brief"):
        extra = caps.get("function", "")
        norm["instruction"] = (caps["brief"] + ((" — " + extra) if extra and extra not in caps["brief"] else "")).strip()
    if caps.get("source_title") and "detailed" not in norm:
        norm["detailed"] = caps["source_title"]
    if caps.get("style") and norm.get("detailed"):
        norm["detailed"] += f" (style: {caps['style']})"
    return norm or caps


def load_subdir(sub):
    """-> (dialect, [ {id, name, code, caps, n_images} ]) for a parquet subdir or a file tree"""
    top = sub.split("/")[0]
    dialect = DIALECT_OF.get(top, "blender")
    if top == "articraft" and "urdf" in sub:
        dialect = "urdf"
    rows = []
    p = f"{META}/{sub}/metadata.parquet"
    if os.path.exists(p):
        df = pd.read_parquet(p)
        for r in df.itertuples():
            caps = r.captions if isinstance(r.captions, dict) else (json.loads(r.captions) if isinstance(r.captions, str) else {})
            meta = {}
            try:
                meta = json.loads(r.meta_json) if isinstance(r.meta_json, str) else (r.meta_json or {})
            except Exception:
                pass
            rows.append({"id": str(r.id), "name": str(getattr(r, "name", "") or ""),
                         "code": getattr(r, "code", None) or "", "caps": dict(caps),
                         "n_images": len(meta.get("renders", []) or [])})
        return dialect, rows
    # file tree
    for cp in sorted(glob.glob(f"{FILES}/{sub}/*/code.py") + glob.glob(f"{FILES}/{sub}/*/code.html")
                     + glob.glob(f"{FILES}/{sub}/*/index.html") + glob.glob(f"{FILES}/{sub}/*/*/code.py")
                     + glob.glob(f"{FILES}/{sub}/*/*/code.html") + glob.glob(f"{FILES}/{sub}/*/*/index.html")):
        d = os.path.dirname(cp)
        try:
            code = open(cp, encoding="utf-8", errors="replace").read()
            caps = json.load(open(os.path.join(d, "captions.json"))) if os.path.exists(os.path.join(d, "captions.json")) else {}
        except Exception:
            continue
        rows.append({"id": os.path.relpath(d, FILES), "name": os.path.basename(d), "code": code,
                     "caps": caps, "n_images": len(glob.glob(os.path.join(d, "renders", "*.png")))})
    return dialect, rows


_TOK = None


def _init():
    global _TOK
    from transformers import AutoTokenizer
    _TOK = AutoTokenizer.from_pretrained("/wekafs/ict/hx_624/models/Qwen3.5-9B")


def convert_one(args):
    sub, out_root, bench_factories = args
    dialect, rows = load_subdir(sub)
    if not rows:
        return {"subdir": sub, "error": "no rows"}
    qc = collections.Counter()
    seen_code, out, tok_lens, img_counts = {}, [], [], []
    for r in rows:
        code = r["code"] if isinstance(r["code"], str) else ""
        img_counts.append(r.get("n_images", 0))
        if not code.strip():
            qc["empty_code"] += 1
            continue
        if len(code.strip()) < 40:
            qc["short_code"] += 1
            continue
        if bad_char_count(code) > 5:
            qc["bad_chars"] += 1
            continue
        h = hashlib.md5(code.encode()).hexdigest()
        if h in seen_code:
            qc["duplicate_code"] += 1
            continue
        seen_code[h] = 1
        if not syntax_ok(code, dialect):
            qc["syntax_bad"] += 1          # exported anyway, but counted
        if MEDIA.search(code) or any(MEDIA.search(v) for v in r["caps"].values() if isinstance(v, str)):
            qc["media_placeholder"] += 1
            code = MEDIA.sub(lambda m: f"<{m.group(1)} >", code)
        if r["name"] in bench_factories or r["name"].rsplit("_", 1)[0] in bench_factories:
            qc["benchmark_overlap"] += 1   # flagged, and excluded below
            continue
        caps = normalize_caps(r["caps"])
        n_before = len(out)
        for kind in ("instruction", "detailed", "factory"):
            pr = phrase(dialect, kind, caps.get(kind))
            if not pr or len(pr) < 20:
                continue
            pr = MEDIA.sub(lambda m: f"<{m.group(1)} >", pr)
            n = len(_TOK(pr).input_ids) + len(_TOK(code).input_ids) + 60
            tok_lens.append(n)
            if n > 8192:
                qc["too_long_gt8192"] += 1
            out.append({"id": r["id"], "caption_type": kind, "n_tokens": n,
                        "system": SYS[dialect],
                        "conversations": [{"from": "human", "value": pr},
                                          {"from": "gpt", "value": f"```{FENCE[dialect]}\n{code.strip()}\n```"}]})
        if len(out) == n_before:
            qc["no_caption"] += 1
    name = sub.replace("/", "__")
    d = os.path.join(out_root, sub + "_llamafactory")
    os.makedirs(d, exist_ok=True)
    json.dump(out, open(os.path.join(d, "train.json"), "w"), ensure_ascii=False)
    key = f"3dcv_{name}"
    json.dump({key: {"file_name": "train.json", "formatting": "sharegpt",
                     "columns": {"messages": "conversations", "system": "system"}}},
              open(os.path.join(d, "dataset_info.json"), "w"), indent=2)
    tok_lens.sort()
    stat = {"subdir": sub, "dialect": dialect, "source_samples": len(rows), "exported_pairs": len(out),
            "unique_samples_exported": len(seen_code) - qc["benchmark_overlap"],
            "images_per_sample_median": (sorted(img_counts)[len(img_counts) // 2] if img_counts else 0),
            "tokens_p50": tok_lens[len(tok_lens) // 2] if tok_lens else 0,
            "tokens_p90": tok_lens[int(len(tok_lens) * 0.9)] if tok_lens else 0,
            "tokens_max": tok_lens[-1] if tok_lens else 0,
            **{k: v for k, v in qc.items()}}
    json.dump(stat, open(os.path.join(d, "qc.json"), "w"), indent=2)
    issues = "\n".join(f"- **{k}**: {v}" for k, v in sorted(qc.items(), key=lambda x: -x[1])) or "- none"
    open(os.path.join(d, "README.md"), "w").write(f"""# {sub} — LLaMA-Factory format

Generated by `finetune/scripts/convert_subdirs.py` from `{sub}/metadata.parquet` (or its file tree).

| | |
|---|---|
| dialect | `{dialect}` |
| source samples | {len(rows):,} |
| exported pairs | {len(out):,} (one row per caption variant: instruction / detailed / factory) |
| unique code samples exported | {stat['unique_samples_exported']:,} |
| token length p50 / p90 / max | {stat['tokens_p50']} / {stat['tokens_p90']} / {stat['tokens_max']} |
| renders per sample (median) | {stat['images_per_sample_median']} |

## Data-quality findings

{issues}

`syntax_bad` rows are still exported (a shader can be valid without balanced braces in our crude check); every
other counter means the sample was dropped. `benchmark_overlap` = the sample belongs to one of the 212
3DCodeBench evaluation factories and is excluded to avoid test leakage.

## Use

```yaml
dataset_dir: <this folder>
dataset: {key}
```
""")
    return stat


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/wekafs/ict/hx_624/hf_subdirs")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--limit_subdirs", type=int, default=None)
    a = ap.parse_args()
    bench = frozenset(os.path.basename(p).replace("_seed0", "") for p in glob.glob(f"{BENCH_DIR}/*_seed0"))
    subs = ["/".join(p.split("/")[-3:-1]) for p in sorted(glob.glob(f"{META}/*/*/metadata.parquet"))]
    subs += ["blender_distill", "threejs_distill", "animation2code"]
    if a.limit_subdirs:
        subs = subs[: a.limit_subdirs]
    print(f"[conv] {len(subs)} subdirs -> {a.out}", flush=True)
    os.makedirs(a.out, exist_ok=True)
    stats = []
    with ProcessPoolExecutor(a.workers, initializer=_init) as ex:
        for st in ex.map(convert_one, [(s, a.out, bench) for s in subs]):
            stats.append(st)
            print(f"[conv] {st.get('subdir'):45s} pairs={st.get('exported_pairs', 0):7,} "
                  f"issues={ {k: v for k, v in st.items() if k in ('empty_code','short_code','bad_chars','duplicate_code','syntax_bad','no_caption','too_long_gt8192','media_placeholder','benchmark_overlap')} }", flush=True)
    df = pd.DataFrame(stats)
    df.to_csv(os.path.join(a.out, "quality_report.csv"), index=False)
    try:
        df.to_excel(os.path.join(a.out, "quality_report.xlsx"), index=False)
    except Exception as e:
        print("[conv] xlsx skipped:", e)
    json.dump(stats, open(os.path.join(a.out, "quality_report.json"), "w"), indent=2)
    print(f"[conv] wrote quality_report.{{csv,xlsx,json}} | total exported pairs: {int(df['exported_pairs'].sum()):,}")


if __name__ == "__main__":
    main()

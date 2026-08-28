"""Flatten every multi-file sample into ONE self-contained file, so the whole corpus is single-file SFT data.

Two sources are multi-file in the original dataset and both can be flattened exactly:

  shadertoy   `shader_json.renderpass` holds the source of every tab. Shadertoy itself compiles
              `Common + <pass>`, so we emit that concatenation verbatim:
                * the Image pass with Common prepended  → the sample's main program
                * each Buffer pass with Common prepended → an extra sample (it is a complete shader too)
              A shader whose passes read `iChannel` as a *buffer* is still emitted, with a one-line comment
              stating which channels are buffers, because the code itself is complete.
  articraft   `code` is empty in the metadata; the real `code.urdf` lives inside the subdir tars, which are
              downloaded separately and extracted by `--urdf_tars`.

Everything is then re-emitted in LLaMA-Factory ShareGPT format next to the other converted subdirs.

usage:
  python scripts/flatten_multifile.py --shadertoy [--out /wekafs/ict/hx_624/hf_subdirs]
  python scripts/flatten_multifile.py --urdf_tars /wekafs/ict/hx_624/data/3dcodeverse_tars
"""
import argparse
import collections
import glob
import io
import json
import os
import re
import tarfile

import pandas as pd

META = "/wekafs/ict/hx_624/data/3dcodeverse_meta"
SYS_GLSL = ("You are an expert shader programmer. Given a description, write a complete Shadertoy-style GLSL ES 3.00 "
            "fragment shader defining `void mainImage(out vec4 fragColor, in vec2 fragCoord)`. Output ONLY the code in one ```glsl block.")
SYS_URDF = ("You are an expert in articulated 3D assets. Given a description, write a complete, self-contained URDF "
            "file (links, joints, inertial and visual geometry) for it. Output ONLY the code in one ```xml block.")
IMPERATIVE = ("write", "create", "implement", "make", "build", "generate", "design", "produce")


def phrase(kind, text, lead):
    t = (text or "").strip()
    if not t:
        return None
    if kind == "instruction" and t.lower().startswith(IMPERATIVE):
        return t
    return lead + t


def flatten_shadertoy(out_root):
    subs = sorted(glob.glob(f"{META}/shadertoy/*/metadata.parquet"))
    stats = collections.Counter()
    for p in subs:
        sub = "/".join(p.split("/")[-3:-1])
        df = pd.read_parquet(p)
        rows = []
        for r in df.itertuples():
            try:
                sj = json.loads(r.shader_json) if isinstance(r.shader_json, str) else (r.shader_json or {})
            except Exception:
                sj = {}
            passes = (sj.get("Shader", sj) or {}).get("renderpass") or []
            if not passes:
                continue
            common = "\n".join(q.get("code") or "" for q in passes if q.get("type") == "common").strip()
            caps = r.captions if isinstance(r.captions, dict) else (json.loads(r.captions) if isinstance(r.captions, str) else {})
            for q in passes:
                t = q.get("type")
                if t not in ("image", "buffer"):
                    continue                      # sound/cubemap passes are a different program shape
                body = (q.get("code") or "").strip()
                if len(body) < 40:
                    continue
                buf_inputs = [i for i in (q.get("inputs") or []) if (i or {}).get("ctype") == "buffer"]
                header = ""
                if common:
                    header += "// ---- Common tab ----\n" + common + "\n// ---- " + str(q.get("name")) + " ----\n"
                    stats["merged_common"] += 1
                if buf_inputs:
                    header = f"// note: iChannel{','.join(str(i.get('channel')) for i in buf_inputs)} read a previous buffer pass\n" + header
                code = header + body
                tag = "" if t == "image" else f"::{str(q.get('name')).replace(' ', '')}"
                lead = "Write a GLSL fragment shader for Shadertoy that renders: "
                if t == "buffer":
                    lead = "Write the buffer pass of a Shadertoy shader that computes: "
                used = 0
                for kind in ("instruction", "detailed", "factory"):
                    pr = phrase(kind, caps.get(kind), lead)
                    if not pr or len(pr) < 20:
                        continue
                    used += 1
                    rows.append({"id": f"{r.id}{tag}", "caption_type": kind, "pass_type": t,
                                 "has_common": bool(common), "system": SYS_GLSL,
                                 "conversations": [{"from": "human", "value": pr},
                                                   {"from": "gpt", "value": f"```glsl\n{code}\n```"}]})
                stats["pass_" + t] += 1 if used else 0
        if not rows:
            continue
        d = os.path.join(out_root, sub + "_llamafactory_flat")
        os.makedirs(d, exist_ok=True)
        pd.DataFrame(rows).to_parquet(os.path.join(d, "train.parquet"), index=False, compression="zstd")
        key = "3dcv_" + sub.replace("/", "__") + "_flat"
        json.dump({key: {"file_name": "train.parquet", "formatting": "sharegpt",
                         "columns": {"messages": "conversations", "system": "system"}}},
                  open(os.path.join(d, "dataset_info.json"), "w"), indent=2)
        stats["rows"] += len(rows)
        stats["subdirs"] += 1
    print(f"[flat] shadertoy: {stats['subdirs']} subdirs, {stats['rows']:,} rows "
          f"(image passes {stats['pass_image']:,}, buffer passes {stats['pass_buffer']:,}, "
          f"{stats['merged_common']:,} programs got their Common tab merged in)", flush=True)
    return dict(stats)


def extract_urdf(tar_dir, out_root):
    """pull code.urdf out of the articraft tars and re-emit the two subdirs with real code"""
    code_by_key = {}
    for t in sorted(glob.glob(f"{tar_dir}/articraft/urdf_*/*.tar")):
        sub = t.split("/")[-2]
        try:
            with tarfile.open(t) as tf:
                for m in tf:
                    if not m.isfile() or not m.name.endswith(".urdf"):
                        continue
                    key = os.path.basename(os.path.dirname(m.name))
                    try:
                        code_by_key[(sub, key)] = tf.extractfile(m).read().decode("utf-8", "replace")
                    except Exception:
                        pass
        except Exception as e:
            print(f"[flat] tar unreadable {t}: {e}", flush=True)
    print(f"[flat] urdf: extracted {len(code_by_key):,} .urdf files from {tar_dir}", flush=True)
    total = 0
    for sub in ("articraft/urdf_geo_only", "articraft/urdf_tex"):
        p = f"{META}/{sub}/metadata.parquet"
        if not os.path.exists(p):
            continue
        df = pd.read_parquet(p)
        rows, miss = [], 0
        for r in df.itertuples():
            key = str(r.key) if hasattr(r, "key") else None
            code = code_by_key.get((sub.split("/")[-1], key)) or code_by_key.get((sub.split("/")[-1], str(r.id).split("/")[-1]))
            if not code or len(code.strip()) < 40:
                miss += 1
                continue
            caps = r.captions if isinstance(r.captions, dict) else (json.loads(r.captions) if isinstance(r.captions, str) else {})
            for kind in ("instruction", "detailed", "factory"):
                pr = phrase(kind, caps.get(kind), "Write a URDF description of: ")
                if not pr or len(pr) < 20:
                    continue
                rows.append({"id": str(r.id), "caption_type": kind, "system": SYS_URDF,
                             "conversations": [{"from": "human", "value": pr},
                                               {"from": "gpt", "value": f"```xml\n{code.strip()}\n```"}]})
        if rows:
            d = os.path.join(out_root, sub + "_llamafactory")
            os.makedirs(d, exist_ok=True)
            pd.DataFrame(rows).to_parquet(os.path.join(d, "train.parquet"), index=False, compression="zstd")
            key = "3dcv_" + sub.replace("/", "__")
            json.dump({key: {"file_name": "train.parquet", "formatting": "sharegpt",
                             "columns": {"messages": "conversations", "system": "system"}}},
                      open(os.path.join(d, "dataset_info.json"), "w"), indent=2)
            json.dump({"subdir": sub, "dialect": "urdf", "source_samples": len(df), "exported_pairs": len(rows),
                       "unique_samples_exported": len(df) - miss, "missing_in_tar": miss,
                       "note": "code recovered from the subdir tars (the metadata `code` column is empty)"},
                      open(os.path.join(d, "qc.json"), "w"), indent=2)
        print(f"[flat] {sub}: {len(rows):,} pairs from {len(df)-miss:,} samples ({miss} not found in the tars)", flush=True)
        total += len(rows)
    return total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/wekafs/ict/hx_624/hf_subdirs")
    ap.add_argument("--shadertoy", action="store_true")
    ap.add_argument("--urdf_tars", default=None)
    a = ap.parse_args()
    if a.shadertoy:
        flatten_shadertoy(a.out)
    if a.urdf_tars:
        extract_urdf(a.urdf_tars, a.out)


if __name__ == "__main__":
    main()

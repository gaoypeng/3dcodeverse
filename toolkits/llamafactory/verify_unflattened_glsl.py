"""Compile-verify the 44 shadertoy shards that the flattening pass never reached.

Those shards' metadata carries no `shader_json`, so there was no renderpass structure to merge a Common tab
from and `flatten_multifile.py` skipped them — exactly 44 of 112, with zero mismatches. The consequence is that
73,281 shaders are verified (99.04% compile) while 46,680 have never been near a compiler. This runs the same
checker and the same minimal repairs over those, and writes the `compiles` / `repair` columns the flattened
folders already carry, so a training mix can filter the whole corpus by one rule instead of two.

usage: python scripts/verify_unflattened_glsl.py [--workers 40] [--limit_shards N] [--out DIR]
"""
import argparse
import collections
import glob
import json
import os
import re
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qc_repair import glsl_compile, repair_glsl

SRC = "/wekafs/ict/hx_624/hf_subdirs/shadertoy"
FENCE = re.compile(r"^```[a-zA-Z]*\n|\n?```$")


def strip_fence(v):
    return FENCE.sub("", v.strip())


def verify_one(code):
    """-> (compiles, repair_label, code_to_keep). Never returns a program that does not compile as 'repaired'."""
    ok, _ = glsl_compile(code)
    if ok:
        return True, "original", code
    try:
        fixed, recipe = repair_glsl(code)
    except Exception:
        return False, "unfixable", code
    if fixed and fixed != code:
        ok2, _ = glsl_compile(fixed)
        if ok2:
            return True, f"repaired:{recipe or 'other'}", fixed
    return False, "unfixable", code


def do_shard(args):
    sub, out_root = args
    p = f"{SRC}/{sub}_llamafactory/train.parquet"
    df = pd.read_parquet(p)
    # one shader appears once per caption variant; compile each distinct program once
    by_code = {}
    codes = [strip_fence(c[1]["value"]) for c in df["conversations"]]
    for c in set(codes):
        by_code[c] = verify_one(c)
    okc, howc, conv = [], [], []
    for row, c in zip(df.itertuples(), codes):
        ok, how, keep = by_code[c]
        okc.append(ok); howc.append(how)
        turns = [dict(t) for t in row.conversations]
        turns[1] = dict(turns[1], value=f"```glsl\n{keep}\n```")
        conv.append(turns)
    df["conversations"] = conv
    df["compiles"] = okc
    df["repair"] = howc
    d = os.path.join(out_root, f"{sub}_llamafactory")
    os.makedirs(d, exist_ok=True)
    df.to_parquet(os.path.join(d, "train.parquet"), index=False)
    for extra in ("dataset_info.json", "qc.json", "README.md"):
        s = f"{SRC}/{sub}_llamafactory/{extra}"
        if os.path.exists(s):
            open(os.path.join(d, extra), "w").write(open(s).read())
    st = {"shard": sub, "rows": len(df), "unique_programs": len(by_code),
          "compiles": int(sum(okc)), "repaired": int(sum(1 for h in howc if h.startswith("repaired"))),
          "unfixable": int(sum(1 for h in howc if h == "unfixable"))}
    json.dump(st, open(os.path.join(d, "verify.json"), "w"), indent=2)
    return st


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=40)
    ap.add_argument("--limit_shards", type=int, default=None)
    ap.add_argument("--out", default="/wekafs/ict/hx_624/hf_subdirs_verified/shadertoy")
    a = ap.parse_args()
    flat = {os.path.basename(p)[: -len("_llamafactory_flat")] for p in glob.glob(f"{SRC}/*_llamafactory_flat")}
    allp = {os.path.basename(p)[: -len("_llamafactory")] for p in glob.glob(f"{SRC}/*_llamafactory")}
    todo = sorted(allp - flat)
    if a.limit_shards:
        todo = todo[: a.limit_shards]
    os.makedirs(a.out, exist_ok=True)
    print(f"[verify] {len(todo)} shards never verified -> {a.out}", flush=True)
    from concurrent.futures import ProcessPoolExecutor
    tot = collections.Counter()
    with ProcessPoolExecutor(a.workers) as ex:
        for st in ex.map(do_shard, [(s, a.out) for s in todo]):
            for k in ("rows", "unique_programs", "compiles", "repaired", "unfixable"):
                tot[k] += st[k]
            print(f"[verify] {st['shard']:4s} rows={st['rows']:>6,} uniq={st['unique_programs']:>5,} "
                  f"ok={st['compiles']:>6,} repaired={st['repaired']:>4,} unfixable={st['unfixable']:>4,}", flush=True)
    rate = tot["compiles"] / max(tot["rows"], 1) * 100
    print(f"[verify] TOTAL rows={tot['rows']:,} unique={tot['unique_programs']:,} "
          f"compiles={tot['compiles']:,} ({rate:.2f}%) repaired={tot['repaired']:,} unfixable={tot['unfixable']:,}", flush=True)


if __name__ == "__main__":
    main()

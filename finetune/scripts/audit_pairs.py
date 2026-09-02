"""Audit every pair dataset for the invariants that fail silently at train time.

Sampling proves a problem exists; it cannot prove one does not. Checking four datasets said "paths are relative";
a full scan of all 411 found 36,964 absolute paths in three of them. This scans everything.

usage: python scripts/audit_pairs.py [--media_root DIR] [--fix]
"""
import argparse
import collections
import glob
import json
import os

import pandas as pd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--media_root", default="/wekafs/ict/hx_624/data/pair_media_root")
    ap.add_argument("--roots", default="/wekafs/ict/hx_624/hf_pairs*")
    a = ap.parse_args()
    dirs = sorted(glob.glob(f"{a.roots}/*_llamafactory_*"))
    bad = collections.Counter()
    tot = rows = 0
    examples = {}
    for d in dirs:
        kind = os.path.basename(d).rsplit("_", 1)[-1]
        try:
            df = pd.read_parquet(f"{d}/train.parquet")
        except Exception as e:
            bad["unreadable_parquet"] += 1
            examples.setdefault("unreadable_parquet", f"{d}: {type(e).__name__}")
            continue
        q = json.load(open(f"{d}/qc.json"))
        if len(df) != q["rows"]:
            bad["row_count_vs_qc"] += 1
        for r in df.to_dict("records"):
            rows += 1
            # parquet hands back numpy arrays, not lists: an isinstance check against (list, tuple) alone
            # marks every single row as malformed, which is a defect in the checker, not the data
            conv = r.get("conversations")
            try:
                conv = list(conv)
            except Exception:
                conv = None
            if conv is None or len(conv) != 2:
                bad["bad_turn_shape"] += 1
                continue
            code = conv[1]["value"]
            if not code.startswith("```") or not code.rstrip().endswith("```"):
                bad["unfenced_code"] += 1
            if not str(r.get("system") or "").strip():
                bad["empty_system"] += 1
            if kind == "text":
                if any("<image>" in t["value"] for t in conv):
                    bad["placeholder_in_text_pair"] += 1
                continue
            # `or []` on a numpy array raises rather than defaulting; the same numpy-vs-list confusion as above
            raw = r.get("images")
            imgs = [str(x) for x in raw] if raw is not None else []
            if sum(t["value"].count("<image>") for t in conv) != len(imgs):
                bad["placeholder_mismatch"] += 1
            for x in imgs:
                tot += 1
                if x.startswith("/"):
                    bad["absolute_path"] += 1
                    examples.setdefault("absolute_path", x)
                elif not x.endswith(".jpg"):
                    bad["not_jpg"] += 1
                    examples.setdefault("not_jpg", x)
                elif not os.path.exists(os.path.join(a.media_root, x)):
                    bad["unresolvable"] += 1
                    examples.setdefault("unresolvable", x)
    print(f"  {len(dirs)} datasets | {rows:,} rows | {tot:,} image references")
    print(f"  problems: {dict(bad) if bad else 'NONE'}")
    for k, v in examples.items():
        print(f"    example {k}: {v[:100]}")
    raise SystemExit(1 if bad else 0)


if __name__ == "__main__":
    main()

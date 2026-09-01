"""
batch_convert.py
Parallel DeepCAD vec(.h5) -> CadQuery .py conversion over the official split.

Usage:
  python batch_convert.py --vec_dir /data/.../cad_vec \
      --split /data/.../train_val_test_split.json \
      --out_dir /data/.../converted_cq --workers 16 [--limit 1000]
"""
import os
import json
import argparse
from pathlib import Path
from joblib import Parallel, delayed
from convert_vec_to_cq import vec_to_cadquery_code, extract_h5


def process(model_id, vec_dir, out_dir):
    # model_id like "0000/00000007"
    sub, name = model_id.split("/")
    src = Path(vec_dir) / sub / (name + ".h5")
    dest_dir = Path(out_dir) / sub
    if not src.exists():
        return model_id, "MISSING_H5"
    try:
        vec = extract_h5(str(src))
        code = vec_to_cadquery_code(vec)
        dest_dir.mkdir(parents=True, exist_ok=True)
        (dest_dir / (name + ".py")).write_text(code)
        return model_id, "OK"
    except Exception as e:
        return model_id, f"CONVERT_FAIL: {type(e).__name__}: {e}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vec_dir", required=True)
    ap.add_argument("--split", required=True)
    ap.add_argument("--out_dir", required=True)
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--limit", type=int, default=0, help="0 = all")
    ap.add_argument("--report", default="conversion_report.json")
    args = ap.parse_args()

    split = json.load(open(args.split))
    all_ids = split["train"] + split["validation"] + split["test"] \
        if "validation" in split else split["train"] + split["val"] + split["test"]
    if args.limit:
        all_ids = all_ids[: args.limit]

    print(f"Converting {len(all_ids)} models with {args.workers} workers...")
    results = Parallel(n_jobs=args.workers, verbose=5)(
        delayed(process)(mid, args.vec_dir, args.out_dir) for mid in all_ids
    )

    ok = [r for r in results if r[1] == "OK"]
    fails = [r for r in results if r[1] != "OK"]
    print(f"\nConverted: {len(ok)}/{len(all_ids)}, Failed: {len(fails)}")
    # bucket failure reasons
    buckets = {}
    for _, status in fails:
        key = status.split(":")[0]
        buckets[key] = buckets.get(key, 0) + 1
    print("Failure buckets:", buckets)
    json.dump({"ok": [r[0] for r in ok], "fail": [list(r) for r in fails],
               "buckets": buckets},
              open(args.report, "w"), indent=2)


if __name__ == "__main__":
    main()

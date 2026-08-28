"""
attach_text.py
Parse the Text2CAD CSV into a {uid -> {level: description}} JSON map.

Text2CAD uid format ("0035/00359148") matches our DeepCAD model_id exactly.
We keep the 4 human-readable levels (abstract/beginner/intermediate/expert)
and drop the training-oriented columns (all_level_data, nli_data).
"""
import csv
import json
import argparse

csv.field_size_limit(10 * 1024 * 1024)  # expert descriptions can be long

LEVELS = ["abstract", "beginner", "intermediate", "expert"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    text_map = {}
    n = 0
    with open(args.csv, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            uid = (row.get("uid") or "").strip()
            if not uid:
                continue
            entry = {}
            for lvl in LEVELS:
                v = (row.get(lvl) or "").strip()
                if v:
                    entry[lvl] = v
            if entry:
                text_map[uid] = entry
            n += 1
            if n % 20000 == 0:
                print(f"  parsed {n} rows, {len(text_map)} with text")

    json.dump(text_map, open(args.out, "w"))
    print(f"Done: {len(text_map)} uids with descriptions -> {args.out}")
    # quick coverage sample
    sample = next(iter(text_map.items()))
    print("sample uid:", sample[0], "levels:", list(sample[1].keys()))


if __name__ == "__main__":
    main()

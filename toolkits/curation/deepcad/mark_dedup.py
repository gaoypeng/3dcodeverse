"""
mark_dedup.py
Annotate the JSONL with dedup info (non-destructive) and report cross-split
leakage (the same vec appearing in both train and test/val is a real ML hazard).

Adds to metadata:
  is_canonical : bool   (representative of its vec group)
  duplicate_of : str    (deepcad_id of the canonical, if this is a duplicate)
"""
import json
import argparse
from collections import defaultdict


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jsonl", required=True)
    ap.add_argument("--dups", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    dups = json.load(open(args.dups))  # {canonical: [dup,...]}
    dup_to_canon = {}
    group_of = {}
    for canon, members in dups.items():
        group_of.setdefault(canon, canon)
        for m in members:
            dup_to_canon[m] = canon
            group_of[m] = canon

    # First pass: collect split per deepcad_id for leakage analysis
    split_of = {}
    with open(args.jsonl) as f:
        for line in f:
            e = json.loads(line)
            split_of[e["metadata"]["deepcad_id"]] = e["metadata"]["split"]

    # leakage: groups whose members span >1 split
    group_splits = defaultdict(set)
    all_ids = set(split_of)
    for did in all_ids:
        canon = group_of.get(did, did)
        group_splits[canon].add(split_of[did])
    leak_groups = {g: s for g, s in group_splits.items() if len(s) > 1}
    leaked_models = sum(1 for did in all_ids
                        if len(group_splits[group_of.get(did, did)]) > 1)

    n_canon = 0
    n_dup = 0
    with open(args.jsonl) as f, open(args.out, "w") as out:
        for line in f:
            e = json.loads(line)
            did = e["metadata"]["deepcad_id"]
            is_canon = did not in dup_to_canon
            e["metadata"]["is_canonical"] = is_canon
            e["metadata"]["duplicate_of"] = dup_to_canon.get(did)
            if is_canon:
                n_canon += 1
            else:
                n_dup += 1
            out.write(json.dumps(e) + "\n")

    print(f"Annotated: {n_canon} canonical, {n_dup} duplicates")
    print(f"Cross-split leakage: {len(leak_groups)} vec-groups span multiple "
          f"splits, affecting {leaked_models} models")
    print(f"  -> dedup-to-canonical leaves {n_canon} unique models")


if __name__ == "__main__":
    main()

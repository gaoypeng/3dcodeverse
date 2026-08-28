"""
finalize.py
Two non-destructive post-processing passes on the corpus:

1. Fidelity tier   : metadata.fidelity_tier from IoU
     high   iou >= 0.9
     medium 0.5 <= iou < 0.9
     low    iou < 0.5
     unverified  iou is null (no ground-truth mesh)
   plus metadata.low_fidelity (bool) for quick filtering.

2. Leakage-free split : metadata.split_dedup -- every member of a vec-duplicate
   group is reassigned to the canonical member's split, so no vec spans >1 split.
   Original DeepCAD split is preserved in metadata.split.

Writes a new JSONL and reports the resulting split sizes + a zero-leakage check.
"""
import json
import argparse
from collections import defaultdict


def tier(iou):
    if iou is None:
        return "unverified"
    if iou < 0.5:
        return "low"
    if iou < 0.9:
        return "medium"
    return "high"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jsonl", required=True)
    ap.add_argument("--dups", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    dups = json.load(open(args.dups))           # {canonical: [dup,...]}
    canon_of = {}                                # member -> canonical
    for canon, members in dups.items():
        canon_of[canon] = canon
        for m in members:
            canon_of[m] = canon

    # pass 1: original split per id
    split_of = {}
    with open(args.jsonl) as f:
        for line in f:
            e = json.loads(line)
            split_of[e["metadata"]["deepcad_id"]] = e["metadata"]["split"]

    def group_split(did):
        c = canon_of.get(did, did)
        return split_of.get(c, split_of[did])

    counts = defaultdict(int)
    tiers = defaultdict(int)
    # group-level split for leakage verification
    group_splits = defaultdict(set)

    with open(args.jsonl) as f, open(args.out, "w") as out:
        for line in f:
            e = json.loads(line)
            md = e["metadata"]
            did = md["deepcad_id"]
            new_split = group_split(did)
            md["split_dedup"] = new_split
            t = tier(md.get("iou"))
            md["fidelity_tier"] = t
            md["low_fidelity"] = (t in ("low", "unverified"))
            counts[new_split] += 1
            tiers[t] += 1
            group_splits[canon_of.get(did, did)].add(new_split)
            out.write(json.dumps(e) + "\n")

    leak = sum(1 for s in group_splits.values() if len(s) > 1)
    print("Split_dedup sizes:", dict(counts))
    print("Fidelity tiers:", dict(tiers))
    print(f"Cross-split groups AFTER re-split: {leak}  (should be 0)")


if __name__ == "__main__":
    main()

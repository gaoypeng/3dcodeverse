"""Sample a training mix out of the md_max corpus with a per-dialect TOKEN budget.

md_max is ~0.77M pairs / ~0.9B tokens and 97% CadQuery+GLSL; every real run wants a different slice of it.

  python scripts/sample_mix.py --out data/md_27b_max \
      --budget blender=all,openscad=all,threejs=all,web=all,cadquery=25M,glsl=25M \
      [--src data/md_max] [--captions_per_sample 2] [--seed 5]

--captions_per_sample caps how many prompt variants of the SAME code may be kept (1 = no augmentation,
2 = instruction+detailed, 3 = everything) so you can ablate caption augmentation at a fixed token budget.
"""
import argparse
import collections
import json
import os
import random


def parse_budget(s):
    out = {}
    for part in s.split(","):
        if not part.strip():
            continue
        k, v = part.split("=")
        v = v.strip().lower()
        out[k.strip()] = None if v == "all" else int(float(v.rstrip("mk")) * (1e6 if v.endswith("m") else 1e3 if v.endswith("k") else 1))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="/wekafs/ict/hx_624/llm-ft/data/md_max")
    ap.add_argument("--out", required=True)
    ap.add_argument("--budget", required=True, help="dialect=tokens|all, comma separated")
    ap.add_argument("--captions_per_sample", type=int, default=3)
    ap.add_argument("--seed", type=int, default=5)
    a = ap.parse_args()
    random.seed(a.seed)
    budget = parse_budget(a.budget)

    rows = [json.loads(l) for l in open(f"{a.src}/train.jsonl")]
    # estimate tokens per row: chars/3.4 is close enough for budgeting (exact counts live in md_max/report.json)
    for r in rows:
        r["_ntok"] = int(sum(len(m["content"]) for m in r["messages"]) / 3.4)
    by = collections.defaultdict(list)
    for r in rows:
        by[r["dialect"]].append(r)

    kept, stats = [], {}
    for dia, pool in by.items():
        if dia not in budget:
            continue
        random.shuffle(pool)
        per_sample = collections.Counter()
        take, tok = [], 0
        for r in pool:
            if per_sample[r["id"]] >= a.captions_per_sample:
                continue
            if budget[dia] is not None and tok + r["_ntok"] > budget[dia]:
                continue
            per_sample[r["id"]] += 1
            take.append(r)
            tok += r["_ntok"]
            if budget[dia] is not None and tok >= budget[dia]:
                break
        kept += take
        stats[dia] = {"n": len(take), "Mtok_est": round(tok / 1e6, 1), "uniq_samples": len(per_sample)}
    random.shuffle(kept)
    os.makedirs(a.out, exist_ok=True)
    with open(f"{a.out}/train.jsonl", "w") as f:
        for r in kept:
            f.write(json.dumps({k: v for k, v in r.items() if k != "_ntok"}, ensure_ascii=False) + "\n")
    os.system(f"cp {a.src}/val.jsonl {a.out}/val.jsonl")
    json.dump(stats, open(f"{a.out}/mix.json", "w"), indent=2)
    print(json.dumps(stats, indent=1))
    print(f"[mix] {len(kept)} pairs | ~{sum(v['Mtok_est'] for v in stats.values()):.1f}M tokens -> {a.out}")


if __name__ == "__main__":
    main()

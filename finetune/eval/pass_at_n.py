import json, sys
pfx, n = sys.argv[1], int(sys.argv[2])
ok = {}; best = {}
for i in range(1, n + 1):
    for l in open(f"/wekafs/ict/hx_624/llm-ft/eval/out/{pfx}_s{i}/metrics.jsonl"):
        r = json.loads(l); t = r["task"]
        ok.setdefault(t, 0); ok[t] += r["exec"] == "OK"
        if "f@0.05" in r: best[t] = max(best.get(t, 0), r["f@0.05"])
T = len(ok)
print(f"pass@1 (avg over samples): {sum(ok.values())/(n*T):.3f} | pass@{n} (any sample OK): {sum(v>0 for v in ok.values())/T:.3f} | best-of-{n} F@0.05 (all, fail=0): {sum(best.values())/T:.3f}")

"""Final consolidation for the flattened Shadertoy corpus.

Order of preference, so the data stays as close to the author's source as possible:
  1. the ORIGINAL merged program, if the (now correct) validator compiles it  -> untouched
  2. the agent-repaired program from glsl_repair/<cat>_fixed/, if it compiles -> repaired
  3. otherwise                                                               -> kept, marked compiles=False
Writes the `compiles` / `repair` columns back into every shadertoy *_llamafactory_flat parquet.
"""
import collections, glob, hashlib, json, os, re, sys
from concurrent.futures import ProcessPoolExecutor
import pandas as pd
sys.path.insert(0, "/wekafs/ict/hx_624/llm-ft/scripts")
from qc_repair import code_of, glsl_compile

REPAIR = "/wekafs/ict/hx_624/glsl_repair"

def load_fixed():
    out = {}
    for cat in ("overload", "undeclared", "const_init", "syntax", "redefinition", "type", "other"):
        d = f"{REPAIR}/{cat}_fixed"
        idx = f"{REPAIR}/{cat}/index.json"
        if not (os.path.isdir(d) and os.path.exists(idx)):
            continue
        meta = json.load(open(idx))
        for f in glob.glob(f"{d}/*.glsl"):
            stem = os.path.basename(f)[:-5]
            h = (meta.get(stem) or {}).get("hash")
            if h:
                out[h] = (cat, open(f).read())
    return out

def job(args):
    h, body, fixed = args
    ok, _ = glsl_compile(body)
    if ok:
        return h, True, "original", None
    if fixed:
        cat, code = fixed
        ok2, _ = glsl_compile(code)
        if ok2:
            return h, True, f"repaired:{cat}", code
    return h, False, "unfixable", None

def main():
    fixed = load_fixed()
    print(f"[fin] agent repairs available: {len(fixed)}", flush=True)
    dirs = sorted(glob.glob("/wekafs/ict/hx_624/hf_subdirs/shadertoy/*_llamafactory_flat"))
    uniq, where = {}, collections.defaultdict(list)
    for d in dirs:
        df = pd.read_parquet(os.path.join(d, "train.parquet"))
        for i, row in enumerate(df.itertuples()):
            b = code_of(row.conversations[1]["value"]); h = hashlib.md5(b.encode()).hexdigest()
            uniq.setdefault(h, b); where[h].append(d)
    items = [(h, b, fixed.get(h)) for h, b in uniq.items()]
    print(f"[fin] validating {len(items)} unique programs", flush=True)
    res = {}
    with ProcessPoolExecutor(28) as ex:
        for n, (h, ok, how, code) in enumerate(ex.map(job, items, chunksize=8)):
            res[h] = (ok, how, code)
            if (n+1) % 10000 == 0:
                g = sum(1 for v in res.values() if v[0])
                print(f"[fin] {n+1}/{len(items)} ok={g} ({100*g/(n+1):.1f}%)", flush=True)
    stats = collections.Counter(v[1] for v in res.values())
    for d in dirs:
        p = os.path.join(d, "train.parquet"); df = pd.read_parquet(p)
        conv = df["conversations"].tolist(); okc, howc = [], []
        for c in conv:
            b = code_of(c[1]["value"]); h = hashlib.md5(b.encode()).hexdigest()
            ok, how, code = res.get(h, (False, "unknown", None))
            if code:
                c[1]["value"] = "```glsl\n" + code.strip() + "\n```"
            okc.append(ok); howc.append(how)
        df["conversations"] = conv; df["compiles"] = okc; df["repair"] = howc
        df.to_parquet(p, index=False, compression="zstd")
        json.dump({"rows": len(df), "compiles": int(sum(okc)),
                   "repaired": int(sum(1 for x in howc if x.startswith("repaired")))},
                  open(os.path.join(d, "qc.json"), "w"), indent=2)
    good = sum(1 for v in res.values() if v[0])
    json.dump({"unique_programs": len(items), "compile_ok": good,
               "compile_rate": round(good/len(items), 4), "how": dict(stats)},
              open("/wekafs/ict/hx_624/hf_subdirs/glsl_final_report.json", "w"), indent=2)
    print(f"[fin] FINAL {good}/{len(items)} = {100*good/len(items):.1f}% | {dict(stats)}", flush=True)

if __name__ == "__main__":
    main()

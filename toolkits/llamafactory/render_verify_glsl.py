"""Render-verify the compile-verified Shadertoy corpus: compiling is not the same as drawing something.

Samples (or exhaustively walks) the unique programs in the flattened shadertoy datasets, renders each in headless
WebGL2, and records status / distinct colours / pixel std / whether the image changes with iTime. Writes
`renders_ok`, `renders_status` back into the parquet rows when run with --write.

usage: python scripts/render_verify_glsl.py [--sample 3000] [--workers 8] [--write]
"""
import argparse, collections, glob, hashlib, json, os, random, re, sys, tempfile
from concurrent.futures import ProcessPoolExecutor
import pandas as pd
sys.path.insert(0, "/wekafs/ict/hx_624/llm-ft/eval")
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "/wekafs/ict/hx_624/cache/ms-playwright")

def code_of(m):
    x = re.findall(r"```[a-z]*\n(.*?)```", m, flags=re.S)
    return (x[0] if x else m).strip()

def job(chunk):
    """one persistent browser per worker, many shaders per browser (a browser launch costs 3-5 s)"""
    from glsl_render_batch import BatchRenderer
    out = []
    with BatchRenderer() as r:
        for h, code in chunk:
            x = r.run(code)
            out.append((h, x.get("status", "FAIL"), x.get("distinct", 0), x.get("std", 0.0),
                        bool(x.get("changed")), (x.get("error") or "")[:120]))
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=3000)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    uniq, where = {}, collections.defaultdict(list)
    for f in sorted(glob.glob("/wekafs/ict/hx_624/hf_subdirs/shadertoy/*_llamafactory_flat/train.parquet")):
        df = pd.read_parquet(f)
        if "compiles" not in df: continue
        for i, row in enumerate(df[df["compiles"]].itertuples()):
            b = code_of(row.conversations[1]["value"]); h = hashlib.md5(b.encode()).hexdigest()
            uniq.setdefault(h, b); where[h].append(f)
    items = list(uniq.items())
    print(f"[render] {len(items)} unique compile-verified programs", flush=True)
    random.seed(11); random.shuffle(items)
    if a.sample: items = items[:a.sample]
    res, stats = {}, collections.Counter()
    CH = 60
    chunks = [items[i:i+CH] for i in range(0, len(items), CH)]
    done = 0
    with ProcessPoolExecutor(a.workers) as ex:
        for out in ex.map(job, chunks):
            for h, st, dis, std, ch, err in out:
                res[h] = (st, dis, std, ch, err); stats[st] += 1
            done += len(out)
            if done % 1200 < CH:
                print(f"[render] {done}/{len(items)} {dict(stats)}", flush=True)
    animated = sum(1 for v in res.values() if v[3])
    rep = {"checked": len(res), "status": dict(stats), "animated": animated,
           "render_rate": round(stats["OK"]/max(1,len(res)), 4),
           "static_examples": [{"hash": h, "err": v[4]} for h, v in list(res.items()) if v[0] == "STATIC"][:5],
           "fail_examples": [{"hash": h, "err": v[4]} for h, v in list(res.items()) if v[0] == "FAIL"][:5]}
    json.dump(rep, open("/wekafs/ict/hx_624/hf_subdirs/glsl_render_report.json", "w"), indent=2)
    print(f"[render] OK {stats['OK']}/{len(res)} ({100*stats['OK']/max(1,len(res)):.1f}%) | animated {animated} | {dict(stats)}", flush=True)
    if a.write:
        for f in sorted(glob.glob("/wekafs/ict/hx_624/hf_subdirs/shadertoy/*_llamafactory_flat/train.parquet")):
            df = pd.read_parquet(f)
            if "compiles" not in df: continue
            st = []
            for row in df.itertuples():
                b = code_of(row.conversations[1]["value"]); h = hashlib.md5(b.encode()).hexdigest()
                st.append(res.get(h, ("unchecked",))[0])
            df["renders_status"] = st; df["renders_ok"] = [s == "OK" for s in st]
            df.to_parquet(f, index=False, compression="zstd")
        print("[render] wrote renders_status/renders_ok into the parquets", flush=True)

if __name__ == "__main__":
    main()

"""Survey every metadata.parquet in 3DCodeVerse: counts, code length, caption availability, language, license, multi_file.
Writes data/analysis/3dcodeverse_survey.md (+ .json). Token counts are estimated with the Qwen3.5 tokenizer on a sample."""
import glob, json, os, random, sys
import numpy as np, pandas as pd
ROOT = "/wekafs/ict/hx_624/data/3dcodeverse_meta"
OUT = "/wekafs/ict/hx_624/llm-ft/data/analysis"; os.makedirs(OUT, exist_ok=True)
try:
    from transformers import AutoTokenizer
    tok = AutoTokenizer.from_pretrained("/wekafs/ict/hx_624/models/Qwen3.5-9B")
except Exception as e:
    tok = None; print("no tokenizer:", e)
random.seed(0)
rows = []
files = sorted(glob.glob(f"{ROOT}/**/metadata.parquet", recursive=True))
print(len(files), "metadata.parquet files")
agg = {}
for f in files:
    rel = os.path.relpath(os.path.dirname(f), ROOT)
    src = rel.split("/")[0]
    df = pd.read_parquet(f)
    n = len(df)
    code = df["code"].fillna("")
    clen = code.str.len().values
    meta0 = json.loads(df["meta_json"].iloc[0]) if "meta_json" in df and n else {}
    lang = df["language"].iloc[0] if "language" in df else meta0.get("language")
    caps = df["captions"].iloc[0] if "captions" in df else None
    cap_keys = list(caps.keys()) if isinstance(caps, dict) else []
    # caption availability
    def has(k): return float(np.mean([isinstance(c, dict) and bool(c.get(k)) for c in df["captions"]])) if "captions" in df else 0.0
    cap_avail = {k: round(has(k), 3) for k in ["detailed", "instruction", "factory", "brief", "synthetic_prompt"]}
    # token estimate on up to 200 random samples
    tok_p50 = tok_p90 = tok_mean = None
    if tok is not None and n:
        idx = random.sample(range(n), min(200, n))
        tl = np.array([len(tok(code.iloc[i]).input_ids) for i in idx])
        tok_p50, tok_p90, tok_mean = int(np.percentile(tl, 50)), int(np.percentile(tl, 90)), float(tl.mean())
    uniq = code.nunique()
    rows.append(dict(subset=rel, source=src, n=n, language=lang, license=meta0.get("license"), multi_file=meta0.get("multi_file"),
                     entry=meta0.get("entry"), chars_p50=int(np.percentile(clen, 50)) if n else 0, chars_p90=int(np.percentile(clen, 90)) if n else 0,
                     tok_p50=tok_p50, tok_p90=tok_p90, tok_mean=tok_mean, est_total_tokens=(tok_mean * n if tok_mean else None),
                     unique_code_frac=round(uniq / max(n, 1), 3), caption_keys=cap_keys, cap_avail=cap_avail, columns=list(df.columns)))
    a = agg.setdefault(src, dict(n=0, est_tokens=0.0, langs=set()))
    a["n"] += n; a["est_tokens"] += (tok_mean * n if tok_mean else 0); a["langs"].add(str(lang))
    print(f"{rel:45s} n={n:6d} lang={str(lang):16s} tok_p50={tok_p50} tok_p90={tok_p90} uniq={uniq/max(n,1):.2f} caps={cap_avail}")
json.dump({"subsets": rows, "by_source": {k: dict(n=v["n"], est_tokens=v["est_tokens"], langs=sorted(v["langs"])) for k, v in agg.items()}},
          open(f"{OUT}/3dcodeverse_survey.json", "w"), indent=1, default=str)
with open(f"{OUT}/3dcodeverse_survey.md", "w") as f:
    f.write("| source | #samples | languages | est. tokens (code only) |\n|---|---|---|---|\n")
    for k, v in sorted(agg.items(), key=lambda kv: -kv[1]["n"]):
        f.write(f"| {k} | {v['n']} | {', '.join(sorted(v['langs']))} | {v['est_tokens']/1e6:.1f}M |\n")
    f.write("\n| subset | n | lang | tok p50 | tok p90 | unique code | captions (detailed/instruction/factory) | license |\n|---|---|---|---|---|---|---|---|\n")
    for r in rows:
        f.write(f"| {r['subset']} | {r['n']} | {r['language']} | {r['tok_p50']} | {r['tok_p90']} | {r['unique_code_frac']} | {r['cap_avail']['detailed']}/{r['cap_avail']['instruction']}/{r['cap_avail']['factory']} | {r['license']} |\n")
print("written", OUT)

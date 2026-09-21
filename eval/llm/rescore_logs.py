"""Re-score the official 3DCodeBench frontier-model logs with this package's executor and metrics.

`YipengGao/3DCode/3DCodeBench_ModelLogs/data/{text_to_3D,image_to_3D}.parquet` hold every generated script of
the paper's 12 models (one row per trial, `code` column).  Writing them into the gen-dir layout lets
`run_eval --stages exec render score` produce exec / Chamfer / SigLIP-2 / DINOv3 numbers for the frontier
models on *this* machine — the calibration that tells whether a local 8B number is comparable to the paper.

    python -m llm.rescore_logs --setting text_to_3D --models gpt-5.5 claude-opus-4-7
    python -m llm.run_eval --backend reference --run official_gpt-5.5 --suites 3dcodebench_official_text --stages exec render score

Runs are named `official_<model>`; the suite is `3dcodebench_official_text` (or `_img4`).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import config
from .generate import finish, write_answer
from .suites import load_prompts

SUITE = {"text_to_3D": "3dcodebench_official_text", "image_to_3D": "3dcodebench_official_img4"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--setting", choices=list(SUITE), default="text_to_3D")
    ap.add_argument("--models", nargs="*", default=None, help="model ids as in the parquet (default: all)")
    ap.add_argument("--out", type=Path, default=config.OUT_DIR)
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    import pandas as pd

    p = config.HUB_DIR / "3DCode" / "3DCodeBench_ModelLogs" / "data" / f"{a.setting}.parquet"
    if not p.exists():
        raise SystemExit(f"{p} missing — run download.py --logs")
    df = pd.read_parquet(p)
    counts = df.groupby("model").size()
    if a.list:
        print(df.assign(ok=df.status.eq("OK")).groupby("model").agg(n=("ok", "size"), ok=("ok", "mean")).sort_values("ok"))
        return
    suite = SUITE[a.setting]
    rows = {r["id"]: r for r in load_prompts(suite)}
    for model in (a.models or sorted(counts.index)):
        sub = df[df.model == model].drop_duplicates("instance")
        gen_dir = a.out / f"official_{model}" / suite
        recs = []
        for _, r in sub.iterrows():
            tid = r["instance"]
            if tid not in rows:
                continue
            code = r["code"] if isinstance(r["code"], str) else ""
            text = code if code.lstrip().startswith("```") or not code.strip() else f"```python\n{code}\n```"
            recs.append(write_answer(gen_dir, rows[tid], text, {"n_new_tokens": int(r["output_tokens"]) if r.get("output_tokens") == r.get("output_tokens") else None,
                                                              "finished": r["status"] == "OK", "truncated": False,
                                                              "official_status": r["status"], "cost_usd": r.get("cost_usd")}))
        finish(gen_dir, recs, {"backend": "official_logs", "model": model, "temperature": float(sub.temperature.iloc[0]) if "temperature" in sub else None,
                               "max_new_tokens": None, "setting": a.setting})
        print(f"[rescore] {model}: {len(recs)} instances -> {gen_dir}")


if __name__ == "__main__":
    main()

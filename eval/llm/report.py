"""Collect summary.json files under an output root into one table (markdown + json).

Layout expected: <out>/<run>/<suite>[/sN]/summary.json   (run = model tag, sN = sample index for pass@k runs).
pass@k / best-of-N are computed per (run, suite) from the metrics.jsonl of every sample directory.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from . import metrics
from ._jsonl import read_rows

COLS = ["run", "suite", "n", "exec", "exec_rate", "f05_all", "f10_ok", "cd_ok", "iou_ok", "siglip2", "dinov3", "floating", "watertight", "judge",
        "render_rate", "trunc", "tok", "no_fence", "multi_blk", "T"]


def fmt(v, kind="f"):
    if v is None:
        return "–"
    if kind == "pct":
        return f"{100 * v:.1f}%"
    if kind == "f":
        return f"{v:.3f}"
    return str(v)


def collect(root: Path) -> list[dict]:
    rows = []
    for p in sorted(root.rglob("summary.json")):
        s = json.loads(p.read_text())
        rel = p.relative_to(root).parts   # run / suite / [sN] / summary.json
        s["run"], s["_dir"] = rel[0], str(p.parent)
        s["sample"] = rel[2] if len(rel) == 4 else "s0"
        for j in sorted(p.parent.glob("judge_*_summary.json")):      # latest judge tag wins
            js = json.loads(j.read_text())
            s["judge_overall_cond"], s["judge_overall_pen"], s["judge"] = js.get("overall_cond"), js.get("overall_pen"), js.get("judge")
        rows.append(s)
    return rows


def pass_at_k(rows: list[dict]) -> list[dict]:
    """Group sample dirs per (run, suite) → pass@1..N, best-of-N F@0.05(all)."""
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        groups[(r["run"], r["suite"])].append(r)
    out = []
    for (run, suite), rs in groups.items():
        if len(rs) < 2:
            continue
        per_task: dict[str, list[dict]] = defaultdict(list)
        for r in rs:
            for m in read_rows(Path(r["_dir"]) / "metrics.jsonl"):
                per_task[m["id"]].append(m)
        n = len(rs)
        ks = [k for k in (1, 2, 4, 8, n) if k <= n]
        rec = {"run": run, "suite": suite, "n_samples": n, "n_tasks": len(per_task)}
        for k in sorted(set(ks)):
            rec[f"pass@{k}"] = metrics.mean([metrics.pass_at_k(len(v), sum(m["status"] == "OK" for m in v), k) for v in per_task.values()])
        if any("f@0.05" in m for v in per_task.values() for m in v):
            rec["best_of_n_f05_all"] = metrics.mean([max([m.get("f@0.05", 0.0) or 0.0 for m in v]) for v in per_task.values()])
        out.append(rec)
    return out


def markdown(rows: list[dict]) -> str:
    lines = ["| " + " | ".join(COLS) + " |", "|" + "---|" * len(COLS)]
    for s in sorted(rows, key=lambda r: (r["run"], r["suite"], r["sample"])):
        g, x = s.get("gen", {}), s.get("extract", {})
        im, st = s.get("image_sim", {}), s.get("structural", {})
        sig = (im.get("siglip2") or {}).get("view_paired", {}).get("conditional")
        din = (im.get("dinov3") or {}).get("view_paired", {}).get("conditional")
        vals = [s["run"], s["suite"] + ("" if s["sample"] == "s0" else f"/{s['sample']}"), s["n"], s["exec_ok"],
                fmt(s["exec_rate"], "pct"), fmt(s.get("f05_mean_all")), fmt(s.get("f10_mean_scored")),
                fmt(s.get("chamfer_mean_scored")), fmt(s.get("iou_mean_scored")), fmt(sig), fmt(din),
                fmt(st.get("floating_part_rate_cond")), fmt(st.get("is_watertight_cond"), "pct"), fmt(s.get("judge_overall_cond")),
                fmt(s.get("render_rate"), "pct"),
                s.get("truncated"), fmt(s.get("mean_new_tokens"), "i") if s.get("mean_new_tokens") is None else int(s["mean_new_tokens"]),
                x.get("no_fence"), x.get("multi_block"), g.get("temperature")]
        lines.append("| " + " | ".join(str(v) for v in vals) + " |")
    return "\n".join(lines)


def build(root: Path, out_md: Path | None = None) -> str:
    rows = collect(root)
    pk = pass_at_k(rows)
    md = ["# 3dcodeverse_eval results", f"root: `{root}`", "",
          "exec = OK / n; f05_all = mean F@0.05 with failures as 0 (headline geometry number); f10_ok / cd_ok / iou_ok = mean over "
          "executed tasks; render_rate = GLSL compiled *and* painted a non-uniform image; trunc = hit max_new_tokens; "
          "siglip2 / dinov3 = official view-paired similarity, conditional; floating = official floating-part rate (cond.); watertight = "
          "share of executed meshes that are watertight; judge = VLM absolute overall 1-5 (cond.); no_fence / multi_blk = extraction diagnostics.", "", markdown(rows)]
    if pk:
        md += ["", "## pass@k over sampled runs", "", "| run | suite | samples | tasks | " + " | ".join(k for k in pk[0] if k.startswith("pass@")) + " | best-of-N F@0.05 |",
               "|---|---|---|---|" + "---|" * (len([k for k in pk[0] if k.startswith("pass@")]) + 1)]
        for r in pk:
            pks = " | ".join(fmt(r[k]) for k in r if k.startswith("pass@"))
            md.append(f"| {r['run']} | {r['suite']} | {r['n_samples']} | {r['n_tasks']} | {pks} | {fmt(r.get('best_of_n_f05_all'))} |")
    text = "\n".join(md) + "\n"
    (out_md or root / "report.md").write_text(text)
    (root / "report.json").write_text(json.dumps({"summaries": rows, "pass_at_k": pk}, indent=2))
    return text


if __name__ == "__main__":
    import argparse
    from . import config

    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=config.OUT_DIR)
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args()
    print(build(a.root, a.out))

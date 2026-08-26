"""Generate llm_finetune_exps.md — the full experiment log, built from what is actually on disk.

Reads every training run (runs/*/train_results.json + its LLaMA-Factory config) and every evaluation
(eval/out/*/summary.json + extract_stats.json), joins them by the naming conventions used in this project,
and writes one markdown file with: the run inventory, 3DCodeBench results, per-dialect results, and the
zero-shot baselines. Curated commentary lives in docs/REPORT.md; this file is the raw log.

usage: python scripts/build_exp_log.py > llm_finetune_exps_generated.md
"""
import glob
import json
import os
import re

ROOT = "/wekafs/ict/hx_624/llm-ft"
CFG = f"{ROOT}/configs/lf"


def jload(p, default=None):
    try:
        return json.load(open(p))
    except Exception:
        return default


def yload(p):
    try:
        import yaml
        return yaml.safe_load(open(p))
    except Exception:
        return {}


def hhmm(sec):
    if not sec:
        return "–"
    h, m = divmod(int(sec) // 60, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m"


def run_rows():
    rows = []
    for d in sorted(glob.glob(f"{ROOT}/runs/*/")):
        name = os.path.basename(d.rstrip("/"))
        if name.startswith("_smoke"):
            continue
        tr = jload(os.path.join(d, "train_results.json")) or jload(os.path.join(d, "all_results.json"))
        if not tr:
            continue
        # find the config that produced it
        cfg = {}
        for c in glob.glob(f"{CFG}/*.yaml"):
            y = yload(c)
            if isinstance(y, dict) and str(y.get("output_dir", "")).rstrip("/").endswith(name):
                cfg = y
                cfg["_file"] = os.path.basename(c)
                break
        rows.append({
            "run": name,
            "base": os.path.basename(str(cfg.get("model_name_or_path", "")).rstrip("/")) or "–",
            "stage": cfg.get("stage", "–"),
            "type": ("full" if cfg.get("finetuning_type") == "full" else f"lora r{cfg.get('lora_rank', '?')}") if cfg else "–",
            "data": cfg.get("dataset", "–"),
            "cutoff": cfg.get("cutoff_len", "–"),
            "epochs": tr.get("epoch", cfg.get("num_train_epochs", "–")),
            "lr": cfg.get("learning_rate", "–"),
            "ds": "zero3" if cfg.get("deepspeed") else "ddp",
            "loss": round(tr["train_loss"], 3) if tr.get("train_loss") is not None else "–",
            "eval_loss": round(tr["eval_loss"], 3) if tr.get("eval_loss") is not None else "–",
            "runtime": hhmm(tr.get("train_runtime")),
            "cfg": cfg.get("_file", "–"),
        })
    return rows


def bench_rows():
    rows = []
    for p in sorted(glob.glob(f"{ROOT}/eval/out/*/summary.json")):
        s = jload(p, {})
        if "exec_ok_rate" not in s:
            continue
        name = p.split("/")[-2]
        x = jload(os.path.join(os.path.dirname(p), "extract_stats.json"), {}) or {}
        rows.append({
            "eval": name,
            "n": s.get("n_tasks"),
            "exec": s.get("exec_ok"),
            "rate": round(100 * s["exec_ok_rate"], 1),
            "f05_all": s.get("f@0.05_mean_all(fail=0)"),
            "f05_ok": s.get("f@0.05_mean_scored"),
            "f01_ok": s.get("f@0.1_mean_scored"),
            "chamfer": s.get("chamfer_mean_scored"),
            "tok": x.get("mean_new_tokens"),
        })
    return sorted(rows, key=lambda r: -r["rate"])


def dialect_rows():
    rows = []
    for p in sorted(glob.glob(f"{ROOT}/eval/out/*/summary.json")):
        s = jload(p, {})
        if "dialect" not in s:
            continue
        name = p.split("/")[-2]
        rate = s.get("gen_exec_rate", s.get("exec_rate"))
        rows.append({
            "eval": name,
            "dialect": s["dialect"],
            "n": s.get("n"),
            "ok": s.get("gen_exec_ok", s.get("exec_ok")),
            "rate": round(100 * rate, 1) if rate is not None else None,
            "ref_ok": s.get("ref_exec_ok"),
            "f05_all": s.get("f@0.05_mean_all"),
            "f05_ok": s.get("f@0.05_mean_scored"),
            "chamfer": s.get("chamfer_mean_scored"),
        })
    return rows


def fmt(v, nd=3):
    if v is None:
        return "–"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def main():
    print("## A. Training runs (from `runs/*/train_results.json` + the config that produced each)\n")
    print("| run | base | stage | method | data | cutoff | ep | lr | par | train loss | eval loss | wall | config |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in run_rows():
        print(f"| `{r['run']}` | {r['base']} | {r['stage']} | {r['type']} | `{r['data']}` | {r['cutoff']} | {fmt(r['epochs'],1)} | {r['lr']} | {r['ds']} | {r['loss']} | {r['eval_loss']} | {r['runtime']} | `{r['cfg']}` |")

    print("\n## B. 3DCodeBench (212 tasks: generate → run in Blender → export GLB → Chamfer/F vs GT)\n")
    print("| eval run | exec | rate | F@0.05 (all, fail=0) | F@0.05 (ok) | F@0.1 (ok) | Chamfer (ok) | mean out tok |")
    print("|---|---|---|---|---|---|---|---|")
    for r in bench_rows():
        print(f"| `{r['eval']}` | {r['exec']}/{r['n']} | {r['rate']}% | {fmt(r['f05_all'])} | {fmt(r['f05_ok'])} | {fmt(r['f01_ok'])} | {fmt(r['chamfer'])} | {fmt(r['tok'],0)} |")

    print("\n## C. Held-out dialect sets (per-dialect executors; reference code is executed the same way)\n")
    for dia in ["blender_heldout", "cadquery", "openscad", "glsl", "threejs"]:
        rs = [r for r in dialect_rows() if r["dialect"] == dia]
        if not rs:
            continue
        rs.sort(key=lambda r: -(r["rate"] or 0))
        print(f"\n### {dia} (n={rs[0]['n']}, reference exec OK={rs[0]['ref_ok'] if rs[0]['ref_ok'] else '–'})\n")
        print("| eval run | exec | rate | F@0.05 (all) | F@0.05 (ok) | Chamfer (ok) |")
        print("|---|---|---|---|---|---|")
        for r in rs:
            print(f"| `{r['eval']}` | {r['ok']}/{r['n']} | {r['rate']}% | {fmt(r['f05_all'])} | {fmt(r['f05_ok'])} | {fmt(r['chamfer'])} |")


if __name__ == "__main__":
    main()

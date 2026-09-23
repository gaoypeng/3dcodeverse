"""Score one gen dir of one suite → <gen_dir>/metrics.jsonl + summary.json (one schema for every suite).

summary.json
  suite, dialect, model, n, statuses{OK,EMPTY,FAIL,CRASH,TIMEOUT,MISSING}, exec_ok, exec_rate,
  n_ref_ok (tasks whose reference produced a mesh — the denominator of the *_all geometry means),
  n_scored, chamfer_mean_scored, chamfer_median_scored, f05_mean_scored, f10_mean_scored, iou_mean_scored,
  f05_mean_all, f10_mean_all, iou_mean_all          (fail = 0, over n_ref_ok)
  render_ok, render_rate                            (glsl: compiled AND painted a non-uniform image)
  truncated, mean_new_tokens, extract{no_fence, multi_block, chosen_not_last, from_think_fallback, ...}
  gen{backend, model, temperature, seed, max_new_tokens, no_think}
"""
from __future__ import annotations

import json
from collections import Counter

import numpy as np
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from . import metrics
from ._jsonl import read_rows
from .suites import get_suite, load_prompts, resolve


def _geom(args):
    gen_mesh, ref_mesh, n_points = args
    try:
        return metrics.geometry_metrics(gen_mesh, ref_mesh, n_points=n_points)
    except Exception as e:  # noqa: BLE001
        return {"metric_error": f"{type(e).__name__}: {str(e)[:200]}"}


def _geom_official(args):
    gen_mesh, ref_mesh = args
    try:
        return metrics.chamfer_official(gen_mesh, ref_mesh)
    except Exception as e:  # noqa: BLE001
        return {"metric_error_official": f"{type(e).__name__}: {str(e)[:200]}"}


STRUCT_FIELDS = ("n_components", "floating_part_rate", "floating_area_fraction", "largest_component_volume_fraction",
                 "nonmanifold_edge_rate", "boundary_edge_rate", "is_watertight", "ground_contact", "com_over_support",
                 "com_support_margin")
STRUCT_PENALTY = {"floating_part_rate": 1.0, "floating_area_fraction": 1.0, "largest_component_volume_fraction": 0.0,
                  "nonmanifold_edge_rate": 1.0, "boundary_edge_rate": 1.0, "is_watertight": 0.0, "ground_contact": 0.0,
                  "com_over_support": 0.0}


def _struct(args):
    """Official structural-integrity fields (structural.py) for one generated mesh (+ component ratio vs the reference)."""
    gen_mesh, ref_mesh = args
    from . import structural

    up = 1 if str(gen_mesh).endswith(".glb") else 2
    try:
        rec, st = structural.score_glb(gen_mesh, 0.01, up, 1e-3, 100_000)
        if rec is None:
            return {"struct_status": st}
        out = {"struct_status": st, **{k: rec.get(k) for k in STRUCT_FIELDS}}
        if ref_mesh:
            rrec, rst = structural.score_glb(ref_mesh, 0.01, up, 1e-3, 100_000)
            if rrec:
                out["ref_n_components"] = rrec["n_components"]
                out["abs_log2_component_ratio"] = abs(float(np.log2(max(rec["n_components"] / max(rrec["n_components"], 1), 1e-9))))
        return out
    except Exception as e:  # noqa: BLE001
        return {"struct_status": f"SCORE_FAIL: {type(e).__name__}: {str(e)[:120]}"}


def score_dir(gen_dir: Path, suite: str, ref: str = "executed", workers: int = 4, with_geometry: bool = True,
              image_encoders: tuple[str, ...] = ("siglip2", "dinov3"), device: str = "cuda") -> dict:
    gen_dir = Path(gen_dir)
    spec = get_suite(suite)
    rows = {r["id"]: r for r in load_prompts(suite)}
    ex = {}
    if (gen_dir / "exec_results.jsonl").exists():
        ex = {r["id"]: r for r in read_rows(gen_dir / "exec_results.jsonl")}
    gens = {}
    if (gen_dir / "gens.jsonl").exists():
        gens = {r["id"]: r for r in read_rows(gen_dir / "gens.jsonl")}
    gen_stats = json.loads((gen_dir / "gen_stats.json").read_text()) if (gen_dir / "gen_stats.json").exists() else {}
    n_prompts_total = len(rows)
    if gens:   # a --limit run: score only what was generated, but say so in the summary
        rows = {tid: r for tid, r in rows.items() if tid in gens}

    out_rows, geom_jobs, struct_jobs = [], [], []
    for tid, row in rows.items():
        e = ex.get(tid, {})
        r = {"id": tid, "status": e.get("status", "MISSING"), "error": (e.get("error") or None) and str(e.get("error"))[:200],
             "latency_s": e.get("latency_s"), "n_new_tokens": gens.get(tid, {}).get("n_new_tokens"),
             "truncated": gens.get(tid, {}).get("truncated")}
        if "render" in spec.metrics:
            r["render"] = e.get("render")
        ref_rel = row["reference"].get("mesh")
        if ref == "canonical" and ref_rel:
            cand = resolve(ref_rel).with_name("ref_canonical.glb")
            ref_rel = str(cand.relative_to(resolve("."))) if cand.exists() else ref_rel
        ref_mesh = resolve(ref_rel) if ref_rel else None
        r["has_ref"] = bool(ref_mesh and ref_mesh.exists())
        if with_geometry and "geometry" in spec.metrics and r["status"] == "OK" and e.get("mesh") and r["has_ref"]:
            geom_jobs.append((len(out_rows), (e["mesh"], str(ref_mesh), spec.n_points)))
        if with_geometry and spec.dialect in ("blender", "cadquery", "openscad") and r["status"] == "OK" and e.get("mesh"):
            struct_jobs.append((len(out_rows), (e["mesh"], str(ref_mesh) if r["has_ref"] else None)))
        out_rows.append(r)

    if geom_jobs:
        with ProcessPoolExecutor(workers) as pool:
            for (i, _), res in zip(geom_jobs, pool.map(_geom, [j for _, j in geom_jobs])):
                out_rows[i].update(res)
            if "chamfer_official" in spec.metrics:
                for (i, _), res in zip(geom_jobs, pool.map(_geom_official, [(j[0], j[1]) for _, j in geom_jobs])):
                    out_rows[i].update(res)

    if struct_jobs:
        with ProcessPoolExecutor(workers) as pool:
            for (i, _), res in zip(struct_jobs, pool.map(_struct, [j for _, j in struct_jobs])):
                out_rows[i].update(res)

    image_summ = {}
    if "image_sim" in spec.metrics and any(r["reference"].get("renders") for r in rows.values()):
        rendered = {p.parent.parent.parent.name for p in gen_dir.glob("*/exec/renders/Image_035.png")}
        for tid in rows:
            out_rows[[r["id"] for r in out_rows].index(tid)]["rendered"] = tid in rendered
        if rendered:
            from . import image_sim
            for enc in image_encoders:
                try:
                    image_summ[enc] = image_sim.score_gen_dir(gen_dir, rows, resolve, enc, device, text_sim=(enc.startswith("siglip") and not any(r.get("images") for r in rows.values())))
                    per = {r["id"]: r for r in read_rows(gen_dir / f"image_sim_{enc}.jsonl")}
                    for r in out_rows:
                        p = per.get(r["id"], {})
                        r[f"{enc}_paired"], r[f"{enc}_assigned"] = p.get("view_paired"), p.get("best_assignment")
                        if "text_image" in p:
                            r[f"{enc}_text"] = p["text_image"]
                except Exception as e:  # noqa: BLE001
                    image_summ[enc] = {"error": f"{type(e).__name__}: {str(e)[:200]}"}
                    print(f"[score] image_sim {enc} failed: {image_summ[enc]['error']}", flush=True)

    with (gen_dir / "metrics.jsonl").open("w") as f:
        for r in out_rows:
            f.write(json.dumps(r) + "\n")

    n = len(out_rows)
    st = Counter(r["status"] for r in out_rows)
    ok = st.get("OK", 0)
    summ = {"suite": suite, "dialect": spec.dialect, "model": gen_stats.get("model"), "n": n, "n_prompts_total": n_prompts_total,
            "statuses": dict(st),
            "exec_ok": ok, "exec_rate": ok / n if n else None,
            "truncated": sum(bool(r.get("truncated")) for r in out_rows),
            "mean_new_tokens": metrics.mean([r.get("n_new_tokens") for r in out_rows]),
            "extract": {k: gen_stats.get(k) for k in ("no_fence", "multi_block", "chosen_not_last", "from_think_fallback",
                                                       "syntax_valid", "no_dialect_marker", "with_think")},
            "gen": {k: gen_stats.get(k) for k in ("backend", "model", "temperature", "seed", "max_new_tokens", "no_think",
                                                   "tok_per_s", "wall_s")}}
    if "geometry" in spec.metrics:
        with_ref = [r for r in out_rows if r["has_ref"]]
        scored = [r for r in with_ref if "chamfer" in r]
        n_ref = len(with_ref)
        summ.update(n_ref_ok=n_ref, n_scored=len(scored),
                    chamfer_mean_scored=metrics.mean([r["chamfer"] for r in scored]),
                    chamfer_median_scored=metrics.median([r["chamfer"] for r in scored]),
                    f05_mean_scored=metrics.mean([r["f@0.05"] for r in scored]),
                    f10_mean_scored=metrics.mean([r["f@0.1"] for r in scored]),
                    iou_mean_scored=metrics.mean([r.get("iou_vox") for r in scored]),
                    f05_mean_all=(sum(r["f@0.05"] for r in scored) / n_ref) if n_ref else None,
                    f10_mean_all=(sum(r["f@0.1"] for r in scored) / n_ref) if n_ref else None,
                    iou_mean_all=(sum(r.get("iou_vox") or 0 for r in scored) / n_ref) if n_ref else None)
    if struct_jobs:
        sc = [r for r in out_rows if r.get("struct_status") == "OK"]
        summ["structural"] = {"n_scored": len(sc)}
        for k, pen in STRUCT_PENALTY.items():
            vals = [float(r[k]) for r in sc if r.get(k) is not None]
            summ["structural"][f"{k}_cond"] = metrics.mean(vals)
            summ["structural"][f"{k}_pen"] = (sum(vals) + pen * (n - len(vals))) / n if n else None
        summ["structural"]["n_components_median_cond"] = metrics.median([r.get("n_components") for r in sc])
        summ["structural"]["abs_log2_component_ratio_cond"] = metrics.mean([r.get("abs_log2_component_ratio") for r in sc])
    if "chamfer_official" in spec.metrics:
        sc = [r for r in out_rows if r.get("cd_official_yawmin") is not None]
        pen = 1.5 * max((r["cd_official_yawmin"] for r in sc), default=0.0)   # official penalty: 1.5 x worst observed
        summ.update(cd_official_cond=metrics.mean([r["cd_official"] for r in sc]),
                    cd_official_yawmin_cond=metrics.mean([r["cd_official_yawmin"] for r in sc]),
                    cd_official_yawmin_pen=(sum(r.get("cd_official_yawmin") if r.get("cd_official_yawmin") is not None else pen for r in out_rows) / n) if n else None)
    if image_summ:
        summ["image_sim"] = image_summ
    if "render" in spec.metrics:
        rok = sum(r.get("render") == "OK" for r in out_rows)
        summ.update(render_ok=rok, render_rate=rok / n if n else None,
                    render_static=sum(r.get("render") == "STATIC" for r in out_rows))
    (gen_dir / "summary.json").write_text(json.dumps(summ, indent=2))
    line = f"[score] {suite} {summ.get('model')}: exec {ok}/{n} = {100 * (summ['exec_rate'] or 0):.1f}%"
    if "geometry" in spec.metrics:
        line += f" | F@0.05 all {summ['f05_mean_all'] or 0:.3f} (ok {summ['f05_mean_scored'] or 0:.3f}) | CD {summ['chamfer_mean_scored'] or 0:.3f} | IoU {summ['iou_mean_scored'] or 0:.3f}"
    if "render" in spec.metrics:
        line += f" | render OK {summ['render_ok']}/{n}"
    if struct_jobs and summ.get("structural", {}).get("floating_part_rate_cond") is not None:
        st_ = summ["structural"]
        line += f" | floating {st_['floating_part_rate_cond']:.3f} | watertight {st_['is_watertight_cond']:.2f} | stable {st_['com_over_support_cond']:.2f}"
    for enc, s in image_summ.items():
        if "view_paired" in s:
            line += f" | {enc} paired {s['view_paired']['penalized'] or 0:.3f} (cond {s['view_paired']['conditional'] or 0:.3f})"
    print(line, flush=True)
    return summ


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("gen_dir", type=Path)
    ap.add_argument("--suite", required=True)
    ap.add_argument("--ref", choices=["executed", "canonical"], default="executed")
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    score_dir(a.gen_dir, a.suite, a.ref, a.workers)

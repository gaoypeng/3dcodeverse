"""Score-vs-complexity study over recorded runs.

Every finished run carries a judge verdict and (since the complexity vector
landed) an objective measure of *how much artifact was actually built*.  This
script joins the two so the harness can be asked the only question that matters
for the complexity wave: **when the artifact gets richer, does the score go up
or down, and what does a complexity point cost?**

    python bench/complexity_report.py bench/out/static_v2_flash [more roots...]
    python bench/complexity_report.py bench/out --recursive --csv /tmp/c.csv
    python bench/complexity_report.py bench/out/complexity_v3 --battery bench/prompts/complexity_v3.yaml

A "root" is a battery directory (``<battery>/runs/<slug>``), a runs directory,
or a single run directory — all three are detected.  A run is the round
``codeverse3d.addons.select`` picks (a record it cannot read: the last round).  The
complexity vector is read from that round's recorded measurement when present and
recomputed from its own GLB (``select.round_file``: ``artifacts/rNN/object.glb``, or an old
run's ``artifacts/object.glb`` for the round it rebuilt there) otherwise, so the whole historic
corpus is usable.  Rows without a judgment or without
geometry are skipped and counted.  There is no pass rate: a run is not passed or failed.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

for _p in (Path(__file__).resolve().parents[2] / "harness", Path(__file__).resolve().parents[1]):
    sys.path.insert(0, str(_p))  # this tree's codeverse3d (harness/) + the `bench` package (eval/)

from bench.stats import correlation  # noqa: E402
from codeverse3d.addons import select  # noqa: E402
from codeverse3d.contracts.run import RoundRecord, RunId, RunRecord  # noqa: E402
from codeverse3d.proc import read_json_or_none  # noqa: E402
from codeverse3d.record.record import find_run_dirs  # noqa: E402
from codeverse3d.spatial.complexity import (  # noqa: E402
    COMPLEXITY_WEIGHTS,
    ComplexityVector,
    band_of,
)
from codeverse3d.workspace import Workspace  # noqa: E402

CRITERIA = (
    "intent_fidelity",
    "structure_plausibility",
    "geometry_detail",
    "proportions_scale",
    "assembly_fit",
    "materials",
    "craftsmanship_no_artifacts",
)
#: the complexity axis ``materials`` is renamed ``n_materials`` in a row: the
#: static rubric has a *criterion* called ``materials`` and the two must not collide.
AXIS_COL = {"materials": "n_materials"}
AXES = tuple(AXIS_COL.get(a, a) for a in COMPLEXITY_WEIGHTS) + ("plan_parts",)
#: index buckets the scatter table is printed in
BUCKETS = (0.0, 0.30, 0.40, 0.50, 0.60, 0.70, 1.01)


# --------------------------------------------------------------------------- rows
class Row(dict):
    """One run: identity + complexity axes + judge criteria + cost.  A plain dict
    so the CSV/JSON writers stay trivial."""


_read_json = read_json_or_none


def _vector_for(run: Path, rnd: dict[str, Any] | None) -> ComplexityVector | None:
    """The round's recorded complexity vector, else one recomputed from the round's own GLB.

    The historic corpus predates the vector, so recomputing is the normal path
    for anything recorded before 2026-08-24 — that is what makes the whole
    corpus comparable.  The round is named by its index alone (a historic round may not
    validate); ``addons.select`` answers for its own files, never the last build's.
    """
    ws, one = Workspace(run), RoundRecord(index=int((rnd or {}).get("index") or 0), kind="")
    own = (((rnd or {}).get("measurement") or {}).get("extra") or {}).get("complexity")
    for block in (own, select.round_complexity_block(ws, one)):
        if isinstance(block, dict):
            try:
                return ComplexityVector.model_validate(block)
            except Exception:  # noqa: BLE001 - an older/foreign block must not stop the scan
                pass
    glb = select.round_file(ws, one)
    if glb is None:
        return None
    from codeverse3d.spatial.complexity import complexity_of_glb

    try:
        return complexity_of_glb(glb)
    except Exception:  # noqa: BLE001 - a corrupt GLB skips the row, never the scan
        return None


def _picked_round(run: Path, record: dict[str, Any], rec: RunRecord | None) -> dict[str, Any] | None:
    """The round ``addons/select`` picks, as its raw dict; the last round when the record
    will not load (the scan reads the historic corpus as-is)."""
    rounds = record.get("rounds") or []
    try:
        idx = select.summarise(run, record=rec).picked_round if rec is not None else None
    except Exception:  # noqa: BLE001 - one unreadable record must not stop the scan
        idx = None
    return next((r for r in rounds if r.get("index") == idx), rounds[-1] if rounds else None)


def row_for(run: Path, battery: str, slug: str | None = None) -> Row | None:
    record = _read_json(run / "record.json")
    if not record:
        return None
    try:  # validated once; the historic corpus is read as a raw dict where it will not load
        rec: RunRecord | None = RunRecord.model_validate(record)
    except Exception:  # noqa: BLE001
        rec = None
    rnd = _picked_round(run, record, rec)
    judgment = (rnd or {}).get("judgment") or {}
    scores = judgment.get("scores") or {}
    vec = _vector_for(run, rnd)
    if vec is None or not scores:
        return None
    plan_parts = len((record.get("plan") or {}).get("parts") or [])
    row = Row(
        battery=battery,
        slug=slug or run.name,
        track=(record.get("spec") or {}).get("track", ""),
        language=(record.get("spec") or {}).get("language", ""),
        tier=next((t for t in (record.get("spec") or {}).get("tags", []) if t in ("easy", "medium", "hard")), ""),
        status=record.get("status", ""),
        rounds=len(record.get("rounds") or []),
        plan_parts=plan_parts,
        parts_per_plan_part=round(vec.part_count / plan_parts, 3) if plan_parts else None,
        gate_errors=sum(len([f for f in (g.get("findings") or []) if f.get("severity") == "error"])
                        for g in (rnd or {}).get("gates") or []),
        overall=judgment.get("overall"),
        issues=len(judgment.get("issues") or []),
        cost_usd=round(float((record.get("total_usage") or {}).get("cost_usd") or 0.0), 4),
        minutes=round(rec.minutes, 2) if rec is not None and rec.minutes is not None else None,
        index=vec.index,
        band=vec.band,
    )
    row.update({AXIS_COL.get(a, a): getattr(vec, a) for a in COMPLEXITY_WEIGHTS})
    row.update({c: scores.get(c) for c in CRITERIA})
    return row


def iter_runs(root: Path) -> Iterable[tuple[str, str, Path]]:
    """``(battery, slug, run dir)`` for a battery dir, a runs dir or a single run.

    Discovery and naming go through the same machinery as the flywheel
    (``find_run_dirs`` + ``RunId``), so a nested battery (compare_backends
    ``cells/<id>/<arm>/run``, ab_plan ``arms/…/cells/…/run``) yields one distinct
    slug per run instead of dozens of rows all called ``run``."""
    if (root / "record.json").is_file():
        battery = root.parent.parent.name if root.parent.name == "runs" else root.parent.name
        yield battery, root.name, root
        return
    battery = root.name if (root / "runs").is_dir() else (root.parent.name or root.name)
    for d in find_run_dirs(root):
        yield battery, RunId(battery=battery, rel=d.relative_to(root).as_posix()).slug, d


def collect(roots: Sequence[Path], *, recursive: bool = False) -> tuple[list[Row], int]:
    """Rows for every run under ``roots`` (``--recursive``: also one level down)."""
    targets: list[Path] = []
    for r in roots:
        if recursive and r.is_dir() and not (r / "runs").is_dir() and not (r / "record.json").is_file():
            targets.extend(sorted(p for p in r.iterdir() if p.is_dir()))
        else:
            targets.append(r)
    rows: list[Row] = []
    skipped = 0
    for t in targets:
        if not t.is_dir():
            continue
        for battery, slug, run in iter_runs(t):
            row = row_for(run, battery, slug)
            if row is None:
                skipped += 1
            else:
                rows.append(row)
    return rows, skipped


# --------------------------------------------------------------------------- stats
def _pairs(rows: Sequence[Row], x: str, y: str) -> tuple[list[float], list[float]]:
    xs, ys = [], []
    for r in rows:
        a, b = r.get(x), r.get(y)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            xs.append(float(a))
            ys.append(float(b))
    return xs, ys


def correlations(rows: Sequence[Row], targets: Sequence[str], predictors: Sequence[str]) -> list[dict[str, Any]]:
    out = []
    for t in targets:
        entry: dict[str, Any] = {"target": t}
        for p in predictors:
            xs, ys = _pairs(rows, p, t)
            entry[p] = (correlation(xs, ys), correlation(xs, ys, ranked=True), len(xs))
        out.append(entry)
    return out


def _fmt(v: float | None, w: int = 6) -> str:
    return " " * (w - 1) + "-" if v is None else f"{v:>{w}.2f}"


def _mean(vals: Iterable[float | None]) -> float | None:
    v = [float(x) for x in vals if isinstance(x, (int, float))]
    return sum(v) / len(v) if v else None


# --------------------------------------------------------------------------- report
def scatter_table(rows: Sequence[Row]) -> str:
    """Score vs complexity, bucketed by index — the scatter as a text table."""
    lines = [
        "| complexity band | n | mean index | mean overall | detail | struct | fit | craft | $/run | min |",
        "|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|",
    ]
    for lo, hi in zip(BUCKETS, BUCKETS[1:], strict=False):
        b = [r for r in rows if isinstance(r.get("index"), (int, float)) and lo <= r["index"] < hi]
        if not b:
            continue
        lines.append(
            f"| {band_of((lo + hi) / 2)} {lo:.2f}–{min(hi, 1.0):.2f} | {len(b)} |"
            f"{_fmt(_mean(r['index'] for r in b))} |{_fmt(_mean(r['overall'] for r in b))} |"
            f"{_fmt(_mean(r['geometry_detail'] for r in b))} |"
            f"{_fmt(_mean(r['structure_plausibility'] for r in b))} |{_fmt(_mean(r['assembly_fit'] for r in b))} |"
            f"{_fmt(_mean(r['craftsmanship_no_artifacts'] for r in b))} |"
            f"{_fmt(_mean(r['cost_usd'] for r in b))} |{_fmt(_mean(r['minutes'] for r in b))} |"
        )
    return "\n".join(lines)


def correlation_table(rows: Sequence[Row], predictors: Sequence[str] = AXES) -> str:
    targets = ("overall", *CRITERIA, "cost_usd", "minutes")
    head = "| target | " + " | ".join(p[:12] for p in ("index", *predictors)) + " |"
    lines = [head, "|---" * (len(predictors) + 2) + "|"]
    for entry in correlations(rows, targets, ("index", *predictors)):
        cells = []
        for p in ("index", *predictors):
            r, _rho, _n = entry[p]
            cells.append(_fmt(r, 6))
        lines.append(f"| {entry['target']} |" + "|".join(cells) + "|")
    return "\n".join(lines)


def dollars_per_point(rows: Sequence[Row]) -> dict[str, Any]:
    """$ and minutes per complexity point (index × 100) actually delivered."""
    cost = sum(float(r.get("cost_usd") or 0.0) for r in rows)
    mins = sum(float(r.get("minutes") or 0.0) for r in rows)
    points = sum(100.0 * float(r.get("index") or 0.0) for r in rows)
    return {
        "runs": len(rows),
        "total_usd": round(cost, 3),
        "total_minutes": round(mins, 1),
        "complexity_points": round(points, 1),
        "usd_per_point": round(cost / points, 4) if points else None,
        "minutes_per_point": round(mins / points, 3) if points else None,
    }


def per_run_table(rows: Sequence[Row], limit: int = 60) -> str:
    lines = [
        "| run | parts | tris | sil | featdens | hollow | index | band | overall | detail | $ | min |",
        "|---|--:|--:|--:|--:|--:|--:|---|--:|--:|--:|--:|",
    ]
    for r in sorted(rows, key=lambda r: -(r.get("index") or 0))[:limit]:
        lines.append(
            f"| {r['battery']}/{r['slug']} | {r['part_count']} | {r['tri_count']} |"
            f" {r['silhouette']:.1f} | {r['feature_density']:.0f} | {r['hollowness']:.2f} |"
            f" {r['index']:.3f} | {r['band']} |{_fmt(r.get('overall'))} |{_fmt(r.get('geometry_detail'))} |"
            f" {r.get('cost_usd', 0):.2f} | {r.get('minutes') or 0:.0f} |"
        )
    return "\n".join(lines)


def battery_expectations(battery_path: Path, rows: Sequence[Row]) -> str:
    """Measured index vs the ``expected_complexity`` band each prompt declares."""
    import yaml

    data = yaml.safe_load(battery_path.read_text()) or {}
    want = {
        p["id"]: p.get("expected_complexity")
        for p in data.get("prompts", [])
        if isinstance(p, dict) and p.get("expected_complexity")
    }
    if not want:
        return ""
    by_slug = {r["slug"]: r for r in rows}
    lines = ["| prompt | expected | measured | in band? | overall |", "|---|---|--:|---|--:|"]
    for pid, band in want.items():
        r = by_slug.get(pid)
        lo, hi = float(band[0]), float(band[1])
        if r is None:
            lines.append(f"| {pid} | {lo:.2f}–{hi:.2f} | - | not run | - |")
            continue
        ok = "yes" if lo <= r["index"] <= hi else ("LOW" if r["index"] < lo else "HIGH")
        lines.append(f"| {pid} | {lo:.2f}–{hi:.2f} | {r['index']:.3f} | {ok} |{_fmt(r.get('overall'))} |")
    return "\n".join(lines)


def render_report(rows: Sequence[Row], skipped: int, battery: Path | None = None) -> str:
    out = [
        f"# complexity report — {len(rows)} runs ({skipped} skipped: no judgment or no geometry)",
        "",
        "## score vs complexity",
        "",
        scatter_table(rows),
        "",
        "## pearson r (row = judge target, column = complexity axis)",
        "",
        correlation_table(rows),
        "",
        "## cost of complexity",
        "",
        "```",
        json.dumps(dollars_per_point(rows), indent=2),
        "```",
    ]
    if battery is not None:
        table = battery_expectations(battery, rows)
        if table:
            out += ["", "## battery expectations", "", table]
    out += ["", "## per run (richest first)", "", per_run_table(rows)]
    return "\n".join(out)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("roots", nargs="+", type=Path, help="battery dir, runs dir or run dir")
    ap.add_argument("--recursive", action="store_true", help="treat each root as a directory OF batteries")
    ap.add_argument("--battery", type=Path, help="battery yaml with expected_complexity bands")
    ap.add_argument("--csv", type=Path, help="write the per-run rows as CSV")
    ap.add_argument("--json", dest="json_out", type=Path, help="write the per-run rows as JSON")
    ap.add_argument("--out", type=Path, help="write the markdown report here instead of stdout")
    args = ap.parse_args(argv)

    rows, skipped = collect(args.roots, recursive=args.recursive)
    if not rows:
        print("no runs with both a judgment and geometry found", file=sys.stderr)
        return 1
    if args.csv:
        fields = list(rows[0].keys())
        with args.csv.open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
    if args.json_out:
        args.json_out.write_text(json.dumps(rows, indent=2))
    report = render_report(rows, skipped, args.battery)
    if args.out:
        args.out.write_text(report)
        print(f"wrote {args.out}")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

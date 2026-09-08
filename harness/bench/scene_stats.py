"""Where a scene run failed: plan, assets, layout, assembly, gates or the judge.

The scene track is the least measured one in the harness — no recorded run existed when
this was written — and "the scene track does not work" is not actionable until the failure
is attributed to a LAYER.  The pipeline is
``plan -> assets -> env -> layouts -> assemble -> build -> gates -> render -> judge``,
and each layer leaves its own events and artefacts:

    python bench/scene_stats.py bench/out/scene_baseline

Reports, per battery: how many plans needed a re-ask, what the planner chose for each
asset (``threejs`` procedural module vs ``blender_glb`` hero built with bpy), how many
assets needed a repair pass, how many placements each zone produced, which gates failed
and how often, and what the judge complained about.  The point is the LAYER attribution:
a defect in the plan or in the assets follows any change to the assembly language.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # this tree's codeverse, not the editable install

from codeverse.flywheel.record import unique_files  # noqa: E402
from codeverse.proc import read_jsonl_lenient  # noqa: E402
from codeverse.spatial.node import browser_was_lost  # noqa: E402

#: pipeline stages in the order the scene track runs them
STAGES = ("plan", "skeleton", "assets", "env", "layouts", "assemble", "generate", "build")


def scene_records(root: Path) -> list[tuple[Path, dict]]:
    """``(run dir, record)`` for every scene run under ``root``, once per run on disk.

    The walk is ``flywheel.record.unique_files`` (symlinked cells collapsed, the
    ``_assets`` / ``_cand`` sub-workspaces skipped) — the one walker every scene survey
    shares (``scene_regate`` re-gates exactly the runs this counts)."""
    out = []
    for rec in unique_files(root, "record.json"):
        try:
            data = json.loads(rec.read_text())
        except (OSError, ValueError):
            continue
        if (data.get("spec") or {}).get("track") == "scene":
            out.append((rec.parent, data))
    return out


def runs(root: Path) -> list[tuple[str, dict, list[dict]]]:
    """``(prompt id, record, events)`` per scene run under ``root``, once per run."""
    out = []
    for run, data in scene_records(root):
        events = read_jsonl_lenient(run / "events.jsonl", dicts_only=True)
        out.append((str((data.get("spec") or {}).get("id", run.name)).split("/")[-1], data, events))
    # by id, then by path: two runs of one prompt (a battery with reps) share the id, and a
    # bare sorted() would then compare their record dicts and raise.  unique_files yields
    # paths sorted, and the sort is stable, so the key is the id alone.
    out.sort(key=lambda row: row[0])
    return out


def layers(rows: list[tuple[str, dict, list[dict]]]) -> dict:
    """One counter per layer, over every run."""
    plan = Counter()
    build = Counter()
    assets = Counter()
    asset_kind = Counter()
    tris: list[int] = []
    placements: list[int] = []
    gates = Counter()
    gate_errors = Counter()
    judge = Counter()
    scores: list[float] = []
    for _pid, rec, events in rows:
        for e in events:
            kind = e.get("event", "")
            if kind == "plan.invalid":
                plan["invalid"] += 1
            elif kind == "plan.restart":
                plan["restart"] += 1
            elif kind == "plan.done":
                plan["ok"] += 1
                plan["zones"] += int(e.get("n_zones") or 0)
            elif kind == "asset.generated":
                assets["ok" if e.get("ok") else "failed"] += 1
                assets[str(e.get("strategy") or "?")] += 1
                if e.get("tris"):
                    tris.append(int(e["tris"]))
            elif kind == "asset.judge_degraded":
                assets["judge_degraded"] += 1
            elif kind == "layout.done":
                placements.append(int(e.get("placements") or 0))
                if e.get("reasked"):
                    plan["layout_reask"] += 1
            elif kind == "build.done":
                build["ok" if e.get("ok") else "failed"] += 1
            elif kind == "build.harness_retry":
                build["harness_retry"] += 1
            elif kind == "repair.attempt":
                build["repair"] += 1
            elif kind == "render.failed":
                gates["render_failed"] += 1
        for a in (rec.get("plan") or {}).get("assets") or []:
            asset_kind[str(a.get("kind"))] += 1
        for rnd in rec.get("rounds") or []:
            round_lost = False
            for g in rnd.get("gates") or []:
                name = g.get("gate", "?")
                gates[name] += 1
                errors = [f for f in (g.get("findings") or []) if f.get("severity") == "error"]
                # A finding that names a browser which died under the driver measures the
                # BOX, not the scene (`node.BROWSER_LOST_MARKERS`).  Counting it as a gate
                # failure makes every battery run on a loaded machine look like a defective
                # generator: on scene_baseline (2026-09-05) three of six cells kept zero
                # renders and lost their judge that way.  Reported, never mixed in.
                lost = [f for f in errors if browser_was_lost(f.get("message", ""))]
                real = [f for f in errors if f not in lost]
                gate_errors[name] += len(real)
                if lost:
                    gates[f"{name}:LOST"] += len(lost)
                    round_lost = True
                if not g.get("passed"):
                    gates[f"{name}:FAILED" if real or not lost else f"{name}:BOX"] += 1
            if round_lost:
                gates["rounds_lost_to_the_box"] += 1
            j = rnd.get("judgment") or {}
            # `overall`, not `score`: `Judgment` has no `score` field (contracts/artifacts),
            # so this line never fired on a real record and the median was never printed.
            if j.get("overall") is not None:
                scores.append(float(j["overall"]))
            for issue in j.get("issues") or []:
                judge[f"{issue.get('severity', '?')}/{issue.get('kind', '?')}"] += 1
    return {"plan": plan, "build": build, "assets": assets, "asset_kind": asset_kind, "tris": tris,
            "placements": placements, "gates": gates, "gate_errors": gate_errors,
            "judge": judge, "scores": scores}


def report(root: Path) -> str:
    rows = runs(root)
    if not rows:
        return f"no scene runs under {root}"
    d = layers(rows)
    out = [f"# {len(rows)} scene run(s) under {root}", ""]
    scores = d["scores"]
    if scores:
        out.append(f"scored rounds: {len(scores)}, median {statistics.median(scores):.3f}, "
                   f"min {min(scores):.3f}, max {max(scores):.3f}")
    out += ["", "## plan", f"  {dict(d['plan'])}",
            "", "## assets",
            f"  outcomes: {dict(d['assets'])}",
            f"  planner chose: {dict(d['asset_kind'])}"]
    if d["tris"]:
        out.append(f"  triangles per asset: median {statistics.median(d['tris']):.0f}, max {max(d['tris'])}")
    if d["placements"]:
        out.append(f"\n## layout\n  placements per zone: median {statistics.median(d['placements']):.0f}, "
                   f"zones {len(d['placements'])}")
    # the layer between assembly and the gates: a build that failed in the harness and was
    # re-run, and a build that failed in the scene and went to a repair, are different
    # defects (tracks/repair.build_with_repair)
    out += ["", "## build", f"  {dict(d['build'])}"]
    out += ["", "## gates", "| gate | ran | failed | ERROR findings | lost to the box |", "|---|--:|--:|--:|--:|"]
    skip = (":FAILED", ":LOST", ":BOX")
    for name in sorted(n for n in d["gates"] if not n.endswith(skip) and n != "rounds_lost_to_the_box"):
        out.append(f"| {name} | {d['gates'][name]} | {d['gates'][f'{name}:FAILED']} | "
                   f"{d['gate_errors'][name]} | {d['gates'][f'{name}:LOST']} |")
    lost_rounds = d["gates"]["rounds_lost_to_the_box"]
    if lost_rounds:
        out.append(f"\n  {lost_rounds} round(s) lost their browser mid-render: those findings are the "
                   f"machine, not the scene, and are counted in the last column only.")
    if d["judge"]:
        out += ["", "## judge issues (severity/kind)"]
        out += [f"  {n:3}x {k}" for k, n in d["judge"].most_common(12)]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("root", type=Path, help="a battery directory (or any directory holding scene runs)")
    print(report(ap.parse_args(argv).root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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
from collections import Counter
from pathlib import Path

#: pipeline stages in the order the scene track runs them
STAGES = ("plan", "skeleton", "assets", "env", "layouts", "assemble", "generate", "build")


def runs(root: Path) -> list[tuple[str, dict, list[dict]]]:
    """``(prompt id, record, events)`` per scene run under ``root``, once per run."""
    out, seen = [], set()
    for rec in root.rglob("record.json"):
        if rec.resolve() in seen or "_assets" in rec.parts or "_cand" in rec.parts:
            continue
        seen.add(rec.resolve())
        try:
            data = json.loads(rec.read_text())
        except (OSError, ValueError):
            continue
        if (data.get("spec") or {}).get("track") != "scene":
            continue
        ev = rec.parent / "events.jsonl"
        events = []
        if ev.is_file():
            for line in ev.read_text().splitlines():
                try:
                    events.append(json.loads(line))
                except ValueError:
                    continue
        out.append((str((data.get("spec") or {}).get("id", rec.parent.name)).split("/")[-1], data, events))
    return sorted(out)


def layers(rows: list[tuple[str, dict, list[dict]]]) -> dict:
    """One counter per layer, over every run."""
    plan = Counter()
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
            elif kind == "render.failed":
                gates["render_failed"] += 1
        for a in (rec.get("plan") or {}).get("assets") or []:
            asset_kind[str(a.get("kind"))] += 1
        for rnd in rec.get("rounds") or []:
            for g in rnd.get("gates") or []:
                name = g.get("gate", "?")
                gates[name] += 1
                if not g.get("passed"):
                    gates[f"{name}:FAILED"] += 1
                for f in g.get("findings") or []:
                    if f.get("severity") == "error":
                        gate_errors[name] += 1
            j = rnd.get("judgment") or {}
            if j.get("score") is not None:
                scores.append(float(j["score"]))
            for issue in j.get("issues") or []:
                judge[f"{issue.get('severity', '?')}/{issue.get('kind', '?')}"] += 1
    return {"plan": plan, "assets": assets, "asset_kind": asset_kind, "tris": tris,
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
    out += ["", "## gates", "| gate | ran | failed | ERROR findings |", "|---|--:|--:|--:|"]
    for name in sorted(n for n in d["gates"] if not n.endswith(":FAILED")):
        out.append(f"| {name} | {d['gates'][name]} | {d['gates'][f'{name}:FAILED']} | {d['gate_errors'][name]} |")
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

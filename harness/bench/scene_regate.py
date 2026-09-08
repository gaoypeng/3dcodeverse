"""Re-run the scene placement gate over RECORDED workspaces, and diff it against what
the battery recorded.

A gate change is a claim about a corpus, and the corpus is already on disk: every scene
run keeps its `src/`, its `public/assets/` and the findings the gate raised at the time.
Re-probing those workspaces with the current tree costs a browser and no API spend, so
"does this rule remove false positives without hiding real ones?" is answerable before
any battery is re-run — the same way `bench/penetration_thresholds.py` answered the
connectivity threshold from 374 recorded rounds.

    python bench/scene_regate.py bench/out/scene_baseline

Per run it prints the ERROR findings the record holds, the ERROR findings the current
`scene_placement` raises on the same workspace, and the difference split into GONE and
NEW.  A rule that only ever produces GONE has not been shown to be safe — it has been
shown to be quiet; read the NEW column and the ones that stayed.

`scene_frames` is not re-run here: its findings need pixels, so re-evaluating it costs a
render per camera rather than one probe.  Use `runtime_js/render_scene.mjs` directly for
that (see D54's greenhouse measurement).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # this tree's codeverse

from bench.scene_stats import scene_records  # noqa: E402
from codeverse.config import get_settings  # noqa: E402
from codeverse.contracts.artifacts import Severity  # noqa: E402
from codeverse.spatial.scene_placement import placement_gate_safe  # noqa: E402

GATE = "scene_placement"


def scene_runs(root: Path) -> list[Path]:
    """Every recorded scene run under ``root`` that still has a workspace to probe."""
    return [run for run, _ in scene_records(root) if (run / "src" / "scene.js").is_file()]


def recorded_errors(record: dict) -> list[str]:
    """The ERROR messages `scene_placement` raised in the LAST recorded round."""
    for rnd in reversed(record.get("rounds") or []):
        for g in rnd.get("gates") or []:
            if g.get("gate") == GATE:
                return [str(f.get("message", "")) for f in (g.get("findings") or [])
                        if f.get("severity") == "error"]
    return []


def probe(ws: Path, *, timeout_s: float) -> dict | None:
    """Boot the workspace and return its census, or None when it will not boot."""
    driver = get_settings().runtime_js_dir() / "probe_scene.mjs"
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "probe.json"
        subprocess.run(
            ["node", str(driver), "--ws", str(ws), "--gpu", "off",
             "--timeout-ms", str(int(timeout_s * 1000)), "--out", str(out)],
            capture_output=True, text=True, timeout=timeout_s + 60, check=False,
        )
        if not out.is_file():
            return None
        payload = json.loads(out.read_text())
    return payload.get("census") if (payload.get("boot") or {}).get("ok") else None


def stage_layouts(run: Path) -> dict:
    """The per-zone layouts the run recorded.  `contract_findings` needs them: the
    instance-budget check ("zone X holds ~179 instances but its layout budgeted 443")
    has no basis without them, and leaving them out makes a REAL finding look like one
    the gate change removed — which is what happened the first time this ran."""
    f = run / "stages" / "layouts.json"
    if not f.is_file():
        return {}
    try:
        data = json.loads(f.read_text())
    except (OSError, ValueError):
        return {}
    # the stage file is the envelope {stage, inputs_hash, result}; the gate wants the
    # result, which is the zone-name -> layout mapping
    inner = data.get("result") if isinstance(data, dict) else None
    return inner if isinstance(inner, dict) else {}


def regate(run: Path, *, timeout_s: float) -> dict:
    record = json.loads((run / "record.json").read_text())
    before = recorded_errors(record)
    census = probe(run, timeout_s=timeout_s)
    if census is None:
        return {"run": run.name, "booted": False, "before": before, "after": None}
    # the round's own entry point, so this measures the GATE and not a copy of it
    # (it owns the per-kind cap and the indoor inference as well)
    report = placement_gate_safe(census, plan=record.get("plan") or {}, layouts=stage_layouts(run))
    after = [f.message for f in (report.findings if report else []) if f.severity == Severity.ERROR]
    return {"run": run.name, "booted": True, "before": before, "after": after}


def _head(msg: str) -> str:
    """The part of a finding that identifies it, so two wordings of one defect match."""
    return msg.split(" — ")[0].split(" (")[0].strip()


def report(root: Path, *, timeout_s: float) -> str:
    runs = scene_runs(root)
    if not runs:
        return f"no recorded scene workspaces under {root}"
    lines = [f"# {GATE} re-run over {len(runs)} recorded workspace(s) under {root}", ""]
    tot_before = tot_after = tot_gone = tot_new = 0
    rows = []
    for run in runs:
        r = regate(run, timeout_s=timeout_s)
        if not r["booted"]:
            rows.append(f"| {r['run']} | {len(r['before'])} | — | — | — | did not boot |")
            continue
        b, a = {_head(m) for m in r["before"]}, {_head(m) for m in r["after"]}
        gone, new = sorted(b - a), sorted(a - b)
        tot_before += len(r["before"])
        tot_after += len(r["after"])
        tot_gone += len(gone)
        tot_new += len(new)
        rows.append(f"| {r['run']} | {len(r['before'])} | {len(r['after'])} | {len(gone)} | {len(new)} | |")
        if gone or new:
            lines.append(f"### {r['run']}")
            lines += [f"  GONE  {m[:150]}" for m in gone]
            lines += [f"  NEW   {m[:150]}" for m in new]
            lines.append("")
    head = ["| run | recorded ERRORs | now | gone | new | note |", "|---|--:|--:|--:|--:|---|", *rows,
            "", f"**totals** recorded {tot_before}, now {tot_after}, gone {tot_gone}, new {tot_new}", ""]
    return "\n".join([lines[0], "", *head, *lines[1:]])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("root", type=Path, help="a battery directory holding scene runs")
    ap.add_argument("--timeout-s", type=float, default=300.0, help="per-workspace probe budget")
    ap.add_argument("--out", type=Path, help="write the report here as well as to stdout")
    ns = ap.parse_args(argv)
    text = report(ns.root, timeout_s=ns.timeout_s)
    print(text)
    if ns.out:
        ns.out.write_text(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

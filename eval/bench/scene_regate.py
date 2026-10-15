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
NEW — per run by message, and over the corpus PER KIND (typed `data.kind`: floating,
sunken, unsupported, cantilevered, interpenetration, scale, free_exempt), which is the
column a threshold change is read in.  A rule that only ever produces GONE has not been
shown to be safe — it has been shown to be quiet; read the NEW column and the ones that
stayed.

A scene_blender record (entry `src/scene.py`) is probed on the census GLB its last build
kept (`artifacts/census.glb`, through the same driver and placement code, no settle — D3)
with the bpy facts merged as the build merged them; a run whose GLB is gone is rebuilt by
the runtime's own probe on a COPY of the workspace, since that probe writes its artifacts
and a recorded run is read-only.

`scene_frames` is not re-run here: its findings need pixels, so re-evaluating it costs a
render per camera rather than one probe.  Use `runtime_js/render_scene.mjs` directly for
that (see D55's greenhouse measurement).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

for _p in (Path(__file__).resolve().parents[2] / "harness", Path(__file__).resolve().parents[1]):
    sys.path.insert(0, str(_p))  # this tree's codeverse3d (harness/) + the `bench` package (eval/)

from bench._records import records  # noqa: E402
from codeverse3d.contracts.artifacts import GateReport, Severity  # noqa: E402
from codeverse3d.contracts.common import ENTRY_FILE, Language  # noqa: E402
from codeverse3d.languages.base import get_runtime  # noqa: E402
from codeverse3d.proc import read_json_or_none  # noqa: E402
from codeverse3d.spatial.probes import offline_report  # noqa: E402
from codeverse3d.spatial.render_scene import (  # noqa: E402
    SceneRenderError,
    probe_env_args,
    run_scene_script,
)
from codeverse3d.spatial.scene_placement import placement_gate  # noqa: E402
from codeverse3d.workspace import Workspace  # noqa: E402

GATE = "scene_placement"
#: the kinds the corpus table reports, in the order the gate grades them (typed ``data.kind``)
KINDS = ("floating", "sunken", "unsupported", "cantilevered", "interpenetration", "scale", "free_exempt")
#: what a copy of a scene_blender workspace needs to build again — the agent's sources, the hero
#: GLBs, the plan, the stage records the gate reads — never the round's artifacts, renders or git
WORKSPACE_PARTS = ("src", "public", "plan.json", "spec.json", "stages")


def run_language(record: dict[str, Any]) -> Language:
    """The record's scene language (``spec.language``); a record from before scene_blender is three.js."""
    raw = (record.get("spec") or {}).get("language")
    return Language(str(raw)) if raw else Language.SCENE_THREEJS


def scene_runs(root: Path) -> list[Path]:
    """Every recorded scene run under ``root`` that still has its language's entry file to probe."""
    return [rec.parent for rec, data in records(root, track="scene")
            if (rec.parent / ENTRY_FILE[run_language(data)]).is_file()]


def _last_placement_findings(record: dict[str, Any]) -> list[dict[str, Any]]:
    """The `scene_placement` findings of the LAST recorded round (as the record wrote them)."""
    for rnd in reversed(record.get("rounds") or []):
        for g in rnd.get("gates") or []:
            if g.get("gate") == GATE:
                return [f for f in (g.get("findings") or []) if isinstance(f, dict)]
    return []


def recorded_errors(record: dict[str, Any]) -> list[str]:
    """The ERROR messages `scene_placement` raised in the LAST recorded round."""
    return [str(f.get("message", "")) for f in _last_placement_findings(record) if f.get("severity") == "error"]


def recorded_kinds(record: dict[str, Any]) -> Counter[str]:
    """The last recorded round's ERRORs by typed ``data.kind`` (a finding without one is ``untyped``)."""
    return Counter(str((f.get("data") or {}).get("kind") or "untyped")
                   for f in _last_placement_findings(record) if f.get("severity") == "error")


def report_kinds(report: GateReport | None) -> Counter[str]:
    """A fresh report's ERRORs by typed ``data.kind``."""
    return Counter(str(f.data.get("kind") or "untyped") for f in (report.findings if report else [])
                   if f.severity == Severity.ERROR)


def _driver_summary(args: list[str], *, timeout_s: float) -> dict[str, Any] | None:
    """One ``probe_scene.mjs`` boot (the round's driver: same node, same flags), no file written into
    the recorded run; None when the driver itself could not run."""
    try:
        res = run_scene_script("probe_scene.mjs", [*args, "--gpu", "off", "--timeout-ms", str(int(timeout_s * 1000))],
                               timeout_s=timeout_s + 60)
    except SceneRenderError:
        return None
    return res.summary


def _probe_copy(ws: Path, language: Language, *, timeout_s: float) -> dict[str, Any] | None:
    """The runtime's own probe on a copy of the workspace: it rebuilds (Blender) and writes its
    artifacts, and a recorded run is read-only reference material."""
    with tempfile.TemporaryDirectory(prefix="c3d-regate-") as tmp:
        copy = Path(tmp) / ws.name
        copy.mkdir()
        for part in WORKSPACE_PARTS:
            src = ws / part
            if src.is_dir():
                shutil.copytree(src, copy / part)
            elif src.is_file():
                shutil.copy2(src, copy / part)
        res = get_runtime(language).probe(Workspace(copy).create(), timeout_s=timeout_s)
        return res.census if res.ok and res.census else None


def probe(ws: Path, *, timeout_s: float, language: Language = Language.SCENE_THREEJS) -> dict[str, Any] | None:
    """Boot the workspace and return its census, or None when it will not boot.  A three.js
    scene runs as the round's probe does (``run_scene_script`` + ``probe_env_args``: the same
    node, settle / camera-repair / exposure policy); a Blender scene is probed on the census
    GLB its last build kept, else rebuilt on a copy.  Nothing is written into the recorded run."""
    ws = ws.resolve()   # the node driver runs under runtime_js/: a battery root given relative would not be found
    if language is Language.SCENE_BLENDER:
        glb = ws / "artifacts" / "census.glb"
        if not glb.is_file():
            return _probe_copy(ws, language, timeout_s=timeout_s)
        summary = _driver_summary(["--ws", str(ws), "--glb", str(glb), "--no-settle"], timeout_s=timeout_s)
        if summary is None:
            return None
        _, census = offline_report(summary, read_json_or_none(ws / "artifacts" / "bpy_census.json") or {})
        if census:
            census["language"] = language.value   # the hints speak bpy, as the build's census does
        return census or None
    summary = _driver_summary(["--ws", str(ws), *probe_env_args()], timeout_s=timeout_s)
    if summary is None:
        return None
    return summary.get("census") if (summary.get("boot") or {}).get("ok") else None


def regate(run: Path, *, timeout_s: float) -> dict[str, Any]:
    record = json.loads((run / "record.json").read_text())
    language = run_language(record)
    before = recorded_errors(record)
    before_kinds = recorded_kinds(record)
    census = probe(run, timeout_s=timeout_s, language=language)
    if census is None:
        return {"run": run.name, "language": language.value, "booted": False, "before": before,
                "before_kinds": before_kinds, "after": None, "after_kinds": None}
    # the round's own entry point (D82), so this measures the GATE and not a copy of it: it
    # reads the zone layouts and the assets the asset stage could not build from stages/ —
    # a first version of this script passed the layouts envelope unopened and a REAL density
    # finding read as one the gate change had removed
    report = placement_gate(Workspace(run), census, record.get("plan") or {})
    after = [f.message for f in (report.findings if report else []) if f.severity == Severity.ERROR]
    return {"run": run.name, "language": language.value, "booted": True, "before": before,
            "before_kinds": before_kinds, "after": after, "after_kinds": report_kinds(report)}


def kind_diff(before: Counter[str], after: Counter[str]) -> dict[str, tuple[int, int, int, int]]:
    """Per kind ``(recorded, now, gone, new)`` for one run: counts, not identities, so a finding
    that changed its wording stays one finding."""
    out: dict[str, tuple[int, int, int, int]] = {}
    for kind in sorted(set(KINDS) | set(before) | set(after), key=lambda k: (k not in KINDS, KINDS.index(k) if k in KINDS else 0, k)):
        b, a = before.get(kind, 0), after.get(kind, 0)
        if b or a:
            out[kind] = (b, a, max(b - a, 0), max(a - b, 0))
    return out


def _head(msg: str) -> str:
    """The part of a finding that identifies it, so two wordings of one defect match."""
    return msg.split(" — ")[0].split(" (")[0].strip()


def report(root: Path, *, timeout_s: float) -> str:
    runs = scene_runs(root)
    if not runs:
        return f"no recorded scene workspaces under {root}"
    lines = [f"# {GATE} re-run over {len(runs)} recorded workspace(s) under {root}", ""]
    tot_before = tot_after = tot_gone = tot_new = 0
    by_kind: dict[str, list[int]] = {}
    rows = []
    for run in runs:
        r = regate(run, timeout_s=timeout_s)
        if not r["booted"]:
            rows.append(f"| {r['run']} | {r['language']} | {len(r['before'])} | — | — | — | did not boot |")
            continue
        b, a = {_head(m) for m in r["before"]}, {_head(m) for m in r["after"]}
        gone, new = sorted(b - a), sorted(a - b)
        tot_before += len(r["before"])
        tot_after += len(r["after"])
        tot_gone += len(gone)
        tot_new += len(new)
        for kind, counts in kind_diff(r["before_kinds"], r["after_kinds"]).items():
            acc = by_kind.setdefault(kind, [0, 0, 0, 0])
            for i, v in enumerate(counts):
                acc[i] += v
        rows.append(f"| {r['run']} | {r['language']} | {len(r['before'])} | {len(r['after'])} | {len(gone)} | {len(new)} | |")
        if gone or new:
            lines.append(f"### {r['run']}")
            lines += [f"  GONE  {m[:150]}" for m in gone]
            lines += [f"  NEW   {m[:150]}" for m in new]
            lines.append("")
    head = ["| run | language | recorded ERRORs | now | gone | new | note |", "|---|---|--:|--:|--:|--:|---|", *rows,
            "", f"**totals** recorded {tot_before}, now {tot_after}, gone {tot_gone}, new {tot_new}", ""]
    if by_kind:
        head += ["## ERRORs by kind (booted runs; `data.kind`)", "| kind | recorded | now | gone | new |", "|---|--:|--:|--:|--:|"]
        head += [f"| {kind} | {b} | {a} | {g} | {n} |" for kind, (b, a, g, n) in by_kind.items()]
        head.append("")
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

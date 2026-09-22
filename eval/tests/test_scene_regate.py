"""``bench/scene_regate.py``: re-running a gate over recorded workspaces.

The parts that decide whether the diff means anything, pinned without a browser.
Both were wrong on the first run of this script over `bench/out/scene_baseline`:
the layouts envelope was passed through unopened, so `contract_findings` lost its
basis and coastal_village's REAL "zone VillageQuay holds ~179 instances but its
layout budgeted 443" appeared in the GONE column as though the gate change had
removed it.
"""

from __future__ import annotations

import json
from pathlib import Path

from bench.scene_regate import _head, recorded_errors, regate, scene_runs


def _run(root: Path, name: str, *, track: str = "scene", with_src: bool = True) -> Path:
    d = root / name / "run"
    (d / "src").mkdir(parents=True, exist_ok=True)
    if with_src:
        (d / "src" / "scene.js").write_text("export async function createScene() {}")
    (d / "record.json").write_text(json.dumps({"spec": {"track": track, "id": name}, "rounds": []}))
    return d


def test_regate_reads_the_stage_records_the_round_gate_reads(tmp_path: Path, monkeypatch) -> None:
    """The layouts envelope is opened (coastal_village's real density finding came back) and an
    asset the asset stage never built is not "missing": re-gating calls the round's own entry
    point, which reads both from ``stages/``."""
    import bench.scene_regate as sr

    run = _run(tmp_path, "a")
    plan = {"zones": [{"name": "VillageQuay", "contents": ["Bollard", "BronzeCenser"]}], "assets": []}
    (run / "record.json").write_text(json.dumps({"spec": {"track": "scene"}, "plan": plan, "rounds": []}))
    (run / "stages").mkdir()
    (run / "stages" / "layouts.json").write_text(json.dumps({"stage": "layouts", "inputs_hash": "abc", "result": {
        "VillageQuay": {"placements": [{"asset": "Bollard", "count": 43}], "ground_cover": 400}}}))
    (run / "stages" / "assets.json").write_text(json.dumps({"stage": "assets", "inputs_hash": "abc", "result": {
        "BronzeCenser": {"name": "BronzeCenser", "ok": False}}}))
    census = {"fog": {"type": "Fog"}, "background": "#aabbcc",
              "groups": [{"name": "VillageQuay", "instances": 179}],
              "placement": {"assets": [{"name": "Bollard", "zone": "VillageQuay", "ground_gap_m": 0.0,
                                        "support": "Ground"}], "pairs": []}}
    monkeypatch.setattr(sr, "probe", lambda ws, timeout_s: census)
    after = regate(run, timeout_s=1.0)["after"]
    assert any("holds ~179 instances but its layout budgeted 443" in m for m in after), after
    assert not any("BronzeCenser" in m for m in after), after


def test_only_scene_runs_with_a_workspace_are_re_gated(tmp_path: Path) -> None:
    _run(tmp_path, "scene_ok")
    _run(tmp_path, "an_object", track="static_object")
    _run(tmp_path, "scene_no_src", with_src=False)
    names = {p.parent.name for p in scene_runs(tmp_path)}
    assert names == {"scene_ok"}, "a run whose src/ is gone cannot be re-probed"


def test_the_errors_read_back_are_the_last_round_s(tmp_path: Path) -> None:
    run = _run(tmp_path, "c")
    record = {"rounds": [
        {"index": 0, "gates": [{"gate": "scene_placement", "passed": False, "findings": [
            {"severity": "error", "message": "round 0 defect"}]}]},
        {"index": 1, "gates": [{"gate": "scene_placement", "passed": False, "findings": [
            {"severity": "error", "message": "round 1 defect"},
            {"severity": "warn", "message": "a warning is not an error"}]}]},
    ]}
    (run / "record.json").write_text(json.dumps(record))
    assert recorded_errors(record) == ["round 1 defect"]


def test_two_wordings_of_one_defect_match() -> None:
    """The diff is on the identifying head, so a reworded hint or a changed depth does
    not read as one finding gone and another arrived."""
    a = "BlackPine_5 is sunken 3.46 m into AtmosphereHaze — raise it by 3.46 m"
    b = "BlackPine_5 is sunken 3.46 m into AtmosphereHaze (8 columns)"
    assert _head(a) == _head(b)

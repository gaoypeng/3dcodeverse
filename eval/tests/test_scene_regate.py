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

from bench.scene_regate import _head, recorded_errors, scene_runs, stage_layouts


def _run(root: Path, name: str, *, track: str = "scene", with_src: bool = True) -> Path:
    d = root / name / "run"
    (d / "src").mkdir(parents=True, exist_ok=True)
    if with_src:
        (d / "src" / "scene.js").write_text("export async function createScene() {}")
    (d / "record.json").write_text(json.dumps({"spec": {"track": track, "id": name}, "rounds": []}))
    return d


def test_the_layouts_envelope_is_opened(tmp_path: Path) -> None:
    """`stages/layouts.json` is `{stage, inputs_hash, result}`; the gate wants `result`,
    which is the zone-name -> layout mapping."""
    run = _run(tmp_path, "a")
    (run / "stages").mkdir()
    (run / "stages" / "layouts.json").write_text(json.dumps({
        "stage": "layouts", "inputs_hash": "abc",
        "result": {"VillageQuay": {"zone": "VillageQuay", "placements": [1, 2]}},
    }))
    assert stage_layouts(run) == {"VillageQuay": {"zone": "VillageQuay", "placements": [1, 2]}}


def test_a_run_without_layouts_yields_an_empty_mapping(tmp_path: Path) -> None:
    assert stage_layouts(_run(tmp_path, "b")) == {}


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

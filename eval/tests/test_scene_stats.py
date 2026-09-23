"""``bench/scene_stats.py``: a gate finding that names a dead browser is the machine, not a defect (2026-09-05)."""

from __future__ import annotations

import json
from pathlib import Path

from bench.scene_stats import layers, report, runs

DETACHED = "driver: Attempted to use detached Frame '10276B428E350BFA074A02AF37D52E6C'."


def _run(root: Path, name: str, gates: list[dict]) -> None:
    d = root / name / "run"
    d.mkdir(parents=True, exist_ok=True)
    (d / "record.json").write_text(json.dumps({
        "spec": {"track": "scene", "id": name},
        "plan": {"assets": []},
        "rounds": [{"index": 0, "gates": gates, "judgment": {}}],
    }))


def _gate(name: str, passed: bool, messages: list[str]) -> dict:
    return {"gate": name, "passed": passed,
            "findings": [{"severity": "error", "message": m} for m in messages]}


def test_a_dead_browser_is_counted_apart_from_a_defect(tmp_path: Path) -> None:
    _run(tmp_path, "lost", [
        _gate("render_console", False, [DETACHED]),
        _gate("scene_placement", False, ["Crate is sunken 0.9 m into Wall"]),
    ])
    # `scene_frames` with no frames to look at is the same browser loss reported twice
    _run(tmp_path, "both", [_gate("render_console", False, [DETACHED]),
                            _gate("scene_frames", False, ["render produced no result: " + DETACHED])])
    d = layers(runs(tmp_path))
    g = d["gates"]
    # the browser loss lands in its own column and does NOT inflate the gate's failures
    assert g["render_console:LOST"] == 2
    assert g["render_console:FAILED"] == 0
    assert d["gate_errors"]["render_console"] == 0
    assert g["scene_frames:FAILED"] == 0 and g["scene_frames:LOST"] == 1
    # the real defect in the same round is untouched
    assert g["scene_placement:FAILED"] == 1 and sum(d["gate_errors"].values()) == d["gate_errors"]["scene_placement"] == 1
    assert g["rounds_lost_to_the_box"] == 2


def test_the_median_score_is_read_off_the_real_Judgment_shape(tmp_path: Path) -> None:
    """The fixture is built from `Judgment` itself: the script once read `score`, which it never has."""
    from codeverse3d.contracts.artifacts import Judgment

    def judged(name: str, overall: float) -> None:
        d = tmp_path / name / "run"
        d.mkdir(parents=True, exist_ok=True)
        j = Judgment(rubric="scene_v1", scores={"a": overall}, overall=overall, passed=overall > 0.5)
        (d / "record.json").write_text(json.dumps({
            "spec": {"track": "scene", "id": name},
            "plan": {"assets": []},
            "rounds": [{"index": 0, "gates": [], "judgment": j.model_dump(mode="json")}],
        }))

    judged("low", 0.30)
    judged("high", 0.50)
    d = layers(runs(tmp_path))
    assert d["scores"] == [0.30, 0.50] or d["scores"] == [0.50, 0.30]
    text = report(tmp_path)
    assert "scored rounds: 2" in text and "median 0.400" in text


def test_the_build_layer_is_attributed(tmp_path: Path) -> None:
    """Between assembly and the gates sits the build: a harness retry and an agent repair
    are different defects (`tracks/repair.build_with_repair`), and a survey that skips the
    layer reads both as "the gates failed"."""
    d = tmp_path / "scn_cliff" / "run"
    d.mkdir(parents=True)
    (d / "record.json").write_text(json.dumps({"spec": {"track": "scene", "id": "scn_cliff"},
                                               "plan": {"assets": []}, "rounds": []}))
    (d / "events.jsonl").write_text("\n".join(json.dumps(e) for e in [
        {"event": "build.done", "ok": False},
        {"event": "build.harness_retry", "attempt": 1},
        {"event": "build.done", "ok": False, "harness_retry": 1},
        {"event": "repair.attempt", "attempt": 1},
        {"event": "build.done", "ok": True, "attempt": 1},
    ]))
    b = layers(runs(tmp_path))["build"]
    assert dict(b) == {"failed": 2, "harness_retry": 1, "repair": 1, "ok": 1}
    assert "## build" in report(tmp_path)

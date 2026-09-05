"""``bench/scene_stats.py``: a gate finding that names a dead browser is the machine.

Measured on `bench/out/scene_baseline` (2026-09-05): three of six scene cells built,
passed every gate that does not need pixels, and then kept ZERO renders because Chrome
reaped the tab under a box at load 93 with swap full.  Each one left a `render_console`
ERROR reading "driver: Attempted to use detached Frame '<id>'".  Counted as a gate
failure, that reads as a defective generator; the layer attribution the scene battery
exists to produce is then wrong for half its cells.
"""

from __future__ import annotations

import json
from pathlib import Path

from bench.scene_stats import layers, runs

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
    d = layers(runs(tmp_path))
    g = d["gates"]
    # the browser loss lands in its own column and does NOT inflate the gate's failures
    assert g["render_console:LOST"] == 1
    assert g["render_console:FAILED"] == 0
    assert d["gate_errors"]["render_console"] == 0
    # the real defect in the same round is untouched
    assert g["scene_placement:FAILED"] == 1 and d["gate_errors"]["scene_placement"] == 1
    assert g["rounds_lost_to_the_box"] == 1


def test_a_round_that_kept_its_browser_reports_no_loss(tmp_path: Path) -> None:
    _run(tmp_path, "clean", [_gate("render_console", True, []),
                             _gate("scene_frames", False, ["frame too dark: mean luminance 0.13"])])
    d = layers(runs(tmp_path))
    assert d["gates"]["rounds_lost_to_the_box"] == 0
    assert d["gates"]["scene_frames:FAILED"] == 1 and d["gate_errors"]["scene_frames"] == 1


def test_a_gate_that_failed_ONLY_on_the_browser_is_not_a_failure(tmp_path: Path) -> None:
    """`scene_frames` fails when it has no frames to look at.  With the renders lost
    that is the same event reported twice, not a second defect."""
    _run(tmp_path, "both", [_gate("render_console", False, [DETACHED]),
                            _gate("scene_frames", False, ["render produced no result: " + DETACHED])])
    d = layers(runs(tmp_path))
    assert d["gates"]["scene_frames:FAILED"] == 0 and d["gates"]["scene_frames:LOST"] == 1
    assert sum(d["gate_errors"].values()) == 0

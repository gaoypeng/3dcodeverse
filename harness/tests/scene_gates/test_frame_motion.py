"""Measured inter-frame motion (``spatial.frame_motion``) and its ``scene_frames`` finding.

Why this exists: on the scenes_v1 battery the judge answered `nothing_moves` = true on
four rounds whose water and foliage were in fact animating (0.7–4.7 % of pixels changed) —
the two times reach it as tiles in different montage images.  The harness measures the
change instead, so the numbers here are the thing the judge is told to trust.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image

from codeverse.contracts.artifacts import Severity
from codeverse.spatial.frame_metrics import frame_findings, stored_motion
from codeverse.spatial.frame_motion import (
    MOVING_FRAC,
    motion_from_dir,
    motion_rows,
    motion_summary_text,
    scene_moves,
)

W, H = 40, 30


def _png(path: Path, *, changed: int = 0, delta: int = 40, base: int = 90) -> Path:
    """Flat grey frame; the first ``changed`` pixels are shifted by ``delta``."""
    a = np.full((H, W, 3), base, dtype=np.uint8)
    flat = a.reshape(-1, 3)
    flat[:changed] = min(255, base + delta)
    Image.fromarray(a).save(path)
    return path


def _metrics(tmp: Path, *pairs) -> dict:
    views = []
    for name, kind, changed, delta in pairs:
        for t in (0.0, 1.5):
            f = f"{name}_t{t:g}.png"
            _png(tmp / f, changed=0 if t == 0.0 else changed, delta=delta)
            views.append({"name": name, "kind": kind, "path": f, "time_s": t})
    m = {"views": views, "camera_checks": [], "census": {"ground_y": 0.0}}
    (tmp / "metrics.json").write_text(json.dumps(m))
    return m


def test_moving_and_static_cameras_are_measured(tmp_path: Path) -> None:
    n = W * H
    m = _metrics(tmp_path, ("Establishing", "authored", int(0.05 * n), 40),
                 ("Frozen", "authored", 0, 0), ("overview_top", "orbit", int(0.5 * n), 40))
    rows = motion_rows(m, tmp_path)
    by = {r.name: r for r in rows}
    assert [r.name for r in rows] == ["Establishing", "Frozen", "overview_top"]  # authored first
    assert by["Establishing"].moving and by["Establishing"].changed_frac >= MOVING_FRAC
    assert not by["Frozen"].moving and by["Frozen"].changed_frac == 0.0
    assert by["overview_top"].kind == "orbit" and not by["overview_top"].authored
    assert scene_moves(rows) is True
    text = motion_summary_text(rows)
    assert "MEASURED MOTION" in text and "Establishing" in text and "overview_top" not in text
    assert "`nothing_moves` is FALSE" in text


def test_a_frozen_scene_is_reported_as_frozen(tmp_path: Path) -> None:
    m = _metrics(tmp_path, ("Establishing", "authored", 0, 0), ("Detail", "authored", 2, 3))
    rows = motion_rows(m, tmp_path)
    assert scene_moves(rows) is False
    assert "the scene is frozen" in motion_summary_text(rows)


def test_a_tiny_but_strong_change_still_counts_as_motion(tmp_path: Path) -> None:
    # a lantern flickering on: few pixels, huge delta — motion, not a rounding error
    m = _metrics(tmp_path, ("Establishing", "authored", 3, 200))
    rows = motion_rows(m, tmp_path)
    assert rows[0].changed_frac < MOVING_FRAC and rows[0].moving and scene_moves(rows) is True


def test_unmeasurable_views_are_skipped_not_called_static(tmp_path: Path) -> None:
    m = {"views": [{"name": "A", "kind": "authored", "path": "missing_t0.png", "time_s": 0.0},
                   {"name": "A", "kind": "authored", "path": "missing_t1.png", "time_s": 1.5},
                   {"name": "B", "kind": "authored", "path": "b.png", "time_s": 0.0}]}
    assert motion_rows(m, tmp_path) == []          # unreadable + single-time → no rows
    assert scene_moves([]) is None                  # "not measured", never "static"
    assert motion_summary_text([]) == ""
    assert motion_from_dir(tmp_path / "nowhere") == []


def test_frame_gate_reports_motion_as_info_and_freeze_as_error(tmp_path: Path) -> None:
    moving = _metrics(tmp_path, ("Establishing", "authored", int(0.05 * W * H), 40))
    moving["motion"] = [r.as_dict() for r in motion_rows(moving, tmp_path)]
    rep = frame_findings(moving)
    assert rep.passed and [f.data["kind"] for f in rep.findings] == ["motion"]
    assert stored_motion(moving)[0].moving

    frozen = dict(moving)
    frozen["motion"] = [{**frozen["motion"][0], "changed_frac": 0.0, "strong_frac": 0.0, "max_delta": 1}]
    rep2 = frame_findings(frozen)
    f = next(f for f in rep2.findings if f.data["kind"] == "no_motion")
    assert f.severity is Severity.ERROR and not rep2.passed
    assert "amplitude" in f.fix_hint and "0.10" in f.fix_hint


def test_stored_motion_ignores_junk() -> None:
    assert stored_motion({"motion": [{"name": "x"}, "nope", {"name": "y", "t0": "bad"}]})[0].name == "x"


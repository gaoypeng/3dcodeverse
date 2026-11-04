"""The proportion guard: a picture never overrides the brief's dimensions."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from codeverse3d.reference import (
    ASPECT_TOL,
    conflict_note,
    dimension_conflict,
)
from tests.reference.conftest import make_spec


def _box(path: Path, w: int, h: int) -> Path:
    im = Image.new("RGB", (400, 400), (245, 245, 245))
    x0, y0 = (400 - w) // 2, (400 - h) // 2
    ImageDraw.Draw(im).rectangle([x0, y0, x0 + w, y0 + h], fill=(40, 30, 20))
    im.save(path)
    return path


CHAIR = {"width": 0.45, "depth": 0.50, "height": 0.9}


def test_only_a_photo_beyond_the_tolerance_is_a_conflict(tmp_path: Path):
    """A photo beyond ASPECT_TOL of the brief is a conflict; an unreadable image never is."""
    from codeverse3d.contracts.spec import Constraints

    spec = make_spec(constraints=Constraints(dimensions_m=CHAIR))
    info = dimension_conflict(spec, _box(tmp_path / "p.png", 128, 200))
    assert not info["conflict"] and info["relative_error"] < ASPECT_TOL and conflict_note(info) == ""

    info = dimension_conflict(make_spec(), _box(tmp_path / "crank.png", 210, 300))
    assert info["conflict"] and info["relative_error"] > 0.4
    note = conflict_note(info)
    assert "BRIEF's dimensions are correct" in note

    info = dimension_conflict(make_spec(), tmp_path / "missing.png")
    assert info["conflict"] is False and info["error"]

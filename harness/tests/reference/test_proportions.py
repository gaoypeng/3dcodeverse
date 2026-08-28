"""The proportion guard: a picture never overrides the brief's dimensions."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from codeverse.reference import (
    ASPECT_TOL,
    conflict_note,
    dimension_conflict,
    expected_aspect_range,
    expected_front_aspect,
)
from codeverse.spatial.silhouette import silhouette_aspect
from tests.reference.conftest import make_spec


def _box(path: Path, w: int, h: int) -> Path:
    im = Image.new("RGB", (400, 400), (245, 245, 245))
    x0, y0 = (400 - w) // 2, (400 - h) // 2
    ImageDraw.Draw(im).rectangle([x0, y0, x0 + w, y0 + h], fill=(40, 30, 20))
    im.save(path)
    return path


def test_silhouette_aspect(tmp_path: Path):
    assert silhouette_aspect(_box(tmp_path / "a.png", 100, 200)) == pytest.approx(0.5, abs=0.02)
    assert silhouette_aspect(_box(tmp_path / "b.png", 200, 100)) == pytest.approx(2.0, abs=0.08)


def test_expected_aspect_is_a_band_over_every_horizontal_extent():
    """A brief never says which face a photo shows, so the expectation is a range."""
    from codeverse.contracts.spec import Constraints

    assert expected_aspect_range(make_spec()) == (0.14 / 0.30, 0.14 / 0.30)   # width == depth
    chair = make_spec(constraints=Constraints(dimensions_m={"width": 0.45, "depth": 0.50, "height": 0.9}))
    assert expected_aspect_range(chair) == pytest.approx((0.5, 0.5 / 0.9))
    assert expected_front_aspect(chair) == pytest.approx(0.5278, abs=1e-3)
    assert expected_aspect_range(make_spec(constraints=Constraints())) is None
    # height alone says nothing about proportions
    assert expected_aspect_range(make_spec(constraints=Constraints(dimensions_m={"height": 0.65}))) is None


def test_a_slightly_wide_photo_inside_the_band_is_not_a_conflict(tmp_path: Path):
    """The measured chair case: brief band 0.50-0.56, photo 0.64 -> 16 % outside, kept."""
    from codeverse.contracts.spec import Constraints

    chair = make_spec(constraints=Constraints(dimensions_m={"width": 0.45, "depth": 0.50, "height": 0.9}))
    info = dimension_conflict(chair, _box(tmp_path / "chair.png", 128, 200))  # 0.64 w/h
    assert not info["conflict"] and info["relative_error"] < ASPECT_TOL


def test_agreeing_picture_is_no_conflict(tmp_path: Path):
    # brief implies 0.467 w/h; the picture is 0.5 → 7% off
    info = dimension_conflict(make_spec(), _box(tmp_path / "ok.png", 100, 200))
    assert not info["conflict"] and info["relative_error"] < ASPECT_TOL
    assert info["expected_range"] == [0.14 / 0.30, 0.14 / 0.30]
    assert conflict_note(info) == ""


def test_the_coffee_grinder_case_is_flagged(tmp_path: Path):
    """The real failure: the brief says 0.14 x 0.30 (0.47 w/h) but the studio photo
    includes the crank arm and comes out near 0.70 — 49 % wider than the brief."""
    info = dimension_conflict(make_spec(), _box(tmp_path / "crank.png", 210, 300))
    assert info["conflict"] and info["relative_error"] > 0.4
    note = conflict_note(info)
    assert "BRIEF's dimensions are correct" in note and "part inventory" in note
    assert "0.47-0.47" in note


def test_no_stated_dimensions_is_never_a_conflict(tmp_path: Path):
    from codeverse.contracts.spec import Constraints
    spec = make_spec(constraints=Constraints())
    assert dimension_conflict(spec, _box(tmp_path / "x.png", 300, 100))["conflict"] is False


def test_unreadable_image_is_never_a_conflict(tmp_path: Path):
    info = dimension_conflict(make_spec(), tmp_path / "missing.png")
    assert info["conflict"] is False and info["error"]

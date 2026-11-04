"""The render-vs-reference diff."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from codeverse3d.models.base import ModelError
from codeverse3d.reference import compare
from tests.orchestrator_tracks.fakes import FakeChatModel
from tests.reference.conftest import make_spec


def _png(p: Path) -> Path:
    Image.new("RGB", (64, 64), (200, 200, 200)).save(p)
    return p


def test_a_failed_or_impossible_diff_is_soft(tmp_path: Path):
    ref, ren = _png(tmp_path / "ref.png"), _png(tmp_path / "ren.png")
    d = compare(make_spec(), [ref], [ren], model=FakeChatModel(by_label={"reference_diff": [ModelError("nope")]}))
    assert d.mismatches == [] and "diff call failed" in d.error and d.as_text() == ""
    d = compare(make_spec(), [tmp_path / "gone.png"], [ren], model=FakeChatModel())
    assert "nothing to compare" in d.error

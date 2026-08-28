"""The render-vs-reference diff and the refine tasks derived from it."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from codeverse.models.base import ModelError
from codeverse.reference import Mismatch, ReferenceDiff, compare, refine_tasks
from tests.reference.conftest import FakeChat, make_spec

ANSWER = {
    "mismatches": [
        {"kind": "missing_feature", "target": "Hopper", "detail": "reference has a conical hopper; render is flat",
         "severity": "critical"},
        {"kind": "wrong_part_count", "target": "Flutes", "detail": "reference has 8 flutes, render has 0",
         "severity": "major"},
        {"kind": "wrong_proportion", "target": "overall", "detail": "reference body is taller", "severity": "minor"},
        {"kind": "wrong_material", "target": "Body", "detail": "walnut vs grey", "severity": "minor"},
    ],
    "matches": ["the crank handle is present", "the drawer reads correctly"],
}


def _png(p: Path) -> Path:
    Image.new("RGB", (64, 64), (200, 200, 200)).save(p)
    return p


def test_compare_names_mismatches_and_builds_text(tmp_path: Path):
    ref, ren = _png(tmp_path / "ref.png"), _png(tmp_path / "ren.png")
    chat = FakeChat({"reference_diff": [ANSWER]})
    d = compare(make_spec(), [ref], [ren], model=chat, part_names=["Hopper", "Body"],
                measured={"iou": 0.42, "aspect_ratio_err": 0.13, "reliable": True, "view": "front"},
                synthesized=True)
    assert len(d.mismatches) == 4 and d.iou == 0.42 and d.view == "front"
    text = d.as_text()
    assert "IoU 0.420" in text and "8 flutes" in text and "SYNTHESIZED" in text
    assert "already matching: the crank handle is present" in text
    req = chat.requests[0]
    labels = [p.label for p in req.messages[0].parts if getattr(p, "label", None)]
    assert labels == ["REFERENCE 1", "RENDER 1 (ren)"]
    assert "Hopper, Body" in req.messages[0].parts[0].text
    assert "the BRIEF wins" in req.messages[0].parts[0].text  # synthesized caveat


def test_top_orders_by_severity_and_becomes_three_tasks(tmp_path: Path):
    ref, ren = _png(tmp_path / "ref.png"), _png(tmp_path / "ren.png")
    d = compare(make_spec(), [ref], [ren], model=FakeChat({"reference_diff": [ANSWER]}))
    assert [m.severity for m in d.top(3)] == ["critical", "major", "minor"]
    tasks = refine_tasks(d)
    assert len(tasks) == 3
    assert tasks[0].target == "Hopper" and tasks[0].priority == 1 and tasks[0].source == "gate"
    assert "missing feature" in tasks[0].instruction and "conical hopper" in tasks[0].instruction


def test_model_failure_is_soft(tmp_path: Path):
    ref, ren = _png(tmp_path / "ref.png"), _png(tmp_path / "ren.png")
    d = compare(make_spec(), [ref], [ren], model=FakeChat({"reference_diff": [ModelError("nope")]}))
    assert d.mismatches == [] and "diff call failed" in d.error and d.as_text() == ""


def test_missing_images_short_circuit(tmp_path: Path):
    d = compare(make_spec(), [tmp_path / "gone.png"], [_png(tmp_path / "r.png")], model=FakeChat())
    assert "nothing to compare" in d.error


def test_no_mismatches_produces_no_tasks():
    assert refine_tasks(ReferenceDiff()) == []
    d = ReferenceDiff(mismatches=[Mismatch(kind="wrong_shape", detail="x")])
    assert len(refine_tasks(d, top=3)) == 1

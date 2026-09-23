"""The honesty guards: user images win, synthesized ones are marked, dims untouched."""

from __future__ import annotations

from pathlib import Path

import pytest

from codeverse3d.contracts.spec import ReferenceImage
from codeverse3d.reference import (
    SYNTH_NOTE,
    SYNTH_TAG,
    PlausibilityVerdict,
    ReferenceSet,
    ReferenceView,
    attach,
    has_user_references,
    is_synthetic,
    reference_images,
)
from tests.reference.conftest import make_spec


def _set(tmp_path: Path, views=("three_quarter", "front"), accepted=(True, True)) -> ReferenceSet:
    rs = ReferenceSet(prompt="p", image_model="gemini-image:x")
    for v, ok in zip(views, accepted, strict=True):
        p = tmp_path / f"{v}.png"
        p.write_bytes(b"\x89PNG\r\n\x1a\n")
        rs.views.append(ReferenceView(path=str(p), view=v, accepted=ok,
                                      verdict=PlausibilityVerdict(ok=ok, reason="r")))
    return rs


def test_user_images_always_win(tmp_path: Path):
    spec = make_spec(references=[ReferenceImage(path=str(tmp_path / "mine.png"), note="my photo")])
    assert has_user_references(spec)
    out, why = attach(spec, _set(tmp_path))
    assert out is spec and "user --image references win" in why


def test_attach_marks_synthesized_everywhere_and_keeps_constraints(tmp_path: Path):
    spec = make_spec()
    out, why = attach(spec, _set(tmp_path))
    assert out is not spec and "synthesized" in why.lower() and len(out.references) == 2
    assert all(r.note.startswith("SYNTHESIZED") and is_synthetic(r) for r in out.references)
    assert all(SYNTH_NOTE in r.note for r in out.references)
    assert SYNTH_TAG in out.tags
    # never overrides the brief: constraints and prompt are byte-identical
    assert out.constraints == spec.constraints and out.prompt == spec.prompt
    assert spec.references == []  # the input spec is not mutated


@pytest.mark.parametrize(("accepted", "target"), [((True, True), "front.png"), ((True, False), "three_quarter.png")])
def test_the_straight_on_photo_is_the_silhouette_target(tmp_path: Path, accepted, target):
    """The gate measures IoU against a straight-on render; the 3/4 shot is the target only when alone."""
    refs = reference_images(_set(tmp_path, ("three_quarter", "front"), accepted=accepted))
    assert refs[0].role == "target" and Path(refs[0].path).name == target
    assert [r.role for r in refs[1:]] == ["detail"] * (len(refs) - 1) and len(refs) == sum(accepted)

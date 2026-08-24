"""The honesty guards: user images win, synthesized ones are marked, dims untouched."""

from __future__ import annotations

from pathlib import Path

from codeverse.contracts.spec import ReferenceImage
from codeverse.reference.attach import attach, has_user_references, is_synthetic, reference_images
from codeverse.reference.types import (
    SYNTH_NOTE,
    SYNTH_TAG,
    PlausibilityVerdict,
    ReferenceSet,
    ReferenceView,
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
    assert out is not spec and "SYNTHESIZED" in why.upper() or "synthesized" in why
    assert len(out.references) == 2
    assert all(r.note.startswith("SYNTHESIZED") and is_synthetic(r) for r in out.references)
    assert all(SYNTH_NOTE in r.note for r in out.references)
    assert SYNTH_TAG in out.tags
    # never overrides the brief: constraints and prompt are byte-identical
    assert out.constraints == spec.constraints and out.prompt == spec.prompt
    assert spec.references == []  # the input spec is not mutated


def test_front_elevation_is_the_silhouette_target(tmp_path: Path):
    """The gate measures IoU against a straight-on render, so the straight-on photo
    must be the ``target`` and the 3/4 shot only a ``detail`` image."""
    refs = reference_images(_set(tmp_path, ("three_quarter", "front")))
    assert [r.role for r in refs] == ["target", "detail"]
    assert Path(refs[0].path).name == "front.png"


def test_three_quarter_is_the_target_when_it_is_the_only_survivor(tmp_path: Path):
    refs = reference_images(_set(tmp_path, ("three_quarter", "front"), accepted=(True, False)))
    assert len(refs) == 1 and refs[0].role == "target" and Path(refs[0].path).name == "three_quarter.png"


def test_rejected_set_leaves_the_spec_alone(tmp_path: Path):
    spec = make_spec()
    out, why = attach(spec, _set(tmp_path, accepted=(False, False)))
    assert out is spec and "running without one" in why

"""synth_reference: prompt composition, the plausibility gate, caching, fallbacks."""

from __future__ import annotations

from pathlib import Path

from codeverse3d.models.base import ModelError
from codeverse3d.reference import (
    STUDIO_SUFFIX,
    compose_image_prompt,
    synth_reference,
    views_for,
)
from tests.orchestrator_tracks.fakes import FakeChatModel
from tests.reference.conftest import GOOD_GATE, PROMPT_PLAN, FakeImageModel, make_spec


def _chat(gates=None, plan=PROMPT_PLAN):
    return FakeChatModel(by_label={"reference_prompt": [plan], "reference_gate": list(gates or [GOOD_GATE, GOOD_GATE])})


def test_views_and_prompt_composition():
    assert views_for(2) == ["three_quarter", "front"]
    assert views_for(99) == ["three_quarter", "front", "side", "back"]
    p = compose_image_prompt("a walnut coffee grinder", "front", "drawer front")
    assert p.startswith("a walnut coffee grinder, straight-on front elevation")
    assert "the picture must make readable: drawer front" in p and p.endswith(STUDIO_SUFFIX)


def test_synth_accepts_and_caches(cache_dir: Path):
    spec = make_spec()
    img = FakeImageModel()
    rs = synth_reference(spec, model=_chat(), image_model=img, n_views=2, cache_dir=cache_dir)
    assert rs.ok and len(rs.accepted) == 2 and rs.source == "fresh"
    assert all(Path(v.path).is_file() for v in rs.accepted)
    assert [v.view for v in rs.views] == ["three_quarter", "front"]
    # every image prompt carries the harness-owned studio clause
    assert all(STUDIO_SUFFIX in p for p in img.prompts)
    # 1 text call + 2 gate calls + 2 images
    assert rs.usage.cost_usd > 0.13

    again = synth_reference(spec, model=_chat(), image_model=FakeImageModel(), n_views=2, cache_dir=cache_dir)
    assert again.source == "cache" and again.usage.cost_usd == 0.0
    assert [v.path for v in again.accepted] == [v.path for v in rs.accepted]


def test_gate_rejects_wrong_object_collage_and_contradiction(cache_dir: Path):
    bad = dict(GOOD_GATE, shows_requested_object=False, depicted_object="a pepper mill")
    rs = synth_reference(make_spec(), model=_chat([bad, bad]), image_model=FakeImageModel(),
                         n_views=2, cache_dir=cache_dir)
    assert not rs.ok and len(rs.rejected) == 2
    assert "shows a pepper mill" in rs.rejected[0].verdict.failure()
    replies = [dict(GOOD_GATE, is_photo_collage=True),
               dict(GOOD_GATE, contradictions=["brief says a front drawer; the image shows no drawer"])]
    rs = synth_reference(make_spec(), model=_chat(replies), image_model=FakeImageModel(),
                         n_views=2, cache_dir=cache_dir / "b")  # the rejected set above is cached
    assert not rs.ok
    assert "collage" in rs.views[0].verdict.failure()
    assert "contradicts:" in rs.views[1].verdict.failure()


def test_unverifiable_image_is_never_accepted(cache_dir: Path):
    """No text model → nothing can validate the picture → it is not used."""
    rs = synth_reference(make_spec(), model=None, image_model=FakeImageModel(), n_views=1, cache_dir=cache_dir)
    assert not rs.ok and len(rs.views) == 1 and rs.views[0].verdict is None


def test_no_failure_is_fatal(cache_dir: Path):
    rs = synth_reference(make_spec(), model=_chat(), image_model=FakeImageModel(fail=True),
                         n_views=2, cache_dir=cache_dir)
    assert not rs.ok and "image model exploded" in rs.error
    rs = synth_reference(make_spec(), model=_chat(), image_model=None, cache_dir=cache_dir)
    assert not rs.ok and rs.error == "no image model configured"
    # the prompt writer failed: the brief is the prompt
    chat = FakeChatModel(by_label={"reference_prompt": [ModelError("boom")], "reference_gate": [GOOD_GATE]})
    rs = synth_reference(make_spec(), model=chat, image_model=FakeImageModel(), n_views=1, cache_dir=cache_dir)
    assert rs.ok and "coffee grinder" in rs.subject


def test_out_dir_copies_accepted_images(cache_dir: Path, tmp_path: Path):
    """The copies only: ``ground_spec`` writes reference_set.json (test_run_and_cli), once,
    including the all-rejected set ``_publish`` never saw."""
    out = tmp_path / "run" / "artifacts" / "reference"
    rs = synth_reference(make_spec(), model=_chat(), image_model=FakeImageModel(), n_views=2,
                         cache_dir=cache_dir, out_dir=out)
    assert len(rs.accepted) == 2 and all(Path(v.path).parent == out for v in rs.accepted)
    assert rs.synthesized and not (out / "reference_set.json").exists()

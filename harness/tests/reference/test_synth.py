"""synth_reference: prompt composition, the plausibility gate, caching, fallbacks."""

from __future__ import annotations

import json
from pathlib import Path

from codeverse.models.base import ModelError
from codeverse.reference import (
    STUDIO_SUFFIX,
    cache_key,
    compose_image_prompt,
    synth_reference,
    template_hash,
    views_for,
)
from tests.reference.conftest import GOOD_GATE, PROMPT_PLAN, FakeChat, FakeImageModel, make_spec


def _chat(gates=None, plan=PROMPT_PLAN):
    return FakeChat({"reference_prompt": [plan], "reference_gate": list(gates or [GOOD_GATE, GOOD_GATE])})


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


def test_cache_key_moves_with_the_brief_and_the_prompts(cache_dir: Path):
    a = cache_key(make_spec(), n_views=2, text_model="m", image_model="i")
    b = cache_key(make_spec(prompt="a teapot"), n_views=2, text_model="m", image_model="i")
    c = cache_key(make_spec(), n_views=1, text_model="m", image_model="i")
    d = cache_key(make_spec(), n_views=2, text_model="m", image_model="other")
    assert len({a, b, c, d}) == 4 and len(template_hash()) == 12


def test_gate_rejects_wrong_object_and_falls_back(cache_dir: Path):
    bad = dict(GOOD_GATE, shows_requested_object=False, depicted_object="a pepper mill")
    rs = synth_reference(make_spec(), model=_chat([bad, bad]), image_model=FakeImageModel(),
                         n_views=2, cache_dir=cache_dir)
    assert not rs.ok and len(rs.rejected) == 2
    assert "shows a pepper mill" in rs.rejected[0].verdict.failure()


def test_gate_rejects_collage_watermark_and_contradiction(cache_dir: Path):
    replies = [dict(GOOD_GATE, is_photo_collage=True),
               dict(GOOD_GATE, contradictions=["brief says a front drawer; the image shows no drawer"])]
    rs = synth_reference(make_spec(), model=_chat(replies), image_model=FakeImageModel(),
                         n_views=2, cache_dir=cache_dir)
    assert not rs.ok
    assert "collage" in rs.views[0].verdict.failure()
    assert "contradicts:" in rs.views[1].verdict.failure()


def test_unverifiable_image_is_never_accepted(cache_dir: Path):
    """No text model → nothing can validate the picture → it is not used."""
    rs = synth_reference(make_spec(), model=None, image_model=FakeImageModel(), n_views=1, cache_dir=cache_dir)
    assert not rs.ok and len(rs.views) == 1 and rs.views[0].verdict is None


def test_image_failure_is_not_fatal(cache_dir: Path):
    rs = synth_reference(make_spec(), model=_chat(), image_model=FakeImageModel(fail=True),
                         n_views=2, cache_dir=cache_dir)
    assert not rs.ok and "image model exploded" in rs.error


def test_no_image_model_is_not_fatal(cache_dir: Path):
    rs = synth_reference(make_spec(), model=_chat(), image_model=None, cache_dir=cache_dir)
    assert not rs.ok and rs.error == "no image model configured"


def test_prompt_writer_failure_falls_back_to_the_brief(cache_dir: Path):
    chat = FakeChat({"reference_prompt": [ModelError("boom")], "reference_gate": [GOOD_GATE]})
    rs = synth_reference(make_spec(), model=chat, image_model=FakeImageModel(), n_views=1, cache_dir=cache_dir)
    assert rs.ok and "coffee grinder" in rs.subject


def test_out_dir_copies_accepted_images_and_writes_the_set(cache_dir: Path, tmp_path: Path):
    out = tmp_path / "run" / "artifacts" / "reference"
    rs = synth_reference(make_spec(), model=_chat(), image_model=FakeImageModel(), n_views=2,
                         cache_dir=cache_dir, out_dir=out)
    assert all(Path(v.path).parent == out for v in rs.accepted)
    saved = json.loads((out / "reference_set.json").read_text())
    assert saved["synthesized"] is True and len(saved["views"]) == 2

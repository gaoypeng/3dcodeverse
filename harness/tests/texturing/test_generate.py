"""generate.py: dedupe, cache, parallel fan-out, per-texture failures, usage."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from codeverse.texturing.generate import FakeImageModel, generate_textures, prompt_key
from codeverse.texturing.tile import SEAM_MAX


def test_generate_dedupes_caches_and_accounts(tmp_path: Path):
    model = FakeImageModel(usd_per_image=0.01)
    prompts = {"oak": "oak wood grain", "oak_copy": "oak wood grain", "steel": "brushed steel"}
    ts = generate_textures(prompts, tmp_path / "out", model, size=128, cache_dir=tmp_path / "cache", max_workers=3)
    assert set(ts.textures) == set(prompts)
    assert len(model.calls) == 2  # identical prompt generated once
    assert ts.usage.cost_usd == 0.02 and ts.usage.output_tokens == 2 * 1290
    for a in ts.textures.values():
        assert a.ok and Image.open(a.path).size == (128, 128)
        assert a.seam_score <= SEAM_MAX and a.seam_score < a.seam_score_raw
    assert ts.textures["oak_copy"].cached and ts.textures["oak_copy"].prompt_hash == ts.textures["oak"].prompt_hash
    assert (tmp_path / "cache" / "textures" / f"{prompt_key('oak wood grain', model.id, 128)}.png").is_file()
    # second run: everything from cache, no model calls, zero cost
    ts2 = generate_textures(prompts, tmp_path / "out2", model, size=128, cache_dir=tmp_path / "cache")
    assert len(model.calls) == 2 and ts2.usage.cost_usd == 0.0 and all(a.cached for a in ts2.textures.values())


def test_generate_records_failures_per_texture(tmp_path: Path):
    model = FakeImageModel(fail_on=("forbidden",))
    ts = generate_textures({"ok": "oak", "bad": "forbidden pattern"}, tmp_path, model, size=64, cache_dir=tmp_path / "c")
    assert ts.textures["ok"].ok and not ts.textures["bad"].ok
    assert "ModelError" in ts.textures["bad"].error and ts.failed() == {"bad": ts.textures["bad"].error}
    assert set(ts.paths()) == {"ok"}


def test_generate_accepts_texture_plan_and_empty(tmp_path: Path, chair_plan):
    from codeverse.texturing.plan import default_plan

    tp = default_plan(chair_plan)
    ts = generate_textures(tp, tmp_path, FakeImageModel(), size=64, cache_dir=tmp_path / "c")
    assert set(ts.textures) == set(tp.texture_ids())
    assert generate_textures({}, tmp_path, FakeImageModel(), size=64, cache_dir=tmp_path / "c").textures == {}

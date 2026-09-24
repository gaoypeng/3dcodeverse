"""scene_pack.py: heuristic + VLM pack plans, generation, manifest, prompt snippet."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from codeverse3d.contracts.plan import AssetPlan, BBox, CameraPlan, ScenePlan, ZonePlan
from codeverse3d.texturing.plan import (
    scene_pack_plan,
    scene_texture_pack,
    texture_pack_prompt,
)
from tests.orchestrator_tracks.fakes import FakeChatModel
from tests.texturing.conftest import FakeImageModel


@pytest.fixture
def garden_plan() -> ScenePlan:
    bb = BBox(center=(0, 0, 0), extents=(30, 10, 30))
    return ScenePlan(
        title="Zen Garden", summary="a small Kyoto garden", setting="Kyoto, autumn dusk", mood="calm",
        bounds=bb, environment="mossy banks, a koi pond, raked gravel court",
        zones=[ZonePlan(name="Pond", description="koi pond with stepping stones", bbox=bb, contents=["Bridge"]),
               ZonePlan(name="GravelCourt", description="raked gravel karesansui with granite rocks", bbox=bb)],
        assets=[AssetPlan(name="Bridge", kind="blender_glb", description="red lacquered wooden arched bridge", approx_size_m=(4, 2, 1))],
        cameras=[CameraPlan(name="main", position=(10, 3, 10), look_at=(0, 0, 0))],
    )


def test_scene_pack_plan_with_model_and_cap(garden_plan):
    payload = {"textures": [
        {"name": "Moss Ground", "material_family": "other", "subject": "green moss carpet", "tile_size_m": 1.2, "role": "ground"},
        {"name": "moss_ground", "material_family": "other", "subject": "dup", "tile_size_m": 1.0, "role": "ground"},
        {"name": "granite_gravel", "material_family": "stone", "subject": "fine grey gravel", "tile_size_m": 0.8, "role": "ground"},
        {"name": "iron_grate", "material_family": "metal", "subject": "cast iron grate", "tile_size_m": 0.5, "role": "prop"},
        {"name": "zinc_sheet", "material_family": "metal", "subject": "painted zinc", "metallic": 0, "role": "roof"},
    ], "notes": "n"}
    chat = FakeChatModel(default=payload)
    entries, notes, usage = scene_pack_plan(garden_plan, "fake:fake", model=chat, n_max=4)
    assert [e.name for e in entries] == ["moss_ground", "granite_gravel", "iron_grate", "zinc_sheet"] and notes == "n"
    assert usage.cost_usd > 0
    # a metal the planner left unset is metallic (it shipped 0.0 until 2026-09-23); a stated 0 is kept
    assert [e.metallic for e in entries] == [0.0, 0.0, 1.0, 0.0]
    assert entries[0].tile_size_m == 1.2 and "green moss carpet, matte natural surface" in entries[0].prompt
    assert "Zen Garden" in chat.requests[0].messages[0].text and "GravelCourt" in chat.requests[0].messages[0].text
    with pytest.raises(ValueError):
        scene_pack_plan(garden_plan, "fake:fake", model=FakeChatModel(default={"textures": []}))


def test_scene_texture_pack_writes_manifest_and_prompt(tmp_path: Path, garden_plan):
    model = FakeImageModel()
    out = tmp_path / "public" / "textures"
    pack = scene_texture_pack(garden_plan, out, model, "", size=64, cache_dir=tmp_path / "c", n_min=4, n_max=6)
    man = json.loads(Path(pack.manifest_path).read_text())
    assert pack.source == "default" and 4 <= len(man) <= 6 and Path(pack.manifest_path).name == "manifest.json"
    for name, e in man.items():
        assert (out / e["file"]).is_file() and e["file"] == f"{name}.png" and e["tile_size_m"] > 0 and "family" in e
    assert Path(pack.manifest_path).parent == out
    snippet = texture_pack_prompt(man)
    assert "/public/textures/" in snippet and "RepeatWrapping" in snippet and "SRGBColorSpace" in snippet
    assert "loaders.texture.load" in snippet and "tile =" in snippet
    assert texture_pack_prompt({}) == ""
    assert "/textures/x" in texture_pack_prompt({"a": {"file": "a.png", "tile_size_m": 1}}, url_prefix="/textures/x")

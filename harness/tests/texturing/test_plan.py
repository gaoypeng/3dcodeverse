"""plan.py: default heuristics, planner-output finalisation, one vision call + cache."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from codeverse3d.texturing.materials import pbr_for
from codeverse3d.texturing.plan import (
    PlannerOutput,
    PlannerPart,
    TexturePlan,
    compose_image_prompt,
    family_from_text,
    finalize_plan,
    material_plan,
)
from tests.orchestrator_tracks.fakes import FakeChatModel


def test_family_keywords_are_whole_words():
    assert family_from_text("satin oiled walnut") == "wood"
    assert family_from_text("brushed stainless steel") == "metal"
    assert family_from_text("grey-blue linen upholstery") == "fabric"
    assert family_from_text("oiled") == "other"  # 'led' must not match


def test_finalize_plan_defaults_and_unification(chair_plan):
    out = PlannerOutput(parts=[
        PlannerPart(part="Seat", texture_id="oak", material_family="wood", subject="light oak, straight grain"),
        PlannerPart(part="back", texture_id="oak", material_family="fabric", subject="something else", roughness=0.9),
        PlannerPart(part="Ghost", texture_id="x", material_family="wood", subject="unknown part is dropped"),
        # Leg omitted → defaulted from the plan material (brushed steel → metal)
    ])
    tp = finalize_plan(out, chair_plan, model_id="m")
    by = tp.by_part()
    assert set(by) == {"seat", "back", "leg", "knob"}
    assert by["back"].prompt == by["seat"].prompt and by["back"].material_family == "wood"  # shared id → first wins
    assert by["back"].roughness == 0.9  # per-part factor kept
    assert by["leg"].material_family == "metal" and not by["leg"].skip
    assert by["knob"].skip
    assert by["seat"].tile_size_m == 0.35  # wood default
    # an unset factor is THE material table's, the numbers the normaliser writes in the same pass (C5)
    assert (by["seat"].roughness, by["leg"].metallic, by["leg"].roughness) == (
        pbr_for("wood").roughness, pbr_for("metal").metallic, pbr_for("metal").roughness)


def test_compose_prompt():
    p = compose_image_prompt("  red brick wall. ", "stone")
    assert p.startswith("red brick wall, stone surface") and p.endswith("1:1 square")


def test_material_plan_one_call_with_sheet_and_cache(tmp_path: Path, chair_plan, chair_spec):
    sheet = tmp_path / "sheet.png"
    Image.new("RGB", (32, 32), (1, 2, 3)).save(sheet)
    payload = {"parts": [
        {"part": "Seat", "texture_id": "oak_wood", "material_family": "wood", "subject": "light oak", "projection": "planar_y", "tile_size_m": 0.4},
        {"part": "Back", "texture_id": "oak_wood", "material_family": "wood", "subject": "light oak", "projection": "box"},
        {"part": "Leg", "texture_id": "steel", "material_family": "metal", "subject": "brushed steel", "projection": "cylinder", "metallic": 1, "roughness": 0.3},
        {"part": "Knob", "texture_id": "glass", "material_family": "glass", "skip": True, "reason": "glass"},
    ], "notes": "two materials"}
    chat = FakeChatModel(default=payload)
    tp = material_plan(chair_spec, chair_plan, sheet, "fake:fake", model=chat, cache_dir=tmp_path / "cache")
    assert isinstance(tp, TexturePlan) and tp.source == "vlm" and tp.usage.cost_usd == pytest.approx(0.002)
    assert len(chat.requests) == 1
    req = chat.requests[0]
    assert req.response_schema is not None and any(p.type == "image" for p in req.messages[0].parts)
    assert "Seat" in req.messages[0].text and chair_spec.prompt in req.messages[0].text
    assert tp.by_part()["seat"].projection == "planar_y" and tp.by_part()["seat"].tile_size_m == 0.4
    assert tp.texture_ids() == ["oak_wood", "steel"]
    # cache hit: no second call
    tp2 = material_plan(chair_spec, chair_plan, sheet, "fake:fake", model=chat, cache_dir=tmp_path / "cache")
    assert tp2.source == "cache" and len(chat.requests) == 1 and tp2.texture_ids() == tp.texture_ids()
    # different sheet → new key → new call
    Image.new("RGB", (32, 32), (9, 9, 9)).save(sheet)
    material_plan(chair_spec, chair_plan, sheet, "fake:fake", model=chat, cache_dir=tmp_path / "cache")
    assert len(chat.requests) == 2


def test_material_plan_invalid_payload_fails_loud(tmp_path, chair_plan, chair_spec):
    chat = FakeChatModel(default={"nope": 1})
    with pytest.raises(ValueError):
        material_plan(chair_spec, chair_plan, None, "fake:fake", model=chat, cache_dir=tmp_path, use_cache=False)

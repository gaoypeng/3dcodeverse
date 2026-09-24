"""gate.py: seam gate + before/after judge gate decisions."""

from __future__ import annotations

from pathlib import Path

import pytest

from codeverse3d.judges.rubrics import load_rubric
from codeverse3d.texturing.generate import TextureAsset, judge_gate
from tests.texturing.conftest import FakeJudge, fake_render


def _asset(tid: str, seam: float, ok_path: Path) -> TextureAsset:
    return TextureAsset(texture_id=tid, path=str(ok_path), prompt="p", prompt_hash="h", seam_score=seam)


@pytest.mark.parametrize(("rubric", "criterion"), [
    ("static_object_v1", "materials"), ("reference_v1", "material_color"), ("asset_v1", "material_truth"),
    ("scene_v1", "materials_shaders_effects"), ("articulated_v1", ""), ("shader_v2", ""),
])
def test_the_rubric_flags_its_materials_criterion(rubric, criterion):
    """N64: the gate sniffed criterion ids by prefix (law 4); a rubric now says which one it is."""
    assert load_rubric(rubric).materials_criterion == criterion


def _run(tmp_path, chair_spec, chair_plan, chair_glb, verdicts, **kw):
    judge = FakeJudge(verdicts)
    after = tmp_path / "after.glb"
    after.write_bytes(chair_glb.read_bytes() + b"\0" * 7)  # different size → different fake render colour
    res = judge_gate(chair_spec, chair_plan, chair_glb, after, tmp_path / "gate", judge=judge, render=fake_render, **kw)
    assert judge.calls == 2
    return res


def test_judge_gate_rejects_overall_drop_or_flat_materials(tmp_path, chair_spec, chair_plan, chair_glb):
    res = _run(tmp_path, chair_spec, chair_plan, chair_glb, [(0.70, {"materials": 0.6}), (0.65, {"materials": 0.9})])
    assert not res.shipped and "Δoverall" in res.reason
    res = _run(tmp_path, chair_spec, chair_plan, chair_glb, [(0.70, {"materials": 0.6}), (0.72, {"materials": 0.6})])
    assert not res.shipped and "did not improve" in res.reason
    res = _run(tmp_path, chair_spec, chair_plan, chair_glb, [(0.70, {"materials": 0.6}), (0.695, {"materials": 0.7})])
    assert res.shipped  # −0.005 is within the −0.01 tolerance


def test_judge_gate_degraded_never_ships(tmp_path, chair_spec, chair_plan, chair_glb):
    judge = FakeJudge([(0.0, {"materials": 0.0})], degraded=True)
    res = judge_gate(chair_spec, chair_plan, chair_glb, chair_glb, tmp_path / "g", judge=judge, render=fake_render)
    assert not res.shipped and "degraded" in res.reason

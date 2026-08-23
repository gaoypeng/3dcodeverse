"""gate.py: seam gate + before/after judge gate decisions."""

from __future__ import annotations

from pathlib import Path

from codeverse.texturing.gate import judge_gate, material_criterion, seam_gate
from codeverse.texturing.generate import TextureAsset
from tests.texturing.conftest import FakeJudge, fake_render


def _asset(tid: str, seam: float, ok_path: Path) -> TextureAsset:
    return TextureAsset(texture_id=tid, path=str(ok_path), prompt="p", prompt_hash="h", seam_score=seam)


def test_seam_gate(tmp_path: Path):
    p = tmp_path / "a.png"
    p.write_bytes(b"x")
    res = seam_gate({"a": _asset("a", 0.01, p), "b": _asset("b", 0.5, p), "c": _asset("c", 0.0, tmp_path / "none.png")})
    assert res.passed == {"a": 0.01} and res.failed == {"b": 0.5}


def test_material_criterion_lookup():
    assert material_criterion({"intent_fidelity": 1, "materials": 0.5}) == "materials"
    assert material_criterion({"a": 1, "materials_shaders_effects": 0.2}) == "materials_shaders_effects"
    assert material_criterion({"x": 1}) == ""


def _run(tmp_path, chair_spec, chair_plan, chair_glb, verdicts, **kw):
    judge = FakeJudge(verdicts)
    after = tmp_path / "after.glb"
    after.write_bytes(chair_glb.read_bytes() + b"\0" * 7)  # different size → different fake render colour
    res = judge_gate(chair_spec, chair_plan, chair_glb, after, tmp_path / "gate", judge=judge, render=fake_render, **kw)
    assert judge.calls == 2
    return res


def test_judge_gate_ships_on_material_gain(tmp_path, chair_spec, chair_plan, chair_glb):
    res = _run(tmp_path, chair_spec, chair_plan, chair_glb,
               [(0.70, {"materials": 0.6, "x": 0.7}), (0.71, {"materials": 0.7, "x": 0.7})])
    assert res.shipped and res.delta == 0.01 and res.materials_delta == 0.1 and res.materials_criterion == "materials"
    assert res.renders_before and res.renders_after and res.usage.cost_usd == 0.02
    assert Path(res.renders_after.contact_sheet).is_file()


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

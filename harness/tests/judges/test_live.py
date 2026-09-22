"""Live judge tests (real Gemini calls; need keys).  Run: pytest tests/judges -m live -s"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import trimesh

from codeverse3d.config import get_settings
from codeverse3d.contracts.artifacts import RenderSet
from codeverse3d.contracts.plan import AcceptanceItem
from codeverse3d.judges.base import JudgeInput
from codeverse3d.judges.vlm_judge import VlmJudge
from tests.judges.conftest import make_measurement, make_renders, make_spec

pytestmark = pytest.mark.live
MODEL = "gemini:gemini-3.7-flash"


def _stool_glb(out: Path) -> Path:
    seat = trimesh.creation.cylinder(radius=0.18, height=0.03, sections=48)
    seat.apply_translation((0, 0, 0.45 - 0.015))
    parts = {"Seat": seat}
    for i in range(3):
        a = np.deg2rad(90 + i * 120)
        leg = trimesh.creation.cylinder(radius=0.018, height=0.43, sections=20)
        leg.apply_translation((0.13 * np.cos(a), 0.13 * np.sin(a), 0.215))
        parts[f"Leg{i + 1}"] = leg
    scene = trimesh.Scene()
    zup_to_yup = trimesh.transformations.rotation_matrix(-np.pi / 2, (1, 0, 0))
    for name, m in parts.items():
        m.apply_transform(zup_to_yup)
        m.visual = trimesh.visual.ColorVisuals(m, face_colors=(160, 110, 60, 255))
        scene.add_geometry(m, node_name=name, geom_name=name)
    out.parent.mkdir(parents=True, exist_ok=True)
    scene.export(out)
    return out


def _renders_for_test(tmp: Path) -> tuple[RenderSet, object, str]:
    """Real stool renders via spatial.render when possible, else PIL drawings."""
    try:
        from codeverse3d.conventions import OBJECT_VIEWS_QUICK
        from codeverse3d.spatial.measure import measure_glb
        from codeverse3d.spatial.render import render_glb

        glb = _stool_glb(tmp / "stool.glb")
        rs = render_glb(glb, tmp / "renders", views=OBJECT_VIEWS_QUICK, sheet=True)
        return rs, measure_glb(glb), "render_glb(trimesh stool)"
    except Exception as e:  # noqa: BLE001 - live test: fall back but SAY so
        return make_renders(tmp / "renders"), make_measurement(), f"PIL chair drawings (render_glb unavailable: {type(e).__name__}: {str(e)[:120]})"


def test_live_vlm_judge_two_samples(tmp_path):
    assert get_settings().gemini_api_keys, "no Gemini keys"
    renders, meas, source = _renders_for_test(tmp_path)
    prompt = ("A simple three-legged wooden stool, 45 cm tall, round seat 36 cm across." if "stool" in source
              else "A simple wooden dining chair with four legs and a slatted backrest.")
    spec = make_spec(prompt=prompt)
    inp = JudgeInput(spec=spec, renders=renders, measurement=meas,
                     acceptance=[AcceptanceItem(id="A1", text="three legs reach the ground", how="visual", priority="must"),
                                 AcceptanceItem(id="A2", text="seat is round", how="visual", priority="should")],
                     plan_summary="parts: Seat, Leg1, Leg2, Leg3")
    judge = VlmJudge("static_object_v1", model_id=MODEL, n_samples=2, cache_dir=tmp_path / "cache")
    j = judge.judge(inp)
    raw = json.loads(j.raw)
    print(f"\nLIVE source={source}\noverall={j.overall} std={j.score_std} passed={j.passed} n={j.n_samples}"
          f"\nscores={j.scores}\nacceptance={j.acceptance_results}\ncaps={raw.get('caps')}\nsample_errors={raw.get('sample_errors')}"
          f"\nusage={j.usage}\nsummary={j.summary}\nplan={[i.instruction for i in j.improvement_plan]}")
    assert not j.degraded, j.summary
    assert j.n_samples == 2 and raw["sample_errors"] == []  # parsed first try
    assert 0.0 < j.overall < 1.0
    assert set(j.scores) == {c.id for c in judge.rubric.criteria}
    assert set(raw["defects"]) == {d.id for d in judge.rubric.defects}  # checklist answered
    print(f"defects={[d for d, on in raw['defects'].items() if on]} penalty={raw['defect_penalty']}")


def test_live_judge_separates_crafted_from_crude(tmp_path):
    """Dynamic range: a real stool render set vs a crude PIL chair drawing, same brief (stool)."""
    renders, meas, source = _renders_for_test(tmp_path / "stool")
    if "stool" not in source:
        pytest.skip("needs real stool renders")
    spec = make_spec(prompt="A simple three-legged wooden stool, 45 cm tall, round seat 36 cm across.")
    judge = VlmJudge("static_object_v1", model_id=MODEL, n_samples=1, cache_dir=tmp_path / "cache")
    good = judge.judge(JudgeInput(spec=spec, renders=renders, measurement=meas, plan_summary="parts: Seat, Leg1, Leg2, Leg3"))
    crude = judge.judge(JudgeInput(spec=spec, renders=make_renders(tmp_path / "crude"), measurement=make_measurement(),
                                   plan_summary="parts: Seat, Leg1, Leg2, Leg3"))
    gd, cd = json.loads(good.raw), json.loads(crude.raw)
    print(f"\nLIVE stool overall={good.overall} uncapped={gd['overall_uncapped']} defects={[d for d, o in gd['defects'].items() if o]}"
          f"\nLIVE crude overall={crude.overall} uncapped={cd['overall_uncapped']} defects={[d for d, o in cd['defects'].items() if o]}")
    assert not good.degraded and not crude.degraded
    assert good.overall - crude.overall >= 0.25
    assert any(cd["defects"].values())  # the crude drawing trips the checklist


def test_live_pairwise_stool_vs_chair(tmp_path):
    from codeverse3d.judges.pairwise import PairwiseJudge

    renders, _, source = _renders_for_test(tmp_path / "stool")
    if "stool" not in source:
        pytest.skip("needs real stool renders")
    chair = make_renders(tmp_path / "chair")
    spec = make_spec(prompt="A simple three-legged wooden stool, 45 cm tall, round seat.")
    res = PairwiseJudge(MODEL, cache_dir=tmp_path / "cache").compare(spec, renders, chair)
    print(f"\nLIVE pairwise winner={res.winner} conf={res.confidence} err={res.error!r}\nreasons={res.reasons}\nusage={res.usage}")
    assert res.winner == "a"


def test_live_reference_judge(tmp_path):
    from codeverse3d.contracts.spec import ReferenceImage
    from codeverse3d.judges.vlm_judge import ReferenceJudge

    renders, meas, source = _renders_for_test(tmp_path / "stool")
    if "stool" not in source:
        pytest.skip("needs real stool renders")
    front = next(v for v in renders.views if v.name == "front")
    spec = make_spec(prompt="Reproduce the stool in the reference image.",
                     references=[ReferenceImage(path=front.path, role="target", note="the target stool")])
    inp = JudgeInput(spec=spec, renders=renders, measurement=meas, plan_summary="parts: Seat, Leg1, Leg2, Leg3")
    j = ReferenceJudge(MODEL, cache_dir=tmp_path / "cache").judge(inp)
    raw = json.loads(j.raw)
    print(f"\nLIVE reference overall={j.overall} passed={j.passed} scores={j.scores}\nsummary={j.summary}\nusage={j.usage}")
    assert not j.degraded and raw["sample_errors"] == []
    assert j.scores["silhouette_match"] >= 0.95  # reference is the render itself

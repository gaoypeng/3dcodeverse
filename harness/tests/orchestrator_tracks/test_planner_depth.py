"""Planner brief expansion, hierarchical parts, budgets, and quality gates."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from codeverse3d.contracts.chat import ImagePart
from codeverse3d.contracts.common import Backends, Language, Track, Usage
from codeverse3d.contracts.plan import (
    SUBPART_REL_SLACK,
    BBox,
    EngineeringBrief,
    GraphicsPlan,
    PartPlan,
    RefDimension,
    StaticPlan,
    SubAssembly,
    SubPartPlan,
)
from codeverse3d.contracts.spec import Constraints, ReferenceImage, Spec
from codeverse3d.models.base import ModelError
from codeverse3d.tracks import planner as B
from codeverse3d.tracks import planner as BR
from codeverse3d.tracks.planner import (
    MAX_QUALITY_REASKS,
    build_user_prompt,
)
from codeverse3d.tracks.planner import plan as run_planner
from codeverse3d.workspace import Workspace

from .fakes import FakeChatModel


# ----------------------------------------------------------------------------- helpers
def _spec(prompt: str = "a hand-crank coffee grinder", *, must: int = 10, track: Track = Track.STATIC_OBJECT,
          language: Language = Language.BLENDER) -> Spec:
    return Spec(id="s1", track=track, language=language, prompt=prompt,
                constraints=Constraints(must_have=[f"feature {i}" for i in range(must)]),
                backends=Backends(planner="fake:planner", generator="fake:gen", judge="fake:judge"))


def _bbox(cx=0.0, cy=0.0, cz=0.0, ex=1.0, ey=1.0, ez=1.0) -> BBox:
    return BBox(center=(cx, cy, cz), extents=(ex, ey, ez))


def _part(name: str, *, desc: str = "plain box", children=(), **kw) -> PartPlan:
    return PartPlan(name=name, role="r", description=desc, bbox=kw.pop("bbox", _bbox()),
                    material=kw.pop("material", "steel"), children=list(children), **kw)


def _plan(parts) -> StaticPlan:
    return StaticPlan(object_name="Thing", summary="s", overall_bbox=_bbox(ex=2, ey=2, ez=2), parts=list(parts))


def _detailed(i: int) -> str:
    return f"tapered wall 12→8 mm with {i + 3} flutes and a 4 mm chamfer"


# ----------------------------------------------------------------------------- (2) sub-parts
def test_subpart_contracts():
    p = _part("BurrMechanism", children=[
        SubPartPlan(name="Burr", description="conical burr, 24 cutting flutes", bbox=_bbox(ex=0.4, ey=0.4, ez=0.4)),
        SubPartPlan(name="Shaft", description="square shaft 10 mm", bbox=_bbox(cz=0.2, ex=0.1, ey=0.1, ez=0.5)),
        SubPartPlan(name="Nut", description="knurled nut", bbox=_bbox(cz=0.4, ex=0.2, ey=0.2, ez=0.1), instances=2),
    ])
    assert p.leaf_count == 4  # 1 + 1 + 2 copies
    assert _plan([p]).leaf_count == 4
    plain = _part("Plain")  # plans written before sub-parts keep the additive defaults
    assert plain.children == [] and plain.detail_hint == ""

    with pytest.raises(ValidationError) as e:
        _part("Head", children=[SubPartPlan(name="Peg", description="peg", bbox=_bbox(cx=3.0, ex=0.2, ey=0.2, ez=0.2))])
    msg = str(e.value)
    assert "sticks" in msg and " x " in msg and "Peg" in msg and "grow the parent bbox" in msg

    over = 0.5 + SUBPART_REL_SLACK * 0.9  # parent half-extent 0.5, child centre pushes it just inside
    _part("Head", bbox=_bbox(ex=1.0, ey=1.0, ez=1.0),
          children=[SubPartPlan(name="Peg", description="peg", bbox=_bbox(cx=over, ex=0.0, ey=0.2, ez=0.2))])

    with pytest.raises(ValidationError, match="duplicate sub-part name"):
        _part("Head", children=[SubPartPlan(name="Peg", description="a", bbox=_bbox(ex=0.2, ey=0.2, ez=0.2)),
                                SubPartPlan(name="peg", description="b", bbox=_bbox(ex=0.2, ey=0.2, ez=0.2))])
    with pytest.raises(ValidationError, match="repeats its parent"):
        _part("Peg", children=[SubPartPlan(name="Peg", description="a", bbox=_bbox(ex=0.2, ey=0.2, ez=0.2))])
    with pytest.raises(ValidationError, match="same name as top-level part"):
        _plan([_part("Body", children=[SubPartPlan(name="Lid", description="d", bbox=_bbox(ex=0.2, ey=0.2, ez=0.2))]),
               _part("Lid")])


# ----------------------------------------------------------------------------- (3) budgets
def test_plan_budget_contracts():
    assert not B.plan_budget(_spec(must=2)).evidence
    small = B.plan_budget(_spec(must=3))
    big = B.plan_budget(_spec(must=13))
    huge = B.plan_budget(_spec(must=40))
    assert small.target_parts == B.MIN_PARTS < big.target_parts == 13
    assert huge.target_parts == B.PART_CAP[Language.BLENDER.value]
    assert huge.cap_parts == B.PART_CAP[Language.BLENDER.value]
    assert B.plan_budget(_spec(must=40, track=Track.ARTICULATED_OBJECT,
                               language=Language.URDF_BLENDER)).cap_parts == B.PART_CAP[Language.URDF_BLENDER.value]

    brief = _brief().model_copy(update={
        "signature_features": ["a", "b", "c"],
        "sub_assemblies": [SubAssembly(name=f"s{i}", parts=["p", "q", "r"]) for i in range(4)]})
    b = B.plan_budget(_spec(must=0), brief)
    assert b.target_parts == 7 and b.min_assemblies == 3 and "sub-assemblies" in b.reason

    b = B.plan_budget(_spec(must=6, track=Track.GRAPHICS, language=Language.GLSL_SHADER))
    assert b.cap_parts == B.GRAPHICS_CAP and b.target_parts == 5 and b.evidence
    assert "passes" in B.budget_block(b, unit="passes") and "`elements`" in B.budget_block(b, unit="passes")

    text = build_user_prompt(_spec(must=10), budget=B.plan_budget(_spec(must=10)))
    assert "PLAN BUDGET" in text and "10 checklist items" in text


# ----------------------------------------------------------------------------- (4) quality gate
def test_plan_quality_gate_contracts():
    budget = B.plan_budget(_spec(must=12))
    complaint = B.plan_quality_complaint(_plan([_part(f"P{i}", desc=_detailed(i)) for i in range(4)]), budget)
    assert "Only 4 parts" in complaint and "about 12" in complaint

    budget = B.plan_budget(_spec(must=8))
    complaint = B.plan_quality_complaint(_plan([_part(f"P{i}") for i in range(8)]), budget)
    assert "boxes at different sizes" in complaint and "P0" in complaint

    parts = [_part(f"P{i}", desc=_detailed(i), material=f"m{i}") for i in range(8)]
    assert B.plan_quality_complaint(_plan(parts), budget) == ""

    parts = [_part(f"P{i}", desc=_detailed(i), material="wrought iron") for i in range(8)]
    assert B.plan_quality_complaint(_plan(parts), budget) == ""
    thin = [_part(f"P{i}", material="wrought iron") for i in range(8)]
    both = B.plan_quality_complaint(_plan(thin), budget)
    assert "boxes at different sizes" in both and "same material" in both

    kids = [SubPartPlan(name="K", description="knurled collar", bbox=_bbox(ex=0.2, ey=0.2, ez=0.2))]
    parts = [_part(f"P{i}", material=f"m{i}", children=kids) for i in range(8)]
    assert B.plan_quality_complaint(_plan(parts), budget) == ""

    assert B.has_pass_detail("vignette, slight chromatic aberration, tonemap + gamma")
    assert B.has_pass_detail("40-60 blurred discs in 3 depth layers")
    assert not B.has_pass_detail("the background")
    assert not B.has_real_detail("a plain box")
    assert B.has_real_detail("tapered leg with 7 slats")


# ----------------------------------------------------------------------------- (1) brief
def _brief() -> EngineeringBrief:
    return EngineeringBrief(
        object_name="HandCrankCoffeeGrinder", reference="Peugeot 1920s cast-iron box grinder",
        one_line="a wooden box grinder with a cast-iron dome", dimensions_m=[RefDimension(name="width", meters=0.14), RefDimension(name="height", meters=0.30)],
        sub_assemblies=[SubAssembly(name="burr mechanism", purpose="grinds", parts=["burr", "shaft", "nut"])],
        mechanism="the crank turns the shaft which turns the burr",
        visible_from_outside=["the drawer seam", "the crank knob"], hidden_inside=["the spring"],
        not_present=["electric motor", "power cord"], signature_features=["open hopper", "front drawer", "top crank"],
        materials=["body: beech, warm brown"])


def test_brief_block_carries_every_lever_into_the_prompt():
    text = BR.brief_block(_brief())
    for needle in ("Peugeot", "burr mechanism", "does NOT have", "electric motor", "SIGNATURE FEATURES",
                   "open hopper", "NOT visible", "0.140"):
        assert needle in text
    assert BR.brief_block(None) == ""


def test_brief_is_cached_by_prompt_hash_and_the_second_call_is_free(tmp_path):
    calls = {"n": 0}

    def responder(req):
        calls["n"] += 1
        return json.loads(_brief().model_dump_json())

    model = FakeChatModel(responder)
    spec = _spec()
    first, u1 = BR.expand_brief(spec, "fake:planner", model=model, cache_dir=tmp_path)
    second, u2 = BR.expand_brief(spec, "fake:planner", model=model, cache_dir=tmp_path)
    assert calls["n"] == 1 and first == second and u2.cost_usd == 0.0
    # a different request is a different key
    BR.expand_brief(_spec("a violin"), "fake:planner", model=model, cache_dir=tmp_path)
    assert calls["n"] == 2


def test_an_image_run_does_not_drink_the_no_image_brief(tmp_path):
    """RS-4: the reference images are part of the brief cache key."""
    cache = tmp_path / "cache"
    img = tmp_path / "ref.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n-bytes-")
    calls: list[int] = []

    def responder(req):
        calls.append(sum(1 for part in req.messages[0].parts if isinstance(part, ImagePart)))
        return json.loads(_brief().model_dump_json())

    model = FakeChatModel(responder)
    plain = _spec()
    withref = plain.model_copy(update={"references": [ReferenceImage(path=str(img))]})
    BR.expand_brief(plain, "fake:planner", model=model, cache_dir=cache)
    BR.expand_brief(withref, "fake:planner", model=model, cache_dir=cache)

    assert calls == [0, 1]  # the reference run made its own, grounded call


def test_an_unreadable_reference_never_collapses_onto_no_references(tmp_path):
    missing = _spec().model_copy(update={"references": [ReferenceImage(path=str(tmp_path / "gone.png"))]})
    assert BR.brief_cache_key(missing, "m") != BR.brief_cache_key(_spec(), "m")


@pytest.mark.parametrize("error, billed", [
    (RuntimeError("503 storm"), 0.0),
    (ModelError("bad json", usage=Usage(backend="fake", cost_usd=0.002)), 0.002),
])
def test_a_failed_brief_is_never_fatal_and_keeps_what_the_provider_billed(tmp_path, error, billed):
    brief, usage = BR.expand_brief(_spec(), "fake:planner", model=FakeChatModel([error]), cache_dir=tmp_path)
    assert brief is None and usage.cost_usd == pytest.approx(billed)


def test_enrichment_adds_signature_features_as_SHOULD_items_only():
    plan = _plan([_part("P0")])
    B.enrich_plan(plan, _brief())
    sig = [a for a in plan.acceptance if a.id.startswith("sig")]
    assert len(sig) == 3 and all(a.priority == "should" for a in sig)
    assert "Peugeot" in plan.style_notes and "electric motor" in plan.style_notes


def test_graphics_pass_elements_are_folded_into_the_one_markdown_row_that_renders_them():
    """The fold into the passes table row is single-line and idempotent."""
    from codeverse3d.contracts.plan import PassPlan
    from codeverse3d.tracks.graphics import passes_table

    plan = GraphicsPlan(title="T", summary="s", style="st", passes=[PassPlan(
        name="Gears", kind="fullscreen", description="meshing gear train",
        elements=["7 gear wheels, 12-24 teeth", "brass grade | with a pipe"],
        detail_hint="adjacent radii in integer tooth ratios")])
    B.enrich_plan(plan, None)
    once = plan.passes[0].description
    B.enrich_plan(plan, None)
    assert plan.passes[0].description == once
    assert "\n" not in once and "|" not in once and "7 gear wheels" in once
    row = passes_table(plan).splitlines()[-1]
    assert row.count("|") == 4 and "integer tooth ratios" in row


# ----------------------------------------------------------------------------- the loop
def test_planner_spends_one_quality_reask_then_ships_the_plan(tmp_path, switch):
    """A plan that never satisfies the gate is still USED — a mediocre plan beats no plan."""
    switch("C3D_PLAN_BRIEF", "off")
    ws = Workspace(tmp_path / "run")
    ws.create()
    thin = json.loads(_plan([_part(f"P{i}") for i in range(3)]).model_dump_json())
    calls = {"n": 0, "complaints": []}

    def responder(req):
        calls["n"] += 1
        if len(req.messages) > 1:
            calls["complaints"].append(req.messages[-1].text)
        return thin

    spec = _spec(must=10)
    got = run_planner(spec, "fake:planner", StaticPlan, ws, model=FakeChatModel(responder))
    assert calls["n"] == 1 + MAX_QUALITY_REASKS
    assert "Only 3 parts" in calls["complaints"][0]
    assert len(got.parts) == 3 and ws.plan_path.is_file()

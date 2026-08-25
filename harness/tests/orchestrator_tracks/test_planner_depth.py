"""Planner depth: brief expansion, hierarchical sub-parts, plan budgets, quality gate.

Every test here is offline (fake chat models); the live A/B lives in the wave report.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from codeverse.contracts.chat import ImagePart
from codeverse.contracts.common import Backends, Language, Track
from codeverse.contracts.plan import (
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
from codeverse.contracts.spec import Constraints, ReferenceImage, Spec
from codeverse.tracks import brief as BR
from codeverse.tracks import plan_budget as B
from codeverse.tracks.planner import (
    MAX_QUALITY_REASKS,
    build_system_prompt,
    build_user_prompt,
    plan_example,
    plan_with_usage,
)
from codeverse.workspace import Workspace

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
def test_subpart_inside_parent_is_accepted_and_counted():
    p = _part("BurrMechanism", children=[
        SubPartPlan(name="Burr", description="conical burr, 24 cutting flutes", bbox=_bbox(ex=0.4, ey=0.4, ez=0.4)),
        SubPartPlan(name="Shaft", description="square shaft 10 mm", bbox=_bbox(cz=0.2, ex=0.1, ey=0.1, ez=0.5)),
        SubPartPlan(name="Nut", description="knurled nut", bbox=_bbox(cz=0.4, ex=0.2, ey=0.2, ez=0.1), instances=2),
    ])
    assert p.leaf_count == 4  # 1 + 1 + 2 copies
    assert _plan([p]).leaf_count == 4


def test_subpart_outside_parent_bbox_is_rejected_with_the_axis_and_the_overflow():
    with pytest.raises(ValidationError) as e:
        _part("Head", children=[SubPartPlan(name="Peg", description="peg", bbox=_bbox(cx=3.0, ex=0.2, ey=0.2, ez=0.2))])
    msg = str(e.value)
    assert "sticks" in msg and " x " in msg and "Peg" in msg and "grow the parent bbox" in msg


def test_subpart_within_the_relative_slack_is_tolerated():
    # planner boxes are design intent: a child may overhang by SUBPART_REL_SLACK × extent
    over = 0.5 + SUBPART_REL_SLACK * 0.9  # parent half-extent 0.5, child centre pushes it just inside
    _part("Head", bbox=_bbox(ex=1.0, ey=1.0, ez=1.0),
          children=[SubPartPlan(name="Peg", description="peg", bbox=_bbox(cx=over, ex=0.0, ey=0.2, ez=0.2))])


def test_duplicate_and_parent_colliding_subpart_names_are_rejected():
    with pytest.raises(ValidationError, match="duplicate sub-part name"):
        _part("Head", children=[SubPartPlan(name="Peg", description="a", bbox=_bbox(ex=0.2, ey=0.2, ez=0.2)),
                                SubPartPlan(name="peg", description="b", bbox=_bbox(ex=0.2, ey=0.2, ez=0.2))])
    with pytest.raises(ValidationError, match="repeats its parent"):
        _part("Peg", children=[SubPartPlan(name="Peg", description="a", bbox=_bbox(ex=0.2, ey=0.2, ez=0.2))])


def test_a_subpart_may_not_shadow_a_top_level_part_name():
    with pytest.raises(ValidationError, match="same name as top-level part"):
        _plan([_part("Body", children=[SubPartPlan(name="Lid", description="d", bbox=_bbox(ex=0.2, ey=0.2, ez=0.2))]),
               _part("Lid")])


def test_subparts_are_depth_1_by_construction():
    # SubPartPlan has no `children` field at all, so a cycle cannot even be expressed
    assert "children" not in SubPartPlan.model_fields


def test_every_recorded_plan_still_validates():
    """ADDITIVE-only contract change: plans written before sub-parts existed must load."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "bench" / "out"
    seen = 0
    for pj in sorted(root.glob("*/runs/*/plan.json")):
        raw = json.loads(pj.read_text())
        if "parts" not in raw or "root_link" in raw:
            continue  # articulated / graphics
        if any(k in raw["parts"][0] for k in ("children", "detail_hint")):
            continue  # a plan written after this wave landed — not a legacy sample
        p = StaticPlan.model_validate(raw)
        assert all(part.children == [] and part.detail_hint == "" for part in p.parts)
        seen += 1
        if seen >= 40:
            break
    if seen == 0:  # pragma: no cover — a checkout without bench/out
        pytest.skip("no recorded plans on disk")


# ----------------------------------------------------------------------------- (3) budgets
def test_a_short_checklist_is_not_evidence_so_the_count_trigger_stays_off():
    """"a stool" may be four parts: without a checklist or a brief the target is an
    aspiration the prompt states, not something the gate may reject a plan over."""
    thin = _plan([_part(f"P{i}", desc=_detailed(i), material=f"m{i}") for i in range(3)])
    assert not B.plan_budget(_spec(must=2)).evidence
    assert B.plan_quality_complaint(thin, B.plan_budget(_spec(must=2))) == ""
    assert "Only 3 parts" in B.plan_quality_complaint(thin, B.plan_budget(_spec(must=9)))


def test_budget_scales_with_the_checklist_and_is_capped_by_the_language():
    small = B.plan_budget(_spec(must=3))
    big = B.plan_budget(_spec(must=13))
    huge = B.plan_budget(_spec(must=40))
    assert small.target_parts == B.MIN_PARTS < big.target_parts == 13
    assert huge.target_parts == B.PART_CAP[Language.BLENDER.value]
    assert huge.cap_parts == B.PART_CAP[Language.BLENDER.value]
    # urdf links cost a joint + a collision pair each: a much lower ceiling
    assert B.plan_budget(_spec(must=40, track=Track.ARTICULATED_OBJECT,
                               language=Language.URDF_BLENDER)).cap_parts == B.PART_CAP[Language.URDF_BLENDER.value]


def test_budget_uses_the_brief_when_there_is_no_checklist():
    brief = _brief().model_copy(update={
        "signature_features": ["a", "b", "c"],
        "sub_assemblies": [SubAssembly(name=f"s{i}", parts=["p", "q", "r"]) for i in range(4)]})
    b = B.plan_budget(_spec(must=0), brief)
    assert b.target_parts == 7 and b.min_assemblies == 3 and "sub-assemblies" in b.reason


def test_graphics_budget_counts_passes_and_caps_at_six():
    b = B.plan_budget(_spec(must=6, track=Track.GRAPHICS, language=Language.GLSL_SHADER))
    assert b.cap_parts == B.GRAPHICS_CAP and b.target_parts == 5 and b.evidence
    assert "passes" in B.budget_block(b, unit="passes") and "`elements`" in B.budget_block(b, unit="passes")


def test_budget_block_reaches_the_planner_user_prompt():
    text = build_user_prompt(_spec(must=10), budget=B.plan_budget(_spec(must=10)))
    assert "PLAN BUDGET" in text and "10 checklist items" in text


def test_the_scene_track_gets_no_part_budget():
    """A scene is planned in zones and assets — "aim for 10 top-level parts" is nonsense
    there, and the quality gate has no parts to look at either."""
    from codeverse.contracts.plan import ScenePlan

    spec = _spec(must=10, track=Track.SCENE, language=Language.SCENE_THREEJS)
    assert "PLAN BUDGET" not in build_user_prompt(spec)
    scene = ScenePlan(title="T", summary="s", setting="x", bounds=_bbox(), environment="e",
                      zones=[{"name": "Z", "description": "d", "bbox": _bbox()}],
                      cameras=[{"name": "C", "position": (1, 1, 1), "look_at": (0, 0, 0)}])
    assert B.plan_quality_complaint(scene, B.plan_budget(spec)) == ""


def test_plan_templates_render_the_budget_numbers():
    for track, language, model in ((Track.STATIC_OBJECT, Language.BLENDER, StaticPlan),
                                   (Track.GRAPHICS, Language.GLSL_SHADER, GraphicsPlan)):
        spec = _spec(must=9, track=track, language=language)
        template = "tracks/plan_graphics.j2" if track is Track.GRAPHICS else None
        out = build_system_prompt(spec, model, template=template, example=plan_example(Track.STATIC_OBJECT),
                                  budget=B.plan_budget(spec))
        assert str(B.plan_budget(spec).target_parts) in out


# ----------------------------------------------------------------------------- (4) quality gate
def test_gate_fires_on_under_decomposition_and_names_the_number():
    budget = B.plan_budget(_spec(must=12))
    complaint = B.plan_quality_complaint(_plan([_part(f"P{i}", desc=_detailed(i)) for i in range(4)]), budget)
    assert "Only 4 parts" in complaint and "about 12" in complaint


def test_gate_fires_on_boxes_at_different_sizes_and_names_the_offenders():
    budget = B.plan_budget(_spec(must=8))
    complaint = B.plan_quality_complaint(_plan([_part(f"P{i}") for i in range(8)]), budget)
    assert "boxes at different sizes" in complaint and "P0" in complaint


def test_gate_stays_quiet_on_a_plan_that_meets_its_budget():
    budget = B.plan_budget(_spec(must=8))
    parts = [_part(f"P{i}", desc=_detailed(i), material=f"m{i}") for i in range(8)]
    assert B.plan_quality_complaint(_plan(parts), budget) == ""


def test_soft_complaints_never_fire_alone():
    """A wrought-iron arch really is all one material, and a chair really has no assembly."""
    budget = B.plan_budget(_spec(must=8))
    parts = [_part(f"P{i}", desc=_detailed(i), material="wrought iron") for i in range(8)]
    assert B.plan_quality_complaint(_plan(parts), budget) == ""
    thin = [_part(f"P{i}", material="wrought iron") for i in range(8)]
    both = B.plan_quality_complaint(_plan(thin), budget)
    assert "boxes at different sizes" in both and "same material" in both  # rides along with a hard one


def test_children_and_elements_count_as_detail():
    budget = B.plan_budget(_spec(must=8))
    kids = [SubPartPlan(name="K", description="knurled collar", bbox=_bbox(ex=0.2, ey=0.2, ez=0.2))]
    parts = [_part(f"P{i}", material=f"m{i}", children=kids) for i in range(8)]
    assert B.plan_quality_complaint(_plan(parts), budget) == ""


def test_graphics_passes_are_judged_by_technique_not_by_bevels():
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


def test_reference_images_are_part_of_the_brief_cache_key(tmp_path):
    """RS-4: expand_brief attaches spec.references and tells the model to read the
    dimensions off them, so a key that omits them let an --image run silently
    reuse a brief generated WITHOUT the image (and vice versa)."""
    img = tmp_path / "ref.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n-first-")
    plain = _spec()
    withref = plain.model_copy(update={"references": [ReferenceImage(path=str(img))]})

    assert BR.brief_cache_key(plain, "m") != BR.brief_cache_key(withref, "m")

    # --reference synthesises a NEW image per run under identical spec fields:
    # the path alone does not separate the two runs, the bytes must.
    before = BR.brief_cache_key(withref, "m")
    img.write_bytes(b"\x89PNG\r\n\x1a\n-second-")
    assert BR.brief_cache_key(withref, "m") != before

    # ... and the role/note that go into the image label matter too
    other = plain.model_copy(update={"references": [ReferenceImage(path=str(img), role="style")]})
    assert BR.brief_cache_key(other, "m") != BR.brief_cache_key(withref, "m")


def test_an_image_run_does_not_drink_the_no_image_brief(tmp_path):
    """The end-to-end shape of RS-4: two expand_brief calls, one model call each."""
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


def test_brief_failure_is_never_fatal(tmp_path):
    def boom(req):
        raise RuntimeError("503 storm")

    brief, usage = BR.expand_brief(_spec(), "fake:planner", model=FakeChatModel(boom), cache_dir=tmp_path)
    assert brief is None and usage.cost_usd == 0.0


def test_brief_switch_and_track_scope(monkeypatch):
    monkeypatch.delenv(BR.BRIEF_ENV, raising=False)
    assert BR.brief_enabled(_spec())
    assert not BR.brief_enabled(_spec(track=Track.GRAPHICS, language=Language.GLSL_SHADER))
    monkeypatch.setenv(BR.BRIEF_ENV, "off")
    assert not BR.brief_enabled(_spec())
    monkeypatch.setenv(BR.BRIEF_ENV, "on")
    assert BR.brief_enabled(_spec())


# ----------------------------------------------------------------------------- enrichment
def test_the_plan_keeps_subparts_typed_and_does_not_duplicate_them_into_the_description():
    """Depth is rendered from the typed fields by ``tracks/prompting.py``; enrichment must
    not copy it into ``description`` as well or the generation prompt prints it twice."""
    from codeverse.tracks.prompting import part_details

    part = _part("GrindHead", desc="cast iron housing", children=[
        SubPartPlan(name="Burr", description="conical burr, 24 flutes", bbox=_bbox(ex=0.4, ey=0.4, ez=0.4),
                    material="hardened steel"),
        SubPartPlan(name="Nut", description="knurled nut", bbox=_bbox(cz=0.4, ex=0.2, ey=0.2, ez=0.1), instances=2),
    ])
    part.detail_hint = "carries the visible mechanism"
    plan = _plan([part])
    B.enrich_plan(plan, None)
    assert plan.parts[0].description == "cast iron housing"
    rendered = part_details(plan)
    assert rendered.count("Burr") == 1 and rendered.count("carries the visible mechanism") == 1
    assert "hardened steel" in rendered and "Nut** ×2" in rendered


def test_enrichment_adds_signature_features_as_SHOULD_items_only():
    plan = _plan([_part("P0")])
    B.enrich_plan(plan, _brief())
    sig = [a for a in plan.acceptance if a.id.startswith("sig")]
    assert len(sig) == 3 and all(a.priority == "should" for a in sig)
    assert "Peugeot" in plan.style_notes and "electric motor" in plan.style_notes


def test_graphics_pass_elements_are_folded_into_the_one_markdown_row_that_renders_them():
    """``graphics_steps.passes_table`` prints only ``description`` — and it is a markdown
    table row, so the fold must be single-line and idempotent."""
    from codeverse.contracts.plan import PassPlan
    from codeverse.tracks.graphics_steps import passes_table

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


# ----------------------------------------------------------------------------- output room
def test_plan_output_room_grows_with_the_plan_and_is_capped():
    from codeverse.tracks.planner import PLAN_TOKENS_MAX, plan_tokens

    small, big = B.plan_budget(_spec(must=0)), B.plan_budget(_spec(must=20))
    assert 24000 < plan_tokens(small, 24000) < plan_tokens(big, 24000) <= PLAN_TOKENS_MAX
    assert plan_tokens(small, 24000) >= 24000  # never below the caller's floor


def test_a_truncated_plan_is_retried_with_more_room_instead_of_killing_the_run(tmp_path, monkeypatch):
    """Measured failure: the deeper plan overflowed the flat 24 000-token ceiling and the
    ModelError took the whole run down at the plan stage."""
    monkeypatch.setenv(BR.BRIEF_ENV, "off")
    from codeverse.models.base import ModelError

    ws = Workspace(tmp_path / "run")
    ws.create()
    good = json.loads(_plan([_part(f"P{i}", desc=_detailed(i), material=f"m{i}") for i in range(9)]).model_dump_json())
    seen: list[int] = []

    def responder(req):
        seen.append(req.max_output_tokens)
        if len(seen) == 1:
            raise ModelError("structured output unavailable (finish_reason=MAX_TOKENS; raise max_output_tokens)")
        return good

    plan, _u = plan_with_usage(_spec(must=8), "fake:planner", StaticPlan, ws, model=FakeChatModel(responder))
    assert len(seen) == 2 and seen[1] > seen[0] and len(plan.parts) == 9


def test_a_model_error_that_is_not_truncation_still_propagates(tmp_path, monkeypatch):
    monkeypatch.setenv(BR.BRIEF_ENV, "off")
    from codeverse.models.base import ModelError

    ws = Workspace(tmp_path / "run")
    ws.create()

    def responder(req):
        raise ModelError("every key is dead")

    with pytest.raises(ModelError, match="every key is dead"):
        plan_with_usage(_spec(), "fake:planner", StaticPlan, ws, model=FakeChatModel(responder))


# ----------------------------------------------------------------------------- the loop
def test_planner_spends_one_quality_reask_then_ships_the_plan(tmp_path, monkeypatch):
    """A plan that never satisfies the gate is still USED — a mediocre plan beats no plan."""
    monkeypatch.setenv(BR.BRIEF_ENV, "off")
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
    plan, _usage = plan_with_usage(spec, "fake:planner", StaticPlan, ws, model=FakeChatModel(responder))
    assert calls["n"] == 1 + MAX_QUALITY_REASKS
    assert "Only 3 parts" in calls["complaints"][0]
    assert len(plan.parts) == 3 and ws.plan_path.is_file()


def test_a_good_plan_costs_exactly_one_call(tmp_path, monkeypatch):
    monkeypatch.setenv(BR.BRIEF_ENV, "off")
    ws = Workspace(tmp_path / "run")
    ws.create()
    good = json.loads(_plan([_part(f"P{i}", desc=_detailed(i), material=f"m{i}") for i in range(9)]).model_dump_json())
    calls = {"n": 0}

    def responder(req):
        calls["n"] += 1
        return good

    plan, _u = plan_with_usage(_spec(must=8), "fake:planner", StaticPlan, ws, model=FakeChatModel(responder))
    assert calls["n"] == 1 and len(plan.parts) == 9


def test_brief_and_plan_are_one_model_and_the_events_say_so(tmp_path, monkeypatch):
    monkeypatch.setenv(BR.BRIEF_ENV, "on")
    monkeypatch.setenv("CV3D_CACHE_DIR", str(tmp_path / "cache"))
    ws = Workspace(tmp_path / "run")
    ws.create()
    good = json.loads(_plan([_part(f"P{i}", desc=_detailed(i), material=f"m{i}") for i in range(10)]).model_dump_json())
    seen = {"n": 0}

    def responder(req):
        seen["n"] += 1
        if req.label == "planner-brief":
            return json.loads(_brief().model_dump_json())
        assert "ENGINEERING BRIEF" in req.messages[0].text and "PLAN BUDGET" in req.messages[0].text
        return good

    class _Events:
        def __init__(self):
            self.rows = []

        def emit(self, name, **kw):
            self.rows.append((name, kw))

    ev = _Events()
    plan, _u = plan_with_usage(_spec(must=10), "fake:planner", StaticPlan, ws, model=FakeChatModel(responder), events=ev)
    assert seen["n"] == 2
    done = next(kw for name, kw in ev.rows if name == "plan.done")
    assert done["brief"] is True and done["target_parts"] == 10 and done["quality_reasks"] == 0
    assert any(name == "plan.brief" for name, _ in ev.rows)
    assert [a for a in plan.acceptance if a.id.startswith("sig")]

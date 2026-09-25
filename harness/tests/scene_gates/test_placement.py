"""scene_placement gate: census placement table → findings and hints (pure python over a fake census)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from codeverse3d.contracts.artifacts import BuildResult, Severity
from codeverse3d.contracts.common import Language
from codeverse3d.contracts.plan import AssetPlan, BBox, CameraPlan, ScenePlan, ZonePlan
from codeverse3d.spatial.scene_placement import (
    GATE,
    infer_indoor,
    placement_findings,
    placement_gate_safe,
    placement_table_text,
)
from codeverse3d.tracks.scene import ScenePipeline


def _row(name, zone="Yard", gap=0.0, support="Ground", sunk=0.0, into="", water=False, attached=(), h=1.0, exempt=""):
    return {"name": name, "zone": zone, "exempt": exempt, "meshes": 1, "columns": 3,
            "bbox": {"min": [0, 0, 0], "max": [1, h, 1], "size": [1, h, 1]}, "ground_gap_m": gap, "support": support,
            "sunk_m": sunk, "sunk_into": into, "on_water": water, "attached": list(attached)}


def _table(*rows, pairs=(), **over):
    t = {"assets": list(rows), "interpenetrations": list(pairs), "total": len(rows),
         "checked": sum(1 for r in rows if not r["exempt"]), "truncated": False, "exempt": {}, "ground_y": 0.0, "notes": []}
    t.update(over)
    return t


def _by_kind(report, kind):
    return [f for f in report.findings if f.data.get("kind") == kind]


def test_floating_severity_by_gap_then_by_count():
    r = placement_findings(_table(_row("Lantern", gap=0.3), _row("Cup", gap=0.08, support="Table")))
    fl = _by_kind(r, "floating")
    assert {f.target: f.severity for f in fl} == {"Yard/Lantern": Severity.ERROR, "Yard/Cup": Severity.WARN}
    lantern = next(f for f in fl if f.target == "Yard/Lantern")
    assert "floating 0.30 m above Ground" in lantern.message and "lower Yard/Lantern by 0.30 m" in lantern.fix_hint
    assert "userData.placement = 'free'" in lantern.fix_hint
    assert not r.passed
    # more than FLOATING_ERROR_COUNT small hovers → every one is an ERROR
    many = placement_findings(_table(*[_row(f"P{i}", gap=0.07) for i in range(4)]))
    assert all(f.severity == Severity.ERROR for f in _by_kind(many, "floating")) and not many.passed


@pytest.mark.parametrize("row", [
    _row("Sign", gap=0.9, attached=("Post",)),                  # mounted on what it touches
    _row("Rock", sunk=0.6, into="Terrain", h=1.0),              # partial-ok, 60 % buried
    _row("GardenStairways", sunk=1.3, into="Terrain", h=2.0),    # slope-ok: sunk uphill by construction
])
def test_the_gate_accepts_what_the_settle_leaves(row):
    """Audit 2026-09-24 N27: the settle left these (placement_words.json + the touching rule) and the
    gate then reported them every round.  One file now says what is seated."""
    r = placement_findings(_table(row))
    assert [f for f in r.findings if f.severity != Severity.INFO] == []


def test_indoor_tolerance_is_tighter():
    t = _table(_row("Vase", gap=0.03, support="Table"))
    assert _by_kind(placement_findings(t, indoor=False), "floating") == []
    assert _by_kind(placement_findings(t, indoor=False), "unsupported")[0].severity == Severity.WARN
    assert _by_kind(placement_findings(t, indoor=True), "floating")[0].severity == Severity.WARN
    assert infer_indoor("a cosy cabin interior at dusk") and not infer_indoor("mountain meadow at dawn")


def test_sunken_thresholds_height_fraction_and_name_words():
    r = placement_findings(_table(
        _row("Bench", sunk=0.15, into="Terrain", h=0.9),          # WARN: > 0.10 m
        _row("Statue", sunk=0.35, into="Terrain", h=2.0),         # WARN: > 0.30 m but only 17 % of its height
        _row("Lantern", sunk=0.4, into="Terrain", h=0.6),         # ERROR: > 0.30 m and > 25 %
        _row("Rock", sunk=0.45, into="Terrain", h=1.2),           # starter-scene rock: 37 % buried is fine
        _row("Boulder", sunk=0.8, into="Terrain", h=1.0),         # 80 % buried → ERROR even for a rock
        _row("PondBasin", sunk=1.4, into="Terrain", h=1.2),       # dug in by definition
        _row("OldWells", sunk=0.5, into="Terrain", h=1.0),        # plurals too (the settle's rule; was ERROR here)
        _row("SeaReefOutcrops", sunk=0.45, into="Terrain", h=1.0),  # partial-ok plural: 45 % buried is fine
        _row("Jetty", sunk=0.97, into="Terrain", h=1.1, water=True),   # posts in the pond bed
    ))
    sev = {f.target: f.severity for f in _by_kind(r, "sunken")}
    assert sev == {"Yard/Bench": Severity.WARN, "Yard/Statue": Severity.WARN, "Yard/Lantern": Severity.ERROR, "Yard/Boulder": Severity.ERROR}
    lantern = next(f for f in r.findings if f.target == "Yard/Lantern")
    assert "sunken 0.40 m into Terrain" in lantern.message and "raise Yard/Lantern by 0.40 m" in lantern.fix_hint


def test_findings_are_capped_per_kind_and_the_summary_counts_them():
    r = placement_findings(_table(*[_row(f"Pebble{i}", gap=0.06 + i * 0.01) for i in range(12)]))
    fl = _by_kind(r, "floating")
    assert len(fl) == 8 and fl[0].data["gap_m"] >= fl[-1].data["gap_m"]     # worst first
    assert any(f.data.get("kind") == "truncated" and "4 more floating" in f.message for f in r.findings)
    assert "8 floating" in r.findings[0].message


def test_probe_error_is_a_warning_that_passes():
    r = placement_findings({"error": "boom"})
    assert r.passed and r.findings[0].severity == Severity.WARN and "placement probe failed: boom" in r.findings[0].message


def _plan():
    bb = BBox(center=(0, 0, 0), extents=(10, 5, 10))
    return ScenePlan(title="t", summary="s", setting="meadow", mood="calm", bounds=bb, environment="sunny",
                     zones=[ZonePlan(name="Yard", description="d", bbox=bb, contents=[]), ZonePlan(name="House", description="d", bbox=bb, contents=[])],
                     assets=[], cameras=[CameraPlan(name="overview", position=(1, 1, 1), look_at=(0, 0, 0), fov=50, purpose="p")],
                     animation=[], effects=[])


def test_pipeline_gates_are_placement_alone_and_never_raise(tmp_path):
    from codeverse3d.workspace import Workspace

    plan = _plan()
    ctx = SimpleNamespace(runtime=SimpleNamespace(), plan=plan, extra={}, ws=Workspace(tmp_path / "run").create())
    build = BuildResult(ok=True, language="scene_threejs", census={"totals": {"meshes": 3}, "placement": _table(_row("Lantern", gap=0.3))})
    gates = ScenePipeline().gates(ctx, 0, build, None)
    assert [g.gate for g in gates] == [GATE] and not gates[-1].passed
    # no table (scene did not boot / old driver) → no placement gate at all
    assert [g.gate for g in ScenePipeline().gates(ctx, 0, BuildResult(ok=False, language="scene_threejs", census={}), None)] == []
    # a broken table is a WARN, not an exception
    broken = placement_gate_safe({"placement": {"assets": [{"no_name": 1}]}}, plan=plan)
    assert broken is not None and broken.passed and "placement probe failed" in broken.findings[0].message
    assert placement_gate_safe({}, plan=plan) is None


def test_check_placement_returns_the_round_gates_verdict(tmp_path):
    """The tool's verdict is the round gate's: same census, plan and stage records."""
    from codeverse3d.spatial.registry import ToolContext, get_tool
    from codeverse3d.workspace import Workspace

    ws = Workspace(tmp_path / "run").create()
    census = {"totals": {}, "fog": None, "placement": _table(_row("Lantern", gap=0.3), _row("Rock", sunk=0.3, into="Terrain", h=1.0))}
    (ws.artifacts / "census.json").write_text(json.dumps(census))
    plan = _contract_plan()
    ws.plan_path.write_text(plan.model_dump_json())
    ws.stages.mkdir(parents=True, exist_ok=True)
    (ws.stages / "assets.json").write_text(json.dumps(
        {"stage": "assets", "inputs_hash": "h", "result": {"Bench": {"name": "Bench", "kind": "threejs", "ok": False}}}))
    obs = get_tool("check_placement").call(ToolContext(workspace=ws, language=Language.SCENE_THREEJS.value), {})
    gate = ScenePipeline().gates(SimpleNamespace(ws=ws, plan=plan, extra={}), 0,
                                 BuildResult(ok=True, language="scene_threejs", census=census), None)[0]
    assert not obs.ok and not gate.passed and obs.numbers["errors"] == len(gate.errors)
    assert all(f.message in obs.text for f in gate.errors)
    kinds = {f.data.get("kind") for f in gate.findings}
    # Bench was never built: House (its only content) is neither missing it nor empty
    assert {"floating", "no_fog"} <= kinds and not {"missing_content", "zone_empty"} & kinds
    # an interior plan: the tighter tolerance is named, and the table text is what the agent reads
    ws.plan_path.write_text(plan.model_copy(update={"setting": "a cabin interior"}).model_dump_json())
    obs = get_tool("check_placement").call(ToolContext(workspace=ws, language=Language.SCENE_THREEJS.value), {})
    assert not obs.ok and "indoor tolerance 2 cm" in obs.text
    text = placement_table_text(census["placement"])
    assert "Yard/Lantern | +0.300 | Ground | 0.000 | - | - | -" in text and "Yard/Rock | +0.000 | Ground | 0.300 | Terrain" in text


# --------------------------------------------------------------------- plan-aware contract checks
def _contract_plan() -> ScenePlan:
    bb = BBox(center=(0, 0, 0), extents=(40, 8, 40))
    zb = BBox(center=(0, 0, 0), extents=(20, 8, 20))
    return ScenePlan(
        title="t", summary="s", setting="meadow", mood="calm", bounds=bb, environment="sunny",
        zones=[ZonePlan(name="Yard", description="d", bbox=zb, contents=["Lantern", "Bench"]),
               ZonePlan(name="House", description="d", bbox=zb, contents=["Bench"])],
        assets=[AssetPlan(name="Lantern", kind="threejs", description="d", approx_size_m=(0.4, 0.6, 0.4)),
                AssetPlan(name="Bench", kind="threejs", description="d", approx_size_m=(1.6, 0.9, 0.6))],
        cameras=[CameraPlan(name="overview", position=(1, 1, 1), look_at=(0, 0, 0), fov=50, purpose="p")])


def test_contract_checks_fire_on_atmosphere_contents_scale_and_bounds():
    far = _row("FarCrate")
    far["bbox"] = {"min": [100, 0, 100], "max": [101, 1, 101], "size": [1, 1, 1]}
    census = {"fog": None, "background": None,
              "placement": _table(_row("Lantern", h=3.0), far)}   # lantern 5x the planned 0.6 m
    rep = placement_gate_safe(census, plan=_contract_plan())
    kinds = {f.data.get("kind") for f in rep.findings}
    assert {"no_fog", "no_background", "missing_content", "zone_empty", "scale", "out_of_bounds"} <= kinds
    assert not rep.passed
    missing = next(f for f in rep.findings if f.data.get("kind") == "missing_content")
    assert missing.target == "Yard" and "Bench" in missing.message
    empty = next(f for f in rep.findings if f.data.get("kind") == "zone_empty")
    assert empty.target == "House"
    scale = next(f for f in rep.findings if f.data.get("kind") == "scale")
    assert scale.severity == Severity.ERROR and "5.0x" in scale.message


def test_a_wrapper_of_instances_is_scale_checked_as_one_instance():
    """The row's `families` carry the instance: a run of fence panels is not one 14 m panel."""
    bb = BBox(center=(0, 0, 0), extents=(40, 8, 40))
    plan = ScenePlan(
        title="t", summary="s", setting="headland", mood="blue hour", bounds=bb, environment="dusk",
        zones=[ZonePlan(name="Yard", description="d", bbox=BBox(center=(0, 0, 0), extents=(20, 8, 20)), contents=["PicketFence"])],
        assets=[AssetPlan(name="PicketFence", kind="threejs", description="d", approx_size_m=(2.4, 1.2, 0.1))],
        cameras=[CameraPlan(name="overview", position=(1, 1, 1), look_at=(0, 0, 0), fov=50, purpose="p")])
    run = _row("PicketFences", h=2.24)
    run["bbox"] = {"min": [0, 0, 0], "max": [7.3, 2.24, 14.73], "size": [7.3, 2.24, 14.73]}
    census = {"fog": {"type": "Fog", "near": 10, "far": 60}, "background": "#aabbcc", "placement": _table(run)}
    scale = [f for f in placement_gate_safe(census, plan=plan).findings if f.data.get("kind") == "scale"]
    assert len(scale) == 1 and scale[0].severity == Severity.ERROR and "6.1x" in scale[0].message

    run["families"] = {"PicketFence": {"n": 12, "size_m": 2.24}, "Picket": {"n": 72, "size_m": 1.2}}
    rep = placement_gate_safe(census, plan=plan)
    assert not [f for f in rep.findings if f.data.get("kind") == "scale"], [f.message for f in rep.findings]

    run["families"] = {"PicketFence": {"n": 12, "size_m": 9.6}}      # the instances really are 4x
    scale = [f for f in placement_gate_safe(census, plan=plan).findings if f.data.get("kind") == "scale"]
    assert len(scale) == 1 and scale[0].data["instances"] == 12
    assert scale[0].message.startswith("each of the 12 picket_fence instances in Yard/PicketFences measures 9.60 m")
    assert scale[0].fix_hint.startswith("scale each picket_fence")


def test_the_typed_interior_flag_wins_over_the_setting_words():
    from codeverse3d.spatial.scene_placement import is_interior

    assert is_interior({"setting": "a mountain meadow at dawn", "interior": True})
    assert not is_interior({"setting": "a mountain meadow at dawn", "interior": False})
    assert is_interior({"setting": "a cosy cabin interior at dusk"})          # no flag: the words decide
    assert is_interior(_contract_plan().model_copy(update={"interior": True}))


def test_fog_that_ends_inside_the_plan_is_warned_for_exteriors_only():
    plan = _contract_plan()                                            # 40 m bounds
    census = {"fog": {"type": "Fog", "near": 18, "far": 50, "density": None}, "background": "#aabbcc",
              "placement": _table(_row("Lantern", h=0.6), _row("Bench", h=0.9), _row("BenchB", zone="House", h=0.9))}
    short = [f for f in placement_gate_safe(census, plan=plan).findings if f.data.get("kind") == "fog_short"]
    assert len(short) == 1 and short[0].severity == Severity.WARN and "50 m ends inside the plan's 40 m world (1.2x)" in short[0].message
    census["fog"] = {"type": "Fog", "near": 30, "far": 90, "density": None}                      # 2.25 x: fine
    assert not [f for f in placement_gate_safe(census, plan=plan).findings if f.data.get("kind") == "fog_short"]
    census["fog"] = {"type": "FogExp2", "near": None, "far": None, "density": 0.06}             # dissolves within ~17 m
    assert [f for f in placement_gate_safe(census, plan=plan).findings if f.data.get("kind") == "fog_short"]
    inside = plan.model_copy(update={"interior": True})                                          # a room has no horizon
    assert not [f for f in placement_gate_safe(census, plan=inside).findings if f.data.get("kind") == "fog_short"]


def test_interpenetration_pairs_report_once_at_worst_overlap():
    pair = {"a": "Planter", "b": "Arbor", "zone_a": "Yard", "zone_b": "Yard"}
    r = placement_findings(_table(_row("Planter"), _row("Arbor"),
                                  pairs=[{**pair, "aabb_overlap": 0.3, "inside_frac": 0.2},
                                         {**pair, "aabb_overlap": 0.7, "inside_frac": 0.6},
                                         {**pair, "aabb_overlap": 0.5, "inside_frac": 0.4}]))
    inter = _by_kind(r, "interpenetration")
    assert len(inter) == 1 and "70%" in inter[0].message and inter[0].severity == Severity.ERROR


def test_density_gate_fires_on_a_zone_far_under_its_layout_budget():
    layouts = {"Yard": {"placements": [{"asset": "Lantern", "count": 6}],
                        "mid_props": 20, "small_props": 40, "ground_cover": 400},
               "House": {"placements": [], "mid_props": 2}}   # budget 2 < threshold: ignored
    census = {"fog": {"type": "Fog"}, "background": "#aabbcc",
              "groups": [{"name": "Yard", "instances": 30}, {"name": "House", "instances": 1}],
              "placement": _table(_row("Lantern", h=0.6))}
    rep = placement_gate_safe(census, plan=_contract_plan(), layouts=layouts)
    under = [f for f in rep.findings if f.data.get("kind") == "underdressed"]
    assert len(under) == 1 and under[0].target == "Yard"
    assert "466" in under[0].message and "~30" in under[0].message
    # the same zone with the budget met is quiet
    census["groups"][0]["instances"] = 240   # >= half of 466
    rep = placement_gate_safe(census, plan=_contract_plan(), layouts=layouts)
    assert not [f for f in rep.findings if f.data.get("kind") == "underdressed"]


def test_no_backdrop_fires_outdoors_and_stays_quiet_with_a_ring_or_indoors():
    base = {"fog": {"type": "Fog"}, "background": "#aabbcc",
            "placement": _table(_row("Lantern_3", h=0.6), _row("Bench", h=0.9),
                                _row("BenchB", zone="House", h=0.9))}
    near_only = dict(base, groups=[
        {"name": "Yard", "kind": "content", "bbox": {"min": [-10, 0, -10], "max": [10, 4, 10]}}])
    rep = placement_gate_safe(near_only, plan=_contract_plan(), layouts=None)
    hits = [f for f in rep.findings if f.data.get("kind") == "no_backdrop"]
    assert len(hits) == 1 and hits[0].target == "env" and "25 m" in hits[0].message
    # a silhouette ring past 1.25x the half-extent quiets it
    ringed = dict(base, groups=near_only["groups"] + [
        {"name": "BackdropHills", "kind": "content", "bbox": {"min": [-40, 0, -40], "max": [40, 5, 40]}}])
    rep = placement_gate_safe(ringed, plan=_contract_plan(), layouts=None)
    assert not [f for f in rep.findings if f.data.get("kind") == "no_backdrop"]
    # an interior never asks for one
    indoor = _contract_plan().model_copy(update={"setting": "a candlelit library interior"})
    rep = placement_gate_safe(near_only, plan=indoor, layouts=None)
    assert not [f for f in rep.findings if f.data.get("kind") == "no_backdrop"]


def test_a_group_or_row_with_a_null_box_is_skipped_not_a_crash():
    null_box = {"min": [None, None, None], "max": [None, None, None], "size": [None, None, None]}
    ghost = _row("Bench")
    ghost["bbox"] = null_box
    census = {"fog": None, "background": "#aabbcc", "placement": _table(_row("Lantern", h=3.0), ghost),
              "groups": [{"name": "HarbourBridgeZone", "kind": "content", "bbox": null_box},
                         {"name": "Yard", "kind": "content", "bbox": {"min": [-10, 0, -10], "max": [10, 4, 10]}}]}
    rep = placement_gate_safe(census, plan=_contract_plan())
    kinds = {f.data.get("kind") for f in rep.findings}
    assert {"no_fog", "scale", "no_backdrop"} <= kinds   # every check still ran around the null box
    assert not [f for f in rep.findings if "failed" in f.message.lower()]


def test_a_ring_of_identical_copies_round_the_world_is_warned_but_not_a_rotunda():
    """Columns round a rotunda sit inside the content and are architecture, not a stamped ring."""
    plan = _contract_plan()                                            # 40 m half-extent
    ring = {"name": "FarPeaks", "n": 16, "radius_m": 36.0, "radius_cv": 0.01, "gap_cv": 0.02, "size_cv": 0.0}
    census = {"fog": {"type": "Fog", "near": 10, "far": 120}, "background": "#aabbcc",
              "placement": _table(_row("Lantern", h=0.6), _row("Bench", h=0.9), _row("BenchB", zone="House", h=0.9)),
              "groups": [{"name": "FarShore", "kind": "content", "stamps": [ring]}]}
    hits = [f for f in placement_gate_safe(census, plan=plan).findings if f.data.get("kind") == "stamped_ring"]
    assert len(hits) == 1 and hits[0].severity == Severity.WARN and "FarShore/FarPeaks: 16 near-identical copies" in hits[0].message
    assert "36 m ring" in hits[0].message and "silhouettes" in hits[0].fix_hint
    census["groups"][0]["stamps"] = [dict(ring, size_cv=0.15)]                            # loop 24: a 15 % size jitter is still a stamp
    assert [f for f in placement_gate_safe(census, plan=plan).findings if f.data.get("kind") == "stamped_ring"]
    census["groups"][0]["stamps"] = [dict(ring, size_cv=0.3, radius_cv=0.2)]              # varied: a real skyline
    assert not [f for f in placement_gate_safe(census, plan=plan).findings if f.data.get("kind") == "stamped_ring"]
    census["groups"][0]["stamps"] = [dict(ring, name="Column", n=12, radius_m=4.0)]       # a rotunda: inside the content
    assert not [f for f in placement_gate_safe(census, plan=plan).findings if f.data.get("kind") == "stamped_ring"]


def test_blender_census_gets_bpy_words_in_placement_hints():
    """Same checks, same numbers; a scene_blender census (``census["language"]``) is told what to type in bpy."""
    t = _table(_row("Lantern", gap=0.3))
    three = placement_findings(t)
    bpy = placement_findings(t, language="scene_blender")
    f3, fb = _by_kind(three, "floating")[0], _by_kind(bpy, "floating")[0]
    assert f3.severity == fb.severity and f3.message == fb.message
    assert "userData.placement" in f3.fix_hint and "heightAt" in f3.fix_hint
    assert 'Lantern["placement"] = "free"' in fb.fix_hint and "ctx.height_at" in fb.fix_hint
    census = {"placement": t, "fog": None, "background": None, "language": "scene_blender"}
    gate = placement_gate_safe(census, plan={"title": "t", "setting": "outdoor", "zones": []})
    no_fog = next(f for f in gate.findings if f.data.get("kind") == "no_fog")
    assert "Volume" in no_fog.fix_hint and "THREE.Fog" not in no_fog.fix_hint

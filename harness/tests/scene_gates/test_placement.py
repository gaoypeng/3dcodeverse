"""scene_placement gate: census placement table → findings and hints (pure python over a fake census)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from codeverse3d.config import Placement, get_settings
from codeverse3d.contracts.artifacts import BuildResult, Severity
from codeverse3d.contracts.common import Language
from codeverse3d.contracts.plan import AssetPlan, BBox, CameraPlan, ScenePlan, ZonePlan
from codeverse3d.spatial.node import runtime_js_dir
from codeverse3d.spatial.scene_placement import (
    GATE,
    infer_indoor,
    placement_findings,
    placement_gate_safe,
    placement_table_text,
)
from codeverse3d.tracks.scene import ScenePipeline


def _row(name, zone="Yard", gap=0.0, support="Ground", sunk=0.0, into="", water=False, attached=(), h=1.0, exempt="",
         columns=3, gap_max=None, resting=None, tilt=None):
    row = {"name": name, "zone": zone, "exempt": exempt, "meshes": 1, "columns": columns,
           "bbox": {"min": [0, 0, 0], "max": [1, h, 1], "size": [1, h, 1]}, "ground_gap_m": gap, "support": support,
           "sunk_m": sunk, "sunk_into": into, "on_water": water, "attached": list(attached)}
    # the column measures host_placement.mjs added on 2026-10-14; a row without them is an older census
    for key, v in (("gap_max_m", gap_max), ("columns_resting", resting), ("tilt_m", tilt)):
        if v is not None:
            row[key] = v
    return row


def _table(*rows, pairs=(), **over):
    t = {"assets": list(rows), "interpenetrations": list(pairs), "total": len(rows),
         "checked": sum(1 for r in rows if not r["exempt"]), "truncated": False, "exempt": {}, "ground_y": 0.0, "notes": []}
    t.update(over)
    return t


def _by_kind(report, kind):
    return [f for f in report.findings if f.data.get("kind") == kind]


def test_floating_severity_by_gap_then_by_count():
    """Settings.placement (strict since 2026-10-14): a hover past floating_error_m 0.08 m is an ERROR,
    under it a WARN — and more than floating_error_count 2 hovers make every one an ERROR."""
    p = get_settings().placement
    assert (p.floating_error_m, p.floating_error_count) == (0.08, 2)
    r = placement_findings(_table(_row("Lantern", gap=0.3), _row("Cup", gap=0.07, support="Table")))
    fl = _by_kind(r, "floating")
    assert {f.target: f.severity for f in fl} == {"Yard/Lantern": Severity.ERROR, "Yard/Cup": Severity.WARN}
    lantern = next(f for f in fl if f.target == "Yard/Lantern")
    assert "floating 0.30 m above Ground" in lantern.message and "lower Yard/Lantern by 0.30 m" in lantern.fix_hint
    assert "userData.placement = 'free'" in lantern.fix_hint
    assert not r.passed
    # a 9 cm hover is past the strict line (a WARN under the 2026-09 0.15 m)
    assert _by_kind(placement_findings(_table(_row("Cup", gap=0.09, support="Table"))), "floating")[0].severity == Severity.ERROR
    # up to floating_error_count small hovers are WARNs; one more and every one is an ERROR
    two = placement_findings(_table(*[_row(f"P{i}", gap=0.07) for i in range(2)]))
    assert all(f.severity == Severity.WARN for f in _by_kind(two, "floating")) and two.passed
    three = placement_findings(_table(*[_row(f"P{i}", gap=0.07) for i in range(3)]))
    assert all(f.severity == Severity.ERROR for f in _by_kind(three, "floating")) and not three.passed


def test_thresholds_are_read_from_settings_when_the_gate_runs(switch):
    """An A/B child's ``C3D_PLACEMENT__*`` is what its rounds are judged by (read at call time, never
    at import); an explicit ``thresholds=`` wins over the process settings; the summary records them."""
    t = _table(_row("Lantern", gap=0.3))
    assert _by_kind(placement_findings(t), "floating")[0].severity == Severity.ERROR
    switch("C3D_PLACEMENT__FLOATING_ERROR_M", "0.5")
    lenient = placement_findings(t)
    assert _by_kind(lenient, "floating")[0].severity == Severity.WARN
    assert lenient.findings[0].data["thresholds"]["floating_error_m"] == 0.5
    assert "ERROR past 50 cm" in lenient.findings[0].message
    strict = placement_findings(t, thresholds=Placement(floating_error_m=0.1))
    assert _by_kind(strict, "floating")[0].severity == Severity.ERROR
    assert strict.findings[0].data["thresholds"]["floating_error_m"] == 0.1


def test_the_probe_s_overlap_floor_is_the_default_warn_level():
    """host_placement.mjs reports pairs past OVERLAP_MIN_FRAC and the gate WARNs past overlap_warn:
    the two are one number, or the strict WARN band would have nothing to read (0.20 → 0.10, D105)."""
    js = (runtime_js_dir() / "lib" / "host_placement.mjs").read_text(encoding="utf-8")
    assert f"export const OVERLAP_MIN_FRAC = {Placement().overlap_warn:.2f};" in js
    assert Placement().overlap_warn == 0.10 and Placement().overlap_error == 0.35


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
    """sunk_m 0.10 WARN; sunk_error_m 0.20 AND sunk_error_frac 20 % of the height ERROR (the strict
    2026-10-14 numbers; 0.30 + 25 % before); name words from placement_words.json."""
    r = placement_findings(_table(
        _row("Bench", sunk=0.15, into="Terrain", h=0.9),          # WARN: > 0.10 m but under 0.20
        _row("Statue", sunk=0.35, into="Terrain", h=2.0),         # WARN: > 0.20 m but only 17 % of its height
        _row("Crate", sunk=0.25, into="Terrain", h=1.0),          # ERROR: > 0.20 m and 25 % (a WARN under 0.30 + 25 %)
        _row("Lantern", sunk=0.4, into="Terrain", h=0.6),         # ERROR: > 0.20 m and > 20 %
        _row("Rock", sunk=0.45, into="Terrain", h=1.2),           # starter-scene rock: 37 % buried is fine
        _row("Boulder", sunk=0.8, into="Terrain", h=1.0),         # 80 % buried → ERROR even for a rock
        _row("PondBasin", sunk=1.4, into="Terrain", h=1.2),       # dug in by definition
        _row("OldWells", sunk=0.5, into="Terrain", h=1.0),        # plurals too (the settle's rule; was ERROR here)
        _row("SeaReefOutcrops", sunk=0.45, into="Terrain", h=1.0),  # partial-ok plural: 45 % buried is fine
        _row("Jetty", sunk=0.97, into="Terrain", h=1.1, water=True),   # posts in the pond bed
    ))
    sev = {f.target: f.severity for f in _by_kind(r, "sunken")}
    assert sev == {"Yard/Bench": Severity.WARN, "Yard/Statue": Severity.WARN, "Yard/Crate": Severity.ERROR,
                   "Yard/Lantern": Severity.ERROR, "Yard/Boulder": Severity.ERROR}
    lantern = next(f for f in r.findings if f.target == "Yard/Lantern")
    assert "sunken 0.40 m into Terrain" in lantern.message and "raise Yard/Lantern by 0.40 m" in lantern.fix_hint


def test_findings_are_capped_per_kind_and_the_summary_counts_them():
    r = placement_findings(_table(*[_row(f"Pebble{i}", gap=0.06 + i * 0.01) for i in range(12)]))
    fl = _by_kind(r, "floating")
    assert len(fl) == 8 and fl[0].data["gap_m"] >= fl[-1].data["gap_m"]     # worst first
    assert any(f.data.get("kind") == "truncated" and "4 more floating" in f.message for f in r.findings)
    # the summary counts what was MEASURED (the judge's measured block, the loop's diagnosis) and
    # says how many the per-kind cap left in the prompt
    summary = r.findings[0]
    assert "12 (8 listed) floating" in summary.message
    assert summary.data["counts"]["floating"] == 12 and summary.data["listed"]["floating"] == 8


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


def test_interpenetration_bands_are_the_strict_ones():
    """overlap_warn 0.10 / overlap_error 0.35 (0.20 / 0.60 before): a 12 % overlap is a WARN now
    that the probe measures it at all, a 40 % one an ERROR; an arm that raises overlap_warn
    filters what the probe reported below it."""
    rows = (_row("Crate"), _row("Wall"), _row("Bench"), _row("Tree"))
    pairs = [{"a": "Crate", "b": "Wall", "zone_a": "Yard", "zone_b": "Yard", "aabb_overlap": 0.12, "inside_frac": 0.11},
             {"a": "Bench", "b": "Tree", "zone_a": "Yard", "zone_b": "Yard", "aabb_overlap": 0.4, "inside_frac": 0.3}]
    sev = {f.target: f.severity for f in _by_kind(placement_findings(_table(*rows, pairs=pairs)), "interpenetration")}
    assert sev == {"Yard/Crate": Severity.WARN, "Yard/Bench": Severity.ERROR}
    hint = _by_kind(placement_findings(_table(*rows, pairs=pairs)), "interpenetration")[0].fix_hint
    assert "share ≤ 10%" in hint
    lenient = placement_findings(_table(*rows, pairs=pairs), thresholds=Placement(overlap_warn=0.3, overlap_error=0.6))
    assert {f.target: f.severity for f in _by_kind(lenient, "interpenetration")} == {"Yard/Bench": Severity.WARN}


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


# --------------------------------------------------------------------- the strict pass (2026-10-14)
def test_free_exempt_share_is_one_finding_naming_the_tagged_assets():
    """'free' exempts an asset from every check; w1_tearoom (argon) tagged 17 of 36.  The share of
    tagged assets among those that would otherwise be checked is a WARN past free_exempt_warn (25 %)
    and an ERROR past free_exempt_error (50 %), once at least two are tagged; per-language fix."""
    p = get_settings().placement
    assert (p.free_exempt_warn, p.free_exempt_error) == (0.25, 0.5)
    stood = [_row(f"Bench{i}") for i in range(6)]
    quiet = placement_findings(_table(*stood, _row("Lantern", exempt="free"), _row("Bird", exempt="free")))   # 2 of 8 = 25 %
    assert _by_kind(quiet, "free_exempt") == [] and quiet.findings[0].data["free"] == 2
    warn = placement_findings(_table(*stood, *[_row(f"Kite{i}", exempt="free") for i in range(3)]))         # 3 of 9 = 33 %
    f = _by_kind(warn, "free_exempt")
    assert len(f) == 1 and f[0].severity == Severity.WARN and f[0].target == "scene"
    assert "3 of 9 placed assets are tagged placement='free' (33%)" in f[0].message and "Yard/Kite0, Yard/Kite1, Yard/Kite2" in f[0].message
    assert "hanging / airborne things only" in f[0].fix_hint and "heightAt(x, z)" in f[0].fix_hint
    assert f[0].data["names"] == ["Yard/Kite0", "Yard/Kite1", "Yard/Kite2"] and f[0].data["share"] == 0.333
    error = placement_findings(_table(_row("Bench"), _row("Table"), *[_row(f"Prop{i}", exempt="free") for i in range(4)]),
                               language="scene_blender")                                                   # 4 of 6 = 67 %
    f = _by_kind(error, "free_exempt")
    assert f[0].severity == Severity.ERROR and not error.passed and 'obj["placement"] = "free"' in f[0].fix_hint
    assert "4 tagged free (67%)" in error.findings[0].message
    # one hanging lantern beside one bench is 50 % — the floor of two tagged rows keeps it quiet
    assert _by_kind(placement_findings(_table(_row("Bench"), _row("Lantern", exempt="free"))), "free_exempt") == []
    # the levels are Settings
    assert _by_kind(placement_findings(_table(*stood, _row("A", exempt="free"), _row("B", exempt="free")),
                                       thresholds=Placement(free_exempt_warn=0.2)), "free_exempt")[0].severity == Severity.WARN


def test_cantilevered_rests_on_one_of_four_columns():
    """Seated at its lowest column, hanging at the others (a crate seated by its lowest corner on a
    slope): a WARN naming the columns and the far hang; not for slope-following names, fewer than
    four columns, a hang within the floating tolerance, an attached asset, or an older row."""
    crate = _row("Crate", gap=0.0, columns=4, gap_max=0.36, resting=1, tilt=0.36)
    r = placement_findings(_table(crate))
    f = _by_kind(r, "cantilevered")
    assert len(f) == 1 and f[0].severity == Severity.WARN and r.passed
    assert f[0].message == "Yard/Crate rests on 1 of its 4 foot columns: the others hang up to 0.36 m above Ground (tilt 0.36 m)"
    assert "seat every foot" in f[0].fix_hint and "userData.placement = 'free'" in f[0].fix_hint
    assert f[0].data["gap_max_m"] == 0.36 and f[0].data["columns_resting"] == 1 and f[0].data["bbox"]["size"] == [1, 1, 1]
    assert "1 cantilevered" in r.findings[0].message and r.findings[0].data["counts"]["cantilevered"] == 1
    # a rock buried at one corner and hanging elsewhere is the same lean
    rock = _row("Rock", gap=0.0, sunk=0.05, into="Ground", columns=5, gap_max=0.3, resting=1, tilt=0.3)
    assert len(_by_kind(placement_findings(_table(rock)), "cantilevered")) == 1
    for quiet in (
        _row("GardenStairs", gap=0.0, columns=4, gap_max=0.36, resting=1, tilt=0.36),    # slope-ok: uneven by construction
        _row("Crate", gap=0.0, columns=3, gap_max=0.36, resting=1, tilt=0.36),           # an edge or a corner of three
        _row("Crate", gap=0.0, columns=4, gap_max=0.04, resting=1, tilt=0.04),           # within the floating tolerance
        _row("Crate", gap=0.0, columns=4, gap_max=0.36, resting=2, tilt=0.36),           # an edge rests
        _row("Shelf", gap=0.0, columns=4, gap_max=0.36, resting=1, tilt=0.36, attached=("Wall",)),   # mounted
        _row("Crate", gap=0.0, columns=4),                                                # a census from before the measure
    ):
        assert _by_kind(placement_findings(_table(quiet)), "cantilevered") == [], quiet["name"]
    # the hang threshold is the outdoor floating tolerance from Settings
    assert _by_kind(placement_findings(_table(crate), thresholds=Placement(floating_outdoor_m=0.4)), "cantilevered") == []


def test_every_asset_and_pair_finding_carries_its_box():
    """``data.bbox`` = GLB-frame centre + size from the row: the judge's contact crops aim at it."""
    far = _row("FarCrate")
    far["bbox"] = {"min": [100, 0, 100], "max": [101, 1, 101], "size": [1, 1, 1]}
    census = {"fog": {"type": "Fog"}, "background": "#aabbcc", "placement": _table(
        _row("Lantern", gap=0.3, h=3.0), _row("Cup", gap=0.03, support="Table"), _row("Bench", sunk=0.25, into="Terrain"),
        _row("Crate", gap=0.0, columns=4, gap_max=0.36, resting=1, tilt=0.36), _row("Planter"), _row("Arbor"), far,
        pairs=[{"a": "Planter", "b": "Arbor", "zone_a": "Yard", "zone_b": "Yard", "aabb_overlap": 0.5, "inside_frac": 0.4}])}
    rep = placement_gate_safe(census, plan=_contract_plan())
    per_asset = {"floating", "unsupported", "sunken", "cantilevered", "interpenetration", "scale", "out_of_bounds"}
    boxes = {f.data["kind"]: f.data.get("bbox") for f in rep.findings if f.data["kind"] in per_asset}
    assert set(boxes) == per_asset
    assert boxes["floating"] == {"center": [0.5, 1.5, 0.5], "size": [1.0, 3.0, 1.0]}       # the 3 m lantern
    assert boxes["out_of_bounds"] == {"center": [100.5, 0.5, 100.5], "size": [1.0, 1.0, 1.0]}
    assert all(b is not None and set(b) == {"center", "size"} for b in boxes.values()), boxes
    # a zone-level finding (House placed nothing) is about no one box and carries none
    assert next(f for f in rep.findings if f.data["kind"] == "zone_empty").data.get("bbox") is None
    # a row whose box is nulls (a census group with no geometry) carries None, not a crash
    ghost = _row("Ghost", gap=0.3)
    ghost["bbox"] = {"min": [None, None, None], "max": [None, None, None], "size": [None, None, None]}
    g = _by_kind(placement_findings(_table(ghost)), "floating")[0]
    assert g.data["bbox"] is None


def test_the_summary_records_the_thresholds_and_the_probe_s():
    t = _table(_row("Bench"), _row("Bird", exempt="free"), thresholds={"contact_m": 0.02, "floating_m": 0.05, "sunk_m": 0.1, "overlap_min": 0.1})
    s = placement_findings(t, indoor=True).findings[0]
    assert s.data["kind"] == "summary" and s.data["thresholds"]["indoor"] is True and s.data["thresholds"]["floating_m"] == 0.02
    assert s.data["thresholds"]["sunk_error_frac"] == 0.2 and s.data["probe_thresholds"]["overlap_min"] == 0.1
    assert s.data["free"] == 1 and s.data["free_share"] == 0.5 and s.data["counts"]["cantilevered"] == 0
    assert "1 tagged free (50%)" in s.message and "indoor tolerance 2 cm" in s.message

"""``bench/session_stats.py``: the tool-error / cache / cost view a battery is read through.

The numbers docs/COST.md §30 reports came from this view computed by hand, and the
after-side counts were inflated by the ``run/telemetry/trajectories`` symlink — the very
trap that section warns about.  These tests pin the parsing and the dedupe."""

from __future__ import annotations

import json
from pathlib import Path

from bench.session_stats import _sessions, report, round_costs, token_rates, tool_rates


def _session(run: Path, label: str, *, tools: dict[str, tuple[int, int]], prompt: int,
             cached: int, requests: int) -> None:
    d = run / "trajectories" / label
    d.mkdir(parents=True, exist_ok=True)
    (d / "stdout.json").write_text(json.dumps({"stats": {
        "models": {"gemini-3.7-flash": {"tokens": {"prompt": prompt, "cached": cached},
                                        "api": {"totalRequests": requests}}},
        "tools": {"byName": {f"mcp_3dcode_{name}": {"count": c, "fail": f}
                             for name, (c, f) in tools.items()} | {"read_file": {"count": 9, "fail": 9}}},
    }}))


def _run(root: Path, name: str, costs: list[float]) -> Path:
    run = root / name / "run"
    run.mkdir(parents=True, exist_ok=True)
    (run / "record.json").write_text(json.dumps(
        {"rounds": [{"index": i, "usage": {"cost_usd": c}} for i, c in enumerate(costs)]}))
    return run


def test_the_telemetry_symlink_is_counted_once(tmp_path: Path) -> None:
    run = _run(tmp_path, "cell_a", [1.0, 2.0])
    _session(run, "baseline_r00", tools={"build": (10, 1)}, prompt=1000, cached=700, requests=5)
    (run / "telemetry").mkdir()
    (run / "telemetry" / "trajectories").symlink_to(run / "trajectories", target_is_directory=True)

    assert len(_sessions(tmp_path)) == 1                     # not 2, though both paths exist
    calls, failed = tool_rates(_sessions(tmp_path))
    assert calls["build"] == 10 and failed["build"] == 1     # doubling here is what corrupted §30
    assert token_rates(_sessions(tmp_path))["requests"] == 5


def test_rates_and_costs_over_two_sessions(tmp_path: Path) -> None:
    run = _run(tmp_path, "cell_a", [1.0, 3.0, 5.0])
    _session(run, "baseline_r00", tools={"build": (10, 0), "joint_sweep": (4, 2)},
             prompt=1000, cached=700, requests=5)
    _session(run, "refine_r01", tools={"build": (6, 1)}, prompt=1000, cached=900, requests=5)

    calls, failed = tool_rates(_sessions(tmp_path))
    assert calls == {"build": 16, "joint_sweep": 4} and failed == {"build": 1, "joint_sweep": 2}
    tok = token_rates(_sessions(tmp_path))
    assert tok["cache_hit"] == 0.8 and tok["uncached_per_request"] == 40  # (2000-1600)/10
    assert round_costs(tmp_path) == [1.0, 3.0, 5.0]
    assert "| 3.000 |" in report([tmp_path])                 # the median round, not the mean


def test_a_killed_session_is_counted_as_killed_not_as_a_session(tmp_path: Path) -> None:
    """An empty stdout.json is the CLI dying before it printed its stats.  Counting it as a
    session makes every per-session number smaller for a reason that has nothing to do with
    the code under test — 102 of aa_articulated's 162 files are that."""
    from bench.session_stats import _killed

    run = _run(tmp_path, "cell_a", [2.0])
    _session(run, "baseline_r00", tools={"build": (3, 0)}, prompt=100, cached=50, requests=1)
    dead = run / "trajectories" / "refine_r01"
    dead.mkdir(parents=True)
    (dead / "stdout.json").write_text("")
    # the telemetry symlink reaches the dead session too: killed must not double either
    (run / "telemetry").mkdir()
    (run / "telemetry" / "trajectories").symlink_to(run / "trajectories", target_is_directory=True)

    assert len(_sessions(tmp_path)) == 1 and _killed(tmp_path) == 1
    calls, _ = tool_rates(_sessions(tmp_path))
    assert calls == {"build": 3}
    assert "| 1 | 1 |" in report([tmp_path])                  # sessions | killed


def test_a_sub_workspace_is_not_the_battery_cell_above_it(tmp_path: Path) -> None:
    """A scene asset's own run lives under ``_cand/`` / ``_assets/`` with its own
    trajectories; ``flywheel.record.find_runs`` skips those and so must this, or one cell's
    numbers absorb every candidate it rejected."""
    run = _run(tmp_path, "cell_a", [1.0])
    _session(run, "baseline_r00", tools={"build": (2, 0)}, prompt=100, cached=50, requests=1)
    sub = run / "_cand" / "c1" / "run"
    _session(sub, "baseline_r00", tools={"build": (99, 9)}, prompt=999, cached=0, requests=9)

    calls, failed = tool_rates(_sessions(tmp_path))
    assert dict(calls) == {"build": 2} and failed["build"] == 0


def test_the_coupling_survey_counts_degrees_of_freedom_not_joints(tmp_path: Path) -> None:
    """``bench/coupling_stats.py`` reads the mechanism claim off the recorded URDFs: what
    the sampler used to drive (every movable joint) against what it drives now (the
    independent ones).  Both are properties of the file, so no re-run is needed."""
    from bench.coupling_stats import per_prompt, report, survey

    urdf = """<?xml version="1.0"?>
<robot name="rig">
  <link name="base"/><link name="a"/><link name="b"/>
  <joint name="drive" type="revolute"><parent link="base"/><child link="a"/>
    <axis xyz="0 0 1"/><limit lower="0" upper="1" effort="1" velocity="1"/></joint>
  <joint name="follow" type="revolute"><parent link="a"/><child link="b"/>
    <axis xyz="0 0 1"/><limit lower="0" upper="1" effort="1" velocity="1"/>
    <mimic joint="drive" multiplier="1" offset="0"/></joint>
</robot>
"""
    cell = tmp_path / "cells" / "art_hard_umbrella" / "arm" / "run" / "artifacts"
    cell.mkdir(parents=True)
    (cell / "robot.urdf").write_text(urdf)
    (tmp_path / "cells" / "plain" / "arm" / "run" / "artifacts").mkdir(parents=True)
    (tmp_path / "cells" / "plain" / "arm" / "run" / "artifacts" / "robot.urdf").write_text(
        urdf.replace('<mimic joint="drive" multiplier="1" offset="0"/>', ""))

    s = survey(tmp_path)
    assert s["urdfs"] == 2 and s["prompts"] == 2 and s["coupled_prompts"] == 1
    assert s["coupled"] == [(2, 1)]                       # 2 movable joints, 1 degree of freedom
    assert "| 2 | 2 | 1 | 1 | 2 | 1 | 1 |" in report([tmp_path])
    assert "| art_hard_umbrella | 1 | 2 | 1 | 1 |" in per_prompt(tmp_path)


def test_the_coupling_survey_also_reads_the_gate_that_judges_the_poses(tmp_path: Path) -> None:
    """The mechanical counts say which poses were sampled; a coupled battery is run for
    what the ``joint_sweep`` gate then reports about them, so the survey prints both."""
    from bench.coupling_stats import gate_stats, report

    run = tmp_path / "cells" / "cpl_umbrella" / "arm" / "run"
    run.mkdir(parents=True)
    (run / "record.json").write_text(json.dumps({"rounds": [
        {"index": 0, "gates": [{"gate": "joint_sweep", "passed": False, "findings": [
            {"severity": "error", "message": "links a|b overlap"},
            {"severity": "warn", "message": "shallow rest overlap"}]}]},
        {"index": 1, "gates": [{"gate": "joint_sweep", "passed": True, "findings": []},
                               {"gate": "connectivity", "passed": False, "findings": [
                                   {"severity": "error", "message": "not this gate"}]}]},
    ]}))

    g = gate_stats(tmp_path)
    assert g == {"rounds": 2, "sweep_ran": 2, "sweep_failed": 1, "sweep_errors": 1}
    assert "| 2 | 2 | 1 | 1 | 0.50 |" in report([tmp_path])


def test_the_coupling_survey_counts_runs_not_copies_of_the_same_urdf(tmp_path: Path) -> None:
    """A run holds robot.urdf three times — src/ (written), artifacts/ (built) and
    deliverable/ (finalised) — and `bench run` lays cells out as runs/<id>/, not
    cells/<id>/.  Counting files tripled every mechanical number and named every prompt
    "artifacts"."""
    from bench.coupling_stats import _prompt_of, _urdfs

    urdf = """<?xml version="1.0"?>
<robot name="rig"><link name="base"/><link name="a"/>
  <joint name="drive" type="revolute"><parent link="base"/><child link="a"/>
    <axis xyz="0 0 1"/><limit lower="0" upper="1" effort="1" velocity="1"/></joint>
</robot>
"""
    run = tmp_path / "runs" / "cpl_umbrella"
    for where in ("src", "artifacts", "deliverable"):
        (run / where).mkdir(parents=True)
        (run / where / "robot.urdf").write_text(urdf)

    found = _urdfs(tmp_path)
    assert [p.parent.name for p in found] == ["artifacts"], "one URDF per run, the built one"
    assert _prompt_of(found[0]) == "cpl_umbrella"


def _sweep(pair: str, depth_m: float, *, poses: list[dict], worst_pose: dict) -> dict:
    """An aggregated `joint_sweep` penetration finding, in the shape `aggregate_findings`
    really emits: the pair is `target`, the worst depth is `data["max_depth_m"]`, and
    `data["pose"]` is the pose that worst depth was measured in ({} = rest).  The message
    is deliberately junk here — reading it was the bug."""
    return {"gate": "articulation", "severity": "error", "target": pair,
            "message": "(wording changes; nothing may parse this)",
            "data": {"kind": "penetration", "depth_m": depth_m, "max_depth_m": depth_m,
                     "pose": worst_pose, "poses": poses, "n_poses": len(poses)}}


def test_the_two_penetration_probes_are_compared_not_merged(tmp_path: Path) -> None:
    """The corroboration view is what stopped a threshold change: for every pair
    connectivity records at or over a candidate depth, did the same round's sweep flag it?
    Aligning the numbers would have created failures the dense probe does not support."""
    from bench.penetration_thresholds import corroborate

    rec = {"spec": {"id": "b/p", "track": "articulated_object"}, "rounds": [{
        "gates": [
            {"gate": "connectivity", "passed": True, "findings": [
                {"severity": "warn", "target": "knob", "data": {"kind": "penetration", "other": "post",
                                                                "depth_m": 0.004}},
                {"severity": "warn", "target": "arm", "data": {"kind": "penetration", "other": "deck",
                                                               "depth_m": 0.003}},
            ]},
            {"gate": "joint_sweep", "passed": False, "findings": [
                _sweep("arm|deck", 0.006, poses=[{}, {"j": 0.15}], worst_pose={"j": 0.15}),
                _sweep("rod|frame", 0.009, poses=[{"j": 0.3}], worst_pose={"j": 0.3}),
            ]},
        ]}]}

    c = corroborate([rec], 0.002)
    assert c["both"] == 1                      # arm|deck: both probes
    assert c["conn_only"] == 1                 # knob|post: a static contact only the sparse draw calls penetration
    assert c["sweep_only"] == 1                # rod|frame: the sparse draw never saw it
    assert c["examples"] == ["b/p: knob|post"]
    assert corroborate([rec], 0.005)["both"] == 0     # nothing that deep on the sparse side


def test_scene_stats_attributes_a_failure_to_a_layer(tmp_path: Path) -> None:
    """"The scene track does not work" is not actionable until the failure is attributed to
    a layer: a defect in the plan or in the assets follows any change to the assembly
    language, a defect in assembly does not."""
    from bench.scene_stats import layers, report, runs

    run = tmp_path / "runs" / "scn_easy_garden"
    run.mkdir(parents=True)
    (run / "record.json").write_text(json.dumps({
        "spec": {"id": "scenes_v1/scn_easy_garden", "track": "scene"},
        "plan": {"assets": [{"name": "Lantern", "kind": "threejs"},
                            {"name": "Bridge", "kind": "blender_glb"}]},
        "rounds": [{"index": 0, "gates": [
            {"gate": "scene_placement", "passed": False, "findings": [
                {"severity": "error", "message": "Lantern floats 0.4 m"}]},
            {"gate": "scene_frames", "passed": True, "findings": []}],
            "judgment": {"score": 0.3, "issues": [{"severity": "critical", "kind": "layout"}]}}],
    }))
    (run / "events.jsonl").write_text("\n".join(json.dumps(e) for e in [
        {"t": 1, "event": "plan.invalid", "attempt": 0},
        {"t": 2, "event": "plan.done", "n_zones": 3},
        {"t": 3, "event": "asset.generated", "asset": "Lantern", "ok": True, "strategy": "single-shot", "tris": 1200},
        {"t": 4, "event": "asset.generated", "asset": "Bridge", "ok": False, "strategy": "single-shot+repair"},
        {"t": 5, "event": "layout.done", "zone": "Court", "placements": 6, "reasked": True},
    ]))

    assert len(runs(tmp_path)) == 1
    d = layers(runs(tmp_path))
    assert d["plan"]["invalid"] == 1 and d["plan"]["zones"] == 3 and d["plan"]["layout_reask"] == 1
    assert d["assets"]["ok"] == 1 and d["assets"]["failed"] == 1
    assert dict(d["asset_kind"]) == {"threejs": 1, "blender_glb": 1}
    assert d["gates"]["scene_placement:FAILED"] == 1 and d["gate_errors"]["scene_placement"] == 1
    assert d["gates"]["scene_frames:FAILED"] == 0
    assert d["judge"]["critical/layout"] == 1

    text = report(tmp_path)
    assert "| scene_placement | 1 | 1 | 1 |" in text and "'threejs': 1" in text


def test_the_depth_comparison_is_restricted_to_one_pose() -> None:
    """`survey`'s two-probe table used to compare the sweep's worst-over-ALL-poses depth
    with connectivity's rest-pose depth, so "2-3x deeper on the same pairs" was not a
    statement about one pose at all.  Over the 374-round corpus, restricted to the pairs
    whose worst sweep pose IS rest, the two probes agree exactly (4.4/4.4, 4.4/4.4,
    2.2/2.2, 2.5/2.5 mm)."""
    from bench.penetration_thresholds import survey, sweep_pairs

    moved = _sweep("a|b", 0.0088, poses=[{}, {"j": 0.15}], worst_pose={"j": 0.15})
    at_rest = _sweep("c|d", 0.0044, poses=[{}], worst_pose={})
    gate = {"gate": "joint_sweep", "findings": [moved, at_rest]}

    assert set(sweep_pairs(gate, rest_only=False)) == {("a", "b"), ("c", "d")}
    assert set(sweep_pairs(gate, rest_only=True)) == {("c", "d")}, "the 8.8 mm is a moved pose"

    rec = {"spec": {"id": "b/p", "track": "articulated_object"}, "rounds": [{"gates": [
        {"gate": "connectivity", "passed": True, "findings": [
            {"severity": "warn", "target": "c", "data": {"kind": "penetration", "other": "d", "depth_m": 0.0044}},
            {"severity": "warn", "target": "a", "data": {"kind": "penetration", "other": "b", "depth_m": 0.0030}},
        ]},
        gate,
    ]}]}
    s = survey([rec])
    assert s["sweep_findings"] == 2 and s["sweep_distinct"] == 2
    assert [(p, sw, cn) for _, p, sw, cn in s["both"]] == [("c|d", 0.0044, 0.0044)], \
        "only the rest-worst pair is comparable, and there the two probes agree"


def test_a_finding_whose_message_changes_still_counts() -> None:
    """The whole point of reading `target` / `data`: a wording change in
    `aggregate_findings` used to silently zero this report."""
    from bench.penetration_thresholds import sweep_pairs

    f = _sweep("x|y", 0.01, poses=[{}], worst_pose={})
    f["message"] = "completely different wording"
    assert sweep_pairs({"gate": "joint_sweep", "findings": [f]}, rest_only=False) == {("x", "y"): 0.01}

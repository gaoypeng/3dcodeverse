"""`bench/skill_targets.py`: the per-skill deterministic readout over a recorded battery or A/B dir.
(The claims table itself is tested with the harness: harness/tests/skills/test_targets.py.)"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from codeverse3d.addons.skill_targets import TARGETS


# --------------------------------------------------------------------------- the readout
def _record(language: str, gates: list[dict], *, build_ok: bool = True) -> dict:
    return {"spec": {"language": language}, "final_score": 0.5, "status": "plateau",
            "rounds": [{"index": 0, "gates": gates, "build": {"ok": build_ok}}]}


def _battery(tmp_path: Path, cells: dict[str, dict]) -> Path:
    root = tmp_path / "battery"
    for name, rec in cells.items():
        d = root / "runs" / name
        d.mkdir(parents=True)
        (d / "record.json").write_text(json.dumps(rec))
    return root


def _finding(gate: str, message: str, severity: str = "warn") -> dict:
    return {"gate": gate, "severity": severity, "message": message}


CONNECTIVITY = [_finding("connectivity", "'Lid' and 'Body' interpenetrate by ≈7.4 mm (12% of surface samples inside)"),
                _finding("connectivity", "'Leg_0' and 'Seat' interpenetrate by ≈2.1 mm (3% of surface samples inside)"),
                _finding("connectivity", "all 7 parts are connected (9 contacts, gap ≤ 1 mm)", "info")]
CONTRACT = [_finding("contract", "part 'Seat' bbox deviates from the plan (worst 2.1× tolerance)"),
            _finding("contract", "'Leg' (each instance) bbox deviates from the plan (worst 1.4× tolerance)")]


def test_the_readout_counts_the_kinds_the_row_names_over_the_graded_runs_it_is_defined_for(tmp_path):
    """A concurrency probe or an e2e smoke run has no gate report; counting it would give
    a GLB-derived row a different n from the gate-derived rows beside it."""
    from bench.skill_targets import battery_rows, load_runs

    root = _battery(tmp_path, {
        "chair": _record("blender", [{"gate": "connectivity", "findings": CONNECTIVITY},
                                     {"gate": "contract", "findings": CONTRACT}]),
        "lamp": _record("blender", [{"gate": "connectivity", "findings": CONNECTIVITY[2:]},
                                    {"gate": "contract", "findings": []}]),
        "robot": _record("urdf_blender", [{"gate": "joint_sweep", "findings": [
            _finding("joint_sweep", "links 'a' and 'b' overlap by 6.0 mm at pose j=1.0", "error"),
            _finding("joint_sweep", "links 'a' and 'c' overlap by 3.0 mm at rest", "warn")]}]),
        "probe": {"spec": {"language": "blender"}, "rounds": [{"index": 0, "gates": []}]},
    })
    rows = {r["skill"]: r for r in battery_rows(load_runs(root), list(TARGETS), which="last", cache=None)}
    assert rows["c3d-part-contact"]["n"] == 3               # both languages carry a part graph; the probe is out
    assert rows["c3d-part-contact"]["mean"] == pytest.approx(2 / 3)        # 2 pairs + 0 + 0, INFO ignored
    assert rows["c3d-bbox-contract"]["mean"] == pytest.approx(1 / 3)       # part_bbox only: chair 1
    assert rows["c3d-repeats-and-mirrors"]["mean"] == pytest.approx(1 / 3)  # instance_bbox only: chair 1
    assert rows["c3d-urdf-joints"]["n"] == 1               # the blender runs are not eligible
    assert rows["c3d-urdf-joints"]["mean"] == 1.0          # ERRORs only; the rest-pose WARN is not one


def test_the_ab_mode_pairs_by_prompt(tmp_path):
    from bench.skill_targets import ab_arms, ab_rows

    root = tmp_path / "ab"
    for arm, findings in (("control", CONNECTIVITY), ("variant", CONNECTIVITY[1:])):
        d = root / "arms" / arm / "cells" / "chair" / "run"
        d.mkdir(parents=True)
        (d / "record.json").write_text(json.dumps(
            _record("blender", [{"gate": "connectivity", "findings": findings}])))
        # a provider outage leaves `run_a` and `run_a.attempt1` side by side in one cell: pairing
        # the ungraded one would drop a prompt that actually ran
        cell = root / "arms" / arm / "cells" / "stool"
        for leaf, rec in (("run_a", {"spec": {"language": "blender"}, "rounds": []}),
                          ("run_a.attempt1",
                           _record("blender", [{"gate": "connectivity", "findings": CONNECTIVITY}]))):
            (cell / leaf).mkdir(parents=True)
            (cell / leaf / "record.json").write_text(json.dumps(rec))
    arms = ab_arms(root)
    assert arms is not None
    rows = {r["skill"]: r for r in ab_rows(arms, list(TARGETS), which="last", cache=None)}
    r = rows["c3d-part-contact"]
    assert (r["n"], r["control_mean"], r["variant_mean"], r["mean_delta"]) == (2, 2.0, 1.5, -0.5)
    assert (r["better"], r["worse"], r["tied"]) == (1, 0, 1)


def test_the_cli_runs_over_a_synthetic_battery(tmp_path, capsys):
    from bench.skill_targets import main

    root = _battery(tmp_path, {"chair": _record("blender", [{"gate": "connectivity", "findings": CONNECTIVITY}])})
    assert main([str(root), "--json", "--no-cache"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "battery"
    row = next(r for r in payload["rows"] if r["skill"] == "c3d-part-contact")
    assert row["n"] == 1 and row["mean"] == 2.0


def test_the_confidence_block_says_how_many_pairs_an_effect_needs():
    from bench.skill_targets import confidence
    from bench.stats import t975

    c = confidence([-1.0, 1.0, -1.0, 1.0], control_mean=4.0)
    assert c["sd"] == pytest.approx(1.1547, rel=1e-3)
    assert c["ci95"] == pytest.approx(t975(3) * c["se"])    # the 95 % t-interval, 3 df
    assert c["resolvable_effect"] == 1.0                     # 25% of a control mean of 4
    assert c["n_to_resolve"] == 8                            # first n with t(n-1)*1.1547/sqrt(n) <= 1.0
    assert confidence([1.0], control_mean=4.0)["sd"] is None


def test_the_shader_preflight_row_reads_the_gate_not_the_raw_driver_json(tmp_path):
    """The raw driver report has errors / warnings and no findings; the row read it and
    counted 0 on every run.  It reads the round's shader_preflight gate, else the last
    build's gate file."""
    from bench.skill_targets import battery_rows, load_runs

    warn = _finding("shader_preflight", "ShaderMaterial 'Neon' ignores scene.fog")
    root = _battery(tmp_path, {
        "in_round": _record("scene_threejs", [{"gate": "shader_preflight", "findings": [warn, warn]}]),
        "on_disk": _record("scene_threejs", [{"gate": "scene_placement", "findings": []}]),
    })
    gate_dir = root / "runs" / "on_disk" / "artifacts" / "gates"
    gate_dir.mkdir(parents=True)
    (gate_dir / "shader_preflight.json").write_text(json.dumps({"gate": "shader_preflight", "findings": [warn]}))
    (gate_dir.parent / "shader_preflight.json").write_text(json.dumps({"errors": [], "warnings": ["x"]}))
    rows = {r["skill"]: r for r in battery_rows(load_runs(root), list(TARGETS), which="last", cache=None)}
    assert rows["c3d-threejs-shader-traps"]["n"] == 2
    assert rows["c3d-threejs-shader-traps"]["mean"] == 1.5   # 2 in the round, 1 in the gate file


def test_a_glb_row_measures_the_measured_rounds_own_glb(tmp_path, monkeypatch):
    """`--round first` read artifacts/object.glb — the LAST build's — for a GLB-derived metric."""
    import bench.skill_targets as st
    from codeverse3d.addons.skill_targets import SRC_GLB, Target

    gates = [{"gate": "connectivity", "findings": CONNECTIVITY}]
    root = _battery(tmp_path, {"chair": {"spec": {"language": "blender"}, "rounds": [
        {"index": i, "gates": gates, "build": {"ok": True}} for i in (0, 1)]}})
    run_dir = root / "runs" / "chair"
    for name, byte in (("r00/object.glb", 10), ("r01/object.glb", 11), ("object.glb", 99)):
        (run_dir / "artifacts" / name).parent.mkdir(parents=True, exist_ok=True)
        (run_dir / "artifacts" / name).write_bytes(bytes([byte]))
    monkeypatch.setattr(st, "_feature_density", lambda glb: float(glb.read_bytes()[0]))
    target = Target(skill="x", metric="feature_density", direction="up", unit="", source=SRC_GLB, why="")
    run = st.load_runs(root)[0]
    assert st.measure(target, run, which="first") == 10.0
    assert st.measure(target, run, which="last") == 11.0


def test_a_typed_finding_counts_by_its_kind_and_an_old_record_by_its_shape():
    """N52: the harness routes on ``data.kind``; the readout counts the same kind, and reads a
    record written before its gate typed the finding by the old message shape."""
    from bench.skill_targets import count_kinds

    typed = {"gate": "contract", "severity": "warn", "message": "reworded", "data": {"kind": "part_bbox"}}
    camera = {"gate": "scene_frames", "severity": "warn", "message": "x", "data": {"kind": "camera_below_high_ground"}}
    rd = {"gates": [{"gate": "contract", "findings": [typed, *CONTRACT]},
                    {"gate": "scene_frames", "findings": [camera]}]}
    assert count_kinds(rd, ("contract/part_bbox",), ()) == 2
    assert count_kinds(rd, ("scene_frames/camera_*",), ()) == 1

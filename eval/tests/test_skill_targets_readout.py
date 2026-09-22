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


def test_the_readout_counts_the_kinds_the_row_names(tmp_path):
    from bench.skill_targets import battery_rows, load_runs

    root = _battery(tmp_path, {
        "chair": _record("blender", [{"gate": "connectivity", "findings": CONNECTIVITY},
                                     {"gate": "contract", "findings": CONTRACT}]),
        "lamp": _record("blender", [{"gate": "connectivity", "findings": CONNECTIVITY[2:]},
                                    {"gate": "contract", "findings": []}]),
    })
    rows = {r["skill"]: r for r in battery_rows(load_runs(root), list(TARGETS), which="last", cache=None)}
    assert rows["c3d-part-contact"]["n"] == 2
    assert rows["c3d-part-contact"]["mean"] == 1.0          # 2 pairs + 0, INFO ignored
    assert rows["c3d-bbox-contract"]["mean"] == 0.5         # part_bbox only
    assert rows["c3d-repeats-and-mirrors"]["mean"] == 0.5   # instance_bbox only


def test_a_row_only_counts_the_languages_it_is_defined_over(tmp_path):
    from bench.skill_targets import battery_rows, load_runs

    root = _battery(tmp_path, {
        "robot": _record("urdf_blender", [{"gate": "joint_sweep", "findings": [
            _finding("joint_sweep", "links 'a' and 'b' overlap by 6.0 mm at pose j=1.0", "error"),
            _finding("joint_sweep", "links 'a' and 'c' overlap by 3.0 mm at rest", "warn")]}]),
        "chair": _record("blender", [{"gate": "connectivity", "findings": CONNECTIVITY}]),
    })
    rows = {r["skill"]: r for r in battery_rows(load_runs(root), list(TARGETS), which="last", cache=None)}
    assert rows["c3d-urdf-joints"]["n"] == 1               # the blender run is not eligible
    assert rows["c3d-urdf-joints"]["mean"] == 1.0          # ERRORs only; the rest-pose WARN is not one
    assert rows["c3d-part-contact"]["n"] == 2              # both languages carry a part graph


def test_an_ungraded_run_is_not_in_the_population(tmp_path):
    """A concurrency probe or an e2e smoke run has no gate report; counting it would give
    a GLB-derived row a different n from the gate-derived rows beside it."""
    from bench.skill_targets import battery_rows, load_runs

    root = _battery(tmp_path, {
        "chair": _record("blender", [{"gate": "connectivity", "findings": CONNECTIVITY}]),
        "probe": {"spec": {"language": "blender"}, "rounds": [{"index": 0, "gates": []}]},
    })
    rows = {r["skill"]: r for r in battery_rows(load_runs(root), list(TARGETS), which="last", cache=None)}
    assert rows["c3d-part-contact"]["n"] == 1


def test_the_ab_mode_pairs_by_prompt(tmp_path):
    from bench.skill_targets import ab_arms, ab_rows

    root = tmp_path / "ab"
    for arm, findings in (("control", CONNECTIVITY), ("variant", CONNECTIVITY[1:])):
        d = root / "arms" / arm / "cells" / "chair" / "run"
        d.mkdir(parents=True)
        (d / "record.json").write_text(json.dumps(
            _record("blender", [{"gate": "connectivity", "findings": findings}])))
    arms = ab_arms(root)
    assert arms is not None
    rows = {r["skill"]: r for r in ab_rows(arms, list(TARGETS), which="last", cache=None)}
    r = rows["c3d-part-contact"]
    assert (r["n"], r["control_mean"], r["variant_mean"], r["mean_delta"]) == (1, 2.0, 1.0, -1.0)
    assert (r["better"], r["worse"], r["tied"]) == (1, 0, 0)


def test_a_battery_dir_is_not_mistaken_for_an_ab_dir(tmp_path):
    from bench.skill_targets import ab_arms

    root = _battery(tmp_path, {"chair": _record("blender", [])})
    assert ab_arms(root) is None


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


def test_a_retried_cell_pairs_on_the_attempt_that_reached_a_gate(tmp_path):
    """A provider outage leaves `...flash` and `...flash.attempt1` side by side in one cell;
    pairing the ungraded one would drop a prompt that actually ran."""
    from bench.skill_targets import ab_arms, ab_rows

    root = tmp_path / "ab"
    for arm in ("control", "variant"):
        cell = root / "arms" / arm / "cells" / "chair"
        for leaf, rec in (("run_a", {"spec": {"language": "blender"}, "rounds": []}),
                          ("run_a.attempt1",
                           _record("blender", [{"gate": "connectivity", "findings": CONNECTIVITY}]))):
            (cell / leaf).mkdir(parents=True)
            (cell / leaf / "record.json").write_text(json.dumps(rec))
    rows = {r["skill"]: r for r in ab_rows(ab_arms(root), list(TARGETS), which="last", cache=None)}
    assert rows["c3d-part-contact"]["n"] == 1


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

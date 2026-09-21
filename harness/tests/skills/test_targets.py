"""Keep bundle target claims complete and the benchmark readout honest."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

from codeverse.addons.skill_targets import (
    BY_SKILL,
    DIRECTIONS,
    METRICS,
    SOURCES,
    SRC_GATE,
    TARGETS,
    gate_kinds_claimed,
    target_for,
)
from codeverse.skills import bundle_dirs, skills_dir
from codeverse.skills.registry import ROUTED_SKILLS, finding_kind

BUNDLES = bundle_dirs()
pytestmark = pytest.mark.skipif(not BUNDLES, reason=f"no bundles in {skills_dir()} yet")


def _live_kinds() -> set[str]:
    from codeverse.skills import registry

    return {v for k, v in vars(registry).items()
            if k.isupper() and isinstance(v, str) and "/" in v}


def _frontmatter(d: Path) -> dict:
    text = (d / "SKILL.md").read_text(encoding="utf-8")
    m = re.match(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n", text, re.S)
    assert m, f"{d.name}: no frontmatter"
    return (yaml.safe_load(m.group(1)) or {}).get("metadata") or {}


# --------------------------------------------------------------------------- the table
def test_target_table_exactly_covers_the_shipped_and_routed_bundles():
    """No orphan rows, unmeasured routes, duplicate rows or bundles maintained by taste."""
    have = {d.name for d in BUNDLES}
    assert len(BY_SKILL) == len(TARGETS), "a bundle has more than one target row"
    assert set(BY_SKILL) == have == set(ROUTED_SKILLS)


def test_every_gate_kind_a_target_counts_is_a_live_kind():
    unknown = sorted(gate_kinds_claimed() - _live_kinds())
    assert unknown == [], (
        f"targets.py counts gate kind(s) registry.py no longer defines: {unknown} — the "
        f"readout would report a silent zero")


def test_target_rows_and_bundle_metadata_are_complete():
    for target in TARGETS:
        assert target.direction in DIRECTIONS, target.skill
        assert target.source in SOURCES, target.skill
        assert target.unit and target.why, f"{target.skill}: target needs a unit and rationale"
        if target.source == SRC_GATE:
            assert target.kinds, f"{target.skill}: a gate target needs finding kinds"
        if not target.measurable:
            assert target.caveat, f"{target.skill}: an unmeasurable target needs a caveat"

    for bundle in BUNDLES:
        metadata, target = _frontmatter(bundle), target_for(bundle.name)
        assert target is not None, bundle.name
        assert metadata.get("target_metric") == target.metric, bundle.name
        assert metadata.get("target_direction") == target.direction, bundle.name
        assert metadata.get("target_unit") == target.unit, bundle.name
        assert metadata.get("target_measurable") == ("true" if target.measurable else "false"), (
            bundle.name
        )
        baseline = str(metadata.get("target_baseline") or "").strip()
        assert baseline, f"{bundle.name}: target_baseline is missing"
        assert re.search(r"\bn\s*=\s*\d+", baseline), (
            f"{bundle.name}: target_baseline must state n=<runs>"
        )


def test_no_two_bundles_claim_the_same_quantity():
    """Two bundles sharing a metric means an A/B cannot say which one moved it."""
    assert len(METRICS) == len(TARGETS)
    seen: dict[str, str] = {}
    for t in TARGETS:
        for k in t.kinds:
            assert k not in seen, f"{t.skill} and {seen[k]} both count {k}"
            seen[k] = t.skill


# --------------------------------------------------------------------------- classification
def test_every_actionable_gl_frames_message_classifies():
    messages = (
        "very large frame-to-frame change (max |Δ| 0.412); flicker or hard cuts",
        "very low visual detail (edge density 0.0014); the image is a near-flat gradient",
        "frames do not change over time (mean |Δ| 0.0001); the shader looks static",
        "frames are essentially black (mean luminance 0.004)",
        "frames are blown out white (mean luminance 0.991)",
        "NaN/Inf pixels in 3 frame(s) (first at t=1s: nan=1200 inf=0)",
        "no frames were rendered",
    )
    for message in messages:
        assert finding_kind("gl_frames", message, "warn") == "gl_frames/motion_or_detail", (
            message
        )


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
    assert rows["cv3d-part-contact"]["n"] == 2
    assert rows["cv3d-part-contact"]["mean"] == 1.0          # 2 pairs + 0, INFO ignored
    assert rows["cv3d-bbox-contract"]["mean"] == 0.5         # part_bbox only
    assert rows["cv3d-repeats-and-mirrors"]["mean"] == 0.5   # instance_bbox only


def test_a_row_only_counts_the_languages_it_is_defined_over(tmp_path):
    from bench.skill_targets import battery_rows, load_runs

    root = _battery(tmp_path, {
        "robot": _record("urdf_blender", [{"gate": "joint_sweep", "findings": [
            _finding("joint_sweep", "links 'a' and 'b' overlap by 6.0 mm at pose j=1.0", "error"),
            _finding("joint_sweep", "links 'a' and 'c' overlap by 3.0 mm at rest", "warn")]}]),
        "chair": _record("blender", [{"gate": "connectivity", "findings": CONNECTIVITY}]),
    })
    rows = {r["skill"]: r for r in battery_rows(load_runs(root), list(TARGETS), which="last", cache=None)}
    assert rows["cv3d-urdf-joints"]["n"] == 1               # the blender run is not eligible
    assert rows["cv3d-urdf-joints"]["mean"] == 1.0          # ERRORs only; the rest-pose WARN is not one
    assert rows["cv3d-part-contact"]["n"] == 2              # both languages carry a part graph


def test_an_ungraded_run_is_not_in_the_population(tmp_path):
    """A concurrency probe or an e2e smoke run has no gate report; counting it would give
    a GLB-derived row a different n from the gate-derived rows beside it."""
    from bench.skill_targets import battery_rows, load_runs

    root = _battery(tmp_path, {
        "chair": _record("blender", [{"gate": "connectivity", "findings": CONNECTIVITY}]),
        "probe": {"spec": {"language": "blender"}, "rounds": [{"index": 0, "gates": []}]},
    })
    rows = {r["skill"]: r for r in battery_rows(load_runs(root), list(TARGETS), which="last", cache=None)}
    assert rows["cv3d-part-contact"]["n"] == 1


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
    r = rows["cv3d-part-contact"]
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
    row = next(r for r in payload["rows"] if r["skill"] == "cv3d-part-contact")
    assert row["n"] == 1 and row["mean"] == 2.0


def test_the_confidence_block_says_how_many_pairs_an_effect_needs():
    from bench.skill_targets import confidence

    c = confidence([-1.0, 1.0, -1.0, 1.0], control_mean=4.0)
    assert c["sd"] == pytest.approx(1.1547, rel=1e-3)
    assert c["ci95"] == pytest.approx(2 * c["se"])
    assert c["resolvable_effect"] == 1.0                     # 25% of a control mean of 4
    assert c["n_to_resolve"] == 5                            # (2*1.1547/1.0)^2
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
    assert rows["cv3d-part-contact"]["n"] == 1


def test_3dcode_skills_list_shows_every_bundles_claim():
    """The ledger has to be visible from the CLI, or it is a document nobody opens."""
    from typer.testing import CliRunner

    from codeverse.cli.main import app

    r = CliRunner().invoke(app, ["skills", "list"])
    assert r.exit_code == 0
    assert "target" in r.output
    assert "none" not in r.output, "a bundle with no target row would print `none` here"

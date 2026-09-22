"""Keep bundle target claims complete and the benchmark readout honest."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from codeverse3d.addons.skill_targets import (
    BY_SKILL,
    DIRECTIONS,
    METRICS,
    SOURCES,
    SRC_GATE,
    TARGETS,
    gate_kinds_claimed,
    target_for,
)
from codeverse3d.skills import bundle_dirs, skills_dir
from codeverse3d.skills.registry import ROUTED_SKILLS, finding_kind

BUNDLES = bundle_dirs()
pytestmark = pytest.mark.skipif(not BUNDLES, reason=f"no bundles in {skills_dir()} yet")


def _live_kinds() -> set[str]:
    from codeverse3d.skills import registry

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


def test_3dcode_skills_list_shows_every_bundles_claim():
    """The ledger has to be visible from the CLI, or it is a document nobody opens."""
    from typer.testing import CliRunner

    from codeverse3d.cli.main import app

    r = CliRunner().invoke(app, ["skills", "list"])
    assert r.exit_code == 0
    assert "target" in r.output
    assert "none" not in r.output, "a bundle with no target row would print `none` here"

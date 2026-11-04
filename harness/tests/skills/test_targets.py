"""Keep bundle target claims complete and the benchmark readout honest."""

from __future__ import annotations

import pytest

from codeverse3d.addons.skill_targets import (
    BY_SKILL,
    TARGETS,
    Target,
)
from codeverse3d.skills import bundle_dirs, skills_dir
from codeverse3d.skills.registry import ROUTED_SKILLS

BUNDLES = bundle_dirs()
pytestmark = pytest.mark.skipif(not BUNDLES, reason=f"no bundles in {skills_dir()} yet")

# --------------------------------------------------------------------------- the table
def test_target_table_exactly_covers_the_shipped_and_routed_bundles():
    """No orphan rows, unmeasured routes, duplicate rows or bundles maintained by taste."""
    have = {d.name for d in BUNDLES}
    assert len(BY_SKILL) == len(TARGETS), "a bundle has more than one target row"
    assert set(BY_SKILL) == have == set(ROUTED_SKILLS)

def test_no_two_bundles_claim_the_same_quantity():
    """Two bundles sharing a metric means an A/B cannot say which one moved it."""
    assert len({t.metric for t in TARGETS}) == len(TARGETS)
    seen: dict[str, list[Target]] = {}
    for t in TARGETS:
        for k in t.kinds:
            # a run has ONE language: two bundles may count the same kind over disjoint languages
            # (c3d-scene-lighting vs c3d-bpy-scene-lighting, 2026-10-14) and an A/B still names one owner
            for other in seen.get(k, []):
                shared = not t.languages or not other.languages or set(t.languages) & set(other.languages)
                assert not shared, f"{t.skill} and {other.skill} both count {k} over one language"
            seen.setdefault(k, []).append(t)

def test_3dcode_skills_list_shows_every_bundles_claim():
    """The ledger has to be visible from the CLI, or it is a document nobody opens."""
    from typer.testing import CliRunner

    from codeverse3d.cli.main import app

    r = CliRunner().invoke(app, ["skills", "list"])
    assert r.exit_code == 0
    assert "target" in r.output
    assert "none" not in r.output, "a bundle with no target row would print `none` here"

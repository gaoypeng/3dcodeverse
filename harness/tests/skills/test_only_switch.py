"""``C3D_SKILLS_ONLY``: route exactly one bundle, so an effect A/B can attribute its delta."""

from __future__ import annotations

import pytest

from codeverse3d.config import get_settings
from codeverse3d.skills.materialize import attach_skills


class _Part:
    def __init__(self, instances=1, symmetry="none"):
        self.instances, self.symmetry, self.children = instances, symmetry, None


class _Plan:
    parts = (_Part(instances=4), _Part(symmetry="mirror_x"))
    joints = effects = passes = ()
    summary = style_notes = ""


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "AGENTS.md").write_text("# agents\n")
    return tmp_path


def _listed(ws, **kw):
    return attach_skills(ws, track="static_object", language="blender", kind="baseline",
                         plan=_Plan(), **kw).listed


def test_only_restricts_the_library_and_reaches_the_router(ws, monkeypatch):
    """It restricts the library, not the selection: a max of 1 over the full routing picks another bundle."""
    assert _listed(ws, max_skills=1)[0] != "c3d-bbox-contract"
    assert _listed(ws, only=frozenset({"c3d-bbox-contract"}), max_skills=1) == ["c3d-bbox-contract"]
    # a typo makes the variant visibly identical to its control, never the full set
    assert _listed(ws, only=frozenset({"c3d-typo"})) == []
    # ``only=`` omitted, attach_skills reads ``C3D_SKILLS_ONLY`` (a comma list, blanks ignored)
    monkeypatch.setenv("C3D_SKILLS_ONLY", " c3d-bbox-contract , ")
    get_settings.cache_clear()
    assert _listed(ws) == ["c3d-bbox-contract"], "attach_skills must consult the switch when only= is omitted"

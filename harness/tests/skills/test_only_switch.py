"""``C3D_SKILLS_ONLY``: route exactly one bundle, so an effect A/B can attribute its delta.

``C3D_SKILLS=1`` routes up to five bundles.  A delta measured against that is a delta of
the SET, and the wave's question is per bundle.  The switch restricts the LIBRARY rather
than filtering the selection afterwards, and the difference is testable: with a filter,
the cap could spend its slots on higher-priority sheets and drop the one under test.
"""

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


def test_unset_routes_the_whole_set(ws):
    listed = _listed(ws)
    assert len(listed) > 1
    assert "c3d-bbox-contract" in listed


@pytest.mark.parametrize("name", ["c3d-bbox-contract", "c3d-blender-forms", "c3d-repeats-and-mirrors"])
def test_only_routes_exactly_that_bundle(ws, name):
    assert _listed(ws, only=frozenset({name})) == [name]


def test_the_cap_cannot_drop_the_bundle_under_test(ws):
    """The reason it restricts the library and not the selection.  bbox-contract ranks
    below blender-forms and part-contact in the default set, so a max of 1 applied to the
    FULL routing would hand back somebody else's bundle."""
    assert _listed(ws, max_skills=1)[0] != "c3d-bbox-contract"
    assert _listed(ws, only=frozenset({"c3d-bbox-contract"}), max_skills=1) == ["c3d-bbox-contract"]


def test_a_bundle_its_own_rules_do_not_fire_for_is_still_not_routed(ws):
    """`only` narrows what MAY be routed; it never forces an attachment.  urdf-joints has
    no row that fires on a blender static_object, so the arm attaches nothing — and an A/B
    on it would correctly measure no difference rather than a fabricated one."""
    assert _listed(ws, only=frozenset({"c3d-urdf-joints"})) == []


def test_an_unknown_name_attaches_nothing_rather_than_everything(ws, caplog):
    """A typo must make the variant visibly identical to its control, not silently
    measure the full five-bundle set."""
    assert _listed(ws, only=frozenset({"c3d-typo"})) == []
    assert "C3D_SKILLS_ONLY" in caplog.text


def test_the_switch_reaches_the_router(ws, monkeypatch):
    """``only=`` omitted, attach_skills reads ``C3D_SKILLS_ONLY`` (a comma list, blanks
    ignored) — the variant child of an effect A/B sets nothing else."""
    monkeypatch.setenv("C3D_SKILLS_ONLY", " c3d-bbox-contract , ")
    get_settings.cache_clear()
    assert _listed(ws) == ["c3d-bbox-contract"], "attach_skills must consult the switch when only= is omitted"

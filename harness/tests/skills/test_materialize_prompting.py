"""Materialisation into both discovery roots, and the per-backend prompt text (T6).

Two things here are load-bearing and were verified against the shipped CLIs rather than
assumed: claude-code reads only ``.claude/skills`` (no ``.agents`` skill root), and codex
refuses symlinks inside a skill tree.  Both are asserted below, because a CLI upgrade that
changes either ships an empty index and nothing else fails.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from codeverse.skills.materialize import (
    BODY_FILES,
    MARK_BEGIN,
    MARK_END,
    SKILL_ROOTS,
    attach_skills,
    materialize_skills,
    write_index,
)
from codeverse.skills.prompting import (
    MANDATE,
    NATIVE_LOADERS,
    index_block,
    index_tokens,
    inline_body,
    repair_pointers,
)
from codeverse.skills.registry import select


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    for name in BODY_FILES:
        (root / name).write_text("# 3dcode workspace\n\n## Hard rules\n\n1. …\n")
    return root


def _plan(n=3):
    from types import SimpleNamespace as NS

    return NS(parts=[NS(name=f"P{i}", instances=1, symmetry="none", children=[]) for i in range(n)], summary="")


# --------------------------------------------------------------------------- files
def test_bundles_land_in_both_discovery_roots_as_real_files(ws, library):
    skills = [library["cv3d-part-contact"], library["cv3d-blender-forms"]]
    written = materialize_skills(ws, skills)
    for root in SKILL_ROOTS:
        for s in skills:
            f = ws / root / s.name / "SKILL.md"
            assert f.is_file() and not f.is_symlink()
            assert f.read_text() == s.path.read_text()
            assert (ws / root / s.name / "references" / "worked_example.md").is_file()
    # 2 roots x (2 skills + the read control) x (SKILL.md + one reference)
    assert len(written) == 2 * 3 * 2


def test_a_never_routed_control_bundle_goes_in_beside_the_real_ones(ws, library):
    """Signal 4: the probe's own falsification.

    Nothing routes it, nothing indexes it, and if it comes back opened then whatever
    opened it was not the agent choosing to read a skill — git's own diff does it, and so
    does every CLI's activation.  Without this the read rate would report 100% forever.
    """
    from codeverse.skills.materialize import CONTROL_NAME

    materialize_skills(ws, [library["cv3d-part-contact"]])
    for root in SKILL_ROOTS:
        d = ws / root / CONTROL_NAME
        assert (d / "SKILL.md").is_file() and (d / "references" / "control.md").is_file()
        assert CONTROL_NAME not in index_block([library["cv3d-part-contact"]], "api-agent")
    from codeverse.skills.registry import ROUTED_SKILLS

    assert CONTROL_NAME not in ROUTED_SKILLS


def test_the_control_survives_a_reroute_and_never_counts_as_a_stale_bundle(ws, library):
    from codeverse.skills.materialize import CONTROL_NAME

    materialize_skills(ws, [library["cv3d-part-contact"], library["cv3d-bbox-contract"]])
    materialize_skills(ws, [library["cv3d-bbox-contract"]])
    for root in SKILL_ROOTS:
        assert (ws / root / CONTROL_NAME / "SKILL.md").is_file()
        assert not (ws / root / "cv3d-part-contact").exists()


def test_nothing_in_a_materialised_tree_is_a_symlink_because_codex_refuses_them(ws, library):
    materialize_skills(ws, [library["cv3d-part-contact"]])
    for root in SKILL_ROOTS:
        assert not any(p.is_symlink() for p in (ws / root).rglob("*"))


def test_atime_equals_mtime_after_writing_so_the_read_probe_has_a_zero_point(ws, library):
    for p in materialize_skills(ws, [library["cv3d-part-contact"]]):
        st = os.stat(p)
        assert abs(st.st_atime - st.st_mtime) < 0.001


def test_a_skill_that_is_no_longer_routed_is_removed_from_the_workspace(ws, library):
    materialize_skills(ws, [library["cv3d-part-contact"], library["cv3d-blender-forms"]])
    materialize_skills(ws, [library["cv3d-part-contact"]])
    for root in SKILL_ROOTS:
        assert (ws / root / "cv3d-part-contact").is_dir()
        assert not (ws / root / "cv3d-blender-forms").exists()


def test_an_empty_selection_still_sweeps_last_rounds_bundles(ws, library):
    """attach_skills' empty-selection early return used to skip the sweep entirely:
    last round's bundles stayed live in both discovery roots, where the native CLIs
    discover skills by directory (V4b).  An empty route is a legal desired set."""
    materialize_skills(ws, [library["cv3d-part-contact"]])
    out = attach_skills(ws, track="static_object", language="threejs", kind="generation",
                        agent_kind="claude-code", library={})
    assert out.listed == []
    for root in SKILL_ROOTS:
        assert not (ws / root / "cv3d-part-contact").exists(), f"stale bundle survived in {root}"


# --------------------------------------------------------------------------- prompt text
def test_native_loader_backends_get_one_sentence_and_no_second_index(library):
    skills = [library["cv3d-part-contact"], library["cv3d-bbox-contract"]]
    for kind in NATIVE_LOADERS:
        text = index_block(skills, kind)
        assert MANDATE in text
        assert "cv3d-part-contact" not in text, f"{kind} would be double-indexed"
        assert index_tokens(skills, kind) < 60


def test_api_agent_gets_the_index_it_cannot_discover(library):
    """And it is told the set was ROUTED, not offered: our router already filtered it on
    inputs the agent cannot see, so "the ones that match your task" would only lose reads."""
    from codeverse.skills.prompting import MANDATE_ROUTED

    skills = [library["cv3d-part-contact"], library["cv3d-bbox-contract"]]
    text = index_block(skills, "api-agent")
    assert MANDATE_ROUTED in text and MANDATE not in text
    for s in skills:
        assert f"**{s.name}**" in text and f".agents/skills/{s.name}/SKILL.md" in text


def test_an_empty_route_adds_no_text_at_all(library):
    assert index_block([], "api-agent") == "" and index_block([], "codex") == ""


def test_claude_code_is_pointed_at_its_own_root(library):
    text = index_block([library["cv3d-part-contact"]], "unknown-backend")
    assert ".agents/skills/" in text
    from codeverse.skills.prompting import skill_path

    assert skill_path("cv3d-x", agent_kind="claude-code") == ".claude/skills/cv3d-x/SKILL.md"


def test_repair_pointers_name_only_the_gate_fired_skills(library):
    sel = select("static_object", "blender", "repair", signals={"multi_part": True},
                 findings=["connectivity/interpenetration"], library=library)
    text = repair_pointers(sel)
    assert "cv3d-part-contact" in text and "connectivity/interpenetration" in text
    assert "cv3d-blender-forms" not in text  # standing rows are already in the workspace
    assert repair_pointers([]) == ""


def test_inline_body_picks_the_highest_priority_body_and_respects_the_cap(library):
    sel = select("static_object", "blender", "repair", signals={"multi_part": True},
                 findings=["connectivity/interpenetration"], library=library)
    name, text = inline_body(sel)
    assert name == "cv3d-part-contact" and "cv3d-part-contact" in text
    assert inline_body(sel, max_tokens=0) == ("", "")


# --------------------------------------------------------------------------- the section
def test_the_index_is_written_into_every_body_file_and_is_replaceable(ws, library):
    write_index(ws, index_block([library["cv3d-part-contact"]], "api-agent"))
    for name in BODY_FILES:
        body = (ws / name).read_text()
        assert MARK_BEGIN in body and MARK_END in body and "cv3d-part-contact" in body
        assert body.startswith("# 3dcode workspace")
    write_index(ws, index_block([library["cv3d-bbox-contract"]], "api-agent"))
    body = (ws / "AGENTS.md").read_text()
    assert body.count(MARK_BEGIN) == 1 and "cv3d-part-contact" not in body and "cv3d-bbox-contract" in body
    write_index(ws, "")
    assert MARK_BEGIN not in (ws / "AGENTS.md").read_text()


def test_write_index_ignores_body_files_a_workspace_does_not_have(tmp_path: Path):
    assert write_index(tmp_path, "hello") == []


# --------------------------------------------------------------------------- attach
def test_attach_routes_writes_and_reports(ws, library):
    got = attach_skills(ws, track="static_object", language="blender", kind="baseline",
                        agent_kind="api-agent", plan=_plan(), library=library, max_skills=5,
                        allow_unverified=False)
    assert got.listed and got.paths and got.index_tokens > 0
    assert set(got.reasons) == set(got.listed)
    assert all("cv3d-" in p for p in got.paths)
    assert "cv3d-part-contact" in (ws / "AGENTS.md").read_text()
    assert got.inlined == ""


def test_attach_for_a_single_shot_session_inlines_instead_of_writing_files(ws, library):
    got = attach_skills(ws, track="static_object", language="blender", kind="baseline",
                        agent_kind="single-shot", plan=_plan(), library=library, single_shot=True,
                        max_skills=5, allow_unverified=False)
    assert got.inlined and got.paths == [] and got.index_tokens == 0
    assert MARK_BEGIN not in (ws / "AGENTS.md").read_text()


def test_attach_with_nothing_routed_clears_the_section(ws, library):
    write_index(ws, "stale")
    got = attach_skills(ws, track="graphics", language="glsl_shader", kind="asset",
                        agent_kind="codex", library=library, max_skills=5, allow_unverified=False)
    assert got.listed == []
    assert MARK_BEGIN not in (ws / "AGENTS.md").read_text()

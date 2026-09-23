"""Materialisation into both discovery roots, and the per-backend prompt text (T6)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from codeverse3d.skills.materialize import (
    BODY_FILES,
    MARK_BEGIN,
    MARK_END,
    SKILL_ROOTS,
    attach_skills,
    materialize_skills,
    write_index,
)
from codeverse3d.skills.prompting import (
    MANDATE,
    index_block,
    index_tokens,
    inline_body,
    repair_pointers,
)
from codeverse3d.skills.registry import select


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    for name in BODY_FILES:
        (root / name).write_text("# 3dcode workspace\n\n## Hard rules\n\n1. …\n")
    return root


# --------------------------------------------------------------------------- files
def test_bundles_and_the_read_control_land_in_both_roots_and_an_empty_route_sweeps_them(ws, library):
    """codex refuses symlinks; the never-routed control falsifies the read probe (git diff opens files too)."""
    from codeverse3d.skills.materialize import CONTROL_NAME
    from codeverse3d.skills.registry import ROUTED_SKILLS

    skills = [library["c3d-part-contact"], library["c3d-blender-forms"]]
    written = materialize_skills(ws, skills)
    for p in written:   # atime == mtime after writing, so the read probe has a zero point
        st = os.stat(p)  # (checked before anything below reads a bundle and moves its atime)
        assert abs(st.st_atime - st.st_mtime) < 0.001
    for root in SKILL_ROOTS:
        for s in skills:
            assert (ws / root / s.name / "SKILL.md").read_text() == s.path.read_text()
        assert (ws / root / CONTROL_NAME / "references" / "control.md").is_file()
        assert not any(p.is_symlink() for p in (ws / root).rglob("*"))
    assert len(written) == 2 * 3 * 2   # 2 roots x (2 skills + control) x (SKILL.md + one reference)
    assert CONTROL_NAME not in index_block(skills) and CONTROL_NAME not in ROUTED_SKILLS

    # V4b: an empty route still removes last round's bundles — native CLIs discover by directory
    out = attach_skills(ws, track="static_object", language="threejs", kind="generation", library={})
    assert out.listed == []
    for root in SKILL_ROOTS:
        assert not (ws / root / "c3d-part-contact").exists(), f"stale bundle survived in {root}"


# --------------------------------------------------------------------------- prompt text
def test_native_loader_backends_get_one_sentence_and_no_second_index(library):
    skills = [library["c3d-part-contact"], library["c3d-bbox-contract"]]
    text = index_block(skills)
    assert MANDATE in text
    assert "c3d-part-contact" not in text, "the native loaders would double-index it"
    assert index_tokens(skills) < 60


def test_repair_pointers_and_the_inlined_body_come_from_the_gate_fired_skill(library):
    sel = select("static_object", "blender", "repair", signals={"multi_part": True},
                 findings=["connectivity/interpenetration"], library=library)
    text = repair_pointers(sel)
    assert "c3d-part-contact" in text and "connectivity/interpenetration" in text
    assert "c3d-blender-forms" not in text  # standing rows are already in the workspace
    assert repair_pointers([]) == ""
    # single-shot: the highest-priority body is inlined, within the cap
    name, text = inline_body(sel)
    assert name == "c3d-part-contact" and "c3d-part-contact" in text
    assert inline_body(sel, max_tokens=0) == ("", "")


# --------------------------------------------------------------------------- the section
def test_the_index_is_written_into_every_body_file_and_is_replaceable(ws, library):
    write_index(ws, "## Skills\n\nc3d-part-contact")
    for name in BODY_FILES:
        body = (ws / name).read_text()
        assert MARK_BEGIN in body and MARK_END in body and "c3d-part-contact" in body
        assert body.startswith("# 3dcode workspace")
    write_index(ws, "## Skills\n\nc3d-bbox-contract")
    body = (ws / "AGENTS.md").read_text()
    assert body.count(MARK_BEGIN) == 1 and "c3d-part-contact" not in body and "c3d-bbox-contract" in body
    write_index(ws, "")
    assert MARK_BEGIN not in (ws / "AGENTS.md").read_text()

"""The spec's rules, enforced where a test can see them (design §6.2, T1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from codeverse3d.skills import iter_skills, load_skill
from codeverse3d.skills.model import (
    BODY_MAX_LINES,
    SkillError,
    parse_skill,
    validate_bundle,
)
from tests.skills.conftest import write_bundle


@pytest.mark.parametrize("text, expect", [
    ("no frontmatter at all\n", "missing YAML frontmatter"),
    ("---\nname: [1, 2]\n---\nbody\n", "must match"),
    ("---\ndescription: only this\n---\nbody\n", "no `name`"),
    ("---\nname: c3d-demo\n---\nbody\n", "no `description`"),
    ("---\nname: c3d-demo\ndescription: d\nwhen_to_use: x\n---\nbody\n", "unknown frontmatter key"),
    ("---\nname: c3d-demo\ndescription: use <script> tags\n---\nbody\n", "prompt-injection"),
    ("---\nname: c3d_demo\ndescription: d\n---\nbody\n", "must match"),
    ("---\n- a\n- b\n---\nbody\n", "must be a YAML mapping"),
    ("---\nname: c3d-demo\ndescription: d\nmetadata:\n  a:\n    b: c\n---\nbody\n", "must be a scalar"),
    ("---\nname: c3d-elsewhere\ndescription: d\n---\nbody\n", "directory name"),
    (f"---\nname: c3d-demo\ndescription: {'x' * 1025}\n---\nbody\n", "1024"),   # the spec's limit
])
def test_malformed_frontmatter_is_rejected_with_a_reason(tmp_path: Path, text: str, expect: str):
    d = tmp_path / "c3d-demo"
    d.mkdir()
    (d / "SKILL.md").write_text(text)
    with pytest.raises(SkillError, match=expect):
        parse_skill(d)


def test_validate_reports_budget_evidence_layout_symlinks_and_the_verified_date(tmp_path: Path):
    d = write_bundle(tmp_path, "c3d-big", evidence="guesswork", body="x\n" * (BODY_MAX_LINES + 5))
    (d / "references" / "deeper").mkdir()
    (d / "references" / "deeper" / "x.md").write_text("nope")
    (d / "references" / "link.md").symlink_to(d / "SKILL.md")   # codex refuses symlinks
    issues = " | ".join(validate_bundle(d))
    assert "cap is" in issues and "metadata.evidence" in issues and "one level of references" in issues
    assert "symlink" in issues
    d = tmp_path / "c3d-demo"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: c3d-demo\ndescription: d\nmetadata:\n  evidence: measured\n---\nbody\n")
    assert any("verified" in i for i in validate_bundle(d))


def test_a_broken_bundle_is_skipped_not_fatal_but_strict_raises(tmp_path: Path, caplog):
    write_bundle(tmp_path, "c3d-good")
    bad = tmp_path / "c3d-bad"
    bad.mkdir()
    (bad / "SKILL.md").write_text("no frontmatter\n")
    with caplog.at_level("WARNING"):
        assert [s.name for s in iter_skills(tmp_path)] == ["c3d-good"]
    assert any("c3d-bad" in r.getMessage() for r in caplog.records)
    with pytest.raises(SkillError):
        list(iter_skills(tmp_path, strict=True))


def test_load_skill_by_name(tmp_path: Path):
    write_bundle(tmp_path, "c3d-demo")
    assert load_skill("c3d-demo", tmp_path).name == "c3d-demo"
    with pytest.raises(SkillError, match="no skill"):
        load_skill("c3d-missing", tmp_path)

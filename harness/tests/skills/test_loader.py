"""The spec's rules, enforced where a test can see them (design §6.2, T1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from codeverse.skills import all_skills, bundle_dirs, iter_skills, load_skill
from codeverse.skills.loader import (
    BODY_MAX_LINES,
    SkillError,
    parse_skill,
    split_frontmatter,
    validate_bundle,
)
from tests.skills.conftest import write_bundle


def test_parses_frontmatter_body_and_references(tmp_path: Path):
    d = write_bundle(tmp_path, "cv3d-demo", description="What it does. When to use it.",
                     references={"traps.md": "x\n", "table.md": "y\n"})
    s = parse_skill(d)
    assert s.name == "cv3d-demo" and s.dir == d and s.path == d / "SKILL.md"
    assert s.description.startswith("What it does")
    assert s.license == "Apache-2.0" and s.evidence == "measured" and s.verified == "2026-08-25"
    assert s.references == ("references/table.md", "references/traps.md")
    assert s.body_tokens > 0 and s.body_lines >= 1
    assert s.ref().name == "cv3d-demo"


def test_a_directory_argument_finds_the_skill_file(tmp_path: Path):
    d = write_bundle(tmp_path, "cv3d-demo")
    assert parse_skill(d).name == parse_skill(d / "SKILL.md").name


@pytest.mark.parametrize("text, expect", [
    ("no frontmatter at all\n", "missing YAML frontmatter"),
    ("---\nname: [1, 2]\n---\nbody\n", "must match"),
    ("---\ndescription: only this\n---\nbody\n", "no `name`"),
    ("---\nname: cv3d-demo\n---\nbody\n", "no `description`"),
    ("---\nname: cv3d-demo\ndescription: d\nwhen_to_use: x\n---\nbody\n", "unknown frontmatter key"),
    ("---\nname: cv3d-demo\ndescription: use <script> tags\n---\nbody\n", "prompt-injection"),
    ("---\nname: cv3d_demo\ndescription: d\n---\nbody\n", "must match"),
    ("---\nname: cv3d--demo\ndescription: d\n---\nbody\n", "must match"),
    ("---\nname: Cv3d-Demo\ndescription: d\n---\nbody\n", "must match"),
    ("---\n- a\n- b\n---\nbody\n", "must be a YAML mapping"),
    ("---\nname: cv3d-demo\ndescription: d\nmetadata:\n  a:\n    b: c\n---\nbody\n", "must be a scalar"),
])
def test_malformed_frontmatter_is_rejected_with_a_reason(tmp_path: Path, text: str, expect: str):
    d = tmp_path / "cv3d-demo"
    d.mkdir()
    (d / "SKILL.md").write_text(text)
    with pytest.raises(SkillError, match=expect):
        parse_skill(d)


def test_name_must_equal_the_directory_name(tmp_path: Path):
    d = tmp_path / "cv3d-elsewhere"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: cv3d-demo\ndescription: d\n---\nbody\n")
    with pytest.raises(SkillError, match="directory name"):
        parse_skill(d)


def test_description_length_is_the_spec_limit(tmp_path: Path):
    d = tmp_path / "cv3d-demo"
    d.mkdir()
    (d / "SKILL.md").write_text(f"---\nname: cv3d-demo\ndescription: {'x' * 1025}\n---\nbody\n")
    with pytest.raises(SkillError, match="1024"):
        parse_skill(d)


def test_split_frontmatter_keeps_the_body_verbatim():
    fm, body = split_frontmatter("---\nname: a\n---\nline1\nline2\n")
    assert fm == "name: a" and body == "line1\nline2\n"


def test_validate_reports_budget_evidence_and_layout(tmp_path: Path):
    d = write_bundle(tmp_path, "cv3d-big", evidence="guesswork", body="x\n" * (BODY_MAX_LINES + 5))
    (d / "references" / "deeper").mkdir()
    (d / "references" / "deeper" / "x.md").write_text("nope")
    issues = " | ".join(validate_bundle(d))
    assert "cap is" in issues and "metadata.evidence" in issues and "one level of references" in issues


def test_validate_flags_a_symlink_because_codex_refuses_them(tmp_path: Path):
    d = write_bundle(tmp_path, "cv3d-demo")
    (d / "references" / "link.md").symlink_to(d / "SKILL.md")
    assert any("symlink" in i for i in validate_bundle(d))


def test_validate_wants_a_verified_date(tmp_path: Path):
    d = tmp_path / "cv3d-demo"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: cv3d-demo\ndescription: d\nmetadata:\n  evidence: measured\n---\nbody\n")
    assert any("verified" in i for i in validate_bundle(d))


def test_a_clean_bundle_has_no_issues(tmp_path: Path):
    assert validate_bundle(write_bundle(tmp_path, "cv3d-demo")) == []


def test_iteration_skips_modules_dunder_dirs_and_claims(tmp_path: Path):
    write_bundle(tmp_path, "cv3d-demo")
    (tmp_path / "_claims").mkdir()
    (tmp_path / "_claims" / "cv3d-demo.toml").write_text("")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "loader.py").write_text("x = 1\n")
    (tmp_path / "not-a-bundle").mkdir()
    assert [p.name for p in bundle_dirs(tmp_path)] == ["cv3d-demo"]
    assert list(all_skills(tmp_path)) == ["cv3d-demo"]


def test_a_broken_bundle_is_skipped_not_fatal_but_strict_raises(tmp_path: Path, caplog):
    write_bundle(tmp_path, "cv3d-good")
    bad = tmp_path / "cv3d-bad"
    bad.mkdir()
    (bad / "SKILL.md").write_text("no frontmatter\n")
    with caplog.at_level("WARNING"):
        assert [s.name for s in iter_skills(tmp_path)] == ["cv3d-good"]
    assert any("cv3d-bad" in r.getMessage() for r in caplog.records)
    with pytest.raises(SkillError):
        list(iter_skills(tmp_path, strict=True))


def test_load_skill_by_name(tmp_path: Path):
    write_bundle(tmp_path, "cv3d-demo")
    assert load_skill("cv3d-demo", tmp_path).name == "cv3d-demo"
    with pytest.raises(SkillError, match="no skill"):
        load_skill("cv3d-missing", tmp_path)


def test_the_library_dir_is_overridable_for_tests_and_the_live_smoke(tmp_path: Path, monkeypatch):
    from codeverse.skills import SKILLS_DIR_ENV, skills_dir

    write_bundle(tmp_path, "cv3d-demo")
    monkeypatch.setenv(SKILLS_DIR_ENV, str(tmp_path))
    assert skills_dir() == tmp_path

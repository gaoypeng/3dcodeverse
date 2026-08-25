"""The read probe — the measurement that replaces "0 of 16 read_cookbook calls".

If these tests are wrong, every later conclusion about whether skills work is wrong, so
they pin the semantics explicitly: surfaced != deep, a bundle with no references cannot
be probed for depth and says so, and api-agent's exact log wins over the probe.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from codeverse.skills.materialize import attach_skills
from codeverse.skills.telemetry import (
    READS_FILE,
    SKILLS_FILE,
    TELEMETRY_DIR,
    append_usage,
    deep_read_rate,
    is_skill_path,
    probe_reads,
    record_exact_read,
)
from tests.skills.conftest import write_bundle


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    (root / "AGENTS.md").write_text("# body\n")
    return root


def _plan(n=3):
    from types import SimpleNamespace as NS

    return NS(parts=[NS(name=f"P{i}", instances=1, symmetry="none", children=[]) for i in range(n)], summary="")


def _attach(ws, library, **kw):
    return attach_skills(ws, track="static_object", language="blender", kind="baseline",
                         agent_kind="api-agent", plan=_plan(), library=library,
                         max_skills=5, allow_unverified=False, **kw)


def _touch_read(path: Path) -> None:
    """Simulate a reader on a filesystem whose atime we cannot rely on in a test."""
    st = os.stat(path)
    os.utime(path, (st.st_mtime + 30, st.st_mtime))


def test_nothing_read_means_nothing_read(ws, library):
    got = _attach(ws, library)
    usage = probe_reads(ws, got)
    assert usage.listed == got.listed
    assert usage.surfaced == [] and usage.deep == [] and usage.body_tokens_read == 0
    assert usage.deep_read_rate == 0.0


def test_opening_skill_md_is_surfaced_but_not_deep(ws, library):
    got = _attach(ws, library)
    name = got.listed[0]
    _touch_read(ws / ".agents" / "skills" / name / "SKILL.md")
    usage = probe_reads(ws, got)
    assert usage.surfaced == [name] and usage.deep == []


def test_opening_a_reference_is_the_deep_signal(ws, library):
    got = _attach(ws, library)
    name = got.listed[0]
    _touch_read(ws / ".agents" / "skills" / name / "references" / "worked_example.md")
    usage = probe_reads(ws, got)
    assert usage.deep == [name] and usage.surfaced == [name]  # deep implies reached
    assert usage.body_tokens_read == next(r.body_tokens for r in usage.reads if r.name == name)
    assert 0 < usage.deep_read_rate <= 1


def test_the_claude_copy_counts_too_because_claude_code_reads_only_that_root(ws, library):
    got = _attach(ws, library)
    name = got.listed[0]
    _touch_read(ws / ".claude" / "skills" / name / "SKILL.md")
    assert probe_reads(ws, got).surfaced == [name]


def test_a_bundle_with_no_references_says_depth_is_unmeasurable(tmp_path, ws):
    from codeverse.skills import all_skills

    lib_dir = tmp_path / "lib"
    write_bundle(lib_dir, "cv3d-part-contact", references={})
    got = _attach(ws, all_skills(lib_dir, strict=True))
    read = probe_reads(ws, got).reads[0]
    assert read.deep_measurable is False and read.deep is False


def test_api_agent_exact_reads_override_the_probe_and_carry_the_turn(ws, library):
    got = _attach(ws, library)
    name = got.listed[0]
    record_exact_read(ws, f".agents/skills/{name}/SKILL.md", turn=3, label="baseline")
    record_exact_read(ws, f".agents/skills/{name}/references/worked_example.md", turn=5, label="baseline")
    record_exact_read(ws, "src/model.py", turn=1)          # not a skill: ignored
    usage = probe_reads(ws, got)
    row = next(r for r in usage.reads if r.name == name)
    assert row.surfaced and row.deep and row.first_seen_turn == 3
    lines = (ws / TELEMETRY_DIR / READS_FILE).read_text().splitlines()
    assert len(lines) == 2 and json.loads(lines[0])["skill"] == name


def test_is_skill_path_recognises_both_roots_and_nothing_else():
    assert is_skill_path(".agents/skills/cv3d-x/SKILL.md") == "cv3d-x"
    assert is_skill_path(".claude/skills/cv3d-x/references/a.md") == "cv3d-x"
    assert is_skill_path("./.agents/skills/cv3d-x/SKILL.md") == "cv3d-x"
    assert is_skill_path("src/model.py") == ""
    assert is_skill_path(".agents/settings.json") == ""


def test_a_damaged_reads_file_does_not_break_the_probe(ws, library):
    got = _attach(ws, library)
    (ws / TELEMETRY_DIR).mkdir(exist_ok=True)
    (ws / TELEMETRY_DIR / READS_FILE).write_text("not json\n{}\n")
    assert probe_reads(ws, got).surfaced == []


def test_usage_is_appended_as_one_json_line_per_session(ws, library):
    got = _attach(ws, library)
    usage = probe_reads(ws, got)
    append_usage(ws, usage, round=0, kind="baseline", agent="api-agent:gemini-3.7-flash")
    append_usage(ws, usage, round=1, kind="refine", agent="api-agent:gemini-3.7-flash")
    rows = [json.loads(x) for x in (ws / TELEMETRY_DIR / SKILLS_FILE).read_text().splitlines()]
    assert [r["round"] for r in rows] == [0, 1]
    assert rows[0]["listed"] == usage.listed and "index_tokens" in rows[0]


def test_deep_read_rate_aggregates_across_sessions(ws, library):
    got = _attach(ws, library)
    a = probe_reads(ws, got)
    _touch_read(ws / ".agents" / "skills" / got.listed[0] / "references" / "worked_example.md")
    b = probe_reads(ws, got)
    rates = deep_read_rate([a, b])
    assert rates[got.listed[0]] == (1, 2)
    assert rates[got.listed[1]] == (0, 2)


def test_telemetry_never_raises_into_a_run(tmp_path):
    from codeverse.skills.model import SkillsMaterialized

    record_exact_read(tmp_path / "does" / "not" / "exist", ".agents/skills/x/SKILL.md", turn=1)
    assert append_usage(tmp_path / "nope" / "nope", probe_reads(tmp_path, SkillsMaterialized())) is None or True

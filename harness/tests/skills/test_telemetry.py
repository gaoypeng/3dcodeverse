"""The read probe — the measurement that replaces "0 of 16 read_cookbook calls".

If these tests are wrong, every later conclusion about whether skills work is wrong, so
they pin the semantics explicitly: surfaced != deep, and a bundle with no references
cannot be probed for depth and says so.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from codeverse3d.skills.materialize import attach_skills
from codeverse3d.skills.telemetry import (
    SKILLS_FILE,
    TELEMETRY_DIR,
    append_usage,
    probe_reads,
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
                         agent_kind="unknown-backend", plan=_plan(), library=library,
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
    from codeverse3d.skills import all_skills

    lib_dir = tmp_path / "lib"
    write_bundle(lib_dir, "c3d-part-contact", references={})
    got = _attach(ws, all_skills(lib_dir, strict=True))
    read = probe_reads(ws, got).reads[0]
    assert read.deep_measurable is False and read.deep is False


def test_usage_is_appended_as_one_json_line_per_session(ws, library):
    got = _attach(ws, library)
    usage = probe_reads(ws, got)
    append_usage(ws, usage, round=0, kind="baseline", agent="gemini-cli:gemini-3.6-flash")
    append_usage(ws, usage, round=1, kind="refine", agent="gemini-cli:gemini-3.6-flash")
    rows = [json.loads(x) for x in (ws / TELEMETRY_DIR / SKILLS_FILE).read_text().splitlines()]
    assert [r["round"] for r in rows] == [0, 1]
    assert rows[0]["listed"] == usage.listed and "index_tokens" in rows[0]


def test_the_control_bundle_is_materialised_and_starts_unread(ws, library):
    from codeverse3d.skills.materialize import CONTROL_NAME, materialize_skills

    materialize_skills(ws, [library["c3d-part-contact"]])
    usage = probe_reads(ws, _materialized(library, "c3d-part-contact"))
    assert usage.control_present and not usage.control_read
    assert usage.probe_trustworthy
    assert CONTROL_NAME not in usage.listed


def test_a_control_that_was_opened_makes_the_rate_unmeasurable(ws, library):
    """The whole point: a session where everything looks read is a session that measured
    nothing, and it must say so instead of reporting a confident 100%."""
    from codeverse3d.skills.materialize import CONTROL_NAME, materialize_skills
    from codeverse3d.skills.prompting import AGENTS_SKILL_ROOT

    materialize_skills(ws, [library["c3d-part-contact"]])
    _touch(ws / AGENTS_SKILL_ROOT / "c3d-part-contact" / "references" / "worked_example.md")
    _touch(ws / AGENTS_SKILL_ROOT / CONTROL_NAME / "references" / "control.md")
    usage = probe_reads(ws, _materialized(library, "c3d-part-contact"))
    assert usage.deep == ["c3d-part-contact"]        # the raw signal is still reported
    assert usage.control_read and not usage.probe_trustworthy
    assert usage.deep_read_rate is None                # but the RATE refuses to lie


def test_git_diff_alone_trips_the_control(tmp_path, library):  # noqa: PLR0915
    """The confound that invalidated the original design, reproduced.

    ``Workspace.changed_files`` runs `git add -A -N` then `git diff --numstat` after every
    agent session, and git reads each untracked file to diff it.  That flips every bundle
    file to "read" with no agent involved at all.
    """
    import subprocess

    from codeverse3d.skills.materialize import materialize_skills

    ws = tmp_path / "ws"
    (ws / "src").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=ws, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
                    "--allow-empty", "-m", "init"], cwd=ws, check=True)
    for f in materialize_skills(ws, [library["c3d-part-contact"]]):
        # back-date by more than ATIME_EPSILON_S: a real session lasts minutes, and the
        # probe deliberately ignores an access within a second of the write
        st = os.stat(f)
        os.utime(f, (st.st_mtime - 10, st.st_mtime - 10))

    before = probe_reads(ws, _materialized(library, "c3d-part-contact"))
    assert before.probe_trustworthy, "materialisation alone must leave the control unread"

    subprocess.run(["git", "add", "-A", "-N"], cwd=ws, check=True)
    subprocess.run(["git", "diff", "--numstat", "HEAD"], cwd=ws, check=True, capture_output=True)

    after = probe_reads(ws, _materialized(library, "c3d-part-contact"))
    assert after.control_read, "git read every untracked file; the control must have caught it"
    assert after.deep_read_rate is None


def _materialized(library, *names):
    from codeverse3d.skills.model import Selection, SkillsMaterialized

    sels = [Selection(skill=library[n], priority=50, rules=("R1",), reason="test") for n in names]
    return SkillsMaterialized(listed=list(names), selections=sels)


def _touch(path):
    import os
    import time

    st = os.stat(path)
    os.utime(path, (st.st_mtime + 60, st.st_mtime))
    time.sleep(0)

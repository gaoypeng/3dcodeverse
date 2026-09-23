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
from tests.skills.test_materialize_prompting import _plan


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    (root / "AGENTS.md").write_text("# body\n")
    return root


def _attach(ws, library, **kw):
    return attach_skills(ws, track="static_object", language="blender", kind="baseline",
                         plan=_plan(), library=library,
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
    from codeverse3d.skills.materialize import materialize_skills

    materialize_skills(ws, [library["c3d-part-contact"]])
    usage = probe_reads(ws, _materialized(library, "c3d-part-contact"))
    assert usage.control_present and not usage.control_read


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
    assert usage.control_read and usage.deep_read_rate is None   # but the RATE refuses to lie


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
    assert not before.control_read, "materialisation alone must leave the control unread"

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


# ===================================================================== transcript evidence
# Since 2026-09-22 every CLI backend folds its session's tool calls into
# trajectories/<label>/transcript.jsonl (agents/cli_common.record_tool_calls), and the probe
# reads THOSE first.  The rows below are shaped like the four live 2026-09-22 sessions
# (tests/agents/test_tool_trace.py holds the vendor-side excerpts).

def _session(ws: Path, label: str, calls: list[dict], *, t: float, traced: bool = True) -> None:
    d = ws / "trajectories" / f"{label}_r00"
    d.mkdir(parents=True, exist_ok=True)
    rows = [{"t": t, "kind": "invoke", "argv": ["cli"], "attempt": 1},
            *({"t": t + 1, "kind": "tool_call", **c} for c in calls)]
    if traced:
        rows.append({"t": t + 2, "kind": "tool_trace", "calls": len(calls), "source": "test"})
    with (d / "transcript.jsonl").open("a") as fh:
        fh.writelines(json.dumps(r) + "\n" for r in rows)


def _git_read_everything(ws: Path) -> None:
    """What `git add -A -N` + `git diff` does to the atime of every bundle file."""
    for f in ws.rglob("*.md"):
        if "/skills/" in str(f):
            _touch_read(f)


def test_the_cli_transcript_is_the_evidence_when_every_session_left_one(ws, library):
    import time

    got = _attach(ws, library)
    a, b = got.listed[:2]
    _session(ws, "baseline", [
        {"tool": "Skill", "args": {"skill": a}, "skill": a},                                   # claude / gemini
        {"tool": "command_execution", "args": {"command": f"sed -n '1,280p' .agents/skills/{b}/SKILL.md"}},
        {"tool": "view_file", "args": {"AbsolutePath": f"{ws}/.agents/skills/{b}/references/worked_example.md"}},
    ], t=time.time() + 1)
    _git_read_everything(ws)          # the atime probe would now call every bundle, and the control, read
    usage = probe_reads(ws, got)
    assert usage.evidence == "transcript"
    assert usage.surfaced == [a, b] and usage.deep == [b]
    assert not usage.control_read
    assert usage.deep_read_rate == 1 / len(got.listed)


def test_a_session_without_a_trace_sends_the_probe_back_to_atime(ws, library):
    """A partial trace would under-count exactly as a blind probe over-counts."""
    import time

    got = _attach(ws, library)
    _session(ws, "baseline", [{"tool": "Skill", "args": {}, "skill": got.listed[0]}], t=time.time() + 1)
    _session(ws, "repair", [], t=time.time() + 2, traced=False)   # e.g. a CLI killed before its record
    usage = probe_reads(ws, got)
    assert usage.evidence == "atime" and usage.surfaced == []


def test_only_sessions_after_the_attach_are_credited(ws, library):
    import time

    got = _attach(ws, library)
    name = got.listed[0]
    _session(ws, "old", [{"tool": "Skill", "args": {}, "skill": name}], t=got.attached_at - 100)
    _session(ws, "baseline", [{"tool": "Read", "args": {"file_path": "src/model.py"}}], t=time.time() + 1)
    usage = probe_reads(ws, got)
    assert usage.evidence == "transcript" and usage.surfaced == []   # last round's activation is last round's


def test_a_call_the_cli_marked_failed_read_nothing(ws, library):
    """codex 0.155.1, 2026-09-22: its first `sed` expanded the skill-root alias wrongly and exited 2."""
    import time

    got = _attach(ws, library)
    name = got.listed[0]
    _session(ws, "baseline", [{"tool": "command_execution", "failed": True,
                               "args": {"command": f"sed -n 1,240p /home/u/.codex/skills/.system/{name}/SKILL.md"}}],
             t=time.time() + 1)
    assert probe_reads(ws, got).surfaced == []


def test_an_agent_that_opens_the_control_is_reported_not_called_blind(ws, library):
    import time

    from codeverse3d.skills.materialize import CONTROL_NAME

    got = _attach(ws, library)
    _session(ws, "baseline", [{"tool": "read_file", "args": {"file_path": f".agents/skills/{CONTROL_NAME}/SKILL.md"}}],
             t=time.time() + 1)
    usage = probe_reads(ws, got)
    assert usage.evidence == "transcript" and usage.control_read
    assert usage.deep_read_rate == 0.0   # exact: it read the control, and nothing else


def test_the_path_match_is_exact_about_names_and_about_reading():
    from codeverse3d.skills.telemetry import _called

    def called(*args: dict) -> tuple[bool, bool]:
        return _called("c3d-scene", [{"args": a} for a in args])

    assert called({"file_path": "/ws/.claude/skills/c3d-scene/SKILL.md"}) == (True, False)
    assert called({"command": "cat r1/c3d-scene/SKILL.md"}) == (True, False)            # codex's root alias
    assert called({"AbsolutePath": "/ws/.agents/skills/c3d-scene/references/a.md"}) == (True, True)
    assert called({"DirectoryPath": "/ws/.agents/skills/c3d-scene/references"}) == (False, False)  # a listing
    assert called({"file_path": ".agents/skills/c3d-scene-water/SKILL.md"}) == (False, False)      # a longer name
    assert called({"file_path": ".agents/skills/xc3d-scene/SKILL.md"}) == (False, False)
    assert called({"pattern": "src/**"}) == (False, False)


def test_the_report_counts_exact_sessions_and_drops_only_blind_ones(tmp_path):
    from typer.testing import CliRunner

    from codeverse3d.cli.main import app

    tele = tmp_path / "run" / TELEMETRY_DIR
    tele.mkdir(parents=True)
    rows = [  # exact, the agent opened the control too: counted, and said so
        {"agent": "gemini-cli:gemini-3.7-flash", "listed": ["c3d-a"], "evidence": "transcript",
         "control_present": True, "control_read": True, "reads": [{"name": "c3d-a", "surfaced": True, "deep": True}]},
        # atime, control fired: blind, dropped
        {"agent": "codex:gpt-5.6-luna", "listed": ["c3d-a"], "control_present": True, "control_read": True,
         "reads": [{"name": "c3d-a", "surfaced": True, "deep": True}]},
    ]
    (tele / SKILLS_FILE).write_text("".join(json.dumps(r) + "\n" for r in rows))
    r = CliRunner().invoke(app, ["skills", "report", str(tmp_path), "--json"])
    assert r.exit_code == 0, r.output
    out = json.loads(r.output)
    assert out["transcript_sessions"] == 1 and out["control_opened_by_agent_sessions"] == 1
    assert out["control_read_sessions"] == 1
    assert [(s["backend"], s["deep"]) for s in out["skills"]] == [("gemini-cli", 1)]

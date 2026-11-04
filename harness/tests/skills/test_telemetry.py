"""The read probe's semantics: surfaced != deep, the control, transcript before atime."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from codeverse3d.skills.materialize import attach_skills
from codeverse3d.skills.telemetry import (
    SKILLS_FILE,
    TELEMETRY_DIR,
    probe_reads,
)


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
                         plan=_plan(), library=library,
                         max_skills=5, allow_unverified=False, **kw)

def _touch_read(path: Path) -> None:
    """Simulate a reader on a filesystem whose atime we cannot rely on in a test."""
    st = os.stat(path)
    os.utime(path, (st.st_mtime + 30, st.st_mtime))

def test_skill_md_is_surfaced_a_reference_is_deep_and_the_claude_copy_counts(ws, library):
    got = _attach(ws, library)
    a, b = got.listed[:2]
    _touch_read(ws / ".agents" / "skills" / a / "references" / "worked_example.md")
    _touch_read(ws / ".claude" / "skills" / b / "SKILL.md")   # claude-code reads only that root
    usage = probe_reads(ws, got)
    assert usage.surfaced == [a, b] and usage.deep == [a]      # deep implies reached
    assert usage.body_tokens_read == next(r.body_tokens for r in usage.reads if r.name == a)
    assert usage.deep_read_rate == 1 / len(got.listed)

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

def test_the_cli_transcript_is_the_evidence_when_every_session_left_one(ws, library):
    import time

    got = _attach(ws, library)
    a, b = got.listed[:2]
    _session(ws, "baseline", [
        {"tool": "Skill", "args": {"skill": a}, "skill": a},                                   # claude / gemini
        {"tool": "command_execution", "args": {"command": f"sed -n '1,280p' .agents/skills/{b}/SKILL.md"}},
        {"tool": "view_file", "args": {"AbsolutePath": f"{ws}/.agents/skills/{b}/references/worked_example.md"}},
    ], t=time.time() + 1)
    for f in ws.rglob("*.md"):       # git reading every bundle file must not fool the transcript evidence
        if "/skills/" in str(f):
            _touch_read(f)
    usage = probe_reads(ws, got)
    assert usage.evidence == "transcript"
    assert usage.surfaced == [a, b] and usage.deep == [b]
    assert not usage.control_read
    assert usage.deep_read_rate == 1 / len(got.listed)

def test_a_shell_read_the_trace_cannot_parse_counts_while_the_control_is_untouched(ws, library):
    """claude-code 2.1.317 (live, 2026-11-03) read the references with `cd <bundle> && for f in
    $(find references -type f); do cat "$f"; done`: no path to match, but the file's atime moved."""
    import time

    from codeverse3d.skills.materialize import CONTROL_NAME

    got = _attach(ws, library)
    a = got.listed[0]
    cd_cat = f'cd {ws}/.claude/skills/{a} && for f in $(find references -type f); do cat "$f"; done'
    _session(ws, "baseline", [{"tool": "Skill", "args": {"skill": a}, "skill": a},
                              {"tool": "Bash", "args": {"command": cd_cat}}], t=time.time() + 1)
    usage = probe_reads(ws, got)
    assert usage.evidence == "transcript" and usage.surfaced == [a] and usage.deep == []   # the trace alone
    _touch_read(ws / ".claude" / "skills" / a / "references" / "worked_example.md")
    usage = probe_reads(ws, got)
    assert usage.deep == [a] and usage.control_present and not usage.control_read
    # once something opens the control's references too, the atime proves nothing again —
    # and control_read stays the trace's verdict: the agent itself never named the control
    for f in (ws / ".claude" / "skills" / CONTROL_NAME / "references").glob("*.md"):
        _touch_read(f)
    usage = probe_reads(ws, got)
    assert usage.deep == [] and usage.surfaced == [a] and not usage.control_read

def test_only_traced_sessions_after_the_attach_are_credited(ws, library):
    import time

    got = _attach(ws, library)
    name = got.listed[0]
    _session(ws, "old", [{"tool": "Skill", "args": {}, "skill": name}], t=got.attached_at - 100)
    _session(ws, "baseline", [{"tool": "Read", "args": {"file_path": "src/model.py"}}], t=time.time() + 1)
    usage = probe_reads(ws, got)
    assert usage.evidence == "transcript" and usage.surfaced == []   # last round's activation is last round's
    # a session without a trace sends the probe back to atime: a partial trace would
    # under-count exactly as a blind probe over-counts
    _session(ws, "baseline", [{"tool": "Skill", "args": {}, "skill": name}], t=time.time() + 2)
    _session(ws, "repair", [], t=time.time() + 3, traced=False)   # e.g. a CLI killed before its record
    usage = probe_reads(ws, got)
    assert usage.evidence == "atime" and usage.surfaced == []

def test_the_report_counts_exact_sessions_and_drops_only_blind_ones(tmp_path):
    from typer.testing import CliRunner

    from codeverse3d.cli.main import app

    root = tmp_path / "bat"
    tele = root / "run" / TELEMETRY_DIR
    tele.mkdir(parents=True)
    rows = [  # exact, the agent opened the control too: counted, and said so
        {"agent": "gemini-cli:gemini-3.8-flash", "listed": ["c3d-a"], "evidence": "transcript",
         "control_present": True, "control_read": True, "reads": [{"name": "c3d-a", "surfaced": True, "deep": True}]},
        # atime, control fired: blind, dropped
        {"agent": "codex:gpt-6-luna", "listed": ["c3d-a"], "control_present": True, "control_read": True,
         "reads": [{"name": "c3d-a", "surfaced": True, "deep": True}]},
    ]
    (tele / SKILLS_FILE).write_text("".join(json.dumps(r) + "\n" for r in rows))
    # N58: a best-of-N candidate's session counts, and a cell another battery symlinks in is read, once
    for d in (root / "run" / "_cand" / "c0", tmp_path / "other" / "cell"):
        (d / TELEMETRY_DIR).mkdir(parents=True)
        (d / TELEMETRY_DIR / SKILLS_FILE).write_text(json.dumps(rows[0]) + "\n")
    (root / "cell").symlink_to(tmp_path / "other" / "cell")
    (root / "alias").symlink_to(tmp_path / "other" / "cell")
    r = CliRunner().invoke(app, ["skills", "report", str(root), "--json"])
    assert r.exit_code == 0, r.output
    out = json.loads(r.output)
    assert out["transcript_sessions"] == 3 and out["control_opened_by_agent_sessions"] == 3
    assert out["control_read_sessions"] == 1
    assert [(s["backend"], s["deep"]) for s in out["skills"]] == [("gemini-cli", 3)]

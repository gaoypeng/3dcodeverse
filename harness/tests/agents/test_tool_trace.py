"""Each CLI backend folds its tool calls into transcript.jsonl; fixtures are trimmed live sessions (2026-09-22)."""

from __future__ import annotations

import json
import sqlite3

from codeverse3d.agents.backends import (
    ClaudeCodeAgent,
    ClaudeStream,
    agy_tool_calls,
)
from codeverse3d.agents.cli_common import (
    TOOL_CALL_ROW,
    TOOL_TRACE_ROW,
    TRACE_ARG_CHARS,
    ToolCall,
    begin_session,
    record_tool_calls,
)
from codeverse3d.contracts.agent import AgentJob
from codeverse3d.workspace import Workspace

WS = "/tmp/probe/live/claude1"

# --------------------------------------------------------------------------- claude-code
CLAUDE_STREAM = [
    {"type": "system", "subtype": "init", "cwd": WS, "session_id": "893f6373", "model": "claude-opus-5-5",
     "claude_code_version": "2.1.280", "tools": ["Bash", "Read", "Skill"],
     "skills": ["c3d-bbox-contract", "c3d-blender-forms", "c3d-part-contact", "c3d-repeats-and-mirrors",
                "zz-c3d-read-control", "dataviz"]},
    {"type": "system", "subtype": "thinking_tokens", "estimated_tokens": 12},
    {"type": "assistant", "message": {"model": "claude-opus-5-5", "id": "msg_1", "role": "assistant", "content": [
        {"type": "text", "text": "I'll start by loading the mandatory skills for this task"}]}},
    {"type": "assistant", "message": {"model": "claude-opus-5-5", "id": "msg_1", "role": "assistant", "content": [
        {"type": "tool_use", "id": "toolu_01KmUt", "name": "Skill", "input": {"skill": "c3d-bbox-contract"},
         "caller": {"type": "direct"}}]}},
    {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "toolu_01KmUt", "content": "Launching skill: c3d-bbox-contract"}]},
     "tool_use_result": {"success": True, "commandName": "c3d-bbox-contract"}},
    {"type": "assistant", "message": {"model": "claude-opus-5-5", "id": "msg_2", "role": "assistant", "content": [
        {"type": "tool_use", "id": "toolu_02", "name": "Read",
         "input": {"file_path": f"{WS}/.claude/skills/c3d-bbox-contract/references/worked_example.md"}}]}},
    {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "toolu_02", "content": "..."}]}},
    {"type": "assistant", "message": {"model": "claude-opus-5-5", "id": "msg_3", "role": "assistant", "content": [
        {"type": "tool_use", "id": "toolu_03", "name": "mcp__3dcode__check_contract", "input": {}}]}},
    {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "toolu_03", "is_error": True,
         "content": "check_contract: plan.json not found in the workspace"}]}},
    {"type": "result", "subtype": "success", "is_error": False, "num_turns": 25, "result": "done",
     "session_id": "893f6373", "total_cost_usd": 0.95, "usage": {"input_tokens": 32, "output_tokens": 30106}},
]


def _stream_text(events) -> str:
    return "\n".join(json.dumps(e) for e in events) + "\n"


def test_claude_stream_folds_the_index_the_skill_calls_and_their_failures():
    s = ClaudeStream()
    for line in _stream_text(CLAUDE_STREAM).splitlines():
        s.feed(line)
    assert s.skills[:5] == ["c3d-bbox-contract", "c3d-blender-forms", "c3d-part-contact",
                            "c3d-repeats-and-mirrors", "zz-c3d-read-control"]
    assert [(c.tool, c.skill, c.failed) for c in s.calls] == [
        ("Skill", "c3d-bbox-contract", False), ("Read", "", False), ("mcp__3dcode__check_contract", "", True)]
    s.feed("not json")      # chatter and a chunked >1 MB line are skipped, never raised
    s.feed('{"type": "assistant", "message": {"content": [')
    assert len(s.calls) == 3


def test_claude_is_launched_streaming(tmp_ws: Workspace):
    s = begin_session(AgentJob(workspace=str(tmp_ws.root), prompt="p", spatial_tools=False), "claude-code")
    argv = ClaudeCodeAgent("sonnet", binary="claude").build_argv(s)
    assert "Skill" in argv[argv.index("--allowedTools") + 1].split(",")
    assert "--disable-slash-commands" not in argv   # claude-code: "Disable all skills"
    assert argv[argv.index("--output-format") + 1] == "stream-json" and "--verbose" in argv


# --------------------------------------------------------------------------- agy
def _pb(field: int, payload: bytes) -> bytes:
    """One length-delimited protobuf field (the only wire type the fixture needs)."""
    def varint(n: int) -> bytes:
        out = b""
        while True:
            b, n = n & 0x7F, n >> 7
            out += bytes([b | (0x80 if n else 0)])
            if not n:
                return out
    return varint(field << 3 | 2) + varint(len(payload)) + payload


def _agy_step(call_id: str, tool: str, args: dict) -> bytes:
    """The shape of a 1.2.2 planner step: field 20 → field 7 → {1: id, 2: tool, 3: JSON args},
    next to other nested strings (the bot id, session metadata)."""
    call = _pb(1, call_id.encode()) + _pb(2, tool.encode()) + _pb(3, json.dumps(args).encode())
    meta = _pb(5, _pb(12, b"73608b9c-31f0-45cb-98a3-92299de1db80"))
    return meta + _pb(20, _pb(6, b"bot-1dd6e6ec") + _pb(7, call))


def test_agy_tool_calls_come_out_of_its_conversation_database(tmp_path):
    db = tmp_path / "a0319af6.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE steps (idx integer PRIMARY KEY, step_type integer, step_payload blob)")
    skill = f"{WS}/.agents/skills/c3d-bbox-contract/SKILL.md"
    rows = [(0, 14, _pb(19, _pb(2, b"Workspace root: /tmp/x"))),
            (1, 15, _agy_step("call_4419682", "view_file", {"AbsolutePath": skill, "toolAction": "Reading skill"})),
            (2, 132, _agy_step("call_4419682", "view_file", {"AbsolutePath": skill})),   # the result echoes it
            (3, 15, _agy_step("call_3378924", "run_command", {"CommandLine": "ls -la"})),
            (4, 15, b"\xff\xfe not protobuf")]
    con.executemany("INSERT INTO steps VALUES (?, ?, ?)", rows)
    con.commit()
    con.close()
    calls = agy_tool_calls(db)
    assert [(c.tool, c.args.get("AbsolutePath") or c.args.get("CommandLine")) for c in calls] == [
        ("view_file", skill), ("run_command", "ls -la")]


# --------------------------------------------------------------------------- the rows
def test_record_tool_calls_writes_compact_rows_and_one_closing_marker(tmp_ws: Workspace):
    s = begin_session(AgentJob(workspace=str(tmp_ws.root), prompt="p", spatial_tools=False), "codex")
    n = record_tool_calls(s, [ToolCall("Write", {"file_path": "src/a.py", "content": "x" * 5000}),
                              ToolCall("Skill", {"skill": "c3d-part-contact"}, skill="c3d-part-contact"),
                              ToolCall("apply", {"changes": [{"path": "src/b.py"}]}, failed=True)],
                          source="unit", skills_index=["c3d-part-contact"])
    rows = s.traj.read_transcript()
    assert n == 3 and [r["kind"] for r in rows] == [TOOL_CALL_ROW] * 3 + [TOOL_TRACE_ROW]
    assert rows[0]["args"]["file_path"] == "src/a.py" and len(rows[0]["args"]["content"]) == TRACE_ARG_CHARS + 1
    assert rows[1]["skill"] == "c3d-part-contact" and "skill" not in rows[0] and "failed" not in rows[0]
    assert rows[2]["failed"] is True and rows[2]["args"]["changes"] == '[{"path": "src/b.py"}]'
    assert rows[3] == {**rows[3], "calls": 3, "source": "unit", "skills_index": ["c3d-part-contact"]}
    # a session that called nothing still closes its trace: silence is then a fact
    assert record_tool_calls(s, [], source="unit") == 0
    assert s.traj.read_transcript()[-1]["calls"] == 0

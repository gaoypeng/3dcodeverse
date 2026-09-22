"""Every CLI backend folds its session's tool calls into ``transcript.jsonl`` — the skill
read probe's ground truth (docs/SKILLS.md §4, 2026-09-22).

The fixtures are excerpts of the four live sessions recorded 2026-09-22 (claude-code
2.1.280 stream-json, gemini-cli 0.53.0 chat record, codex 0.155.1 ``exec --json``, agy
1.2.2 conversation database), trimmed to the fields the parsers read.  A vendor that
renames one of them breaks a test here instead of silently reporting "read nothing".
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

from codeverse3d.agents.backends import (
    SYSTEM_SETTINGS,
    ClaudeCodeAgent,
    ClaudeStream,
    CodexEvents,
    agy_tool_calls,
    gemini_chat_records,
    parse_claude_json,
    read_gemini_chats,
    write_system_settings,
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
    {"type": "system", "subtype": "init", "cwd": WS, "session_id": "893f6373", "model": "claude-sonnet-5",
     "claude_code_version": "2.1.280", "tools": ["Bash", "Read", "Skill"],
     "skills": ["c3d-bbox-contract", "c3d-blender-forms", "c3d-part-contact", "c3d-repeats-and-mirrors",
                "zz-c3d-read-control", "dataviz"]},
    {"type": "system", "subtype": "thinking_tokens", "estimated_tokens": 12},
    {"type": "assistant", "message": {"model": "claude-sonnet-5", "id": "msg_1", "role": "assistant", "content": [
        {"type": "text", "text": "I'll start by loading the mandatory skills for this task"}]}},
    {"type": "assistant", "message": {"model": "claude-sonnet-5", "id": "msg_1", "role": "assistant", "content": [
        {"type": "tool_use", "id": "toolu_01KmUt", "name": "Skill", "input": {"skill": "c3d-bbox-contract"},
         "caller": {"type": "direct"}}]}},
    {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "toolu_01KmUt", "content": "Launching skill: c3d-bbox-contract"}]},
     "tool_use_result": {"success": True, "commandName": "c3d-bbox-contract"}},
    {"type": "assistant", "message": {"model": "claude-sonnet-5", "id": "msg_2", "role": "assistant", "content": [
        {"type": "tool_use", "id": "toolu_02", "name": "Read",
         "input": {"file_path": f"{WS}/.claude/skills/c3d-bbox-contract/references/worked_example.md"}}]}},
    {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "toolu_02", "content": "..."}]}},
    {"type": "assistant", "message": {"model": "claude-sonnet-5", "id": "msg_3", "role": "assistant", "content": [
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


def test_the_stream_json_envelope_is_the_result_line_and_an_unfinished_stream_has_none():
    assert parse_claude_json(_stream_text(CLAUDE_STREAM))["num_turns"] == 25
    assert parse_claude_json(_stream_text(CLAUDE_STREAM[:-1])) is None   # killed before its result
    single = json.dumps({"num_turns": 2, "result": "ok"})                  # one document still parses
    assert parse_claude_json(single)["num_turns"] == 2


def test_claude_is_launched_streaming(tmp_ws: Workspace):
    s = begin_session(AgentJob(workspace=str(tmp_ws.root), prompt="p", spatial_tools=False), "claude-code")
    argv = ClaudeCodeAgent("sonnet", binary="claude").build_argv(s)
    assert argv[argv.index("--output-format") + 1] == "stream-json" and "--verbose" in argv
    assert "Skill" in argv[argv.index("--allowedTools") + 1].split(",")
    assert "--disable-slash-commands" not in argv   # claude-code: "Disable all skills"


FAKE_STREAMING_CLAUDE = r'''
args = sys.argv[1:]
assert args[args.index("--output-format") + 1] == "stream-json" and "--verbose" in args
for ev in json.loads(os.environ["FAKE_EVENTS"]):
    print(json.dumps(ev), flush=True)
'''


def test_a_claude_session_writes_its_trace_into_the_transcript(tmp_ws: Workspace, fake_bin, monkeypatch):
    monkeypatch.setenv("FAKE_EVENTS", json.dumps(CLAUDE_STREAM))
    res = ClaudeCodeAgent("sonnet", binary=fake_bin("claude", FAKE_STREAMING_CLAUDE)).run(
        AgentJob(workspace=str(tmp_ws.root), prompt="build it", label="c", timeout_s=30, spatial_tools=False))
    assert res.ok and res.turns == 25, res.errors
    rows = [json.loads(x) for x in Path(res.transcript_path).read_text().splitlines()]
    calls = [r for r in rows if r["kind"] == TOOL_CALL_ROW]
    trace = [r for r in rows if r["kind"] == TOOL_TRACE_ROW]
    assert [c["tool"] for c in calls] == ["Skill", "Read", "mcp__3dcode__check_contract"]
    assert calls[0]["skill"] == "c3d-bbox-contract" and calls[2]["failed"] is True
    assert trace == [{**trace[0], "calls": 3, "source": "claude-code stream-json"}]
    assert "zz-c3d-read-control" in trace[0]["skills_index"]


# --------------------------------------------------------------------------- gemini-cli
GEMINI_RECORD = [
    {"sessionId": "da5c91db-7d90-4dd9-a360-56b03e1fd339", "projectHash": "4b58", "startTime": "2026-09-22T19:31:33.522Z",
     "lastUpdated": "2026-09-22T19:31:33.522Z", "kind": "main"},
    {"$set": {"messages": [{"id": "d049", "type": "user", "content": [{"text": "<session_context>..."}]}]}},
    {"id": "fb86", "type": "gemini", "content": "I will activate the relevant skills for Blender modeling",
     "toolCalls": [
         {"id": "activate_skill__call_48964", "name": "activate_skill", "args": {"name": "c3d-blender-forms"},
          "status": "success"},
         {"id": "activate_skill__call_48965", "name": "activate_skill", "args": {"name": "c3d-part-contact"},
          "status": "success"}]},
    {"$set": {"lastUpdated": "2026-09-22T19:31:48.148Z"}},
    {"id": "fb87", "type": "gemini", "content": "",
     "toolCalls": [{"id": "read_file__call_5", "name": "read_file", "status": "success",
                    "args": {"file_path": ".agents/skills/c3d-part-contact/references/worked_example.md"}},
                   {"id": "read_file__call_6", "name": "read_file", "status": "error",
                    "args": {"file_path": ".agents/skills/zz-c3d-read-control/SKILL.md"}}]},
    # the append-only record re-writes a message as it progresses: one call, not two
    {"id": "fb86", "type": "gemini", "content": "", "toolCalls": [
        {"id": "activate_skill__call_48964", "name": "activate_skill", "args": {"name": "c3d-blender-forms"},
         "status": "success"}]},
]


def _gemini_home(tmp_path: Path, ws: Path, *, since_offset: float = 0.0) -> Path:
    home = tmp_path / "home"
    proj = home / ".gemini" / "tmp" / ws.name
    (proj / "chats").mkdir(parents=True)
    (proj / ".project_root").write_text(str(ws))
    other = home / ".gemini" / "tmp" / "someone-else"
    (other / "chats").mkdir(parents=True)
    (other / ".project_root").write_text("/elsewhere/run")
    (other / "chats" / "session-2026-09-22T19-31-aaaaaaaa.jsonl").write_text(json.dumps(GEMINI_RECORD[2]) + "\n")
    rec = proj / "chats" / "session-2026-09-22T19-31-da5c91db.jsonl"
    rec.write_text("".join(json.dumps(r) + "\n" for r in GEMINI_RECORD))
    if since_offset:
        t = time.time() + since_offset
        os.utime(rec, (t, t))
    return home


def test_gemini_chat_record_is_found_by_workspace_and_time_and_folded_by_call_id(tmp_path):
    ws = tmp_path / "gemini1"
    ws.mkdir()
    home = _gemini_home(tmp_path, ws)
    recs = gemini_chat_records(ws, time.time() - 60, {"GEMINI_CLI_HOME": str(home)})
    assert [p.name for p in recs] == ["session-2026-09-22T19-31-da5c91db.jsonl"]
    calls = read_gemini_chats(recs).calls
    assert [(c.tool, c.skill, c.failed) for c in calls] == [
        ("activate_skill", "c3d-blender-forms", False), ("activate_skill", "c3d-part-contact", False),
        ("read_file", "", False), ("read_file", "", True)]
    # an earlier session's record in the same workspace belongs to that session
    assert gemini_chat_records(ws, time.time() + 60, {"GEMINI_CLI_HOME": str(home)}) == []
    assert gemini_chat_records(tmp_path / "unknown", 0.0, {"GEMINI_CLI_HOME": str(home)}) == []


def test_gemini_system_settings_pin_skills_on_and_keep_the_workspace_trusted(tmp_path):
    """0.53 merges the system file LAST, so a user's (or an agent-planted workspace)
    ``skills.enabled: false`` cannot hide the bundles; ``folderTrust`` off is what makes
    ``isTrustedFolder()`` true, without which ``discoverSkills`` skips both workspace roots."""
    data = json.loads(write_system_settings(tmp_path / "s.json").read_text())
    assert data["skills"]["enabled"] is True and data["skills"] == SYSTEM_SETTINGS["skills"]
    assert data["security"]["folderTrust"] == {"enabled": False}


def test_gemini_sessions_see_the_routed_bundles_and_not_the_clis_own(tmp_path):
    """0.53 lists its two built-in skills (bundle/builtin/) to the model next to the routed
    bundles — seen in the request body a local fake API received, 2026-09-22 — and
    ``skills.disabled`` (by name) is the only switch; the fake API then saw neither."""
    data = json.loads(write_system_settings(tmp_path / "s.json").read_text())
    assert data["skills"]["disabled"] == ["skill-creator", "antigravity-support"]


def test_the_chat_record_is_the_usage_of_a_session_that_printed_no_envelope(tmp_path):
    """Every reply once (the record re-writes a message as it progresses), its tokens in the
    envelope's ``stats.models`` shape — the sum reproduced two live envelopes to the token."""
    ws = tmp_path / "gemini1"
    ws.mkdir()
    home = _gemini_home(tmp_path, ws)
    rec = next((home / ".gemini" / "tmp" / ws.name / "chats").glob("session-*"))
    tok = {"input": 5000, "output": 40, "cached": 3000, "thoughts": 9, "tool": 0, "total": 5049}
    with rec.open("a") as f:
        for row in ({"id": "fb86", "type": "gemini", "model": "gemini-3.7-flash", "timestamp": "2026-09-22T19:31:40.000Z",
                     "content": "", "tokens": tok},
                    {"id": "fb86", "type": "gemini", "model": "gemini-3.7-flash", "timestamp": "2026-09-22T19:31:41.000Z",
                     "content": "", "tokens": tok},                  # the same reply, re-written
                    {"id": "fb99", "type": "gemini", "model": "gemini-3.7-flash", "content": ""}):   # died mid-reply
            f.write(json.dumps(row) + "\n")
    chat = read_gemini_chats(gemini_chat_records(ws, time.time() - 60, {"GEMINI_CLI_HOME": str(home)}))
    assert chat.stats() == {"models": {"gemini-3.7-flash": {"tokens": {"prompt": 5000, "cached": 3000,
                                                                      "candidates": 40, "thoughts": 9}}}}
    assert len(chat.times) == 2 and chat.times[1] - chat.times[0] == 1.0


# --------------------------------------------------------------------------- codex
CODEX_EVENTS = [
    {"type": "thread.started", "thread_id": "01a0ca9a"},
    {"type": "turn.started"},
    {"type": "item.completed", "item": {"id": "item_0", "type": "agent_message",
                                        "text": "I'm using the chair-relevant 3D skills"}},
    # the model expanded the r1 skill-root alias as r0 (the system root): the read failed
    {"type": "item.completed", "item": {
        "id": "item_1", "type": "command_execution", "status": "failed", "exit_code": 2,
        "command": "/bin/bash -lc \"sed -n '1,240p' AGENTS.md && sed -n '1,240p' "
                   "/home/u/.codex/skills/.system/c3d-blender-forms/SKILL.md\"",
        "aggregated_output": "# 3dcode workspace — rules for the coding agent ..."}},
    {"type": "item.completed", "item": {
        "id": "item_3", "type": "command_execution", "status": "completed", "exit_code": 0,
        "command": "/bin/bash -lc \"sed -n '1,280p' .agents/skills/c3d-blender-forms/SKILL.md && "
                   "sed -n '1,260p' .agents/skills/c3d-part-contact/SKILL.md\"",
        "aggregated_output": "--- name: c3d-blender-forms ..."}},
    {"type": "item.completed", "item": {"id": "item_6", "type": "file_change", "status": "completed",
                                        "changes": [{"path": f"{WS}/src/model.py", "kind": "add"}]}},
    {"type": "item.completed", "item": {"id": "item_7", "type": "mcp_tool_call", "server": "3dcode",
                                        "tool": "build", "arguments": {}, "status": "completed"}},
    {"type": "turn.completed", "usage": {"input_tokens": 2292081, "cached_input_tokens": 2196992,
                                         "output_tokens": 18551, "reasoning_output_tokens": 7544}},
]


def test_codex_events_keep_what_was_run_and_whether_it_worked():
    ev = CodexEvents()
    for e in CODEX_EVENTS:
        ev.feed(json.dumps(e))
    assert [(c.tool, c.failed) for c in ev.calls] == [
        ("command_execution", True), ("command_execution", False), ("file_change", False), ("mcp_tool_call", False)]
    assert "aggregated_output" not in ev.calls[0].args          # what ran, never what came back
    assert ev.calls[1].args["command"].count("SKILL.md") == 2
    assert ev.calls[3].args == {"server": "3dcode", "tool": "build", "arguments": {}}
    assert ev.tool_calls == 4


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

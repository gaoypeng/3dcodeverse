"""Each CLI backend folds its tool calls into transcript.jsonl; fixtures are trimmed live sessions (2026-09-22)."""

from __future__ import annotations

import json
import sqlite3

from codeverse3d.agents.backends import (
    agy_tool_calls,
)

WS = "/tmp/probe/live/claude1"

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

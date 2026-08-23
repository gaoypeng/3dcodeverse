"""ClaudeCodeAgent: argv, envelope parsing, fake binary, one live smoke test."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from codeverse.agents.claude_code import ClaudeCodeAgent, parse_claude_json, usage_from_envelope
from codeverse.agents.cli_common import begin_session
from codeverse.agents.materialize import materialize_workspace
from codeverse.contracts.agent import AgentJob
from codeverse.workspace import Workspace

ENVELOPE = {
    "type": "result", "subtype": "success", "is_error": False, "duration_ms": 4200, "duration_api_ms": 3900,
    "num_turns": 3, "result": "Created src/hello.txt", "session_id": "abc", "total_cost_usd": 0.0123,
    "usage": {"input_tokens": 10, "cache_creation_input_tokens": 200, "cache_read_input_tokens": 5000, "output_tokens": 40},
    "modelUsage": {"claude-sonnet-4-6": {"inputTokens": 10, "outputTokens": 40, "costUSD": 0.0123}},
}

FAKE_CLAUDE = r'''
args = sys.argv[1:]
prompt = args[args.index("-p") + 1]
assert "--dangerously-skip-permissions" in args and "--output-format" in args
assert "--strict-mcp-config" in args
assert "FAKE_SERVICE_API_KEY" not in os.environ
mode = os.environ.get("FAKE_MODE", "ok")
os.makedirs("src", exist_ok=True)
open("src/hello.txt", "w").write("hi")
env = json.loads(''' + repr(json.dumps(ENVELOPE)) + r''')
if mode == "error":
    env["is_error"] = True; env["subtype"] = "error_max_turns"
if "--append-system-prompt" in args:
    env["result"] += " | sys=" + args[args.index("--append-system-prompt") + 1]
print("some log line")
print(json.dumps(env))
'''


def test_parse_envelope_variants():
    assert parse_claude_json(json.dumps(ENVELOPE))["session_id"] == "abc"
    assert parse_claude_json("noise\n" + json.dumps(ENVELOPE))["num_turns"] == 3
    assert parse_claude_json(json.dumps([{"type": "assistant"}, ENVELOPE]))["type"] == "result"
    assert parse_claude_json("") is None
    u = usage_from_envelope(ENVELOPE, "sonnet")
    assert u.input_tokens == 10 and u.cached_tokens == 5000 and u.cost_usd == 0.0123 and u.model == "claude-sonnet-4-6"


def test_argv_includes_mcp_when_materialized(tmp_ws: Workspace):
    materialize_workspace(tmp_ws, agent_kind="claude-code", contract_md="c", cookbook_rel="", spatial_tools=True,
                          mcp_command=["python", "-m", "codeverse.spatial.mcp_server", "--workspace", str(tmp_ws.root)])
    a = ClaudeCodeAgent("sonnet", binary="claude")
    s = begin_session(AgentJob(workspace=str(tmp_ws.root), prompt="p", system_append="S", max_turns=7), "claude-code")
    argv = a.build_argv(s, "p")
    assert argv[:3] == ["claude", "-p", "p"]
    assert "--mcp-config" in argv and argv[argv.index("--mcp-config") + 1].endswith(".mcp.json")
    assert "--strict-mcp-config" in argv and argv[argv.index("--max-turns") + 1] == "7"
    assert argv[argv.index("--append-system-prompt") + 1] == "S" and argv[argv.index("--model") + 1] == "sonnet"
    assert "mcp__c3v__*" in argv[argv.index("--allowedTools") + 1]
    s2 = begin_session(AgentJob(workspace=str(tmp_ws.root), prompt="p", spatial_tools=False), "claude-code")
    assert "--mcp-config" not in a.build_argv(s2, "p")


def test_fake_run_success_and_error(tmp_ws: Workspace, fake_bin, monkeypatch):
    a = ClaudeCodeAgent("sonnet", binary=fake_bin("claude", FAKE_CLAUDE))
    res = a.run(AgentJob(workspace=str(tmp_ws.root), prompt="hello", label="c", system_append="SYS", timeout_s=30))
    assert res.ok and res.exit_reason == "completed", res.errors
    assert res.text.endswith("sys=SYS") and res.usage.cost_usd == 0.0123 and res.tool_calls == 2
    assert [f.path for f in res.files_changed] == ["src/hello.txt"]
    rec = json.loads((Path(res.transcript_path).parent / "result.json").read_text())
    assert rec["num_turns"] == 3 and rec["session_id"] == "abc"
    monkeypatch.setenv("FAKE_MODE", "error")
    res2 = a.run(AgentJob(workspace=str(tmp_ws.root), prompt="hello", label="c2", timeout_s=30))
    assert not res2.ok and res2.exit_reason == "budget" and "error_max_turns" in res2.errors[0]


@pytest.mark.live
def test_live_claude_tiny(tmp_ws: Workspace):
    if not shutil.which("claude"):
        pytest.skip("claude not installed")
    a = ClaudeCodeAgent("haiku")
    res = a.run(AgentJob(workspace=str(tmp_ws.root), prompt="Create src/hello.txt containing exactly 'hi'. Reply DONE.",
                         timeout_s=300, label="live", spatial_tools=False, max_turns=6))
    assert res.ok, res.errors
    assert (tmp_ws.src / "hello.txt").read_text().strip() == "hi"
    assert res.usage.output_tokens > 0

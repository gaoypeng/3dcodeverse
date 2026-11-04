"""CodexAgent: JSONL parsing, argv with MCP overrides, fake binary, live smoke."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from codeverse3d.agents.backends import CodexAgent, split_model_effort
from codeverse3d.agents.cli_common import begin_session
from codeverse3d.contracts.agent import AgentJob
from codeverse3d.workspace import Workspace

EVENTS = [
    {"type": "thread.started", "thread_id": "th1"},
    {"type": "turn.started"},
    {"type": "item.completed", "item": {"id": "i0", "type": "reasoning", "text": "thinking"}},
    {"type": "item.completed", "item": {"id": "i1", "type": "command_execution", "command": "ls", "exit_code": 0, "status": "completed"}},
    {"type": "item.completed", "item": {"id": "i2", "type": "file_change", "changes": [{"path": "src/hello.txt", "kind": "add"}], "status": "completed"}},
    {"type": "item.completed", "item": {"id": "i3", "type": "agent_message", "text": "Done."}},
    {"type": "turn.completed", "usage": {"input_tokens": 1000, "cached_input_tokens": 600, "output_tokens": 50}},
]

FAKE_CODEX = r'''
args = sys.argv[1:]
assert args[0] == "exec" and "--json" in args and "-C" in args
ws = args[args.index("-C") + 1]
assert args[-1] == "-", "the prompt comes on stdin"
prompt = sys.stdin.read()
assert "FAKE_SERVICE_API_KEY" not in os.environ
assert "skills.bundled.enabled=false" in args
os.makedirs(os.path.join(ws, "src"), exist_ok=True)
open(os.path.join(ws, "src", "hello.txt"), "w").write(prompt[:5])
mode = os.environ.get("FAKE_MODE", "ok")
events = json.loads(''' + repr(json.dumps(EVENTS)) + r''')
if mode == "fail":
    events = events[:2] + [{"type": "turn.failed", "error": {"message": "boom"}}]
if mode == "usage_limit":
    events = events[:2] + [{"type": "error", "message": "You've hit your usage limit. Visit https://chatgpt.com/codex/"
                            "settings/usage to purchase more credits or try again at Sep 14th, 2026 6:25 PM."},
                           {"type": "turn.failed", "error": {"message": "You've hit your usage limit."}}]
if mode == "storm":
    events = events[:2] + [{"type": "error", "message": "stream disconnected before completion: 503 Service Unavailable"},
                           {"type": "turn.failed", "error": {"message": "exceeded retry limit, last status: 503"}}]
for e in events:
    print(json.dumps(e)); sys.stdout.flush()
'''






def test_argv_sandboxes_the_workspace_approves_mcp_calls_and_states_the_effort(tmp_ws: Workspace):
    a = CodexAgent("gpt-6.1-sol", binary="codex")
    argv = a.build_argv(begin_session(AgentJob(workspace=str(tmp_ws.root), prompt="p"), "codex"))
    assert argv[argv.index("--sandbox") + 1] == "workspace-write"
    # codex exec has nobody to answer the per-tool approval elicitation → every MCP call would be cancelled
    assert 'mcp_servers.3dcode.default_tools_approval_mode="approve"' in argv
    s = begin_session(AgentJob(workspace=str(tmp_ws.root), prompt="p", spatial_tools=False), "codex")
    argv = a.build_argv(s)
    assert "mcp_servers" not in " ".join(argv)
    # `codex exec` defaults to medium; every harness call states the effort instead
    assert argv[argv.index("-c") + 1] == "model_reasoning_effort=high"
    assert split_model_effort("gpt-6-luna", "high") == ("gpt-6-luna", "high")
    with pytest.raises(ValueError, match="reasoning effort"):
        CodexAgent("gpt-6.1-sol", binary="codex", reasoning_effort="ludicrous")


def test_fake_run_and_every_failure_is_typed(tmp_ws: Workspace, fake_bin, monkeypatch):
    a = CodexAgent("gpt-6.1-sol", binary=fake_bin("codex", FAKE_CODEX))
    res = a.run(AgentJob(workspace=str(tmp_ws.root), prompt="hello", label="x", timeout_s=30))
    assert res.ok and res.exit_reason == "completed" and res.text == "Done." and res.tool_calls == 2, res.errors
    assert [f.path for f in res.files_changed] == ["src/hello.txt"] and (tmp_ws.src / "hello.txt").read_text() == "hello"
    assert res.usage.input_tokens == 1000
    # the turn count lands in the TYPED result (one turn.completed event), not only in result.json
    assert res.turns == 1 == json.loads((Path(res.transcript_path).parent / "result.json").read_text())["turns"]
    assert (Path(res.transcript_path).parent / "stdout.jsonl").exists()
    monkeypatch.setenv("FAKE_MODE", "fail")
    res2 = a.run(AgentJob(workspace=str(tmp_ws.root), prompt="hello", label="y", timeout_s=30))
    assert not res2.ok and res2.exit_reason == "error" and "boom" in res2.errors[0]
    assert not res2.transient and not res2.quota            # the task's own failure
    # a provider failure is typed quota or transient
    monkeypatch.setenv("FAKE_MODE", "usage_limit")
    res = a.run(AgentJob(workspace=str(tmp_ws.root), prompt="hello", label="q", timeout_s=30))
    assert not res.ok and res.quota and not res.transient
    monkeypatch.setenv("FAKE_MODE", "storm")
    res = a.run(AgentJob(workspace=str(tmp_ws.root), prompt="hello", label="t", timeout_s=30))
    assert not res.ok and res.transient and not res.quota and res.provider_wait_s == 0.0   # codex does not say

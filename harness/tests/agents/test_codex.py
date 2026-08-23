"""CodexAgent: JSONL parsing, argv with MCP overrides, fake binary, live smoke."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from codeverse.agents.cli_common import begin_session
from codeverse.agents.codex import CodexAgent, parse_codex_jsonl
from codeverse.contracts.agent import AgentJob
from codeverse.workspace import Workspace

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
prompt = args[-1]
if prompt == "-":
    prompt = sys.stdin.read()
assert "FAKE_SERVICE_API_KEY" not in os.environ
os.makedirs(os.path.join(ws, "src"), exist_ok=True)
open(os.path.join(ws, "src", "hello.txt"), "w").write(prompt[:5])
mode = os.environ.get("FAKE_MODE", "ok")
events = json.loads(''' + repr(json.dumps(EVENTS)) + r''')
if mode == "fail":
    events = events[:2] + [{"type": "turn.failed", "error": {"message": "boom"}}]
for e in events:
    print(json.dumps(e)); sys.stdout.flush()
'''


def test_parse_jsonl():
    ev = parse_codex_jsonl("\n".join(json.dumps(e) for e in EVENTS) + "\nnot json\n")
    assert ev.thread_id == "th1" and ev.messages == ["Done."] and ev.tool_calls == 2 and ev.turns_completed == 1
    u = ev.usage("gpt-5.6-sol")
    assert u.input_tokens == 1000 and u.cached_tokens == 600 and u.output_tokens == 50 and u.tool_calls == 2


def test_argv_with_mcp_overrides(tmp_ws: Workspace):
    a = CodexAgent("gpt-5.6-sol", binary="codex")
    s = begin_session(AgentJob(workspace=str(tmp_ws.root), prompt="p"), "codex")
    argv = a.build_argv(s, "p")
    assert argv[:3] == ["codex", "exec", "--json"] and argv[argv.index("-C") + 1] == str(tmp_ws.root)
    assert argv[argv.index("--sandbox") + 1] == "workspace-write" and "--skip-git-repo-check" in argv
    joined = " ".join(argv)
    assert 'mcp_servers.3dcv.command="' in joined and "mcp_servers.3dcv.args=[" in joined and argv[-1] == "p"
    # codex exec has nobody to answer the per-tool approval elicitation → every MCP call would be cancelled
    assert 'mcp_servers.3dcv.default_tools_approval_mode="approve"' in argv
    assert argv[argv.index("--model") + 1] == "gpt-5.6-sol"
    s2 = begin_session(AgentJob(workspace=str(tmp_ws.root), prompt="p", spatial_tools=False), "codex")
    assert "mcp_servers" not in " ".join(a.build_argv(s2, None)) and a.build_argv(s2, None)[-1] == "-"


def test_fake_run(tmp_ws: Workspace, fake_bin, monkeypatch):
    a = CodexAgent("gpt-5.6-sol", binary=fake_bin("codex", FAKE_CODEX))
    res = a.run(AgentJob(workspace=str(tmp_ws.root), prompt="hello", label="x", timeout_s=30))
    assert res.ok and res.exit_reason == "completed" and res.text == "Done." and res.tool_calls == 2, res.errors
    assert [f.path for f in res.files_changed] == ["src/hello.txt"]
    assert res.usage.input_tokens == 1000
    assert (Path(res.transcript_path).parent / "stdout.jsonl").exists()
    monkeypatch.setenv("FAKE_MODE", "fail")
    res2 = a.run(AgentJob(workspace=str(tmp_ws.root), prompt="hello", label="y", timeout_s=30))
    assert not res2.ok and res2.exit_reason == "error" and "boom" in res2.errors[0]


def test_long_prompt_goes_via_stdin(tmp_ws: Workspace, fake_bin, monkeypatch):
    monkeypatch.setattr("codeverse.agents.codex.STDIN_PROMPT_BYTES", 10)
    a = CodexAgent("gpt-5.6-sol", binary=fake_bin("codex", FAKE_CODEX))
    res = a.run(AgentJob(workspace=str(tmp_ws.root), prompt="hello world this is long", label="z", timeout_s=30))
    assert res.ok and (tmp_ws.src / "hello.txt").read_text() == "hello"


@pytest.mark.live
def test_live_codex_mcp_tool_call_is_not_cancelled(tmp_ws: Workspace):
    """Regression: without default_tools_approval_mode=approve codex auto-cancels every 3dcv MCP call."""
    if not shutil.which("codex"):
        pytest.skip("codex not installed")
    from codeverse.agents.cli_common import default_mcp_command
    from codeverse.agents.materialize import materialize_workspace

    materialize_workspace(tmp_ws, agent_kind="codex", contract_md="c", cookbook_rel="threejs/cookbook.md", spatial_tools=True,
                         mcp_command=default_mcp_command(tmp_ws, language="threejs"))
    a = CodexAgent("")
    res = a.run(AgentJob(workspace=str(tmp_ws.root), prompt="Call the 3dcv MCP tool `read_cookbook` once and reply with its first "
                         "five words, then DONE. Do not edit files.", timeout_s=300, label="mcpt", spatial_tools=True,
                         extra={"language": "threejs"}))
    assert res.ok, res.errors
    events = [json.loads(ln) for ln in (Path(res.transcript_path).parent / "stdout.jsonl").read_text().splitlines()
              if ln.startswith("{")]
    calls = [e["item"] for e in events if e.get("type") == "item.completed" and (e.get("item") or {}).get("type") == "mcp_tool_call"]
    assert calls and all(c.get("status") != "failed" for c in calls), calls


@pytest.mark.live
def test_live_codex_tiny(tmp_ws: Workspace):
    if not shutil.which("codex"):
        pytest.skip("codex not installed")
    a = CodexAgent("gpt-5.6-sol")
    res = a.run(AgentJob(workspace=str(tmp_ws.root), prompt="Create src/hello.txt containing exactly 'hi'. Reply DONE.",
                         timeout_s=300, label="live", spatial_tools=False))
    assert res.ok, res.errors
    assert (tmp_ws.src / "hello.txt").read_text().strip() == "hi"
    assert res.usage.input_tokens > 0

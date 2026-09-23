"""CodexAgent: JSONL parsing, argv with MCP overrides, fake binary, live smoke."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from codeverse3d.agents.backends import CodexAgent, parse_codex_jsonl, split_model_effort
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


def test_parse_jsonl():
    ev = parse_codex_jsonl("\n".join(json.dumps(e) for e in EVENTS) + "\nnot json\n")
    assert ev.thread_id == "th1" and ev.messages == ["Done."] and ev.tool_calls == 2 and ev.turns_completed == 1
    u = ev.usage("gpt-5.6-sol")
    assert u.input_tokens == 1000 and u.cached_tokens == 600 and u.output_tokens == 50 and u.tool_calls == 2


def test_reasoning_tokens_are_not_billed_twice():
    """`output_tokens` already contains `reasoning_output_tokens` (Responses API)."""
    ev = parse_codex_jsonl(json.dumps(
        {"type": "turn.completed", "usage": {"input_tokens": 1000, "output_tokens": 9000,
                                             "reasoning_output_tokens": 5000}}))
    u = ev.usage("gpt-5.6-sol")
    assert (u.output_tokens, u.thoughts_tokens) == (4000, 5000)
    # 1000 in @ $4/M + 9000 billable output @ $20/M — the reasoning share counted once
    assert u.cost_usd == pytest.approx(1000 * 4.0 / 1e6 + 9000 * 20.0 / 1e6)


def test_argv_with_mcp_overrides(tmp_ws: Workspace):
    a = CodexAgent("gpt-5.6-sol", binary="codex")
    s = begin_session(AgentJob(workspace=str(tmp_ws.root), prompt="p"), "codex")
    argv = a.build_argv(s)
    assert argv[:3] == ["codex", "exec", "--json"] and argv[argv.index("-C") + 1] == str(tmp_ws.root)
    assert argv[argv.index("--sandbox") + 1] == "workspace-write" and "--skip-git-repo-check" in argv
    joined = " ".join(argv)
    assert 'mcp_servers.3dcode.command="' in joined and "mcp_servers.3dcode.args=[" in joined and argv[-1] == "-"
    # the routed bundles only: codex's five bundled .system skills are switched off (codex debug prompt-input)
    assert argv[argv.index("skills.bundled.enabled=false") - 1] == "-c"
    # codex exec has nobody to answer the per-tool approval elicitation → every MCP call would be cancelled
    assert 'mcp_servers.3dcode.default_tools_approval_mode="approve"' in argv
    assert argv[argv.index("--model") + 1] == "gpt-5.6-sol"
    s2 = begin_session(AgentJob(workspace=str(tmp_ws.root), prompt="p", spatial_tools=False), "codex")
    assert "mcp_servers" not in " ".join(a.build_argv(s2)) and a.build_argv(s2)[-1] == "-"


def test_reasoning_effort_is_explicit(tmp_ws: Workspace, monkeypatch):
    """`codex exec` defaults to medium; every harness call states the effort instead."""
    s = begin_session(AgentJob(workspace=str(tmp_ws.root), prompt="p", spatial_tools=False), "codex")
    argv = CodexAgent("gpt-5.6-sol", binary="codex").build_argv(s)
    assert argv[argv.index("-c") + 1] == "model_reasoning_effort=high"
    a = CodexAgent("gpt-5.6-terra@medium", binary="codex")
    assert (a.model, a.reasoning_effort, a.id) == ("gpt-5.6-terra", "medium", "codex:gpt-5.6-terra")
    assert "model_reasoning_effort=medium" in a.build_argv(s)
    assert "model_reasoning_effort" not in " ".join(CodexAgent("gpt-5.6-sol", binary="codex",
                                                               reasoning_effort="").build_argv(s))
    assert split_model_effort("gpt-5.6-luna", "high") == ("gpt-5.6-luna", "high")
    with pytest.raises(ValueError, match="reasoning effort"):
        CodexAgent("gpt-5.6-sol", binary="codex", reasoning_effort="ludicrous")


def test_fake_run(tmp_ws: Workspace, fake_bin, monkeypatch):
    a = CodexAgent("gpt-5.6-sol", binary=fake_bin("codex", FAKE_CODEX))
    res = a.run(AgentJob(workspace=str(tmp_ws.root), prompt="hello", label="x", timeout_s=30))
    assert res.ok and res.exit_reason == "completed" and res.text == "Done." and res.tool_calls == 2, res.errors
    assert [f.path for f in res.files_changed] == ["src/hello.txt"]
    assert res.usage.input_tokens == 1000
    # the turn count lands in the TYPED result (one turn.completed event), not only in result.json
    assert res.turns == 1 == json.loads((Path(res.transcript_path).parent / "result.json").read_text())["turns"]
    assert (Path(res.transcript_path).parent / "stdout.jsonl").exists()
    monkeypatch.setenv("FAKE_MODE", "fail")
    res2 = a.run(AgentJob(workspace=str(tmp_ws.root), prompt="hello", label="y", timeout_s=30))
    assert not res2.ok and res2.exit_reason == "error" and "boom" in res2.errors[0]
    assert not res2.transient and not res2.quota            # the task's own failure


def test_a_codex_failure_is_typed_quota_or_transient(tmp_ws: Workspace, fake_bin, monkeypatch):
    """codex had no classification at all: its usage limit reached the round loop only because
    the loop's own list said "usage limit", and a 503 death was never transient."""
    a = CodexAgent("gpt-5.6-sol", binary=fake_bin("codex", FAKE_CODEX))
    monkeypatch.setenv("FAKE_MODE", "usage_limit")
    res = a.run(AgentJob(workspace=str(tmp_ws.root), prompt="hello", label="q", timeout_s=30))
    assert not res.ok and res.quota and not res.transient
    monkeypatch.setenv("FAKE_MODE", "storm")
    res = a.run(AgentJob(workspace=str(tmp_ws.root), prompt="hello", label="t", timeout_s=30))
    assert not res.ok and res.transient and not res.quota and res.provider_wait_s == 0.0   # codex does not say


def test_every_prompt_goes_via_stdin(tmp_ws: Workspace, fake_bin):
    a = CodexAgent("gpt-5.6-sol", binary=fake_bin("codex", FAKE_CODEX))
    res = a.run(AgentJob(workspace=str(tmp_ws.root), prompt="hello world", label="z", timeout_s=30))
    assert res.ok and (tmp_ws.src / "hello.txt").read_text() == "hello"


@pytest.mark.live
def test_live_codex_mcp_tool_call_is_not_cancelled(tmp_ws: Workspace):
    """Regression: without default_tools_approval_mode=approve codex auto-cancels every 3dcode MCP call."""
    if not shutil.which("codex"):
        pytest.skip("codex not installed")
    import trimesh

    from codeverse3d.agents.cli_common import default_mcp_command
    from codeverse3d.agents.materialize import materialize_workspace
    from codeverse3d.prompts import load_text

    trimesh.creation.box().export(tmp_ws.artifacts / "object.glb")
    materialize_workspace(tmp_ws, agent_kind="codex", contract_md="c", cookbook_text=load_text("threejs/cookbook.md"), spatial_tools=True,
                         mcp_command=default_mcp_command(tmp_ws, language="threejs"))
    a = CodexAgent("")
    res = a.run(AgentJob(workspace=str(tmp_ws.root), prompt="Call the 3dcode MCP tool `measure` exactly once, then reply with the "
                         "measured part count and DONE. Do not edit files.", timeout_s=300, label="mcpt",
                         spatial_tools=True, language="threejs"))
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

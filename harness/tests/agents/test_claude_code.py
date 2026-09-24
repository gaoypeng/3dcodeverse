"""ClaudeCodeAgent: argv, envelope parsing, fake binary, one live smoke test."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from codeverse3d.agents.backends import (
    ClaudeCodeAgent,
    parse_claude_json,
    primary_served_model,
    usage_from_envelope,
)
from codeverse3d.agents.cli_common import begin_session
from codeverse3d.agents.materialize import materialize_workspace
from codeverse3d.contracts.agent import AgentJob
from codeverse3d.workspace import Workspace

ENVELOPE = {
    "type": "result", "subtype": "success", "is_error": False, "duration_ms": 4200, "duration_api_ms": 3900,
    "num_turns": 3, "result": "Created src/hello.txt", "session_id": "abc", "total_cost_usd": 0.0123,
    "usage": {"input_tokens": 10, "cache_creation_input_tokens": 200, "cache_read_input_tokens": 5000, "output_tokens": 40},
    "modelUsage": {"claude-sonnet-4-6": {"inputTokens": 10, "outputTokens": 40, "costUSD": 0.0123}},
}

#: what a real `claude -p --model sonnet` session bills: the work model PLUS the small
#: background model the CLI uses for its own housekeeping — and claude 2.1.243 lists the
#: AUXILIARY one FIRST.  Token counts and costs are the ones observed in the CC-1 repro.
TWO_MODEL_ENVELOPE = {
    "type": "result", "subtype": "success", "is_error": False, "duration_ms": 10000,
    "num_turns": 1, "result": "pong", "session_id": "xyz", "total_cost_usd": 0.023795,
    "usage": {"input_tokens": 2, "output_tokens": 4, "cache_read_input_tokens": 0},
    "modelUsage": {
        "claude-haiku-4-5-20251001": {"inputTokens": 1035, "outputTokens": 21, "costUSD": 0.000955},
        "claude-sonnet-5": {"inputTokens": 2, "outputTokens": 4, "costUSD": 0.02284},
    },
}


FAKE_CLAUDE = r'''
args = sys.argv[1:]
assert args[args.index("-p") + 1].startswith("--"), "-p takes no prompt argument: the prompt comes on stdin"
prompt = sys.stdin.read()
assert "--dangerously-skip-permissions" in args and "--output-format" in args
assert "--strict-mcp-config" in args
assert "FAKE_SERVICE_API_KEY" not in os.environ
assert args[args.index("--setting-sources") + 1] == "project" and os.environ["CLAUDE_CODE_DISABLE_BUNDLED_SKILLS"] == "1"
mode = os.environ.get("FAKE_MODE", "ok")
if mode == "usage_limit":  # the subscription is spent: a result event, is_error, and claude's own words
    print(json.dumps({"type": "result", "subtype": "success", "is_error": True, "num_turns": 1,
                      "result": "Claude AI usage limit reached|1790000000", "session_id": "q"}))
    sys.exit(1)
if mode == "overloaded":   # what `claude -p` exits with when the API is out of capacity
    print('API Error: 529 {"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}', file=sys.stderr)
    sys.exit(1)
if mode == "rate_limited":
    print("API Error: 429 rate_limit_error: This request would exceed your rate limit", file=sys.stderr)
    sys.exit(1)
os.makedirs("src", exist_ok=True)
open("src/hello.txt", "w").write("hi")
open("src/prompt.txt", "w").write(prompt)
env = json.loads(''' + repr(json.dumps(ENVELOPE)) + r''')
if mode == "error":
    env["is_error"] = True; env["subtype"] = "error_max_turns"
if "--append-system-prompt" in args:
    env["result"] += " | sys=" + args[args.index("--append-system-prompt") + 1]
print(json.dumps({"type": "assistant", "message": {"id": "m1", "content": [
    {"type": "tool_use", "id": "t1", "name": "Write", "input": {"file_path": "src/hello.txt"}}]}}))
print("some log line")
print(json.dumps(env))
'''


def test_parse_envelope_variants():
    assert parse_claude_json(json.dumps(ENVELOPE))["session_id"] == "abc"
    assert parse_claude_json("noise\n" + json.dumps(ENVELOPE))["num_turns"] == 3
    assert parse_claude_json(json.dumps([{"type": "assistant"}, ENVELOPE]))["type"] == "result"
    assert parse_claude_json("") is None
    u = usage_from_envelope(ENVELOPE, "sonnet")
    assert u.cached_tokens == 5000 and u.cost_usd == 0.0123 and u.model == "claude-sonnet-4-6"
    # input_tokens is the TOTAL prompt (10 uncached + 5 000 read + 200 written), as for every
    # other backend: the uncached 10 alone let CostBucket clamp 4 990 cached + the 200 away
    assert u.input_tokens == 5210


def test_the_mcp_config_is_the_typed_jobs_never_the_workspace_file(tmp_ws: Workspace):
    cmd = ["python", "-m", "codeverse3d.spatial.mcp_server", "--workspace", str(tmp_ws.root)]
    materialize_workspace(tmp_ws, agent_kind="claude-code", contract_md="c", cookbook_text="", spatial_tools=True,
                          mcp_command=cmd)
    a = ClaudeCodeAgent("sonnet", binary="claude")
    s = begin_session(AgentJob(workspace=str(tmp_ws.root), prompt="p", mcp_command=cmd), "claude-code")
    (tmp_ws.root / ".mcp.json").write_text(json.dumps({"mcpServers": {"3dcode": {"command": "/tmp/evil", "args": []}}}))
    argv = a.build_argv(s)
    cfg = Path(argv[argv.index("--mcp-config") + 1])
    assert cfg.parent == s.traj.dir
    assert json.loads(cfg.read_text())["mcpServers"]["3dcode"]["command"] == "python"
    assert "mcp__3dcode__*" in argv[argv.index("--allowedTools") + 1]
    s2 = begin_session(AgentJob(workspace=str(tmp_ws.root), prompt="p", spatial_tools=False), "claude-code")
    assert "--mcp-config" not in a.build_argv(s2)


def test_fake_run_success_and_error(tmp_ws: Workspace, fake_bin, monkeypatch):
    a = ClaudeCodeAgent("sonnet", binary=fake_bin("claude", FAKE_CLAUDE))
    res = a.run(AgentJob(workspace=str(tmp_ws.root), prompt="hello", label="c", system_append="SYS", timeout_s=30))
    assert res.ok and res.exit_reason == "completed", res.errors
    assert res.text.endswith("sys=SYS") and res.usage.cost_usd == 0.0123
    # the calls the stream shows, not num_turns - 1 (N75: 24 "calls" for 3 real ones)
    assert res.tool_calls == res.usage.tool_calls == 1
    assert res.turns == 3  # the envelope's num_turns, in the typed result
    assert [f.path for f in res.files_changed] == ["src/hello.txt", "src/prompt.txt"]
    assert (tmp_ws.src / "prompt.txt").read_text() == "hello"   # on stdin, byte for byte
    rec = json.loads((Path(res.transcript_path).parent / "result.json").read_text())
    assert rec["num_turns"] == 3 and rec["session_id"] == "abc"
    monkeypatch.setenv("FAKE_MODE", "error")
    res2 = a.run(AgentJob(workspace=str(tmp_ws.root), prompt="hello", label="c2", timeout_s=30))
    assert not res2.ok and res2.exit_reason == "budget" and "error_max_turns" in res2.errors[0]
    assert res2.transient is False   # the task's own failure


@pytest.mark.parametrize("mode, reason, transient, quota", [
    ("usage_limit", "budget", False, True),     # the subscription is spent
    ("overloaded", "error", True, False),       # a 529 is the provider's, not the task's
    ("rate_limited", "budget", True, False),
])
def test_a_provider_exit_is_typed(tmp_ws: Workspace, fake_bin, monkeypatch, mode, reason, transient, quota):
    a = ClaudeCodeAgent("sonnet", binary=fake_bin("claude", FAKE_CLAUDE))
    monkeypatch.setenv("FAKE_MODE", mode)
    res = a.run(AgentJob(workspace=str(tmp_ws.root), prompt="hello", label="o", timeout_s=30))
    assert not res.ok and res.exit_reason == reason and res.transient is transient and res.quota is quota


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


def test_the_served_model_is_the_work_model_not_the_housekeeping_one():
    """`claude -p` bills two models and lists the auxiliary one first (CC-1)."""
    assert usage_from_envelope(TWO_MODEL_ENVELOPE, "").model == "claude-sonnet-5"
    assert primary_served_model(TWO_MODEL_ENVELOPE, "claude-haiku-4-5-20251001") == "claude-haiku-4-5-20251001"
    env = {**TWO_MODEL_ENVELOPE, "usage": {"input_tokens": 0, "output_tokens": 0}}   # no row lines up
    assert primary_served_model(env, "sonnet") == "claude-sonnet-5"   # the dearest wins


# --------------------------------------------------------------------------- killed sessions
FAKE_KILLED_CLAUDE = r'''
for ev in json.loads(os.environ["FAKE_EVENTS"]):
    print(json.dumps(ev), flush=True)
    time.sleep(0.05)
time.sleep(60)   # an overloaded provider: claude keeps retrying until the watchdog kills it
'''
_MSG_USAGE = {"input_tokens": 2, "cache_creation_input_tokens": 19190, "cache_read_input_tokens": 20354, "output_tokens": 5}
KILLED_STREAM = [
    {"type": "system", "subtype": "init", "session_id": "k", "model": "claude-sonnet-5", "skills": []},
    {"type": "assistant", "message": {"id": "msg_1", "model": "claude-sonnet-5", "usage": _MSG_USAGE,
                                      "content": [{"type": "text", "text": "Reading the plan"}]}},
    {"type": "assistant", "message": {"id": "msg_1", "model": "claude-sonnet-5", "usage": _MSG_USAGE,   # same message
                                      "content": [{"type": "tool_use", "id": "t1", "name": "Read", "input": {}}]}},
    {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1"}]}},
    {"type": "system", "subtype": "api_retry", "attempt": 1, "max_retries": 10, "retry_delay_ms": 500,
     "error_status": 529, "error": "overloaded"},
    {"type": "system", "subtype": "api_retry", "attempt": 2, "max_retries": 10, "retry_delay_ms": 1000,
     "error_status": 529, "error": "overloaded"},
]


def test_a_killed_session_is_booked_from_its_messages_and_its_retries_are_provider_wait(tmp_ws: Workspace,
                                                                                        fake_bin, monkeypatch):
    """A killed claude prints no result event: usage comes from each message, wait from api_retry."""
    from codeverse3d.models.pricing import cache_write_surcharge, estimate_cost

    monkeypatch.setenv("FAKE_EVENTS", json.dumps(KILLED_STREAM))
    monkeypatch.setattr("codeverse3d.agents.cli_common.IDLE_GRACE_S", 1.0)
    a = ClaudeCodeAgent("sonnet", binary=fake_bin("claude", FAKE_KILLED_CLAUDE))
    res = a.run(AgentJob(workspace=str(tmp_ws.root), prompt="hello", label="k", timeout_s=1, spatial_tools=False))
    assert not res.ok and res.exit_reason == "timeout" and res.transient and not res.quota
    u = res.usage
    assert (u.input_tokens, u.cached_tokens, u.output_tokens, u.model) == (2 + 19190 + 20354, 20354, 5, "claude-sonnet-5")
    assert u.cost_usd == pytest.approx(estimate_cost("anthropic", "claude-sonnet-5", u)
                                       + cache_write_surcharge("anthropic", "claude-sonnet-5", 19190)) and u.cost_usd > 0
    assert 0.0 < res.provider_wait_s <= res.duration_s
    rec = json.loads((Path(res.transcript_path).parent / "result.json").read_text())
    assert rec["usage_from"] == "stream messages"

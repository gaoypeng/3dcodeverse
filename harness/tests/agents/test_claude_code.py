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


def test_argv_includes_mcp_when_materialized(tmp_ws: Workspace):
    materialize_workspace(tmp_ws, agent_kind="claude-code", contract_md="c", cookbook_rel="", spatial_tools=True,
                          mcp_command=["python", "-m", "codeverse3d.spatial.mcp_server", "--workspace", str(tmp_ws.root)])
    a = ClaudeCodeAgent("sonnet", binary="claude")
    s = begin_session(AgentJob(workspace=str(tmp_ws.root), prompt="p", system_append="S", max_turns=7,
                           mcp_command=["python", "-m", "codeverse3d.spatial.mcp_server", "--workspace", str(tmp_ws.root)]),
                      "claude-code")
    argv = a.build_argv(s)
    assert argv[:3] == ["claude", "-p", "--output-format"] and "p" not in argv   # the prompt goes on stdin
    # the config is written fresh into THIS session's trajectory dir from the typed job,
    # never the workspace .mcp.json the agent can rewrite between rounds
    cfg = Path(argv[argv.index("--mcp-config") + 1])
    assert cfg.name == "mcp.json" and cfg.parent == s.traj.dir
    server = json.loads(cfg.read_text())["mcpServers"]["3dcode"]
    assert server["command"] == "python" and "--workspace" in server["args"]  # the TYPED job command, verbatim
    (tmp_ws.root / ".mcp.json").write_text(json.dumps(
        {"mcpServers": {"3dcode": {"command": "/tmp/evil", "args": []}}}))
    argv2 = a.build_argv(s)   # a tampered workspace file changes nothing
    assert json.loads(Path(argv2[argv2.index("--mcp-config") + 1]).read_text(
    ))["mcpServers"]["3dcode"]["command"].endswith("python")
    assert "--strict-mcp-config" in argv and argv[argv.index("--max-turns") + 1] == "7"
    assert argv[argv.index("--append-system-prompt") + 1] == "S" and argv[argv.index("--model") + 1] == "sonnet"
    assert "mcp__3dcode__*" in argv[argv.index("--allowedTools") + 1]
    s2 = begin_session(AgentJob(workspace=str(tmp_ws.root), prompt="p", spatial_tools=False), "claude-code")
    assert "--mcp-config" not in a.build_argv(s2)


def test_a_session_sees_only_the_routed_skills(tmp_ws: Workspace):
    """claude-code 2.1.280 listed 26 skills besides the routed bundles (2026-09-22 rig).  No
    "user" setting source drops the account-synced skills and plugins; the env switch drops the
    bundled ones; the two that survive it by design are hidden by name.  Checked against a local
    fake API: the init event then lists exactly the routed bundles."""
    from codeverse3d.agents.backends import CLAUDE_SETTINGS

    a = ClaudeCodeAgent("sonnet", binary="claude")
    s = begin_session(AgentJob(workspace=str(tmp_ws.root), prompt="p", spatial_tools=False), "claude-code")
    argv = a.build_argv(s)
    assert argv[argv.index("--setting-sources") + 1] == "project"
    assert json.loads(argv[argv.index("--settings") + 1]) == CLAUDE_SETTINGS == {
        "skillOverrides": {"design": "off", "doctor": "off"}}
    assert a.build_env(s)["CLAUDE_CODE_DISABLE_BUNDLED_SKILLS"] == "1"
    assert "--disable-slash-commands" not in argv   # claude-code: "Disable all skills" — the routed ones too


def test_the_usage_limit_is_quota_not_a_transient_death(tmp_ws: Workspace, fake_bin, monkeypatch):
    a = ClaudeCodeAgent("sonnet", binary=fake_bin("claude", FAKE_CLAUDE))
    monkeypatch.setenv("FAKE_MODE", "usage_limit")
    res = a.run(AgentJob(workspace=str(tmp_ws.root), prompt="hello", label="u", timeout_s=30))
    assert not res.ok and res.quota and not res.transient and res.exit_reason == "budget"


def test_fake_run_success_and_error(tmp_ws: Workspace, fake_bin, monkeypatch):
    a = ClaudeCodeAgent("sonnet", binary=fake_bin("claude", FAKE_CLAUDE))
    res = a.run(AgentJob(workspace=str(tmp_ws.root), prompt="hello", label="c", system_append="SYS", timeout_s=30))
    assert res.ok and res.exit_reason == "completed", res.errors
    assert res.text.endswith("sys=SYS") and res.usage.cost_usd == 0.0123 and res.tool_calls == 2
    assert res.turns == 3  # the envelope's num_turns, in the typed result
    assert [f.path for f in res.files_changed] == ["src/hello.txt", "src/prompt.txt"]
    assert (tmp_ws.src / "prompt.txt").read_text() == "hello"   # on stdin, byte for byte
    rec = json.loads((Path(res.transcript_path).parent / "result.json").read_text())
    assert rec["num_turns"] == 3 and rec["session_id"] == "abc"
    monkeypatch.setenv("FAKE_MODE", "error")
    res2 = a.run(AgentJob(workspace=str(tmp_ws.root), prompt="hello", label="c2", timeout_s=30))
    assert not res2.ok and res2.exit_reason == "budget" and "error_max_turns" in res2.errors[0]
    assert res2.transient is False   # the task's own failure


def test_an_overloaded_exit_is_transient(tmp_ws: Workspace, fake_bin, monkeypatch):
    """A 529 exit is the provider's, not the task's: it must say so (AgentResult.transient) —
    the storm fallback and the repair loop's stop both key on it, and it was always False."""
    a = ClaudeCodeAgent("sonnet", binary=fake_bin("claude", FAKE_CLAUDE))
    monkeypatch.setenv("FAKE_MODE", "overloaded")
    res = a.run(AgentJob(workspace=str(tmp_ws.root), prompt="hello", label="o", timeout_s=30))
    assert not res.ok and res.exit_reason == "error" and res.transient is True and not res.quota
    monkeypatch.setenv("FAKE_MODE", "rate_limited")
    res = a.run(AgentJob(workspace=str(tmp_ws.root), prompt="hello", label="q", timeout_s=30))
    assert not res.ok and res.exit_reason == "budget" and res.transient is True and not res.quota


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


# --------------------------------------------------------------------------- CC-1
def test_an_alias_records_the_model_that_did_the_work_not_the_housekeeping_one():
    """`claude -p` bills two models and lists the AUXILIARY one first, so
    `served[0]` recorded haiku — which did 0.9% of the tokens — for every
    --model ALIAS ('sonnet', 'opus', and the default arm). A sonnet-vs-default
    comparison was therefore labelled haiku-vs-haiku."""
    for alias in ("sonnet", "opus", ""):
        u = usage_from_envelope(TWO_MODEL_ENVELOPE, alias)
        assert u.model == "claude-sonnet-5", alias
    # the top-level usage block reports the MAIN conversation only — that is the signal
    assert primary_served_model(TWO_MODEL_ENVELOPE, "sonnet") == "claude-sonnet-5"


def test_a_full_id_we_passed_verbatim_is_kept():
    """Full ids were never affected; the bug bit only the form the owner asked for."""
    assert primary_served_model(TWO_MODEL_ENVELOPE, "claude-sonnet-5") == "claude-sonnet-5"
    assert primary_served_model(TWO_MODEL_ENVELOPE, "claude-haiku-4-5-20251001") == "claude-haiku-4-5-20251001"


def test_the_dearest_entry_wins_when_the_token_counts_do_not_match():
    """Fallback for an envelope whose top-level usage block does not line up with any
    modelUsage row: the work model is the one that cost the money, never the side-call."""
    env = {**TWO_MODEL_ENVELOPE, "usage": {"input_tokens": 0, "output_tokens": 0}}
    assert primary_served_model(env, "sonnet") == "claude-sonnet-5"


def test_no_model_usage_block_keeps_what_we_asked_for():
    assert primary_served_model({"usage": {"input_tokens": 1}}, "sonnet") == "sonnet"
    assert usage_from_envelope({"usage": {"input_tokens": 1}}, "sonnet").model == "sonnet"


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
    """claude prints no result event when it is killed (checked 2026-09-22: SIGTERM mid-request →
    nothing), so the envelope's usage never comes.  Each message's own usage block does: the input
    side exact, the output a floor.  Its api_retry events (claude's own, with the announced
    back-off) are the time the provider cost — here until the kill, since nothing followed."""
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

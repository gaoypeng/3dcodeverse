"""GeminiCliAgent with a fake `gemini` binary + one live smoke test."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from codeverse.agents.gemini_cli import GeminiCliAgent, parse_gemini_json, usage_from_stats
from codeverse.config import get_settings
from codeverse.contracts.agent import AgentJob
from codeverse.workspace import Workspace

FAKE_GEMINI = r'''
args = sys.argv[1:]
model = args[args.index("-m") + 1]
prompt = args[args.index("-p") + 1]
mode = os.environ.get("FAKE_MODE", "ok")
assert os.environ.get("GEMINI_API_KEY"), "no key in env"
assert "GEMINI_API_KEYS" not in os.environ
assert "FAKE_SERVICE_API_KEY" not in os.environ, "secret leaked"
assert os.path.isfile(os.environ["GEMINI_CLI_SYSTEM_SETTINGS_PATH"])
if mode in ("fail_once", "fail_once_503", "fail_always"):
    marker = "artifacts/attempts.txt"
    n = int(open(marker).read()) if os.path.exists(marker) else 0
    open(marker, "w").write(str(n + 1))
    if n == 0 or mode == "fail_always":
        if mode == "fail_once_503":
            print("Error when talking to Gemini API: got status: UNAVAILABLE 503", file=sys.stderr)
            sys.exit(247)
        print("Error: 429 RESOURCE_EXHAUSTED quota", file=sys.stderr)
        sys.exit(1)
if mode == "hang":
    time.sleep(60)
served = "gemini-9-pro" if mode == "substitute" else model
os.makedirs("src", exist_ok=True)
open("src/hello.txt", "w").write(prompt[:20])
out = {"session_id": "s1", "response": "DONE: " + prompt[:10],
       "stats": {"models": {served: {"tokens": {"input": 95, "prompt": 100, "candidates": 20, "cached": 5, "thoughts": 7}}},
                 "tools": {"totalCalls": 1}}}
print(json.dumps(out, indent=2))
'''


@pytest.fixture
def agent(fake_bin, monkeypatch):
    binary = fake_bin("gemini", FAKE_GEMINI)
    s = get_settings()
    monkeypatch.setattr(s, "gemini_api_keys", ["k1", "k2"])
    monkeypatch.setattr(s, "cache_dir", Path(binary).parent / "cache")
    monkeypatch.delenv("GEMINI_API_KEYS", raising=False)
    return GeminiCliAgent("gemini-3.7-flash", binary=binary)


def _job(ws: Workspace, **kw) -> AgentJob:
    base = dict(workspace=str(ws.root), prompt="write hello", model="gemini-3.7-flash", timeout_s=20, label="t")
    base.update(kw)
    return AgentJob(**base)


def test_success_path(tmp_ws: Workspace, agent: GeminiCliAgent):
    res = agent.run(_job(tmp_ws))
    assert res.ok and res.exit_reason == "completed", res.errors
    assert res.text.startswith("DONE")
    assert [f.path for f in res.files_changed] == ["src/hello.txt"]
    assert res.usage.input_tokens == 100 and res.usage.output_tokens == 20 and res.usage.thoughts_tokens == 7
    assert res.usage.tool_calls == 1 and res.tool_calls == 1
    traj = Path(res.transcript_path).parent
    assert (traj / "stdout.json").exists() and (traj / "prompt.md").exists() and (traj / "result.json").exists()
    rec = json.loads((traj / "result.json").read_text())
    assert rec["attempts"] == 1 and rec["session_id"] == "s1"
    # commits: pre + agent
    assert "agent:t" in tmp_ws._git("log", "--oneline").stdout


def test_agent_planted_mcp_server_never_reaches_the_cli(tmp_ws: Workspace, agent: GeminiCliAgent):
    """``ws/.gemini/settings.json`` is agent-writable and gemini-cli merged its mcpServers,
    so an agent could choose what the NEXT round's CLI launched (`gemini mcp list` in a
    poisoned workspace tried to start it).  3dcv + ``mcp.allowed`` now live in the
    per-session system settings, which is applied LAST and whose ``mcp.allowed``
    REPLACES rather than merges (audit 2026-08-27)."""
    from codeverse.agents.cli_common import begin_session, default_mcp_command, release_session
    from codeverse.agents.materialize import materialize_workspace

    materialize_workspace(tmp_ws, agent_kind="gemini-cli", contract_md="c", cookbook_rel="",
                          spatial_tools=True, mcp_command=default_mcp_command(tmp_ws))
    planted = tmp_ws.root / ".gemini" / "settings.json"
    data = json.loads(planted.read_text())
    data["mcpServers"] = {"evil": {"command": "/tmp/evil"}}
    data["mcp"] = {"allowed": ["evil"]}
    planted.write_text(json.dumps(data))

    s = begin_session(_job(tmp_ws), "gemini-cli")
    try:
        env = agent.build_env(s, "k1")
    finally:
        release_session(s)
    path = Path(env["GEMINI_CLI_SYSTEM_SETTINGS_PATH"])
    settings = json.loads(path.read_text())
    assert path.parent == s.traj.dir, "per session, in the harness-owned trajectory dir"
    assert list(settings["mcpServers"]) == ["3dcv"] and settings["mcp"]["allowed"] == ["3dcv"]
    assert "evil" not in json.dumps(settings)
    assert settings["security"]["folderTrust"]["enabled"] is False


def test_model_substitution_detected(tmp_ws: Workspace, agent: GeminiCliAgent, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "substitute")
    res = agent.run(_job(tmp_ws))
    assert not res.ok and res.exit_reason == "model_substituted"
    assert "gemini-9-pro" in res.errors[0]


def test_transient_failure_retries_once_with_other_key(tmp_ws: Workspace, agent: GeminiCliAgent, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "fail_once")
    res = agent.run(_job(tmp_ws))
    assert res.ok, res.errors
    assert (tmp_ws.artifacts / "attempts.txt").read_text() == "2"
    rec = json.loads((Path(res.transcript_path).parent / "result.json").read_text())
    assert rec["attempts"] == 2 and (Path(res.transcript_path).parent / "stdout.2.json").exists()
    lines = [json.loads(ln) for ln in Path(res.transcript_path).read_text().splitlines()]
    keys = [ln["key_tail"] for ln in lines if ln["kind"] == "invoke"]
    assert keys == ["k1", "k2"]


def test_single_key_transient_failure_retries_same_key_and_never_raises(tmp_ws: Workspace, agent: GeminiCliAgent, monkeypatch):
    """Settings with ONE key: the retry must reuse it (no KeyPoolExhausted out of run())."""
    from codeverse.agents import gemini_cli as gc

    monkeypatch.setattr(get_settings(), "gemini_api_keys", ["only"])
    gc._POOLS.pop(("only",), None)
    monkeypatch.setenv("FAKE_MODE", "fail_once_503")
    res = agent.run(_job(tmp_ws))
    assert res.ok, res.errors
    assert (tmp_ws.artifacts / "attempts.txt").read_text() == "2"
    lines = [json.loads(ln) for ln in Path(res.transcript_path).read_text().splitlines()]
    assert [ln["key_tail"] for ln in lines if ln["kind"] == "invoke"] == ["only", "only"]


def test_single_key_quota_failure_returns_budget_without_retry(tmp_ws: Workspace, agent: GeminiCliAgent, monkeypatch):
    """429 puts the only key into cooldown: no alternative → return the failed outcome (ok=False), do not raise."""
    from codeverse.agents import gemini_cli as gc

    monkeypatch.setattr(get_settings(), "gemini_api_keys", ["solo"])
    monkeypatch.setattr(gc, "RETRY_KEY_WAIT_S", 0.2)
    gc._POOLS.pop(("solo",), None)
    monkeypatch.setenv("FAKE_MODE", "fail_always")
    res = agent.run(_job(tmp_ws))
    assert not res.ok and res.exit_reason == "budget"
    assert (tmp_ws.artifacts / "attempts.txt").read_text() == "1"
    rec = json.loads((Path(res.transcript_path).parent / "result.json").read_text())
    assert rec["attempts"] == 1 and any("no usable key" in n for n in rec["notes"])


def test_pool_exhausted_before_first_attempt_is_a_budget_result(tmp_ws: Workspace, agent: GeminiCliAgent, monkeypatch):
    from codeverse.agents import gemini_cli as gc
    from codeverse.models.keypool import KeyPoolExhausted

    class Dead:
        def acquire(self, **kw):
            raise KeyPoolExhausted("all 2 keys throttled; waited 120s")

        def report(self, *a, **k):
            return None

    monkeypatch.setattr(gc, "_key_pool", lambda keys: Dead())
    res = agent.run(_job(tmp_ws))
    assert not res.ok and res.exit_reason == "budget" and "exhausted" in res.errors[0]


def test_timeout_is_reported(tmp_ws: Workspace, agent: GeminiCliAgent, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "hang")
    monkeypatch.setattr("codeverse.agents.cli_common.IDLE_GRACE_S", 1.0)
    res = agent.run(_job(tmp_ws, timeout_s=1))
    assert not res.ok and res.exit_reason == "timeout" and res.duration_s < 30


def test_unavailable_when_no_keys(tmp_ws: Workspace, agent: GeminiCliAgent, monkeypatch):
    monkeypatch.setattr(get_settings(), "gemini_api_keys", [])
    ok, why = agent.available()
    assert not ok and "key" in why
    res = agent.run(_job(tmp_ws))
    assert not res.ok and res.exit_reason == "error"


def test_parse_helpers():
    assert parse_gemini_json("noise\n{\"response\": \"x\"}") == {"response": "x"}
    assert parse_gemini_json("") is None
    # no `prompt` key: total prompt = uncached input + cached
    u = usage_from_stats({"models": {"m": {"tokens": {"input": 1, "candidates": 2, "cached": 3, "thoughts": 4}}},
                          "tools": {"totalCalls": 9}}, "m")
    assert (u.input_tokens, u.output_tokens, u.cached_tokens, u.thoughts_tokens, u.tool_calls) == (4, 2, 3, 4, 9)


def test_usage_input_is_total_prompt_and_cost_reprices_cache():
    """gemini-cli reports tokens.input = prompt - cached; pricing wants the TOTAL prompt (regression: ~4x under-billing)."""
    from codeverse.models.pricing import estimate_cost, lookup_price

    tok = {"input": 720_753, "prompt": 14_190_170, "cached": 13_469_417, "candidates": 62_012, "thoughts": 66_809}
    u = usage_from_stats({"models": {"gemini-3.7-flash": {"tokens": tok}}}, "gemini-3.7-flash")
    assert u.input_tokens == tok["prompt"] and u.cached_tokens == tok["cached"]
    price = lookup_price("gemini", "gemini-3.7-flash")
    assert price is not None
    m = 1_000_000
    want = ((tok["prompt"] - tok["cached"]) * price.input + tok["cached"] * price.cached
            + (tok["candidates"] + tok["thoughts"]) * price.output) / m
    assert u.cost_usd == pytest.approx(want, rel=1e-9)
    assert u.cost_usd == pytest.approx(estimate_cost("gemini", "gemini-3.7-flash", u), rel=1e-9)
    assert u.cost_usd > 3 * estimate_cost("gemini", "gemini-3.7-flash", u.model_copy(update={"input_tokens": tok["input"]}))


def test_usage_prices_each_served_model_at_its_own_rate():
    from codeverse.contracts.common import Usage
    from codeverse.models.pricing import estimate_cost

    stats = {"models": {"gemini-3.7-flash": {"tokens": {"prompt": 1_000_000, "cached": 0, "candidates": 1000}},
                        "gemini-3-flash-preview": {"tokens": {"prompt": 1_000_000, "cached": 0, "candidates": 1000}},
                        "unknown-utility-model-x": {"tokens": {"prompt": 1_000_000, "cached": 0, "candidates": 1000}}}}
    u = usage_from_stats(stats, "gemini-3.7-flash")
    assert u.input_tokens == 3_000_000 and u.output_tokens == 3000
    one = Usage(input_tokens=1_000_000, output_tokens=1000)
    want = (estimate_cost("gemini", "gemini-3.7-flash", one) + estimate_cost("gemini", "gemini-3-flash-preview", one)
            + estimate_cost("gemini", "gemini-3.7-flash", one))  # unknown model falls back to the requested rate
    assert u.cost_usd == pytest.approx(want, rel=1e-9)


def test_system_append_is_prepended(tmp_ws: Workspace, agent: GeminiCliAgent):
    res = agent.run(_job(tmp_ws, system_append="BE BRIEF"))
    assert res.ok
    assert (tmp_ws.src / "hello.txt").read_text().startswith("<harness_instr")


@pytest.mark.live
def test_live_gemini_cli_creates_file(tmp_ws: Workspace):
    if not get_settings().gemini_api_keys:
        pytest.skip("no gemini keys")
    agent = GeminiCliAgent("gemini-3.7-flash")
    job = AgentJob(workspace=str(tmp_ws.root), prompt="Create the file src/hello.txt containing exactly the word 'hi'. "
                   "Then reply with the single word DONE.", timeout_s=240, label="live", spatial_tools=False)
    res = agent.run(job)
    assert res.ok, res.errors
    assert (tmp_ws.src / "hello.txt").read_text().strip() == "hi"
    assert res.usage.input_tokens > 0 and res.usage.model == "gemini-3.7-flash"
    assert any(f.path == "src/hello.txt" for f in res.files_changed)

"""GeminiCliAgent with a fake `gemini` binary + one live smoke test."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from codeverse3d.agents.backends import GeminiCliAgent, parse_gemini_json, usage_from_stats
from codeverse3d.config import get_settings
from codeverse3d.contracts.agent import AgentJob
from codeverse3d.workspace import Workspace

FAKE_GEMINI = r'''
from datetime import datetime, timezone
args = sys.argv[1:]
model = args[args.index("-m") + 1]
assert "-p" not in args and "--prompt" not in args, "the prompt goes on stdin"
prompt = sys.stdin.read()
open("artifacts/stdin.txt", "w").write(prompt)
mode = os.environ.get("FAKE_MODE", "ok")

def chat_record():
    """What gemini-cli 0.53 appends to ~/.gemini/tmp/<project>/chats/ as the session runs."""
    proj = os.path.join(os.environ["GEMINI_CLI_HOME"], ".gemini", "tmp", "fake-project")
    os.makedirs(os.path.join(proj, "chats"), exist_ok=True)
    open(os.path.join(proj, ".project_root"), "w").write(os.getcwd())
    now = lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    rows = [{"sessionId": "fake", "kind": "main"},
            {"id": "u1", "type": "user", "timestamp": now(), "content": [{"text": prompt[:20]}]},
            {"id": "g1", "type": "gemini", "timestamp": now(), "model": model, "content": "",
             "toolCalls": [{"id": "write_file__1", "name": "write_file", "args": {"file_path": "src/a.js"}, "status": "success"}],
             "tokens": {"input": 1000, "output": 20, "cached": 400, "thoughts": 7, "tool": 0, "total": 1027}},
            {"id": "g1", "type": "gemini", "timestamp": now(), "model": model, "content": "",     # re-written: once
             "toolCalls": [{"id": "write_file__1", "name": "write_file", "args": {"file_path": "src/a.js"}, "status": "success"}],
             "tokens": {"input": 1000, "output": 20, "cached": 400, "thoughts": 7, "tool": 0, "total": 1027}},
            {"id": "u2", "type": "user", "timestamp": now(), "content": [{"functionResponse": {"id": "write_file__1"}}]},
            {"id": "g2", "type": "gemini", "timestamp": now(), "model": model, "content": "",
             "tokens": {"input": 3000, "output": 50, "cached": 2000, "thoughts": 13, "tool": 0, "total": 3063}}]
    with open(os.path.join(proj, "chats", "session-2026-09-22T21-00-fake0001.jsonl"), "a") as f:
        f.write("".join(json.dumps(r) + "\n" for r in rows))
assert os.environ.get("GEMINI_API_KEY"), "no key in env"
assert "GEMINI_API_KEYS" not in os.environ
assert "FAKE_SERVICE_API_KEY" not in os.environ, "secret leaked"
assert os.path.isfile(os.environ["GEMINI_CLI_SYSTEM_SETTINGS_PATH"])
if mode in ("fail_once", "fail_once_503", "fail_always", "fail_twice_429"):
    marker = "artifacts/attempts.txt"
    n = int(open(marker).read()) if os.path.exists(marker) else 0
    open(marker, "w").write(str(n + 1))
    if n == 0 or mode == "fail_always" or (mode == "fail_twice_429" and n < 2):
        if mode == "fail_once_503":
            print("Error when talking to Gemini API: got status: UNAVAILABLE 503", file=sys.stderr)
            sys.exit(247)
        print("Error: 429 RESOURCE_EXHAUSTED quota", file=sys.stderr)
        sys.exit(1)
if mode == "hang":
    time.sleep(60)
if mode == "crash":        # the agent's own failure: nothing transient about it
    print("TypeError: Cannot read properties of undefined (reading 'parts')", file=sys.stderr)
    sys.exit(2)
if mode == "storm":        # what gemini-cli 0.53 prints, 8-17 times per session, in a 503 storm
    chat_record()
    for i in range(4):
        print(f"Attempt {i + 1} failed with status 503. Retrying with backoff... _ApiError: UNAVAILABLE", file=sys.stderr)
        sys.stderr.flush()
        time.sleep(0.05)
    time.sleep(60)
if mode == "gave_up":      # 0.53 after its own retries: an error object on STDERR, no stats, exit(503) = 247
    chat_record()
    print("Attempt 1 failed with status 503. Retrying with backoff... _ApiError: UNAVAILABLE", file=sys.stderr)
    print("Error when talking to Gemini API Full report available at: /tmp/x.json", file=sys.stderr)
    print(json.dumps({"session_id": "s9", "error": {"type": "Error", "message": "got status: UNAVAILABLE.", "code": 503}},
                     indent=2), file=sys.stderr)
    sys.exit(247)
if mode == "ok_with_record":   # every real session writes both: the envelope must win, never add
    chat_record()
served = "gemini-9-pro" if mode == "substitute" else model
os.makedirs("src", exist_ok=True)
open("src/hello.txt", "w").write(prompt[:20])
out = {"session_id": "s1", "response": "DONE: " + prompt[:10],
       "stats": {"models": {served: {"tokens": {"input": 95, "prompt": 100, "candidates": 20, "cached": 5, "thoughts": 7}}},
                 "tools": {"totalCalls": 1}}}
print(json.dumps(out, indent=2))
'''


@pytest.fixture
def agent(fake_bin, monkeypatch, tmp_path):
    binary = fake_bin("gemini", FAKE_GEMINI)
    monkeypatch.setenv("GEMINI_CLI_HOME", str(tmp_path / "gemini_home"))   # the fake writes its chat record here
    s = get_settings()
    monkeypatch.setattr(s, "gemini_api_keys", ["k1", "k2"])
    monkeypatch.setattr(s, "cache_dir", Path(binary).parent / "cache")
    monkeypatch.delenv("GEMINI_API_KEYS", raising=False)
    # shared_pool caches per (keys, quota) for the life of the PROCESS, so an earlier
    # test in this worker can leave k1 spent or cooling and the next invoke starts on
    # k2 — which broke `keys == ["k1", "k2"]` once the suite went parallel and test
    # grouping changed.  Every test here starts from a fresh pool.
    from codeverse3d.models import gemini as gm
    for sig in [x for x in list(gm._pools) if x and x[0] in ("k1", "only")]:  # noqa: SLF001
        gm._pools.pop(sig, None)  # noqa: SLF001
    return GeminiCliAgent("gemini-3.7-flash", binary=binary)


def test_retry_window_never_restarts():
    """The retry rule itself: what is LEFT of the window, or None when < min(120, T/4)."""
    from codeverse3d.agents.backends import retry_window_left

    assert retry_window_left(1800, 1700) is None          # 100s left < 120 floor
    assert retry_window_left(1800, 900) == 900            # half the window remains
    assert retry_window_left(20, 2) == 18                 # small test jobs keep retrying
    assert retry_window_left(20, 16) is None              # 4s left < 5s (T/4)


def _job(ws: Workspace, **kw) -> AgentJob:
    base = dict(workspace=str(ws.root), prompt="write hello", model="gemini-3.7-flash", timeout_s=20, label="t")
    base.update(kw)
    return AgentJob(**base)


def test_the_prompt_arrives_on_stdin_whole(tmp_ws: Workspace, agent: GeminiCliAgent):
    """No -p: a piped stdin makes gemini-cli 0.53 headless and IS the input.  A 5-zone scene
    prompt runs past 2 000 lines — the window of the read_file tool the old >100 kB file stub
    relied on — and its last zone must still arrive (checked live 2026-09-22, docs/SKILLS.md)."""
    prompt = "\n".join(f"zone {i % 5} requirement line {i}" for i in range(2600)) + "\nLAST-ZONE-SENTINEL"
    res = agent.run(_job(tmp_ws, prompt=prompt))
    assert res.ok, res.errors
    assert (tmp_ws.artifacts / "stdin.txt").read_text() == prompt


def test_a_session_killed_in_a_storm_is_booked_from_its_chat_record(tmp_ws: Workspace, agent: GeminiCliAgent,
                                                                     monkeypatch):
    """The watchdog killed it, so the CLI printed no envelope — and until 2026-09-22 the session
    was booked at $0 while its chat record held every reply's tokens (live: 6.47 M prompt tokens,
    ≈ $1.2, in one baseline).  The record is the usage now, and its tool calls the tool count."""
    from codeverse3d.models.pricing import estimate_cost

    monkeypatch.setenv("FAKE_MODE", "storm")
    monkeypatch.setattr("codeverse3d.agents.cli_common.IDLE_GRACE_S", 1.0)
    res = agent.run(_job(tmp_ws, timeout_s=1))
    assert not res.ok and res.exit_reason == "timeout" and res.transient
    u = res.usage
    assert (u.input_tokens, u.cached_tokens, u.output_tokens, u.thoughts_tokens) == (4000, 2400, 70, 20)  # g1 once
    assert u.cost_usd == pytest.approx(estimate_cost("gemini", "gemini-3.7-flash", u), abs=1e-6) and u.cost_usd > 0
    assert res.tool_calls == u.tool_calls == 1
    # the storm ran from the last reply until the kill: all of it lost to the provider
    assert 0.0 < res.provider_wait_s <= res.duration_s
    rec = json.loads((Path(res.transcript_path).parent / "result.json").read_text())
    assert rec["usage_from"] == ["chat record"]


def test_the_metered_proxy_books_a_killed_sessions_record_once(tmp_ws: Workspace, agent: GeminiCliAgent, monkeypatch,
                                                                tmp_path):
    """Filling AgentResult.usage is the whole fix: MeteredAgent writes the one session row from
    it (it wrote NONE for a $0 session) and the round's BudgetGuard is charged the same usage."""
    from codeverse3d.cost import load_ledger
    from codeverse3d.cost.instrument import MeteredAgent, run_ledger

    monkeypatch.setenv("FAKE_MODE", "storm")
    monkeypatch.setattr("codeverse3d.agents.cli_common.IDLE_GRACE_S", 1.0)
    with run_ledger(tmp_path / "run", run="r1"):
        res = MeteredAgent(agent).run(_job(tmp_ws, timeout_s=1, kind="baseline"))
    (row,) = load_ledger(tmp_path / "run")
    assert row.source == "session" and row.outcome == "timeout" and row.input_tokens == res.usage.input_tokens == 4000
    assert row.cost_usd == pytest.approx(res.usage.cost_usd, abs=1e-6) and row.cost_usd > 0


def test_a_session_that_gave_up_is_booked_from_its_record_and_is_typed_transient(tmp_ws: Workspace,
                                                                                  agent: GeminiCliAgent, monkeypatch):
    """0.53 writes its give-up object to STDERR with no stats and exits 503 & 255 = 247: every
    gemini-cli session of the 2026-09-22 live runs that "completed a round" ended this way."""
    monkeypatch.setattr(get_settings(), "gemini_api_keys", ["only"])
    from codeverse3d.models import gemini as gm
    for sig in [x for x in list(gm._pools) if x and x[0] == "only"]:  # noqa: SLF001
        gm._pools.pop(sig, None)  # noqa: SLF001
    monkeypatch.setenv("FAKE_MODE", "gave_up")
    res = agent.run(_job(tmp_ws))
    assert not res.ok and res.exit_reason == "error" and res.transient and not res.quota
    assert res.usage.input_tokens == 2 * 4000        # both attempts, each from its own record
    rec = json.loads((Path(res.transcript_path).parent / "result.json").read_text())
    assert rec["attempts"] == 2 and rec["usage_from"] == ["chat record", "chat record"] and rec["session_id"] == "s9"


def test_the_envelope_wins_over_the_record_and_nothing_is_booked_twice(tmp_ws: Workspace, agent: GeminiCliAgent,
                                                                       monkeypatch):
    """A completed session leaves an envelope AND a chat record; the record is read (it is the
    tool trace) but its tokens are used only when the envelope has none — the metered proxy
    books AgentResult.usage once, so adding them would double the bill."""
    monkeypatch.setenv("FAKE_MODE", "ok_with_record")
    res = agent.run(_job(tmp_ws))
    assert res.ok and res.usage.input_tokens == 100 and res.usage.output_tokens == 20   # the envelope's, alone
    rec = json.loads((Path(res.transcript_path).parent / "result.json").read_text())
    assert rec["usage_from"] == ["envelope"]
    rows = [json.loads(ln) for ln in Path(res.transcript_path).read_text().splitlines()]
    assert [r["tool"] for r in rows if r["kind"] == "tool_call"] == ["write_file"]      # the record still traced


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
    assert rec["attempts"] == 1 and rec["session_id"] == "s1" and rec["usage_from"] == ["envelope"]
    assert res.provider_wait_s == 0.0
    # commits: pre + agent
    assert "agent:t" in tmp_ws._git("log", "--oneline").stdout


def test_agent_planted_mcp_server_never_reaches_the_cli(tmp_ws: Workspace, agent: GeminiCliAgent):
    """``ws/.gemini/settings.json`` is agent-writable and gemini-cli merged its mcpServers,
    so an agent could choose what the NEXT round's CLI launched (`gemini mcp list` in a
    poisoned workspace tried to start it).  3dcode + ``mcp.allowed`` now live in the
    per-session system settings, which is applied LAST and whose ``mcp.allowed``
    REPLACES rather than merges (audit 2026-08-27)."""
    from codeverse3d.agents.cli_common import begin_session, default_mcp_command, release_session
    from codeverse3d.agents.materialize import materialize_workspace

    materialize_workspace(tmp_ws, agent_kind="gemini-cli", contract_md="c", cookbook_text="",
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
    assert list(settings["mcpServers"]) == ["3dcode"] and settings["mcp"]["allowed"] == ["3dcode"]
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

    monkeypatch.setattr(get_settings(), "gemini_api_keys", ["only"])
    from codeverse3d.models import gemini as gm
    for sig in [s for s in list(gm._pools) if s and s[0] == "only"]:
        gm._pools.pop(sig, None)
    monkeypatch.setenv("FAKE_MODE", "fail_once_503")
    res = agent.run(_job(tmp_ws))
    assert res.ok, res.errors
    assert (tmp_ws.artifacts / "attempts.txt").read_text() == "2"
    lines = [json.loads(ln) for ln in Path(res.transcript_path).read_text().splitlines()]
    assert [ln["key_tail"] for ln in lines if ln["kind"] == "invoke"] == ["only", "only"]


def test_single_key_quota_failure_returns_budget_without_retry(tmp_ws: Workspace, agent: GeminiCliAgent, monkeypatch):
    """429 puts the only key into cooldown: no alternative → return the failed outcome (ok=False), do not raise."""
    from codeverse3d.agents import backends as gc

    monkeypatch.setattr(get_settings(), "gemini_api_keys", ["solo"])
    monkeypatch.setattr(gc, "RETRY_KEY_WAIT_S", 0.2)
    from codeverse3d.models import gemini as gm
    for sig in [s for s in list(gm._pools) if s and s[0] == "solo"]:
        gm._pools.pop(sig, None)
    monkeypatch.setenv("FAKE_MODE", "fail_always")
    res = agent.run(_job(tmp_ws))
    assert not res.ok and res.exit_reason == "budget"
    assert (tmp_ws.artifacts / "attempts.txt").read_text() == "1"
    rec = json.loads((Path(res.transcript_path).parent / "result.json").read_text())
    assert rec["attempts"] == 1 and any("no usable key" in n for n in rec["notes"])


def test_pool_exhausted_before_first_attempt_is_a_budget_result(tmp_ws: Workspace, agent: GeminiCliAgent, monkeypatch):
    from codeverse3d.agents import backends as gc
    from codeverse3d.models.retry import KeyPoolExhausted

    class Dead:
        def session_key(self, **kw):
            raise KeyPoolExhausted("all 2 keys throttled; waited 120s")

        def report(self, *a, **k):
            return None

    monkeypatch.setattr(gc, "_key_pool", lambda keys: Dead())
    res = agent.run(_job(tmp_ws))
    assert not res.ok and res.exit_reason == "budget" and "exhausted" in res.errors[0]


def test_a_cli_session_holds_no_in_flight_slot(tmp_ws: Workspace, agent: GeminiCliAgent, monkeypatch):
    """The slots are machine-wide and a session is minutes long: one held per session would
    let a few dozen sessions starve every planner and judge call on the box.  The session
    takes its KEY from the shared pool (rotation, cooldowns) and nothing else — so nothing
    can leak either, even when invoke raises."""
    from codeverse3d.agents import backends as gc

    pool = gc._key_pool(get_settings().gemini_api_keys)  # noqa: SLF001
    seen: list[int] = []

    def boom(*a, **k):
        seen.append(pool.stats()["in_flight"])
        raise OSError("cannot spawn")

    monkeypatch.setattr(gc, "invoke", boom)
    with pytest.raises(OSError, match="cannot spawn"):
        agent.run(_job(tmp_ws))
    assert seen == [0] and pool.stats()["in_flight"] == 0


def test_timeout_is_reported(tmp_ws: Workspace, agent: GeminiCliAgent, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "hang")
    monkeypatch.setattr("codeverse3d.agents.cli_common.IDLE_GRACE_S", 1.0)
    res = agent.run(_job(tmp_ws, timeout_s=1))
    assert not res.ok and res.exit_reason == "timeout" and res.duration_s < 30
    assert res.transient is False        # a plain hang is not a storm


def test_a_timeout_after_a_503_streak_is_marked_transient(tmp_ws: Workspace, agent: GeminiCliAgent, monkeypatch):
    """Measured 2026-09-07: 23 of 24 gemini-cli sessions of an evening ended timeout / 0 turns
    / $0 after 8-17 consecutive 503s inside the CLI's own retry loop.  The wall still ends the
    session (the CLI never gives up first), but the result says WHY, so a track can fall back
    to the single-shot path instead of shipping the skeleton (tracks.common.generate_for)."""
    monkeypatch.setenv("FAKE_MODE", "storm")
    monkeypatch.setattr("codeverse3d.agents.cli_common.IDLE_GRACE_S", 1.0)
    res = agent.run(_job(tmp_ws, timeout_s=1))
    assert not res.ok and res.exit_reason == "timeout" and res.transient is True
    assert any("4 x 503" in e for e in res.errors), res.errors
    assert res.provider_wait_s > 0.0


def test_the_key_pool_hears_a_5xx_only_when_the_failure_was_transient(tmp_ws: Workspace, agent: GeminiCliAgent,
                                                                     monkeypatch):
    """The pool is shared with every API call and a "5xx" costs the key 20 % of its health: a
    hang, a substituted model or the agent's own crash is not the KEY's fault."""
    from codeverse3d.agents import backends as gc

    monkeypatch.setattr("codeverse3d.agents.cli_common.IDLE_GRACE_S", 1.0)
    pool = gc._key_pool(get_settings().gemini_api_keys)  # noqa: SLF001
    for mode, timeout_s in (("substitute", 20), ("crash", 20), ("hang", 1)):
        monkeypatch.setenv("FAKE_MODE", mode)
        assert not agent.run(_job(tmp_ws, timeout_s=timeout_s, label=mode)).ok
    st = pool.stats()
    assert (st["5xx"], st["ok"], st["error"]) == (0, 0, 0), st
    monkeypatch.setenv("FAKE_MODE", "fail_once_503")   # a real 503, then a rotated success
    assert agent.run(_job(tmp_ws, label="storm")).ok
    st = pool.stats()
    assert (st["5xx"], st["ok"]) == (1, 1), st


def test_unavailable_when_no_keys(tmp_ws: Workspace, agent: GeminiCliAgent, monkeypatch):
    monkeypatch.setattr(get_settings(), "gemini_api_keys", [])
    ok, why = agent.available()
    assert not ok and "key" in why
    res = agent.run(_job(tmp_ws))
    assert not res.ok and res.exit_reason == "error"


def test_parse_helpers():
    assert parse_gemini_json("noise\n{\"response\": \"x\"}") == {"response": "x"}
    assert parse_gemini_json("") is None
    # the CLI prints the envelope INDENTED over many lines (a single-line scan finds nothing)
    assert parse_gemini_json('log line\n{\n  "response": "x",\n  "stats": {\n    "models": {}\n  }\n}\n')["response"] == "x"
    # no `prompt` key: total prompt = uncached input + cached
    u = usage_from_stats({"models": {"m": {"tokens": {"input": 1, "candidates": 2, "cached": 3, "thoughts": 4}}},
                          "tools": {"totalCalls": 9}}, "m")
    assert (u.input_tokens, u.output_tokens, u.cached_tokens, u.thoughts_tokens, u.tool_calls) == (4, 2, 3, 4, 9)


def test_usage_input_is_total_prompt_and_cost_reprices_cache():
    """gemini-cli reports tokens.input = prompt - cached; pricing wants the TOTAL prompt (regression: ~4x under-billing)."""
    from codeverse3d.models.pricing import estimate_cost, lookup_price

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
    from codeverse3d.contracts.common import Usage
    from codeverse3d.models.pricing import estimate_cost

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


def test_quota_failure_gets_a_third_rotated_attempt(tmp_ws: Workspace, agent: GeminiCliAgent, monkeypatch):
    """Two 429s in a row on two keys, the third key answers: the cell is not lost."""
    monkeypatch.setattr(get_settings(), "gemini_api_keys", ["k1", "k2", "k3"])
    from codeverse3d.models import gemini as gm
    for sig in [s for s in list(gm._pools) if s and s[0] in ("k1", "only")]:
        gm._pools.pop(sig, None)
    monkeypatch.setenv("FAKE_MODE", "fail_twice_429")
    res = agent.run(_job(tmp_ws))
    assert res.ok, res.errors
    assert (tmp_ws.artifacts / "attempts.txt").read_text() == "3"
    rec = json.loads((Path(res.transcript_path).parent / "result.json").read_text())
    assert rec["attempts"] == 3
    lines = [json.loads(ln) for ln in Path(res.transcript_path).read_text().splitlines()]
    keys = [ln["key_tail"] for ln in lines if ln["kind"] == "invoke"]
    assert len(keys) == 3 and len(set(keys)) == 3  # a fresh key each time

"""AntigravityAgent (Gemini-API mode only): envelope parsing, argv, fake binary, pool key, live smoke."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from codeverse3d.agents.backends import DEFAULT_AGY_MODEL, AntigravityAgent
from codeverse3d.agents.cli_common import begin_session
from codeverse3d.config import get_settings
from codeverse3d.contracts.agent import AgentJob
from codeverse3d.workspace import Workspace


@pytest.fixture(autouse=True)
def _one_key(monkeypatch, tmp_path, request):
    """agy runs on API keys only: every test gets one key of its own (so its own pool — a 429
    in one test never cools another's key) and a cache dir for the session HOMEs."""
    if request.node.get_closest_marker("live"):
        return   # the real pool and cache
    monkeypatch.setattr(get_settings(), "gemini_api_keys", [f"key-{request.node.name}"])
    monkeypatch.setattr(get_settings(), "cache_dir", tmp_path / "cache")

ENVELOPE = {"conversation_id": "c1", "status": "SUCCESS", "response": "DONE\n", "duration_seconds": 3.0, "num_turns": 1,
            "usage": {"input_tokens": 18589, "output_tokens": 494, "thinking_tokens": 413, "cache_read_tokens": 12199, "total_tokens": 19083}}

FAKE_AGY = r'''
args = sys.argv[1:]
assert "--print" not in args and "-p" not in args, "the prompt comes on stdin (--print - would send '-')"
prompt = sys.stdin.read()
assert "--dangerously-skip-permissions" in args and "--add-dir" in args
assert "FAKE_SERVICE_API_KEY" not in os.environ
log = args[args.index("--log-file") + 1]
os.makedirs("src", exist_ok=True)
open("src/hello.txt", "w").write("hi")
open("src/prompt.txt", "w").write(prompt)
mode = os.environ.get("FAKE_MODE", "ok")
if mode == "retried":   # agy 1.2.8 retried two 503s and a 429 inside the turn, then answered
    from datetime import datetime, timedelta
    t0 = datetime.now() - timedelta(seconds=21)
    with open(log, "w") as f:
        f.write(t0.strftime("I%m%d %H:%M:%S.%f") + "      66 server.go:609] Language server will attempt to listen\n")
        for i, (dt, why, wait) in enumerate([(0, "Error 503, Message: busy, Status: UNAVAILABLE", "4s"),
                                             (4, "Error 503, Message: busy, Status: UNAVAILABLE", "6.069232705s"),
                                             (10, "Error 429, Message: slow down, Status: RESOURCE_EXHAUSTED", "10.690413026s")]):
            f.write((t0 + timedelta(seconds=dt)).strftime("I%m%d %H:%M:%S.%f")
                    + f"     284 run.go:395] Run: attempt {i + 1} failed ({why}, Details: []), retrying in {wait}\n")
if mode == "died":      # a turn that ended on the API: AGY_ERROR on stderr, exit code 3
    print(json.dumps({"conversation_id": "c9", "status": "ERROR", "response": "", "error": "generating: Error 503"}))
    print('AGY_ERROR: {"short_error":"generating: Error 503","status":"UNAVAILABLE","error_code":503,'
          '"code_kind":"http","retryable":true,"error_id":"x"}', file=sys.stderr)
    sys.exit(3)
if mode == "print_timeout":   # agy 1.3.4's own clock: SUCCESS, no response, one stderr line
    print(json.dumps({"conversation_id": "c8", "status": "SUCCESS", "response": "", "num_turns": 1}))
    print("[agy] print timeout after 8m0s with turn in progress; returning partial output", file=sys.stderr)
    sys.exit(0)
if mode == "text":
    print("plain text answer")
else:
    print(''' + repr(json.dumps(ENVELOPE)) + r''')
'''


def test_argv_sets_the_print_timeout_a_minute_past_the_watchdogs_hard_limit(tmp_ws: Workspace):
    a = AntigravityAgent("gemini-4-argon-high", binary="agy")
    s = begin_session(AgentJob(workspace=str(tmp_ws.root), prompt="p", timeout_s=1800), "agy")
    argv = a.build_argv(s)
    assert argv[argv.index("--print-timeout") + 1] == "36m"   # 30 min + the 5 min grace + 1


def test_fake_run_json_and_text(tmp_ws: Workspace, fake_bin, monkeypatch):
    a = AntigravityAgent("gemini-4-argon-high", binary=fake_bin("agy", FAKE_AGY))
    res = a.run(AgentJob(workspace=str(tmp_ws.root), prompt="hello", label="a", timeout_s=30))
    assert res.ok and res.text.strip() == "DONE", res.errors
    u = res.usage
    # agy's input_tokens EXCLUDE the cache reads and its output_tokens CONTAIN the thinking
    # (total == input + output): the pricing convention is total prompt, thinking billed once
    assert (u.input_tokens, u.output_tokens, u.thoughts_tokens, u.cached_tokens) == (18589 + 12199, 81, 413, 12199)
    assert [f.path for f in res.files_changed] == ["src/hello.txt", "src/prompt.txt"]
    assert (tmp_ws.src / "prompt.txt").read_text().endswith("\n\nhello")   # on stdin, after the workspace header
    monkeypatch.setenv("FAKE_MODE", "text")
    res2 = a.run(AgentJob(workspace=str(tmp_ws.root), prompt="hello", label="b", timeout_s=30))
    assert res2.ok and res2.text == "plain text answer" and res2.usage.input_tokens == 0


def test_the_retries_in_agys_own_log_are_its_provider_wait(tmp_ws: Workspace, fake_bin, monkeypatch):
    """agy 1.2.8 logs every model call it retries, with the back-off it announces (fake API,
    2026-09-22: 503, 503, 429, then an answer 20.8 s later) — the burst runs from the first
    failure to the last one plus its back-off."""
    from codeverse3d.agents.backends import agy_retries
    from codeverse3d.agents.cli_common import provider_wait

    a = AntigravityAgent("gemini-4-argon-high", binary=fake_bin("agy", FAKE_AGY))
    monkeypatch.setenv("FAKE_MODE", "retried")
    res = a.run(AgentJob(workspace=str(tmp_ws.root), prompt="hello", label="r", timeout_s=30))
    assert res.ok, res.errors
    rows = agy_retries(Path(res.transcript_path).parent / "agy.log")
    assert [(round(b, 3), why.split(",")[0]) for _, b, why in rows] == [
        (4.0, "Error 503"), (6.069, "Error 503"), (10.69, "Error 429")]
    assert [round(t - rows[0][0], 3) for t, _, _ in rows] == [0.0, 4.0, 10.0]
    assert provider_wait([(t, b) for t, b, _ in rows], end=rows[0][0] + 60) == pytest.approx(20.690413, abs=1e-3)
    # the fake wrote a log 21 s in the past: the session's own wait is capped at its length
    assert 0.0 < res.provider_wait_s <= res.duration_s


def test_agy_says_itself_whether_a_dead_turn_is_worth_a_retry(tmp_ws: Workspace, fake_bin, monkeypatch):
    a = AntigravityAgent("gemini-4-argon-high", binary=fake_bin("agy", FAKE_AGY))
    monkeypatch.setenv("FAKE_MODE", "died")
    res = a.run(AgentJob(workspace=str(tmp_ws.root), prompt="hello", label="d", timeout_s=30))
    assert not res.ok and res.exit_reason == "error" and res.transient and not res.quota
    rec = json.loads((Path(res.transcript_path).parent / "result.json").read_text())
    assert rec["agy_error"]["retryable"] is True and rec["agy_error"]["error_code"] == 503


def test_agys_own_print_timeout_is_a_timeout_not_an_empty_answer(tmp_ws: Workspace, fake_bin, monkeypatch):
    a = AntigravityAgent("gemini-4-argon-high", binary=fake_bin("agy", FAKE_AGY))
    monkeypatch.setenv("FAKE_MODE", "print_timeout")
    res = a.run(AgentJob(workspace=str(tmp_ws.root), prompt="hello", label="pt", timeout_s=30))
    assert not res.ok and res.exit_reason == "timeout" and not res.transient
    assert "--print-timeout" in res.errors[0] and "empty response" not in " ".join(res.errors)


@pytest.mark.live
def test_live_agy_tiny(tmp_ws: Workspace):
    if not shutil.which("agy"):
        pytest.skip("agy not installed")
    a = AntigravityAgent("gemini-4-argon-low")
    res = a.run(AgentJob(workspace=str(tmp_ws.root), prompt="Use your file-writing tool to create src/hello.txt containing exactly 'hi'. Reply DONE.",
                         timeout_s=300, label="live", spatial_tools=False))
    assert res.ok, res.errors
    assert (tmp_ws.src / "hello.txt").read_text().strip() == "hi"


#: agy in Gemini-API mode, as the harness drives it: everything it needs is in its own HOME
FAKE_AGY_API = r'''
args = sys.argv[1:]
prompt = sys.stdin.read()
home = os.environ["HOME"]
settings = json.load(open(os.path.join(home, ".gemini/antigravity-cli/settings.json")))
mcp = json.load(open(os.path.join(home, ".gemini/config/mcp_config.json")))
os.makedirs("src", exist_ok=True)
open("src/seen.json", "w").write(json.dumps({
    "key": os.environ.get("GEMINI_API_KEY"), "home": home, "settings": settings, "mcp": mcp,
    "model": args[args.index("--model") + 1], "effort": args[args.index("--effort") + 1]}))
if os.environ.get("FAKE_MODE") == "429":
    print(json.dumps({"conversation_id": "c9", "status": "ERROR", "response": "", "error": "Error 429"}))
    print('AGY_ERROR: {"status":"RESOURCE_EXHAUSTED","error_code":429,"retryable":true}', file=sys.stderr)
    sys.exit(3)
print(''' + repr(json.dumps(ENVELOPE)) + r''')
'''


def test_a_session_runs_on_a_pool_key_in_a_home_of_its_own(tmp_ws: Workspace, fake_bin, monkeypatch):
    """One pool key, agy's API-mode setting and the 3dcode MCP server in a fresh HOME (gone
    afterwards), the model sent verbatim with its effort, usage priced."""
    from codeverse3d.agents import backends

    key = get_settings().gemini_api_keys[0]
    a = AntigravityAgent("gemini-4-argon-low", binary=fake_bin("agy", FAKE_AGY_API))
    assert a.served_model() == "gemini-4-argon"
    res = a.run(AgentJob(workspace=str(tmp_ws.root), prompt="hello", label="api", timeout_s=30, language="blender"))
    assert res.ok, res.errors
    seen = json.loads((tmp_ws.src / "seen.json").read_text())
    assert seen["key"] == key and seen["settings"] == {"modelProvider": "gemini"}
    assert (seen["model"], seen["effort"]) == ("gemini-api://gemini-4-argon", "low")
    server = seen["mcp"]["mcpServers"]["3dcode"]
    assert server["args"][:2] == ["-m", "codeverse3d.spatial.mcp_server"] and server["env"] == {"HOME": str(Path.home())}
    assert seen["home"] != str(Path.home()) and not Path(seen["home"]).exists()   # deleted after the session
    assert res.usage.model == "gemini-4-argon" and res.usage.cost_usd > 0
    # a bare id runs at the API's own default effort; no model at all is argon
    assert AntigravityAgent("").model == DEFAULT_AGY_MODEL == "gemini-4-argon"
    assert AntigravityAgent("gemini-4-argon").model_args() == ["--model", "gemini-api://gemini-4-argon", "--effort", "high"]
    # a 429 cools the key in the shared pool
    monkeypatch.setenv("FAKE_MODE", "429")
    res2 = a.run(AgentJob(workspace=str(tmp_ws.root), prompt="hello", label="api429", timeout_s=30, spatial_tools=False))
    assert not res2.ok and res2.exit_reason == "budget" and res2.transient
    assert backends._key_pool().stats()["keys"][0]["429"] >= 1
    assert json.loads((tmp_ws.src / "seen.json").read_text())["mcp"] == {"mcpServers": {}}


def test_without_keys_agy_is_unavailable_and_never_starts(tmp_ws: Workspace, fake_bin, monkeypatch):
    """No subscription fallback (owner, 2026-10-13): no keys is a clear error, not a sign-in run."""
    monkeypatch.setattr(get_settings(), "gemini_api_keys", [])
    a = AntigravityAgent("gemini-4-argon", binary=fake_bin("agy", FAKE_AGY))
    ok, why = a.available()
    assert not ok and "API keys" in why
    res = a.run(AgentJob(workspace=str(tmp_ws.root), prompt="hello", label="nokey", timeout_s=30))
    assert not res.ok and res.exit_reason == "error" and not (tmp_ws.src / "hello.txt").exists()

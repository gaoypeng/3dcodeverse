"""cli_common + transcript helpers."""

from __future__ import annotations

import json
import os
import sys
import time

import pytest

from codeverse3d.agents import cli_common
from codeverse3d.agents.cli_common import (
    begin_session,
    clean_env,
    finish_session,
    hardened_env,
    invoke,
    is_quota_failure,
    is_rate_limited,
    is_transient_failure,
    provider_wait,
)
from codeverse3d.contracts.agent import AgentJob
from codeverse3d.contracts.common import Usage
from codeverse3d.workspace import Workspace


def test_clean_and_hardened_env_drop_secrets_but_the_kept_ones(monkeypatch, tmp_ws: Workspace):
    for name in ("OPENAI_API_KEY", "FOO_TOKEN", "GEMINI_API_KEYS", "CODEX_API_KEY"):
        monkeypatch.setenv(name, "s")
    monkeypatch.setenv("NODE_OPTIONS", "--x")
    env = clean_env({"CODEX_API_KEY"})
    assert not {"OPENAI_API_KEY", "FOO_TOKEN", "GEMINI_API_KEYS"} & set(env)
    assert env["CODEX_API_KEY"] == "s" and env["NODE_OPTIONS"] == "--x" and "PATH" in env and "HOME" in env
    assert env["C3D_AGENT_CONTEXT"] == "1" and "GIT_CEILING_DIRECTORIES" not in env
    # the hardened env adds the job's env and the git ceiling, and still drops the secrets
    env = hardened_env(tmp_ws, AgentJob(workspace=str(tmp_ws.root), prompt="p", env={"EXTRA": "2"}), keep=set())
    assert env["EXTRA"] == "2" and env["GIT_CEILING_DIRECTORIES"] == str(tmp_ws.root.parent)
    assert "FAKE_SERVICE_API_KEY" not in env


def test_retry_same_label_round_keeps_first_attempt_trajectory(tmp_ws: Workspace):
    """run_agent_task re-runs a silently-bailing job with the same label+round: attempt 1's files must survive."""
    job = AgentJob(workspace=str(tmp_ws.root), prompt="first", label="baseline", round=0)
    s1 = begin_session(job, "fake")
    s1.traj.write_text("stdout.json", "attempt 1 stdout")
    r1 = finish_session(s1, ok=False, exit_reason="error", text="", usage=Usage(cost_usd=0.5), errors=["bailed"])
    s2 = begin_session(job.model_copy(update={"prompt": "second"}), "fake")
    assert s2.traj.dir != s1.traj.dir and s2.traj.dir.name == "baseline.a2_r00" and s2.attempt == 2
    (tmp_ws.src / "a.py").write_text("x = 1\n")
    r2 = finish_session(s2, ok=True, exit_reason="completed", text="done", usage=Usage(cost_usd=0.01))
    d1 = json.loads(s1.traj.result_path.read_text())
    assert d1["exit_reason"] == "error" and d1["errors"] == ["bailed"] and d1["usage"]["cost_usd"] == 0.5 and d1["attempt"] == 1
    assert (s1.traj.dir / "stdout.json").read_text() == "attempt 1 stdout" and s1.traj.prompt_path.read_text() == "first"
    d2 = json.loads(s2.traj.result_path.read_text())
    assert d2["attempt"] == 2 and d2["job_label"] == "baseline" and d2["label"] == "baseline.a2" and d2["ok"]
    assert [f.path for f in r2.files_changed] == ["src/a.py"] and r1.files_changed == []
    log = tmp_ws._git("log", "--oneline").stdout
    assert "agent:baseline.a2" in log and "pre:baseline" in log


def test_the_prompt_goes_on_stdin_byte_for_byte_and_never_on_argv(tmp_ws: Workspace):
    """A 200 kB, 2 500-line prompt (past argv's 128 KiB cap) reaches the CLI whole."""
    import sys

    s = begin_session(AgentJob(workspace=str(tmp_ws.root), prompt="x", label="p"), "fake")
    prompt = "  leading spaces\n" + "\n".join(f"line {i}: ünïcode ✓" for i in range(2500)) + "\nSENTINEL\n\n"
    got = tmp_ws.root / "artifacts" / "stdin.bin"
    argv = [sys.executable, "-c", f"import sys; open({str(got)!r}, 'wb').write(sys.stdin.buffer.read())"]
    proc = invoke(s, argv, dict(os.environ), prompt=prompt)
    assert proc.rc == 0 and got.read_bytes() == prompt.encode("utf-8")
    row = next(r for r in s.traj.read_transcript() if r["kind"] == "invoke")
    assert row["argv"] == argv and row["stdin_bytes"] == len(prompt.encode("utf-8"))
    assert not (s.traj.dir / "task_prompt.md").exists()


def test_one_failure_vocabulary():
    """Transient = a retry may get through; quota = a spent usage limit, never transient."""
    for text in ("Attempt 3 failed with status 503", "API Error: 529 overloaded_error", "got status: UNAVAILABLE",
                 "Error: 429 RESOURCE_EXHAUSTED", "TypeError: fetch failed sending request", "read ECONNRESET",
                 "stream disconnected before completion", "got an empty response from the model"):
        assert is_transient_failure("x", text), text
    for text in ("SyntaxError: bad code", "at process.processTicksAndRejections (node:internal/process/task_queues:95)",
                 "killed by watchdog (idle) after 305s", "mcp tool build timed out", "the tool is unavailable", ""):
        assert not is_transient_failure(text), text   # a node stack trace says "internal" in lower case
    for text in ("You've hit your usage limit. Visit https://chatgpt.com/codex/settings/usage to purchase more credits",
                 "Claude AI usage limit reached|1790000000", "You've hit your limit · resets 3pm",
                 "insufficient_quota: You exceeded your current quota", "You're out of extra usage"):
        assert is_quota_failure(text) and not is_transient_failure(text, "503"), text
    # gemini's per-key rate limits say "quota" too, and the key pool rotates past them
    gemini_429 = "Quota exceeded for quota metric 'Generate Content API requests per minute'"
    assert not is_quota_failure(gemini_429) and not is_quota_failure("You exceeded your current quota")
    assert is_rate_limited("status 429") and is_rate_limited("RESOURCE_EXHAUSTED") and not is_rate_limited("503")


def test_queued_invocation_and_retry_share_the_run_deadline(tmp_ws: Workspace, monkeypatch):
    clock = [100.0]
    windows = []

    def watchdog(argv, **kwargs):
        windows.append((kwargs["soft_timeout_s"], kwargs["hard_timeout_s"]))
        return cli_common.CompletedProc(rc=0, stdout="", stderr="", duration_s=0)

    s = begin_session(AgentJob(workspace=str(tmp_ws.root), prompt="p", timeout_s=120,
                               hard_deadline_s=110.0), "fake")
    monkeypatch.setattr(cli_common.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(cli_common, "run_with_watchdog", watchdog)
    clock[0] = 104.0  # waiting for a workspace lock consumed four seconds
    invoke(s, ["unused"], {}, prompt="p")
    clock[0] = 109.0
    invoke(s, ["unused"], {}, prompt="p", attempt=2, soft_timeout_s=90)
    assert windows == [(6.0, 6.0), (1.0, 1.0)]
    clock[0] = 111.0
    expired = invoke(s, ["unused"], {}, prompt="p", attempt=3)
    assert len(windows) == 2 and expired.timed_out and expired.duration_s == 0
    assert "before CLI launch" in (s.traj.dir / "stderr.3.log").read_text()
    assert [r["started"] for r in s.traj.read_transcript() if r["kind"] == "invoke"] == [True, True, False]


def test_standalone_invocation_keeps_its_activity_grace(tmp_ws: Workspace, monkeypatch):
    windows = []

    def watchdog(argv, **kwargs):
        windows.append((kwargs["soft_timeout_s"], kwargs["hard_timeout_s"]))
        return cli_common.CompletedProc(rc=0, stdout="", stderr="", duration_s=0)

    monkeypatch.setattr(cli_common, "run_with_watchdog", watchdog)
    s = begin_session(AgentJob(workspace=str(tmp_ws.root), prompt="p", timeout_s=120), "fake")
    invoke(s, ["unused"], {}, prompt="p")
    assert windows == [(120.0, 420.0)]


@pytest.mark.timeout(20, method="thread")
def test_invoke_stops_an_active_cli_at_the_run_deadline(tmp_ws: Workspace, monkeypatch):
    s = begin_session(AgentJob(workspace=str(tmp_ws.root), prompt="p", timeout_s=120,
                               hard_deadline_s=time.monotonic() + 1), "fake")
    monkeypatch.setattr(cli_common, "POLL_S", 0.05)
    code = "import time\nwhile True:\n print('still active', flush=True); time.sleep(0.05)"
    started = time.monotonic()
    proc = invoke(s, [sys.executable, "-c", code], dict(os.environ), prompt="p")
    assert proc.timed_out and proc.killed_reason == "hard_timeout"
    assert "still active" in proc.stdout
    assert time.monotonic() - started < 10


def test_provider_wait_counts_each_burst_from_the_request_that_failed():
    # progress at 0 and 100; a burst of three failures, back-off announced, then progress again
    assert provider_wait([(10.0, 5.0), (20.0, 10.0), (35.0, 20.0)], end=500.0, progress=[0.0, 100.0]) == 55.0
    # unannounced back-off (gemini-cli 5xx lines): a lower bound, the failed request's own time
    assert provider_wait([(40.0, 0.0), (80.0, 0.0)], end=500.0, progress=[30.0, 120.0]) == 50.0
    # a burst nothing followed — the CLI gave up, or the watchdog killed it mid-storm — runs to the end
    assert provider_wait([(40.0, 0.0), (50.0, 0.0)], end=300.0, progress=[0.0, 30.0]) == 270.0
    # progress between two failures splits them into two bursts
    assert provider_wait([(10.0, 1.0), (30.0, 1.0)], end=100.0, progress=[0.0, 20.0, 50.0]) == 22.0
    # a CLI that records no progress (agy's log): each burst from its first failure to the retry
    assert provider_wait([(10.0, 4.0), (14.0, 6.0), (20.0, 10.0)], end=31.0, progress=None) == 20.0
    assert provider_wait([], end=10.0, progress=[1.0]) == 0.0


def test_finish_session_carries_the_typed_failure_and_the_wait(tmp_ws: Workspace):
    s = begin_session(AgentJob(workspace=str(tmp_ws.root), prompt="p", label="w"), "fake")
    res = finish_session(s, ok=False, exit_reason="error", text="", usage=Usage(), transient=True, quota=True,
                         provider_wait_s=1e9)
    assert res.quota and not res.transient            # a spent usage limit is never "retry me"
    assert 0.0 <= res.provider_wait_s <= res.duration_s   # never more than the session lasted


def test_mcp_command_resolution(tmp_ws: Workspace):
    from codeverse3d.agents.cli_common import default_mcp_command, mcp_command_for
    from codeverse3d.agents.materialize import materialize_workspace

    job = AgentJob(workspace=str(tmp_ws.root), prompt="p", language="blender", track="static_object", round=2)
    cmd = mcp_command_for(tmp_ws, job)
    assert cmd[1:3] == ["-m", "codeverse3d.spatial.mcp_server"] and "--language" in cmd and cmd[cmd.index("--round") + 1] == "2"
    job2 = AgentJob(workspace=str(tmp_ws.root), prompt="p", mcp_command=["python", "-m", "x"])
    assert mcp_command_for(tmp_ws, job2) == ["python", "-m", "x"]
    # a workspace .mcp.json NEVER wins: the agent works in that directory and could
    # otherwise choose what the next round's CLI launches (audit 2026-08-27)
    materialize_workspace(tmp_ws, agent_kind="codex", contract_md="c", cookbook_text="", spatial_tools=True,
                          mcp_command=default_mcp_command(tmp_ws, language="cadquery"))
    assert mcp_command_for(tmp_ws, job2) == ["python", "-m", "x"]  # still the typed job
    (tmp_ws.root / ".mcp.json").write_text(json.dumps(
        {"mcpServers": {"3dcode": {"command": "/tmp/evil", "args": ["--pwn"]}}}))
    assert mcp_command_for(tmp_ws, job2) == ["python", "-m", "x"]
    assert "/tmp/evil" not in mcp_command_for(tmp_ws, job)


_ENDINGS = {
    # the watchdog kills a session that printed a transport line and recorded no provider retry (D83)
    "killed": ('print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "working"}}),'
               ' flush=True)\nprint("Error: read ECONNRESET (mcp child)", file=sys.stderr, flush=True)\ntime.sleep(60)\n',
               ("timeout", False, False)),
    "usage_limit": ('print("You\'ve hit your usage limit. Try again later.", file=sys.stderr)\nsys.exit(1)\n',
                    ("budget", False, True)),
    "rate_limited": ('print("Error 429 Too Many Requests: rate limit", file=sys.stderr)\nsys.exit(1)\n',
                     ("budget", True, False)),
}


@pytest.mark.parametrize("ending", sorted(_ENDINGS))
@pytest.mark.parametrize("vendor", ["gemini-cli", "claude-code", "codex", "agy"])
def test_every_cli_backend_ends_by_one_rule(tmp_ws: Workspace, fake_bin, monkeypatch, vendor, ending):
    """N71: one ``classify_end`` — a kill with no provider evidence is not transient, and a spent
    usage limit or a 429 is ``budget``, whichever vendor's CLI said it."""
    from codeverse3d.agents.backends import (
        AntigravityAgent,
        ClaudeCodeAgent,
        CodexAgent,
        GeminiCliAgent,
    )
    from codeverse3d.config import get_settings

    monkeypatch.setattr(cli_common, "IDLE_GRACE_S", 0.3)
    monkeypatch.setattr("codeverse3d.agents.backends.RETRY_KEY_WAIT_S", 0.05)
    monkeypatch.setattr(get_settings(), "gemini_api_keys", ["n71-key"])
    body, want = _ENDINGS[ending]
    binary = fake_bin(vendor, "sys.stdin.read()\n" + body)
    agent = {"gemini-cli": lambda: GeminiCliAgent("m", binary=binary), "claude-code": lambda: ClaudeCodeAgent("m", binary=binary),
             "codex": lambda: CodexAgent("m", binary=binary, reasoning_effort=""),
             "agy": lambda: AntigravityAgent("m", binary=binary)}[vendor]()
    if vendor == "agy":
        monkeypatch.setattr(agent, "served_model", lambda: "m")
    res = agent.run(AgentJob(workspace=str(tmp_ws.root), prompt="p", label=ending, spatial_tools=False,
                             timeout_s=1 if ending == "killed" else 30))
    assert (res.exit_reason, res.transient, res.quota) == want

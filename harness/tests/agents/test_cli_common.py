"""cli_common + transcript helpers."""

from __future__ import annotations

import json
import os

from codeverse3d.agents.cli_common import (
    Trajectory,
    attribute_changes,
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
from codeverse3d.contracts.agent import AgentJob, FileChange
from codeverse3d.contracts.common import Usage
from codeverse3d.workspace import Workspace


def test_clean_env_drops_secrets_but_the_kept_ones(monkeypatch):
    for name in ("OPENAI_API_KEY", "FOO_TOKEN", "GEMINI_API_KEYS", "CODEX_API_KEY"):
        monkeypatch.setenv(name, "s")
    monkeypatch.setenv("NODE_OPTIONS", "--x")
    env = clean_env({"CODEX_API_KEY"})
    assert not {"OPENAI_API_KEY", "FOO_TOKEN", "GEMINI_API_KEYS"} & set(env)
    assert env["CODEX_API_KEY"] == "s" and env["NODE_OPTIONS"] == "--x" and "PATH" in env and "HOME" in env
    assert env["C3D_AGENT_CONTEXT"] == "1" and "GIT_CEILING_DIRECTORIES" not in env


def test_hardened_env_strips_secrets_and_adds_guards(tmp_ws: Workspace, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-y")
    monkeypatch.setenv("KEEPME", "1")
    job = AgentJob(workspace=str(tmp_ws.root), prompt="p", env={"EXTRA": "2"})
    env = hardened_env(tmp_ws, job, keep={"ANTHROPIC_API_KEY"})
    assert "OPENAI_API_KEY" not in env and "FAKE_SERVICE_API_KEY" not in env
    assert env["ANTHROPIC_API_KEY"] == "sk-y" and env["KEEPME"] == "1" and env["EXTRA"] == "2"
    assert env["C3D_AGENT_CONTEXT"] == "1" and env["GIT_CEILING_DIRECTORIES"] == str(tmp_ws.root.parent)


def test_session_roundtrip_tracks_files_and_writes_result(tmp_ws: Workspace):
    job = AgentJob(workspace=str(tmp_ws.root), prompt="do it", label="baseline", round=2)
    s = begin_session(job, "fake")
    assert s.traj.dir.name == "baseline_r02" and s.traj.prompt_path.read_text() == "do it"
    (tmp_ws.src / "model.py").write_text("print(1)\n")
    res = finish_session(s, ok=True, exit_reason="completed", text="done", usage=Usage(input_tokens=5), tool_calls=1)
    assert res.ok and [f.path for f in res.files_changed] == ["src/model.py"]
    assert res.files_changed[0].status == "added" and res.files_changed[0].lines_added == 1
    data = json.loads(s.traj.result_path.read_text())
    assert data["exit_reason"] == "completed" and data["head_before"] != data["head_after"]
    assert tmp_ws.head() == data["head_after"]


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


def test_attribute_changes_pure():
    files = [FileChange(path=p, status="modified") for p in (
        "src/zones/a.js", "src/zones/b.js", "src/assets/x.js", "events.jsonl", "artifacts/census.json",
        "trajectories/zone_a_r00/result.json", "public/assets/y.glb", "README.md", "AGENTS.md")]
    got = attribute_changes(files, write_roots=["src", "public"])
    assert [f.path for f in got] == ["src/zones/a.js", "src/zones/b.js", "src/assets/x.js", "public/assets/y.glb"]
    assert [f.path for f in attribute_changes(files, write_roots=["public"])] == ["public/assets/y.glb"]


def test_the_prompt_goes_on_stdin_byte_for_byte_and_never_on_argv(tmp_ws: Workspace):
    """A 2 500-line, 200 kB prompt — past argv's 128 KiB per-argument cap and past gemini-cli's
    2 000-line read_file window, which is where the old file stub dropped a scene's last zones —
    reaches the CLI whole, and the transcript records its size, not its text."""
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
    """The backends classify what the CLI said about the call that ended its session; the round
    loop reads only the typed flags.  Provider failures a retry may get through are transient; a
    spent usage limit is quota and never transient; the task's own failure is neither."""
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


def test_trajectory_jsonl_and_result(tmp_path):
    t = Trajectory(tmp_path / "traj")
    t.append("a", x=1)
    t.append("b", y="z")
    recs = t.read_transcript()
    assert [r["kind"] for r in recs] == ["a", "b"] and recs[1]["y"] == "z" and "t" in recs[0]
    t.write_result(Usage(input_tokens=3), note="n")
    assert json.loads(t.result_path.read_text()) == {**Usage(input_tokens=3).model_dump(), "note": "n"}


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

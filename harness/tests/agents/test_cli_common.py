"""cli_common + transcript helpers."""

from __future__ import annotations

import json
import os
import threading

from codeverse.agents.cli_common import (
    attribute_changes,
    begin_session,
    deliver_prompt,
    finish_session,
    hardened_env,
    is_quota_failure,
    is_secret_env,
    is_transient_failure,
)
from codeverse.agents.transcript import Trajectory
from codeverse.contracts.agent import AgentJob, FileChange
from codeverse.contracts.common import Usage
from codeverse.workspace import Workspace


def test_secret_detection():
    assert is_secret_env("OPENAI_API_KEY") and is_secret_env("FOO_TOKEN") and is_secret_env("GEMINI_API_KEYS")
    assert not is_secret_env("PATH") and not is_secret_env("HOME") and not is_secret_env("NODE_OPTIONS")


def test_hardened_env_strips_secrets_and_adds_guards(tmp_ws: Workspace, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-y")
    monkeypatch.setenv("KEEPME", "1")
    job = AgentJob(workspace=str(tmp_ws.root), prompt="p", env={"EXTRA": "2"})
    env = hardened_env(tmp_ws, job, keep={"ANTHROPIC_API_KEY"})
    assert "OPENAI_API_KEY" not in env and "FAKE_SERVICE_API_KEY" not in env
    assert env["ANTHROPIC_API_KEY"] == "sk-y" and env["KEEPME"] == "1" and env["EXTRA"] == "2"
    assert env["CV3D_AGENT_CONTEXT"] == "1" and env["GIT_CEILING_DIRECTORIES"] == str(tmp_ws.root.parent)


def test_session_roundtrip_tracks_files_and_writes_result(tmp_ws: Workspace):
    job = AgentJob(workspace=str(tmp_ws.root), prompt="do it", label="baseline", extra={"round": 2})
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
    job = AgentJob(workspace=str(tmp_ws.root), prompt="first", label="baseline", extra={"round": 0})
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
    got = attribute_changes(files, write_roots=["src", "public"], own_hints=frozenset({"src/zones/a.js"}),
                            sibling_hints=frozenset({"src/zones/b.js", "src/assets/"}))
    assert [f.path for f in got] == ["src/zones/a.js", "public/assets/y.glb"]
    # a file both sessions declared stays attributed to both
    got = attribute_changes(files, write_roots=["src"], own_hints=frozenset({"src/zones/b.js"}), sibling_hints=frozenset({"src/zones/b.js"}))
    assert "src/zones/b.js" in [f.path for f in got]


def test_concurrent_sessions_in_one_workspace_attribute_their_own_files(tmp_ws: Workspace):
    """Scene fan-out: zone sessions run in parallel in ONE workspace; each result must list only its own work
    and a session that wrote nothing must report no changes even though siblings wrote files."""
    (tmp_ws.src / "zones").mkdir(parents=True)
    barrier = threading.Barrier(3)
    results = {}

    def session(name: str, write: bool):
        job = AgentJob(workspace=str(tmp_ws.root), prompt="p", label=f"zone_{name}",
                       extra={"round": 0, "files_hint": [f"src/zones/{name}.js"]})
        s = begin_session(job, "fake")
        barrier.wait(timeout=30)  # every session has started before anyone writes
        if write:
            (tmp_ws.src / "zones" / f"{name}.js").write_text(f"// {name}\n")
        (tmp_ws.root / "events.jsonl").open("a").write(f"{name}\n")
        barrier.wait(timeout=30)  # every write landed before anyone finishes
        results[name] = finish_session(s, ok=True, exit_reason="completed", text="", usage=Usage())

    threads = [threading.Thread(target=session, args=("koi", True)), threading.Thread(target=session, args=("gravel", True)),
               threading.Thread(target=session, args=("lantern", False))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert [f.path for f in results["koi"].files_changed] == ["src/zones/koi.js"]
    assert [f.path for f in results["gravel"].files_changed] == ["src/zones/gravel.js"]
    assert results["lantern"].files_changed == []  # bailed agent: siblings' files are not its work


def test_deliver_prompt_uses_file_when_long(tmp_ws: Workspace):
    job = AgentJob(workspace=str(tmp_ws.root), prompt="x")
    s = begin_session(job, "fake")
    short = deliver_prompt(s, "hello")
    assert short == "hello"
    long = "y" * 200_001
    stub = deliver_prompt(s, long)
    assert "task_prompt.md" in stub and (s.traj.dir / "task_prompt.md").read_text() == long
    assert os.path.isabs(s.traj.dir.as_posix())


def test_transient_markers():
    assert is_transient_failure("Error: 429 RESOURCE_EXHAUSTED")
    assert is_transient_failure("", "got an empty response from the model")
    assert is_quota_failure("quota exceeded") and not is_quota_failure("503 UNAVAILABLE")
    assert not is_transient_failure("SyntaxError: bad code")


def test_trajectory_jsonl_and_result(tmp_path):
    t = Trajectory(tmp_path / "traj")
    t.append("a", x=1)
    t.append("b", y="z")
    recs = t.read_transcript()
    assert [r["kind"] for r in recs] == ["a", "b"] and recs[1]["y"] == "z" and "t" in recs[0]
    t.write_result(Usage(input_tokens=3), note="n")
    assert json.loads(t.result_path.read_text()) == {**Usage(input_tokens=3).model_dump(), "note": "n"}


def test_mcp_command_resolution(tmp_ws: Workspace):
    from codeverse.agents.cli_common import default_mcp_command, mcp_command_for
    from codeverse.agents.materialize import materialize_workspace

    job = AgentJob(workspace=str(tmp_ws.root), prompt="p", extra={"language": "blender", "track": "static_object", "round": 2})
    cmd = mcp_command_for(tmp_ws, job)
    assert cmd[1:3] == ["-m", "codeverse.spatial.mcp_server"] and "--language" in cmd and cmd[cmd.index("--round") + 1] == "2"
    job2 = AgentJob(workspace=str(tmp_ws.root), prompt="p", extra={"mcp_command": ["python", "-m", "x"]})
    assert mcp_command_for(tmp_ws, job2) == ["python", "-m", "x"]
    materialize_workspace(tmp_ws, agent_kind="codex", contract_md="c", cookbook_rel="", spatial_tools=True,
                          mcp_command=default_mcp_command(tmp_ws, language="cadquery"))
    assert "cadquery" in mcp_command_for(tmp_ws, job2)  # materialised .mcp.json wins


def test_gemini_system_settings_disable_folder_trust(tmp_path):
    from codeverse.agents.gemini_cli import write_system_settings

    p = write_system_settings(tmp_path / "s.json")
    data = json.loads(p.read_text())
    assert data["security"]["folderTrust"]["enabled"] is False
    assert data["security"]["auth"]["selectedType"] == "gemini-api-key"
    assert data["experimental"]["dynamicModelConfiguration"] is True

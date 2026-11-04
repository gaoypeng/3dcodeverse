"""cli_common + transcript helpers."""

from __future__ import annotations

import json
import os
import sys

import pytest

from codeverse3d.agents import cli_common
from codeverse3d.agents.cli_common import (
    begin_session,
    clean_env,
    hardened_env,
    invoke,
)
from codeverse3d.contracts.agent import AgentJob
from codeverse3d.workspace import Workspace


def test_clean_and_hardened_env_drop_secrets_but_the_kept_ones(monkeypatch, tmp_ws: Workspace):
    for name in ("OPENAI_API_KEY", "FOO_TOKEN", "GEMINI_API_KEYS", "CODEX_API_KEY"):
        monkeypatch.setenv(name, "s")
    monkeypatch.setenv("NODE_OPTIONS", "--x")
    env = clean_env({"CODEX_API_KEY"})
    assert not {"OPENAI_API_KEY", "FOO_TOKEN", "GEMINI_API_KEYS"} & set(env)
    assert env["CODEX_API_KEY"] == "s" and env["NODE_OPTIONS"] == "--x" and "PATH" in env and "HOME" in env
    assert "GIT_CEILING_DIRECTORIES" not in env
    # the hardened env adds the job's env and the git ceiling, and still drops the secrets
    env = hardened_env(tmp_ws, AgentJob(workspace=str(tmp_ws.root), prompt="p", env={"EXTRA": "2"}), keep=set())
    assert env["EXTRA"] == "2" and env["GIT_CEILING_DIRECTORIES"] == str(tmp_ws.root.parent)
    assert "FAKE_SERVICE_API_KEY" not in env




def test_the_prompt_goes_on_stdin_byte_for_byte_and_never_on_argv(tmp_ws: Workspace):
    """A 200 kB, 2 500-line prompt (past argv's 128 KiB cap) reaches the CLI whole."""

    s = begin_session(AgentJob(workspace=str(tmp_ws.root), prompt="x", label="p"), "fake")
    prompt = "  leading spaces\n" + "\n".join(f"line {i}: ünïcode ✓" for i in range(2500)) + "\nSENTINEL\n\n"
    got = tmp_ws.root / "artifacts" / "stdin.bin"
    argv = [sys.executable, "-c", f"import sys; open({str(got)!r}, 'wb').write(sys.stdin.buffer.read())"]
    proc = invoke(s, argv, dict(os.environ), prompt=prompt)
    assert proc.rc == 0 and got.read_bytes() == prompt.encode("utf-8")
    row = next(r for r in s.traj.read_transcript() if r["kind"] == "invoke")
    assert row["argv"] == argv and row["stdin_bytes"] == len(prompt.encode("utf-8"))
    assert not (s.traj.dir / "task_prompt.md").exists()














def test_mcp_command_resolution(tmp_ws: Workspace):
    from codeverse3d.agents.cli_common import default_mcp_command, mcp_command_for
    from codeverse3d.agents.materialize import materialize_workspace

    job = AgentJob(workspace=str(tmp_ws.root), prompt="p", language="blender", track="static_object", round=2)
    cmd = mcp_command_for(tmp_ws, job)
    assert cmd[1:3] == ["-m", "codeverse3d.spatial.mcp_server"] and "--language" in cmd and cmd[cmd.index("--round") + 1] == "2"
    # a workspace .mcp.json NEVER wins: the agent works in that directory and could
    # otherwise choose what the next round's CLI launches (audit 2026-08-27)
    materialize_workspace(tmp_ws, agent_kind="codex", contract_md="c",
                          mcp_command=default_mcp_command(tmp_ws, language="cadquery"))
    assert mcp_command_for(tmp_ws, job) == cmd  # still the typed job
    (tmp_ws.root / ".mcp.json").write_text(json.dumps(
        {"mcpServers": {"3dcode": {"command": "/tmp/evil", "args": ["--pwn"]}}}))
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
@pytest.mark.parametrize("vendor", ["claude-code", "codex", "agy"])
def test_every_cli_backend_ends_by_one_rule(tmp_ws: Workspace, fake_bin, monkeypatch, vendor, ending):
    """N71: one ``classify_end`` — a kill with no provider evidence is not transient, and a spent
    usage limit or a 429 is ``budget``, whichever vendor's CLI said it."""
    from codeverse3d.agents.backends import (
        AntigravityAgent,
        ClaudeCodeAgent,
        CodexAgent,
    )
    from codeverse3d.config import get_settings

    monkeypatch.setattr(cli_common, "IDLE_GRACE_S", 0.3)
    monkeypatch.setattr(get_settings(), "gemini_api_keys", [f"n71-key-{ending}"])   # agy's pool key, one per ending
    body, want = _ENDINGS[ending]
    monkeypatch.setattr(get_settings(), "cache_dir", tmp_ws.root.parent / "cache")
    binary = fake_bin(vendor, "sys.stdin.read()\n" + body)
    agent = {"claude-code": lambda: ClaudeCodeAgent("m", binary=binary),
             "codex": lambda: CodexAgent("m", binary=binary, reasoning_effort=""),
             "agy": lambda: AntigravityAgent("m", binary=binary)}[vendor]()
    res = agent.run(AgentJob(workspace=str(tmp_ws.root), prompt="p", label=ending, spatial_tools=False,
                             timeout_s=1 if ending == "killed" else 30))
    assert (res.exit_reason, res.transient, res.quota) == want

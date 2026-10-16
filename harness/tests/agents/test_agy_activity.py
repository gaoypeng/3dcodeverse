"""D112: an agy session that is taking steps is never idle-killed; one that has stalled still is.

Argon loop iteration 5 (2026-10-16): agy prints nothing until it exits, and D111 narrowed a
``shared_tree`` session's activity watch to its own file — so a session READING was "idle" from
second 0 and its soft window was a hard kill: 22 of 26 env sessions died at 420-423 s with nothing
written, and 4 of 21 refines at 901 s.  agy records every step under its per-session HOME
(``AGY_STEP_DIRS``); those writes are the session's activity, for shared_tree and plain sessions.
"""

from __future__ import annotations

import pytest

from codeverse3d.agents import cli_common
from codeverse3d.agents.backends import AGY_STEP_DIRS, AntigravityAgent
from codeverse3d.config import get_settings
from codeverse3d.contracts.agent import AgentJob
from codeverse3d.workspace import Workspace

#: a fake agy that never prints and never writes the workspace; with STEPS=1 it records a step
#: under its own HOME's brain/ every 0.1 s, the way agy writes brain/<id>/.system_generated/steps/<n>/
FAKE_AGY_STEPS = r'''
sys.stdin.read()
brain = os.path.join(os.environ["HOME"], ".gemini", "antigravity-cli", "brain", "c1", ".system_generated", "steps")
for n in range(600):
    if os.environ.get("STEPS") == "1":
        os.makedirs(os.path.join(brain, str(n)), exist_ok=True)
        open(os.path.join(brain, str(n), "output.txt"), "w").write("read a file")
    time.sleep(0.1)
'''


@pytest.fixture(autouse=True)
def _fast_clocks(monkeypatch, tmp_path, request):
    monkeypatch.setattr(get_settings(), "gemini_api_keys", [f"key-{request.node.name}"])
    monkeypatch.setattr(get_settings(), "cache_dir", tmp_path / "cache")
    monkeypatch.setattr(cli_common, "IDLE_GRACE_S", 1.0)
    monkeypatch.setattr(cli_common, "HARD_GRACE_S", 3.0)
    monkeypatch.setattr(cli_common, "SCAN_S", 0.1)


def _env_tree(ws: Workspace) -> None:
    (ws.root / "src" / "env.js").write_text("// placeholder\n")
    ws.commit("skeleton")


@pytest.mark.parametrize("shared_tree", [True, False])
def test_a_session_taking_steps_runs_to_its_hard_limit(tmp_ws: Workspace, fake_bin, monkeypatch, shared_tree):
    _env_tree(tmp_ws)
    monkeypatch.setenv("STEPS", "1")
    a = AntigravityAgent("gemini-4-argon", binary=fake_bin("agy", FAKE_AGY_STEPS))
    res = a.run(AgentJob(workspace=str(tmp_ws.root), prompt="p", label="env", timeout_s=1, spatial_tools=False,
                         files_hint=["src/env.js"], shared_tree=shared_tree))
    result = (tmp_ws.trajectory_dir("env", 0) / "result.json").read_text()
    assert '"killed_reason": "hard_timeout"' in result, result
    assert res.duration_s >= 3.5   # soft 1 s + hard grace 3 s: past the soft window, and bounded


@pytest.mark.parametrize("shared_tree", [True, False])
def test_a_stalled_session_is_still_killed_at_its_soft_window(tmp_ws: Workspace, fake_bin, monkeypatch, shared_tree):
    _env_tree(tmp_ws)
    monkeypatch.setenv("STEPS", "0")
    a = AntigravityAgent("gemini-4-argon", binary=fake_bin("agy", FAKE_AGY_STEPS))
    res = a.run(AgentJob(workspace=str(tmp_ws.root), prompt="p", label="env", timeout_s=1, spatial_tools=False,
                         files_hint=["src/env.js"], shared_tree=shared_tree))
    result = (tmp_ws.trajectory_dir("env", 0) / "result.json").read_text()
    assert '"killed_reason": "idle"' in result, result
    assert res.duration_s < 3.5


def test_the_step_dirs_are_named_not_walked_into():
    """The watchdog's walk skips ``.gemini`` — the step dirs must be passed themselves."""
    assert all(d.parts[0] == ".gemini" for d in AGY_STEP_DIRS)
    assert ".gemini" in cli_common._SKIP_DIRS


#: a fake agy that logs one retried 503 at t=0 (or not) and then hangs: no output, no step, no write
FAKE_AGY_HANG = r'''
sys.stdin.read()
args = sys.argv[1:]
if os.environ.get("LOGGED_503") == "1":
    from datetime import datetime
    with open(args[args.index("--log-file") + 1], "w") as f:
        f.write(datetime.now().strftime("I%m%d %H:%M:%S.%f") + "     284 run.go:395] Run: attempt 1 failed "
                "(Error 503, Message: busy, Status: UNAVAILABLE, Details: []), retrying in 4s\n")
time.sleep(60)
'''


@pytest.mark.parametrize("logged_503", ["0", "1"])
def test_a_hung_session_ends_the_same_whatever_agys_log_holds(tmp_ws: Workspace, fake_bin, monkeypatch, logged_503):
    """D112 (fix 8): iteration 5's identical 901 s / zero-file kills were transient (retried) when agy had
    logged one 4 s 503 and not otherwise.  Now: stalled, never transient (the key is not penalised)."""
    _env_tree(tmp_ws)
    monkeypatch.setenv("LOGGED_503", logged_503)
    a = AntigravityAgent("gemini-4-argon", binary=fake_bin("agy", FAKE_AGY_HANG))
    res = a.run(AgentJob(workspace=str(tmp_ws.root), prompt="p", label="refine", timeout_s=1, spatial_tools=False))
    assert (res.exit_reason, res.transient, res.stalled, res.files_changed) == ("timeout", False, True, [])


def test_the_end_rule_reads_the_watchdogs_verdict_not_the_vendors_log():
    from codeverse3d.agents.cli_common import CompletedProc, classify_end

    idle = CompletedProc(rc=-9, stdout="", stderr="", duration_s=901, timed_out=True, killed_reason="idle")
    hard = CompletedProc(rc=-9, stdout="", stderr="", duration_s=1200, timed_out=True, killed_reason="hard_timeout")
    assert classify_end(idle, ok=False, text="") == cli_common.SessionEnd("timeout", stalled=True)
    assert classify_end(idle, ok=False, text="half an answer") == cli_common.SessionEnd("timeout")
    assert classify_end(hard, ok=False, text="") == cli_common.SessionEnd("timeout")

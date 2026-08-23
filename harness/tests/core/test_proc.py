"""codeverse/proc.py — subprocess primitives (Batch-1 foundations)."""

from __future__ import annotations

import json
import os
import resource
import time
from pathlib import Path

from codeverse.proc import ProcResult, kill_group, run_subprocess, tail, write_json_atomic


def test_run_subprocess_captures_output(tmp_path: Path):
    r = run_subprocess(["bash", "-c", "echo out; echo err >&2; exit 3"], cwd=tmp_path, timeout_s=10)
    assert isinstance(r, ProcResult)
    assert r.returncode == 3
    assert r.stdout.strip() == "out"
    assert r.stderr.strip() == "err"
    assert not r.timed_out
    assert r.duration_ms >= 0


def test_run_subprocess_stdin(tmp_path: Path):
    r = run_subprocess(["cat"], cwd=tmp_path, timeout_s=10, stdin_text="hello")
    assert r.stdout == "hello"


def test_timeout_kills_the_whole_process_group(tmp_path: Path):
    """A timed-out child AND its grandchildren die (killpg on the new session)."""
    r = run_subprocess(["bash", "-c", "sleep 30 & echo $!; wait"], cwd=tmp_path, timeout_s=0.4)
    assert r.timed_out
    assert r.returncode != 0
    grandchild = int(r.stdout.strip().splitlines()[0])
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        try:
            os.kill(grandchild, 0)
        except ProcessLookupError:
            return  # gone — group kill worked
        time.sleep(0.05)
    raise AssertionError(f"grandchild sleep (pid {grandchild}) survived the group kill")


def test_preexec_fn_runs_in_the_child(tmp_path: Path):
    soft = 256

    def limit() -> None:
        resource.setrlimit(resource.RLIMIT_NOFILE, (soft, soft))

    r = run_subprocess(
        ["python3", "-c", "import resource, sys; sys.stdout.write(str(resource.getrlimit(resource.RLIMIT_NOFILE)[0]))"],
        cwd=tmp_path, timeout_s=20, preexec_fn=limit,
    )
    assert r.stdout.strip() == str(soft)


def test_kill_group_is_public_and_tolerates_dead_proc(tmp_path: Path):
    import subprocess

    proc = subprocess.Popen(["sleep", "0.05"], start_new_session=True)
    proc.wait()
    kill_group(proc)  # must not raise on an already-reaped child


def test_tail_caps_lines_and_chars():
    text = "\n".join(f"line{i}" for i in range(100))
    t = tail(text, max_lines=5)
    assert t.splitlines() == [f"line{i}" for i in range(95, 100)]
    assert len(tail("x" * 10000, max_chars=100)) == 100


def test_write_json_atomic_no_partial_file(tmp_path: Path):
    p = tmp_path / "deep" / "out.json"
    write_json_atomic(p, {"a": 1, "p": Path("b")})
    assert json.loads(p.read_text()) == {"a": 1, "p": "b"}
    assert not p.with_suffix(".json.tmp").exists()


def test_workspace_write_json_delegates(tmp_ws):
    from pydantic import BaseModel

    class M(BaseModel):
        x: int = 2

    p = tmp_ws.root / "artifacts" / "m.json"
    tmp_ws.write_json(p, M())
    assert json.loads(p.read_text()) == {"x": 2}
    assert not p.with_suffix(".json.tmp").exists()

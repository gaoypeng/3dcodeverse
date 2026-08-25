"""codeverse/proc.py — subprocess primitives (Batch-1 foundations)."""

from __future__ import annotations

import json
import os
import resource
import sys
import threading
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


_WRITER = """
import json, sys
from pathlib import Path
from codeverse.proc import write_json_atomic

path, tag, n = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
payload = {"writer": tag, "blob": [tag * 40] * 900}
bad = 0
for _ in range(n):
    write_json_atomic(path, payload)          # a second writer must not break this one
    try:
        json.loads(path.read_text())          # ... and readers must never see a partial file
    except ValueError:
        bad += 1
print(bad)
"""


def test_write_json_atomic_survives_concurrent_writers(tmp_path: Path):
    """RS-1: `tmp = path.with_suffix('.tmp')` was ONE name for every writer, so writer A
    renamed B's half-written tmp into place (readers saw truncated / zero-byte JSON at the
    published path) and B's own replace() then died with FileNotFoundError.  Real callers
    share a path: the scene track fans zone agents out over one workspace root and each
    agent's MCP `build` tool writes artifacts/build_last.json through this function.

    Three processes, same path.  With a private tmp per writer this cannot fail; on the old
    code it fails within a few dozen iterations."""
    import subprocess

    harness = Path(__file__).resolve().parents[2]
    target = tmp_path / "shared.json"
    procs = [subprocess.Popen([sys.executable, "-c", _WRITER, str(target), tag, "40"],
                              cwd=harness, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
             for tag in ("A", "B", "C")]
    outs = [(p.wait(), *p.communicate()) for p in procs]
    for rc, out, errtext in outs:
        assert rc == 0, f"a writer crashed: {errtext[-500:]}"
        assert out.strip() == "0", f"a reader saw a partial file {out.strip()} time(s)"
    assert json.loads(target.read_text())["writer"] in ("A", "B", "C")  # last writer wins, whole
    assert not list(tmp_path.glob("*.tmp")), "no temp file left behind"
# --------------------------------------------------------------------------- atomic writes
def test_unique_tmp_is_per_process_and_per_thread(tmp_path: Path):
    """A FIXED '<name>.tmp' is what made concurrent writers of one destination race."""
    from codeverse.proc import unique_tmp

    out = tmp_path / "cache" / "checker.mjs"
    seen: list[Path] = []
    threads = [threading.Thread(target=lambda: seen.append(unique_tmp(out))) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len({str(p) for p in seen}) == 8
    assert all(p.parent == out.parent and p.name.startswith("checker.mjs.") for p in seen)
    assert str(os.getpid()) in seen[0].name


def test_concurrent_writers_of_one_destination_all_succeed(tmp_path: Path):
    """CQ-2: the second writer's replace() used to find its source already renamed
    away -> FileNotFoundError.  Barrier-synchronised, this failed ~half the time."""
    from codeverse.proc import write_text_atomic

    out = tmp_path / "shared.txt"
    n = 8
    bar = threading.Barrier(n)
    errors: list[BaseException] = []

    def writer() -> None:
        try:
            bar.wait()
            write_text_atomic(out, "payload\n")
        except BaseException as e:  # noqa: BLE001 — the point of the test
            errors.append(e)

    threads = [threading.Thread(target=writer) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    assert out.read_text() == "payload\n"
    assert list(tmp_path.iterdir()) == [out]  # no temp litter left behind


def test_write_json_atomic_round_trips(tmp_path: Path):
    out = tmp_path / "d" / "x.json"
    write_json_atomic(out, {"a": 1})
    assert json.loads(out.read_text()) == {"a": 1}
    assert [p.name for p in out.parent.iterdir()] == ["x.json"]

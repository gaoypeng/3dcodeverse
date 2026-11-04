"""One writer per run directory — see codeverse3d/runlock.py."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from codeverse3d.proc import (
    RunLocked,
    exclusive,
    holder_of,
)

HARNESS_ROOT = Path(__file__).resolve().parents[2]


def _child_script(run_root: Path, ready: Path, go: Path) -> str:
    """A second PROCESS that takes the run mutex, announces it, and waits to be told to let go."""
    return (
        "import sys, time, pathlib\n"
        f"sys.path.insert(0, {str(HARNESS_ROOT)!r})\n"
        "from codeverse3d.proc import exclusive, RunLocked\n"
        f"root, ready, go = pathlib.Path({str(run_root)!r}), pathlib.Path({str(ready)!r}), pathlib.Path({str(go)!r})\n"
        "try:\n"
        "    with exclusive(root, what='child'):\n"
        "        ready.write_text('held')\n"
        "        while not go.exists():\n"
        "            time.sleep(0.02)\n"
        "except RunLocked as e:\n"
        "    ready.write_text('refused: ' + str(e))\n"
    )


def test_a_second_process_is_refused_and_a_killed_holder_never_wedges_the_run(tmp_path: Path):
    """Two processes cannot mutate one workspace; ``--force`` may overwrite a dead run, never evict a
    live holder; the kernel drops the flock when the holder dies."""
    import signal
    import subprocess
    import sys

    from codeverse3d.cli._common import CliError, mutating

    run_root = tmp_path / "runs" / "slug"
    run_root.mkdir(parents=True)
    (run_root / "spec.json").write_text("{}")  # non-empty, so --force would rmtree it
    ready, go = tmp_path / "ready", tmp_path / "go"
    child = subprocess.Popen([sys.executable, "-c", _child_script(run_root, ready, go)])
    try:
        for _ in range(500):  # wait for the child to actually hold it
            if ready.exists():
                break
            time.sleep(0.02)
        assert ready.read_text() == "held", "child never took the lock"
        with pytest.raises(RunLocked) as got, exclusive(run_root):
            pass
        msg = str(got.value)
        assert str(child.pid) in msg  # names the holder, so a human can kill THAT pid
        assert "pkill" in msg, "and must warn against the pattern kill that took out 13 runs"
        with pytest.raises(CliError) as ei, mutating(run_root, what="3dcodeverse make slug"):
            pytest.fail("the mutation boundary must not be entered")
        assert ei.value.exit_code == 2
        assert (run_root / "spec.json").exists(), "the live holder's workspace must survive"
    finally:
        child.send_signal(signal.SIGKILL)
        child.wait(timeout=10)
    assert holder_of(run_root) is None, "a SIGKILLed holder cannot leave a record behind"
    with exclusive(run_root):
        pass

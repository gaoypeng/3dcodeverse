"""Subprocess lifecycle, bounded capture, atomic writes, and tolerant reads."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from codeverse3d.proc import (
    STREAM_BUDGET_BYTES,
    run_subprocess,
)


def test_a_detached_descendant_holding_the_pipes_cannot_extend_the_timeout(tmp_path: Path, monkeypatch):
    """A detached descendant holding stdout cannot extend the caller's timeout."""
    import codeverse3d.proc as proc_mod

    monkeypatch.setattr(proc_mod, "DRAIN_TIMEOUT_S", 0.3)
    t0 = time.monotonic()

    r = run_subprocess(["bash", "-c", "setsid sleep 20 & echo started; sleep 20"],
                       cwd=tmp_path, timeout_s=0.3)

    elapsed = time.monotonic() - t0
    assert r.timed_out and r.returncode != 0
    # timeout_s + one drain window + slack — NOT the 20 s the escaped descendant lives
    assert elapsed < 8.0, f"run_subprocess returned only after {elapsed:.1f}s"


@pytest.mark.timeout(120)
def test_huge_output_is_bounded_head_and_tail(tmp_path: Path):
    """Large output is bounded while preserving its head, tail, and truncation marker; one bad byte
    is replaced instead of discarding the subprocess result."""
    code = (
        "import sys\n"
        "w = sys.stdout.buffer.write\n"
        "w(b'HEADSTART ok \\xff\\xfe end\\n')\n"
        "chunk = b'x' * (1 << 20)\n"
        "for _ in range(48):\n"
        "    w(chunk)\n"
        "w(b'\\nTAILEND\\n')\n"
    )
    r = run_subprocess([sys.executable, "-c", code], cwd=tmp_path, timeout_s=110)
    assert r.returncode == 0 and not r.timed_out
    assert len(r.stdout) <= STREAM_BUDGET_BYTES + 200  # the budget plus the truncation marker
    assert r.stdout.startswith("HEADSTART ok �� end")  # head survived, invalid utf-8 replaced
    assert r.stdout.rstrip("\n").endswith("TAILEND")   # tail survived
    assert "bytes dropped" in r.stdout                 # marker sits between them
    cut = r.stdout.index("bytes dropped")
    assert "x" * 1000 in r.stdout[:cut] and "x" * 1000 in r.stdout[cut:]

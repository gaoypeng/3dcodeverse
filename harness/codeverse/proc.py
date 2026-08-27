"""Subprocess + atomic-JSON primitives shared across the harness.

One home for the run-a-child-process pattern (own process group, wall-clock
timeout, group kill, bounded captured output — :class:`ManagedProcess` and its
thin wrapper :func:`run_subprocess`), the tmp+rename JSON write, and the
tolerant JSON / JSONL readers every "best effort" side-car consumer needs.  Peer
of ``workspace.py``; stdlib-only — imports nothing from ``codeverse`` so wrappers,
spatial helpers and agents can all use it without layering back-edges.

``languages/_common.py`` currently carries the same helpers for its runtime
callers; it will become a re-export of this module.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import signal
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any


@dataclass(frozen=True)
class ProcResult:
    """Outcome of one subprocess run."""

    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    duration_ms: int


def run_subprocess(
    cmd: list[str],
    *,
    cwd: Path | str,
    timeout_s: float,
    env: Mapping[str, str] | None = None,
    stdin_text: str | None = None,
    preexec_fn: Callable[[], None] | None = None,
) -> ProcResult:
    """Run ``cmd`` in its own process group; kill the whole group on timeout.

    Never raises on non-zero exit — callers inspect :attr:`ProcResult.returncode`.
    ``preexec_fn`` runs in the child before exec (e.g. rlimits); the child is
    always started in a new session so the group kill reaps grandchildren.

    Thin wrapper over :class:`ManagedProcess` since 2026-08-27, which changed three
    behaviours on purpose:

    * output decodes as utf-8 ``errors="replace"`` — one bad byte used to raise
      UnicodeDecodeError out of ``communicate()`` and lose the whole result;
    * each stream is capped at :data:`STREAM_BUDGET_BYTES` (head + tail kept, a
      truncation marker in between) instead of growing unbounded in RAM;
    * KeyboardInterrupt — or any exception — mid-run kills the process group before
      propagating.  ``start_new_session`` children sit outside the terminal's
      foreground group, so Ctrl-C's SIGINT never reaches them: without this, every
      interrupt during a blender/node/render run orphaned the whole tree.
    """
    t0 = time.monotonic()
    timed_out = False
    with ManagedProcess(cmd, cwd=cwd, env=env, stdin_text=stdin_text, preexec_fn=preexec_fn) as mp:
        try:
            mp.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            mp.kill()  # straight SIGKILL on the group, as this path has always behaved
    return ProcResult(
        returncode=mp.returncode if mp.returncode is not None else -1,
        stdout=mp.stdout_text,
        stderr=mp.stderr_text,
        timed_out=timed_out,
        duration_ms=int((time.monotonic() - t0) * 1000),
    )


#: how long to keep draining the pipes after the child is dead.  Kept from the old
#: ``_drain_after_kill`` (and ``agents/watchdog.py``'s ``thread.join(timeout=10)``);
#: a descendant that setsid'd or daemonised while inheriting stdout/stderr keeps the
#: pipes open with no upper bound, and waiting on it silently voided ``timeout_s``
#: for every build/render path that funnels through here.
DRAIN_TIMEOUT_S = 10.0
_ABANDONED = "(output truncated: a detached descendant still held the pipe past the drain window)"

#: per-stream cap on captured output: the first ``budget // 2`` bytes and the last
#: ``budget - budget // 2`` bytes are kept, with a truncation marker in between.
#: 8 MiB per stream — roomy for real build/render logs, while a runaway print loop
#: can no longer grow the harness RSS (or the trajectory files, which record full
#: stdout — only the model-facing SHELL_OUT_CAP existed before) without bound.
STREAM_BUDGET_BYTES = 8 * 1024 * 1024

#: longest chunk one pump read returns.  A child that emits gigabytes with no
#: newline would otherwise accumulate the whole "line" in RAM before the stream
#: budget could apply; a line longer than this reaches ``on_line`` as several
#: chunk-sized calls.
_LINE_CAP_BYTES = 1024 * 1024


class _BoundedSink:
    """Head+tail byte buffer for ONE stream (first half of the budget from the head,
    last half from the tail, everything between counted and replaced by a marker).

    The lock only pairs one pump thread's ``feed`` with a late ``text`` read — it is
    never contended in the normal path (``text`` runs after the pump is joined).
    """

    def __init__(self, budget: int) -> None:
        self._lock = threading.Lock()
        self._head_max = budget // 2
        self._tail_max = budget - self._head_max
        self._head = bytearray()
        self._tail: deque[bytes] = deque()
        self._tail_len = 0
        self._dropped = 0

    def feed(self, chunk: bytes) -> None:
        with self._lock:
            room = self._head_max - len(self._head)
            if room > 0:
                self._head += chunk[:room]
                chunk = chunk[room:]
            if not chunk:
                return
            self._tail.append(chunk)
            self._tail_len += len(chunk)
            while self._tail_len > self._tail_max and len(self._tail) > 1:
                gone = self._tail.popleft()
                self._tail_len -= len(gone)
                self._dropped += len(gone)

    def text(self) -> str:
        """Decoded output — ``errors="replace"``, so a bad byte costs one U+FFFD, not the run."""
        with self._lock:
            head = self._head.decode("utf-8", errors="replace")
            tail_part = b"".join(self._tail).decode("utf-8", errors="replace")
            dropped = self._dropped
        if not dropped:
            return head + tail_part
        marker = (f"\n[... {dropped} bytes dropped: stream exceeded the "
                  f"{self._head_max + self._tail_max}-byte capture budget (head+tail kept) ...]\n")
        return head + marker + tail_part


class ManagedProcess:
    """A ``Popen`` in its own session whose process group cannot outlive the ``with`` block.

    The shared lifecycle core under :func:`run_subprocess` and
    ``agents.watchdog.run_with_watchdog`` (which keep their public signatures):

    * binary pipes + two pump threads feeding a :class:`_BoundedSink` per stream
      (:data:`STREAM_BUDGET_BYTES` each, decoded utf-8 ``errors="replace"``);
    * an optional ``on_line(stream, line)`` observer called from the pump threads for
      every line (``stream`` is ``"stdout"``/``"stderr"``, the line is decoded with
      its trailing newline stripped; a line longer than :data:`_LINE_CAP_BYTES`
      arrives as several chunk-sized calls; observer exceptions are suppressed);
    * a stdin WRITER THREAD — a child that never reads a >64 KiB pipe-buffer of
      stdin can no longer wedge the caller's main thread in ``stdin.write`` (codex
      really does deliver prompts via stdin once they exceed 100 kB);
    * ``__exit__``: on ANY exception — KeyboardInterrupt above all, because
      ``start_new_session`` children never receive the terminal's Ctrl-C SIGINT —
      or with the child still running at scope exit, the whole group gets
      SIGTERM → ``grace_s`` → SIGKILL, the pumps are drained for at most
      :data:`DRAIN_TIMEOUT_S`, and the child is reaped with ``wait()`` so no zombie
      is left.  A descendant that setsid'd away and still holds a pipe cannot stall
      the drain: closing the stream from this thread would block on the buffer lock
      the parked reader holds (measured), so the stuck pump is flagged to stop, drops
      its in-flight chunk, and self-cleans whenever its read finally returns —
      ``stderr_text`` then carries a truncation note.
    """

    def __init__(
        self,
        cmd: Sequence[str],
        *,
        cwd: Path | str,
        env: Mapping[str, str] | None = None,
        stdin_text: str | None = None,
        preexec_fn: Callable[[], None] | None = None,
        on_line: Callable[[str, str], None] | None = None,
        stream_budget: int = STREAM_BUDGET_BYTES,
        grace_s: float = 5.0,
    ) -> None:
        self._on_line = on_line
        self._grace_s = grace_s
        self._stop = threading.Event()  # tells an abandoned pump to quit feeding
        self._abandoned = False
        self._sinks = {"stdout": _BoundedSink(stream_budget), "stderr": _BoundedSink(stream_budget)}
        self.proc: subprocess.Popen[bytes] = subprocess.Popen(
            list(cmd),
            cwd=str(cwd),
            env=dict(env) if env is not None else None,
            stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            preexec_fn=preexec_fn,
        )
        assert self.proc.stdout is not None and self.proc.stderr is not None
        self._pumps = [
            threading.Thread(target=self._pump, args=(self.proc.stdout, "stdout"), daemon=True),
            threading.Thread(target=self._pump, args=(self.proc.stderr, "stderr"), daemon=True),
        ]
        for t in self._pumps:
            t.start()
        self._writer: threading.Thread | None = None
        if stdin_text is not None:
            self._writer = threading.Thread(
                target=self._write_stdin, args=(stdin_text.encode("utf-8"),), daemon=True
            )
            self._writer.start()

    # ------------------------------------------------------------- pump / writer threads
    def _pump(self, stream: IO[bytes], tag: str) -> None:
        sink = self._sinks[tag]
        with contextlib.suppress(OSError, ValueError):  # ValueError: stream closed under us
            while True:
                chunk = stream.readline(_LINE_CAP_BYTES)
                if not chunk or self._stop.is_set():
                    break
                sink.feed(chunk)
                if self._on_line is not None:
                    with contextlib.suppress(Exception):  # observer bugs must not kill the pump
                        self._on_line(tag, chunk.decode("utf-8", errors="replace").rstrip("\n"))
        with contextlib.suppress(OSError):
            stream.close()

    def _write_stdin(self, data: bytes) -> None:
        stdin = self.proc.stdin
        if stdin is None:  # pragma: no cover — the constructor only starts us with a pipe
            return
        with contextlib.suppress(OSError, ValueError):  # BrokenPipe (child never read) is OSError
            stdin.write(data)
        with contextlib.suppress(OSError, ValueError):
            stdin.close()

    # ------------------------------------------------------------- what the wrappers need
    @property
    def pid(self) -> int:
        """The child's pid — also its process-GROUP id (``start_new_session``)."""
        return self.proc.pid

    @property
    def returncode(self) -> int | None:
        return self.proc.returncode

    def poll(self) -> int | None:
        return self.proc.poll()

    def wait(self, timeout: float | None = None) -> int:
        """``Popen.wait`` — raises ``subprocess.TimeoutExpired`` exactly like it."""
        return self.proc.wait(timeout=timeout)

    @property
    def stdout_text(self) -> str:
        """Decoded, bounded stdout.  Complete only once the ``with`` block has exited."""
        return self._sinks["stdout"].text()

    @property
    def stderr_text(self) -> str:
        """Decoded, bounded stderr (+ a note when an escaped descendant held the pipe)."""
        text = self._sinks["stderr"].text()
        if self._abandoned:
            return f"{text}\n{_ABANDONED}" if text else _ABANDONED
        return text

    # ------------------------------------------------------------- kills and teardown
    def kill(self) -> None:
        """SIGKILL the whole group right now (fall back to just the child)."""
        try:
            os.killpg(self.proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            with contextlib.suppress(ProcessLookupError):
                self.proc.kill()

    def terminate(self, grace_s: float | None = None) -> None:
        """SIGTERM the group, escalate to SIGKILL after ``grace_s`` (default: the
        constructor's ``grace_s`` — the old ``watchdog.kill_process_group`` contract)."""
        grace = self._grace_s if grace_s is None else grace_s
        if self.proc.poll() is not None:
            return
        try:
            os.killpg(self.proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        except PermissionError:  # cannot signal the group — go for the child itself
            self.proc.terminate()
        try:
            self.proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            self.kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                self.proc.wait(timeout=self._grace_s)

    def __enter__(self) -> ManagedProcess:
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: object) -> None:
        # Any in-flight exception (KeyboardInterrupt included) or a child still
        # running at scope exit: the group dies HERE.  Ctrl-C's SIGINT never reaches
        # a start_new_session child, so this is the only thing standing between an
        # interrupt and an orphaned blender/node/chrome tree.  Never swallows exc.
        if self.proc.poll() is None:
            self.terminate()
        self._drain_and_reap()

    def _drain_and_reap(self) -> None:
        deadline = time.monotonic() + DRAIN_TIMEOUT_S  # module global on purpose: tests dial it down
        if self._writer is not None:
            self._writer.join(timeout=max(0.0, deadline - time.monotonic()))
        for t in self._pumps:
            t.join(timeout=max(0.0, deadline - time.monotonic()))
        if any(t.is_alive() for t in self._pumps):
            self._stop.set()  # the parked daemon pump self-cleans when its read returns
            self._abandoned = True
        with contextlib.suppress(subprocess.TimeoutExpired):
            self.proc.wait(timeout=DRAIN_TIMEOUT_S)  # reap — a zombie survives killpg


def kill_group(proc: subprocess.Popen[str]) -> None:
    """SIGKILL ``proc``'s whole process group (fall back to just the child)."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        proc.kill()


# ------------------------------------------------------------------ env scrubbing
#: COPY of the credential patterns in ``agents/cli_common.is_secret_env`` (its sibling —
#: keep the two in sync; tests/core/test_proc.py pins them together).  Duplicated because
#: proc.py imports nothing from ``codeverse``: the agents package sits above this layer.
_SECRET_EXACT: frozenset[str] = frozenset({
    "GEMINI_API_KEYS", "GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
    "OPENAI_ORG_ID", "HF_TOKEN", "HUGGINGFACE_TOKEN", "GITHUB_TOKEN", "GH_TOKEN",
    "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "SUPABASE_SERVICE_ROLE_KEY", "CODEX_API_KEY",
})
_SECRET_SUFFIXES: tuple[str, ...] = (
    "_API_KEY", "_SECRET", "_SECRET_KEY", "_TOKEN", "_AUTH_TOKEN", "_PASSWORD", "_PRIVATE_KEY",
)


def scrub_secrets(env: dict[str, str]) -> dict[str, str]:
    """``env`` minus credential-shaped variables (exact names + ``*_API_KEY``-style suffixes).

    Model-generated code runs in subprocesses that inherit the harness environment
    (blender, the cadquery python, node) and nothing generated ever legitimately needs
    a credential — so the keys must not be there to leak into logs, artifacts or child
    processes.  Everything else (PATH, HOME, DISPLAY, MESA_*/GALLIUM_* render config...)
    passes through untouched.
    """
    return {
        k: v for k, v in env.items()
        if k not in _SECRET_EXACT and not k.endswith(_SECRET_SUFFIXES)
    }


def tail(text: str, *, max_lines: int = 40, max_chars: int = 4000) -> str:
    """Last ``max_lines`` lines of ``text``, capped at ``max_chars`` characters."""
    lines = text.splitlines()[-max_lines:]
    s = "\n".join(lines)
    return s[-max_chars:] if len(s) > max_chars else s


def unique_tmp(path: Path) -> Path:
    """A temp sibling of ``path`` unique to this process AND thread.

    A FIXED ``<name>.tmp`` is a race: two writers of the same destination both
    create it and the second ``replace()`` finds its source already renamed away,
    raising FileNotFoundError.  The harness fans work out over thread pools, so
    "one writer per destination" does not hold — see ``tracks/scene_asset_gen``,
    where the losing thread's asset gate was silently skipped.
    """
    return path.with_name(f"{path.name}.{os.getpid()}-{threading.get_ident()}.tmp")


def write_text_atomic(path: Path, text: str, *, encoding: str = "utf-8") -> Path:
    """tmp + rename so readers never see a partial file, safe under concurrency."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = unique_tmp(path)
    try:
        tmp.write_text(text, encoding=encoding)
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)  # a failed write must not leave litter behind
        raise
    return path


def write_json_atomic(path: Path, data: Any) -> None:
    """tmp + rename so readers never see a partial file — including when several
    writers race for the same path.

    Delegates to :func:`write_text_atomic`, whose tmp name is private to this writer
    (pid + thread id).  One shared ``<path>.tmp`` was NOT atomic across writers: A could
    rename B's half-written tmp into place — readers then observed truncated or
    zero-byte JSON at the published path — and B's own ``replace()`` died with
    FileNotFoundError.  That is not hypothetical here: the scene track fans zone agents
    out over ONE workspace root and every agent's MCP ``build`` tool writes
    ``artifacts/build_last.json`` and ``artifacts/measurement.json`` through this
    function, and a clobbered ``run_state.json`` is an unresumable run
    (``RunState.load`` refuses to guess).  Last writer to rename still wins.
    """
    write_text_atomic(path, json.dumps(data, indent=2, ensure_ascii=False, default=str))


# ------------------------------------------------------------------ tolerant reads
def read_json_or_none(path: Path | str, *, errors: str | None = None) -> dict[str, Any] | None:
    """Parse ``path`` as a JSON object; ``None`` when absent, unreadable, malformed
    or not a dict at the top level.

    The one tolerant reader behind every "use it if it is there" side-car: it
    replaced identical ``try: json.loads(read_text()) except (OSError, ValueError)``
    copies in ``flywheel/telemetry.read_json``, ``bench/complexity_report._read_json``,
    ``gallery/index._measurement_complexity`` / ``_spec_fields``,
    ``cli/main._best_round_of_record``, ``runlock._holder``,
    ``languages/threejs/lint._load_plan``, ``cost/reconstruct._read_json``,
    ``spatial/_render_common.read_json`` and ``cli/_judge.plan_summary_for`` — all
    the same algorithm, differing only in whether they also checked ``isinstance(dict)``
    (which every caller then relied on anyway).  Strict readers that must raise on
    a corrupt input (``Workspace.read_json``, ``judges/calibration._read_json``)
    deliberately do NOT use this.

    ``errors`` is ``read_text``'s decode policy (``"replace"`` for files another
    process may still be writing).  Reads UTF-8, the encoding :func:`write_json_atomic`
    writes.
    """
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8", errors=errors))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def iter_jsonl_lines(path: Path | str) -> Iterator[tuple[int, str]]:
    """``(line_no, line)`` for every non-blank line of a JSONL file, 1-based.

    Missing file → nothing; decoded UTF-8 with ``errors="replace"`` so one bad byte
    (or a SIGKILL mid-append) costs at most that line.  Shared by
    :func:`read_jsonl_lenient` and by the model-validating readers that keep their own
    per-line parse (``cost/ledger.load_ledger``, ``bench/_jsonl.read_jsonl``).
    """
    p = Path(path)
    if not p.is_file():
        return
    for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines()):
        if line.strip():
            yield i + 1, line


def read_jsonl_lenient(
    path: Path | str, *, log: logging.Logger | None = None, dicts_only: bool = False
) -> list[Any]:
    """Every parseable JSON line of ``path``; unparseable lines are skipped (debug-logged
    to ``log`` when given), ``dicts_only`` also drops non-object rows.

    Replaced the same skip-bad-lines loop in ``events.EventLog.read``,
    ``cost/reconstruct._read_jsonl``, ``flywheel/trajectories.read_events``,
    ``cli/skills_cmd`` (skills report) and ``agents/transcript.read_transcript`` —
    a truncated last line must never lose the rest of the file (the contract
    ``cost.ledger.load_ledger`` has always had).
    """
    out: list[Any] = []
    for i, line in iter_jsonl_lines(path):
        try:
            row = json.loads(line)
        except ValueError as e:
            if log is not None:
                log.debug("%s:%d unreadable: %s", path, i, e)
            continue
        if dicts_only and not isinstance(row, dict):
            continue
        out.append(row)
    return out


def append_jsonl_line(path: Path | str, rec: Any, lock: threading.Lock) -> None:
    """Append ``rec`` as one JSON line under ``lock`` (``ensure_ascii=False``,
    ``default=str`` so paths / enums / datetimes serialise).

    Replaced the identical bodies of ``events.EventLog.emit`` and
    ``agents/transcript.Trajectory.append``.  ``cost/ledger.CostLedger.append`` is NOT
    a copy — it mkdirs, flushes and swallows OSError ("flushed on write") — and keeps
    its own.
    """
    line = json.dumps(rec, ensure_ascii=False, default=str)
    with lock, Path(path).open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")

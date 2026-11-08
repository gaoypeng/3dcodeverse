"""Process-level primitives shared by the instruments.

Run-a-child-process (:class:`ManagedProcess`, :func:`run_subprocess`), atomic JSON writes, a tolerant
JSON reader and the machine-wide :class:`Slots`.  Stdlib-only: imports nothing from ``codeverse3d``.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import random
import signal
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any


@dataclass(frozen=True)
class ProcResult:
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

    Never raises on non-zero exit.  Output decodes as utf-8 ``errors="replace"`` and each stream is
    capped at :data:`STREAM_BUDGET_BYTES`; any exception mid-run (Ctrl-C included) kills the group.
    ``preexec_fn`` runs in the child before exec (e.g. rlimits).
    """
    t0 = time.monotonic()
    timed_out = False
    with ManagedProcess(cmd, cwd=cwd, env=env, stdin_text=stdin_text, preexec_fn=preexec_fn) as mp:
        try:
            mp.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            mp.kill()
    return ProcResult(
        returncode=mp.returncode if mp.returncode is not None else -1,
        stdout=mp.stdout_text,
        stderr=mp.stderr_text,
        timed_out=timed_out,
        duration_ms=int((time.monotonic() - t0) * 1000),
    )


#: how long to keep draining the pipes after the child is dead; a daemonised descendant holding
#: the pipes open would otherwise void ``timeout_s``.
DRAIN_TIMEOUT_S = 10.0
_ABANDONED = "(output truncated: a detached descendant still held the pipe past the drain window)"
_STILL_HELD = "(output truncated: a process in the group still held the pipe past the drain window)"


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


#: per-stream cap on captured output: head and tail halves are kept with a marker between.
STREAM_BUDGET_BYTES = 8 * 1024 * 1024
TERMINATE_GRACE_S = 5.0

#: longest chunk one pump read returns (bounds RAM when a child prints no newline).
_LINE_CAP_BYTES = 1024 * 1024


class _BoundedSink:
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

    Core of :func:`run_subprocess`: two pump
    threads feed a bounded sink per stream, an optional ``on_line(stream, line)`` observer sees
    each line (exceptions suppressed), and stdin is written from its own thread.  ``__exit__``
    on any exception, or with the child still running, SIGTERMs the group (SIGKILL after
    :data:`TERMINATE_GRACE_S`), drains the pumps for at most :data:`DRAIN_TIMEOUT_S` and reaps.
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
    ) -> None:
        self._on_line = on_line
        self._stop = threading.Event()  # tells an abandoned pump to quit feeding
        self._abandoned = ""  # the truncation note to append, once we know which one
        self._sinks = {"stdout": _BoundedSink(STREAM_BUDGET_BYTES), "stderr": _BoundedSink(STREAM_BUDGET_BYTES)}
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
    def returncode(self) -> int | None:
        return self.proc.returncode

    def poll(self) -> int | None:
        return self.proc.poll()

    def wait(self, timeout: float | None = None) -> int:
        return self.proc.wait(timeout=timeout)

    @property
    def stdout_text(self) -> str:
        return self._sinks["stdout"].text()

    @property
    def stderr_text(self) -> str:
        text = self._sinks["stderr"].text()
        if self._abandoned:
            return f"{text}\n{self._abandoned}" if text else self._abandoned
        return text

    # ------------------------------------------------------------- kills and teardown
    def kill(self) -> None:
        try:
            os.killpg(self.proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            with contextlib.suppress(ProcessLookupError):
                self.proc.kill()

    def terminate(self) -> None:
        try:
            os.killpg(self.proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        except PermissionError:  # cannot signal the group — go for the child itself
            self.proc.terminate()
        deadline = time.monotonic() + TERMINATE_GRACE_S
        while time.monotonic() < deadline:
            self.proc.poll()  # reap first: an unreaped zombie leader keeps the group "alive"
            if not _group_alive(self.proc.pid):
                return
            time.sleep(0.02)
        self.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            self.proc.wait(timeout=TERMINATE_GRACE_S)

    def __enter__(self) -> ManagedProcess:
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: object) -> None:
        # start_new_session children never see Ctrl-C, so the group dies here on any exception or
        # if still alive.  Ask whether the GROUP exists, not the leader (it may exit 0 with
        # grandchildren running).
        self.proc.poll()
        if _group_alive(self.proc.pid):
            self.terminate()
        self._drain_and_reap()

    def _drain_and_reap(self) -> None:
        deadline = time.monotonic() + DRAIN_TIMEOUT_S  # module global on purpose: tests dial it down
        if self._writer is not None:
            self._writer.join(timeout=max(0.0, deadline - time.monotonic()))
        for t in self._pumps:
            t.join(timeout=max(0.0, deadline - time.monotonic()))
        with contextlib.suppress(subprocess.TimeoutExpired):
            self.proc.wait(timeout=DRAIN_TIMEOUT_S)  # reap — a zombie survives killpg
        if any(t.is_alive() for t in self._pumps):
            self._stop.set()  # the parked daemon pump self-cleans when its read returns
            self._abandoned = _STILL_HELD if _group_alive(self.proc.pid) else _ABANDONED


# ------------------------------------------------------------------ env scrubbing
#: The one owner of the credential patterns.
_SECRET_EXACT: frozenset[str] = frozenset({
    "GEMINI_API_KEYS", "GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
    "OPENAI_ORG_ID", "HF_TOKEN", "HUGGINGFACE_TOKEN", "GITHUB_TOKEN", "GH_TOKEN",
    "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "SUPABASE_SERVICE_ROLE_KEY", "CODEX_API_KEY",
})
_SECRET_SUFFIXES: tuple[str, ...] = (
    "_API_KEY", "_SECRET", "_SECRET_KEY", "_TOKEN", "_AUTH_TOKEN", "_PASSWORD", "_PRIVATE_KEY",
)


def scrub_secrets(env: dict[str, str]) -> dict[str, str]:
    """``env`` minus credential-shaped variables (exact names + ``*_API_KEY``-style suffixes)."""
    return {
        k: v for k, v in env.items()
        if k not in _SECRET_EXACT and not k.endswith(_SECRET_SUFFIXES)
    }


def tail(text: str, *, max_lines: int = 40, max_chars: int = 4000) -> str:
    lines = text.splitlines()[-max_lines:]
    s = "\n".join(lines)
    return s[-max_chars:] if len(s) > max_chars else s


def sha256_file(path: Path | str) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


#: the one timeout of every ``--version`` probe (a cold Blender start is the slow one)
VERSION_TIMEOUT_S = 60.0


def version_line(cmd: Sequence[str], *, timeout: float = VERSION_TIMEOUT_S) -> tuple[int | None, str]:
    """``(returncode, first output line)`` of a ``--version``-style probe.

    ``returncode`` is None when the binary could not be run at all (missing, timed out)
    and the line then says why; an empty line means the probe printed nothing."""
    try:
        p = subprocess.run(list(cmd), capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return None, f"timed out after {timeout:g}s"
    except OSError as e:
        return None, str(e)
    out = (p.stdout or p.stderr).strip().splitlines()
    return p.returncode, (out[0].strip() if out else "")


def unique_tmp(path: Path) -> Path:
    """A temp sibling of ``path`` unique to this process and thread (a fixed ``.tmp`` races between writers)."""
    return path.with_name(f"{path.name}.{os.getpid()}-{threading.get_ident()}.tmp")


def write_text_atomic(path: Path, text: str, *, encoding: str = "utf-8") -> Path:
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
    write_text_atomic(path, json.dumps(data, indent=2, ensure_ascii=False, default=str))


# ------------------------------------------------------------------ tolerant reads
def read_json_or_none(path: Path | str, *, errors: str | None = None) -> dict[str, Any] | None:
    """Parse ``path`` as a JSON object; ``None`` when absent, unreadable, malformed or not a dict.

    ``errors`` is ``read_text``'s decode policy (``"replace"`` for files still being written).
    """
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8", errors=errors))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


class SlotsBusy(RuntimeError):
    """Every slot stayed busy past the wait budget."""


class Slots:
    """``n`` machine-wide in-flight slots: one lock file per slot, ``<root>/NN.lock``, held with
    ``fcntl.flock`` while one model call is out.  The kernel drops a dead holder's lock, so a
    killed process never strands a slot.
    """

    #: nap between sweeps while every slot is busy (seconds, jittered x0.5..1.5)
    POLL_S = 0.05

    def __init__(self, n: int, root: Path | str) -> None:
        if n < 1:
            raise ValueError(f"Slots needs n >= 1, got {n}")
        self.n = int(n)
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, i: int) -> Path:
        return self.root / f"{i:02d}.lock"

    def try_take(self) -> int | None:
        """A held slot (its file descriptor), or ``None`` when all are busy; never waits.
        The sweep starts at a random slot so a small-cap process is not starved."""
        start = random.randrange(self.n)
        for step in range(self.n):
            fd = os.open(self._path((start + step) % self.n), os.O_RDWR | os.O_CREAT, 0o666)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(fd)
                continue
            except BaseException:
                os.close(fd)
                raise
            return fd
        return None

    def take(self, timeout_s: float | None) -> int:
        """Wait up to ``timeout_s`` (``None`` = forever) for a slot; raises ``SlotsBusy``."""
        deadline = None if timeout_s is None else time.monotonic() + timeout_s
        while (fd := self.try_take()) is None:
            left = None if deadline is None else deadline - time.monotonic()
            if left is not None and left <= 0:
                raise SlotsBusy(f"all {self.n} in-flight slots in {self.root} busy; waited {timeout_s}s")
            nap = random.uniform(0.5, 1.5) * self.POLL_S
            time.sleep(nap if left is None else min(nap, left))
        return fd

    @staticmethod
    def give(fd: int) -> None:
        """Hand a slot back; LOCK_UN first so a forked child's inherited descriptor does not keep it held."""
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def busy(self) -> int:
        """How many slots are held machine-wide (``3dcode doctor``); never takes one."""
        n = 0
        for i in range(self.n):
            try:
                fd = os.open(self._path(i), os.O_RDONLY)
            except OSError:
                continue  # never created, so never taken
            try:
                fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                n += 1
            finally:
                os.close(fd)
        return n

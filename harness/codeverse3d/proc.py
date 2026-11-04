"""Process-level primitives shared across the harness.

Run-a-child-process (:class:`ManagedProcess`, :func:`run_subprocess`), atomic JSON writes, tolerant
JSON / JSONL readers, the event stream (:class:`EventLog`), the per-run mutex (:func:`exclusive`)
and the context-carrying :func:`fan_out`.  Stdlib-only: imports nothing from ``codeverse3d``.
"""

from __future__ import annotations

import contextlib
import contextvars
import fcntl
import hashlib
import json
import logging
import os
import signal
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
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

    Shared core of :func:`run_subprocess` and ``agents/cli_common.run_with_watchdog``: two pump
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
        # started and registered under the guard so a stop can neither miss this group nor race a new one
        with _LIVE_GUARD:
            if _STOPPING.is_set():
                raise ProcessStopping(f"not starting {cmd[0]!r}: this process is stopping")
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
            _LIVE.add(self)
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
        try:
            self.proc.poll()
            if _group_alive(self.proc.pid):
                self.terminate()
            self._drain_and_reap()
        finally:
            with _LIVE_GUARD:
                _LIVE.discard(self)

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


#: every :class:`ManagedProcess` whose group may still be alive; a stop signal kills these
#: (signals reach only the main thread, so worker-thread ``__exit__`` never runs).
_LIVE: set[ManagedProcess] = set()
_LIVE_GUARD = threading.Lock()
_STOPPING = threading.Event()
STOP_GRACE_S = 30.0


class ProcessStopping(RuntimeError):
    """A child was requested after this process began to stop."""


def kill_live_children() -> int:
    with _LIVE_GUARD:
        _STOPPING.set()
        live = list(_LIVE)
    for mp in live:
        mp.kill()
    return len(live)


def install_stop_handlers() -> None:
    """SIGTERM / SIGINT kill every live child group, refuse new ones, then end the process
    (``KeyboardInterrupt`` / exit 143), outright after :data:`STOP_GRACE_S`.  Main thread only."""
    if threading.current_thread() is not threading.main_thread():
        return

    def stop(signum: int, _frame: object) -> None:
        kill_live_children()
        status = 130 if signum == signal.SIGINT else 128 + signum
        backstop = threading.Timer(STOP_GRACE_S, os._exit, args=(status,))
        backstop.daemon = True
        backstop.start()
        if signum == signal.SIGINT:
            raise KeyboardInterrupt
        raise SystemExit(status)

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, stop)


# ------------------------------------------------------------------ env scrubbing
#: The one owner of the credential patterns (``agents/cli_common.clean_env`` uses :func:`scrub_secrets`).
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


def version_line(cmd: Sequence[str], *, timeout: float) -> tuple[int | None, str]:
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


def iter_jsonl_lines(path: Path | str) -> Iterator[tuple[int, str]]:
    """``(line_no, line)`` for every non-blank line of a JSONL file, 1-based.

    Missing file → nothing; invalid bytes are replaced so one bad byte costs at most that line.
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
    line = json.dumps(rec, ensure_ascii=False, default=str)
    with lock, Path(path).open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


# ===================================================================== events
log = logging.getLogger(__name__)


class EventLog:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, event: str, **data: Any) -> None:
        append_jsonl_line(self.path, {"t": round(time.time(), 3), "event": event, **data}, self._lock)

    def read(self) -> list[dict[str, Any]]:
        return read_jsonl_lenient(self.path, log=log)


class _NullEvents:
    """An event sink that drops everything."""

    def emit(self, event: str, **data: Any) -> None:
        pass


#: use ``NULL_EVENTS if events is None else events``, never ``events or NULL_EVENTS`` (an empty fake log is falsy)
NULL_EVENTS = _NullEvents()


# ===================================================================== runlock
LOCKS_DIR = ".locks"


class RunLocked(RuntimeError):
    """Another process or thread is already running this run."""


def _held_message(run_root: Path | str, held: dict, *, action: str) -> str:
    started = time.strftime("%H:%M:%S", time.localtime(held.get("started", 0)))
    name = Path(run_root).name
    return (
        f"refusing to {action} run {name}: it is being run right now by pid {held.get('pid', '?')} "
        f"(started {started}: {held.get('what') or '3dcodeverse'}).  Two processes on one run "
        f"corrupt each other's state.  Look at it with `3dcodeverse status {name}`, or stop that "
        f"ONE process with `kill {held.get('pid', '?')}` — never `pkill -f 3dcodeverse`, which kills every "
        f"other run on this machine too."
    )


def flock_path(run_root: Path | str) -> Path:
    root = Path(run_root).resolve()
    return root.parent / LOCKS_DIR / f"{root.name}.lock"


_HELD: dict[str, list] = {}  # flock path -> [fd, owning thread ident] held by THIS process
_HELD_GUARD = threading.Lock()


def _record_of(fd: int) -> dict | None:
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        raw = os.read(fd, 4096).decode("utf-8", "replace").strip()
        rec = json.loads(raw) if raw else None
    except (OSError, ValueError):
        return None
    return rec if isinstance(rec, dict) else None


def _write_record(fd: int, rec: dict) -> None:
    os.ftruncate(fd, 0)
    os.lseek(fd, 0, os.SEEK_SET)
    os.write(fd, json.dumps(rec).encode())
    os.fsync(fd)


def holder_of(run_root: Path | str) -> dict | None:
    """The ``{pid, started, what}`` of whoever holds this run, without taking it; ``None`` if nobody
    does (a stale record left by a SIGKILL is detected by probing the flock)."""
    try:
        fd = os.open(flock_path(run_root), os.O_RDONLY)
    except OSError:
        return None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except OSError:
            return _record_of(fd)  # refused: somebody is holding it right now
        fcntl.flock(fd, fcntl.LOCK_UN)
        return None
    finally:
        os.close(fd)


@contextmanager
def exclusive(run_root: Path | str, *, what: str = "", action: str = "enter") -> Iterator[None]:
    """THE mutex for one run: hold it around every mutation of the run directory.

    Raises :class:`RunLocked` when another process or thread holds it; the same thread re-enters."""
    key, me = str(flock_path(run_root)), threading.get_ident()
    with _HELD_GUARD:
        entry = _HELD.get(key)
    if entry is not None:
        if entry[1] != me:
            raise RunLocked(_held_message(run_root, holder_of(run_root) or {}, action=action))
        yield  # re-entry: the outermost `with` owns the release
        return
    path = Path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        held = _record_of(fd) or {}
        os.close(fd)
        raise RunLocked(_held_message(run_root, held, action=action)) from None
    except BaseException:
        os.close(fd)
        raise
    try:  # a failed write must not leak the lock
        _write_record(fd, {"pid": os.getpid(), "started": time.time(), "what": what})
        with _HELD_GUARD:
            _HELD[key] = [fd, me]
        yield
    finally:
        with _HELD_GUARD:
            _HELD.pop(key, None)
        try:
            os.ftruncate(fd, 0)  # the file stays (unlinking it races a waiter's open)
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            os.close(fd)


# ===================================================================== fanout


def fan_out[T, R](
    items: Sequence[T] | Iterable[T],
    fn: Callable[[T], R],
    max_workers: int,
    *,
    label: str = "fanout",
    item_name: Callable[[T], str] | None = None,
) -> list[R | Exception]:
    """Run ``fn`` over ``items`` in a thread pool; return results in input order.

    Each result is the return value or the raised ``Exception``.
    """
    items = list(items)
    ctx_snapshot = contextvars.copy_context()
    results: list[R | Exception] = [None] * len(items)  # type: ignore[list-item]
    if not items:
        return results
    workers = max(1, min(max_workers, len(items)))
    t_all = time.time()

    def _run(i: int) -> None:
        item = items[i]
        name = item_name(item) if item_name else str(i)
        t0 = time.time()
        try:
            out: R | Exception = fn(item)
        except Exception as e:  # noqa: BLE001 — captured per item by design
            out = e
            log.warning("%s[%s] failed: %s: %s", label, name, type(e).__name__, e)
        dt = time.time() - t0
        results[i] = out
        log.info("%s[%s] done in %.1fs (%s)", label, name, dt, "error" if isinstance(out, Exception) else "ok")

    if workers == 1:
        for i in range(len(items)):
            _run(i)
    else:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix=label) as pool:
            list(pool.map(lambda i: ctx_snapshot.copy().run(_run, i),
                          range(len(items))))

    n_failed = sum(1 for r in results if isinstance(r, Exception))
    log.info("%s: %d/%d ok in %.1fs", label, len(items) - n_failed, len(items), time.time() - t_all)
    return results

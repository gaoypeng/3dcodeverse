"""What every vendor-CLI backend shares: the trajectory folder (prompt, transcript, stdout/stderr,
result.json), the watchdog'd session process, the hardened environment, write-scope enforcement,
failure classification and the tool-call trace."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import sys
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from codeverse3d.contracts.agent import AgentJob, AgentResult, FileChange
from codeverse3d.contracts.common import Usage, is_harness_owned
from codeverse3d.proc import (
    ManagedProcess,
    append_jsonl_line,
    read_jsonl_lenient,
    scrub_secrets,
    write_json_atomic,
)
from codeverse3d.workspace import Workspace

#: per-session transcript caps; the unabridged (bounded) stream still lands in stdout.json
LINE_BUDGET_BYTES = 64 * 1024 * 1024
LINE_BUDGET_ROWS = 200_000


class Trajectory:

    def __init__(self, directory: Path | str):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._budget = threading.Lock()  # guards the counters below, never nested in _lock
        self._rows = self._bytes = 0
        self._spent = False

    # ------------------------------------------------------------------ paths
    @property
    def prompt_path(self) -> Path:
        return self.dir / "prompt.md"

    @property
    def transcript_path(self) -> Path:
        return self.dir / "transcript.jsonl"

    @property
    def result_path(self) -> Path:
        return self.dir / "result.json"

    # ------------------------------------------------------------------ writes
    def write_prompt(self, prompt: str, system: str = "") -> Path:
        body = prompt if not system else f"<!-- system -->\n{system}\n\n<!-- prompt -->\n{prompt}"
        self.prompt_path.write_text(body)
        return self.prompt_path

    def write_text(self, name: str, text: str) -> Path:
        p = self.dir / name
        p.write_text(text)
        return p

    def append(self, kind: str, **data: Any) -> None:
        """Append one JSONL turn: ``{"t": epoch, "kind": kind, **data}``, until the
        session's byte/row budget trips — then one marker row and nothing more."""
        rec: dict[str, Any] = {"t": round(time.time(), 3), "kind": kind, **data}
        with self._budget:
            if self._spent:
                return
            self._rows += 1
            self._bytes += len(json.dumps(rec, ensure_ascii=False, default=str))
            self._spent = self._rows > LINE_BUDGET_ROWS or self._bytes > LINE_BUDGET_BYTES
            if self._spent:
                rec = {"t": rec["t"], "kind": "line_budget_exhausted",
                       "rows": self._rows - 1, "bytes": self._bytes}
        append_jsonl_line(self.transcript_path, rec, self._lock)

    def write_result(self, result: BaseModel, **extra: Any) -> None:
        write_json_atomic(self.result_path, result.model_dump(mode="json") | extra)

    def read_transcript(self) -> list[dict[str, Any]]:
        return read_jsonl_lenient(self.transcript_path)


# ===================================================================== watchdog
_SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".gemini"}


@dataclass
class CompletedProc:
    """Outcome of :func:`run_with_watchdog`; ``stdout``/``stderr`` are bounded per stream (head + tail kept)."""

    rc: int
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool = False
    killed_reason: str = ""  # "" | idle | hard_timeout


class ActivityTracker:

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last = time.monotonic()

    def touch(self) -> None:
        with self._lock:
            self._last = time.monotonic()

    def idle_for(self) -> float:
        with self._lock:
            return time.monotonic() - self._last


def _latest_mtime(dirs: Iterable[Path]) -> float:
    """Newest mtime under ``dirs``: a directory is walked, a FILE is its own mtime."""
    latest = 0.0
    for d in dirs:
        if d.is_file():
            with contextlib.suppress(OSError):
                latest = max(latest, d.stat().st_mtime)
            continue
        if not d.is_dir():
            continue
        for root, subdirs, files in os.walk(d):
            subdirs[:] = [s for s in subdirs if s not in _SKIP_DIRS]
            for f in files:
                try:
                    latest = max(latest, os.stat(os.path.join(root, f)).st_mtime)
                except OSError:
                    continue
    return latest


def run_with_watchdog(
    cmd: Sequence[str],
    *,
    cwd: Path | str,
    env: Mapping[str, str] | None,
    soft_timeout_s: float,
    idle_grace_s: float = 300.0,
    hard_timeout_s: float | None = None,
    on_line: Callable[[str, str], None] | None = None,
    stdin: str | None = None,
    activity_dirs: Sequence[Path] | None = None,
    poll_s: float = 1.0,
    scan_s: float = 5.0,
) -> CompletedProc:
    """Run ``cmd`` streaming output; kill the process group on idle/hard timeout.

    ``on_line(stream, line)`` is called for every line.  ``activity_dirs`` (default ``cwd/src``) are
    polled for mtime changes that also count as activity.  Process lifecycle lives in
    :class:`codeverse3d.proc.ManagedProcess`; stdin is written from a helper thread.
    """
    if hard_timeout_s is None:
        hard_timeout_s = max(soft_timeout_s * 1.5, soft_timeout_s + 600.0)
    cwd = Path(cwd)
    dirs = list(activity_dirs) if activity_dirs is not None else [cwd / "src"]
    tracker = ActivityTracker()
    t0 = time.monotonic()
    _reject_control_chars(cmd, cwd)

    def observe(stream: str, line: str) -> None:
        tracker.touch()
        if on_line is not None:
            on_line(stream, line)  # ManagedProcess suppresses observer exceptions

    killed = ""
    with ManagedProcess(cmd, cwd=cwd, env=env, stdin_text=stdin, on_line=observe) as mp:
        last_mtime = _latest_mtime(dirs)
        next_scan = t0 + scan_s
        while mp.poll() is None:
            now = time.monotonic()
            elapsed = now - t0
            if now >= next_scan:
                m = _latest_mtime(dirs)
                if m > last_mtime:
                    last_mtime = m
                    tracker.touch()
                next_scan = now + scan_s
            if elapsed >= hard_timeout_s:
                killed = "hard_timeout"
            elif elapsed >= soft_timeout_s and tracker.idle_for() >= idle_grace_s:
                killed = "idle"
            if killed:
                mp.terminate()  # TERM → 5 s grace → KILL: the kill_process_group contract
                break
            time.sleep(poll_s)
    rc = mp.returncode if mp.returncode is not None else -9
    out_text, err_text = mp.stdout_text, mp.stderr_text
    return CompletedProc(
        rc=rc, stdout=out_text, stderr=err_text, duration_s=time.monotonic() - t0,
        timed_out=bool(killed), killed_reason=killed,
    )


def _reject_control_chars(cmd: Sequence[str], cwd: object) -> None:
    for i, arg in enumerate(cmd):
        text = str(arg)
        j = text.find("\x00")
        if j != -1:
            raise ValueError(
                f"argv[{i}] contains a NUL at offset {j} of {len(text)} — a model almost "
                f"certainly emitted \\u0000 in structured output and it was not sanitised "
                f"(cwd={cwd}).  Context: {text[max(0, j - 60):j + 40]!r}"
            )


# ===================================================================== cli_common
log = logging.getLogger(__name__)


def clean_env(keep: set[str] | frozenset[str] = frozenset()) -> dict[str, str]:
    """``os.environ`` minus secrets (``proc.scrub_secrets``) but for the ``keep`` names."""
    env = scrub_secrets(dict(os.environ)) | {k: v for k, v in os.environ.items() if k in keep}
    env["PYTHONUNBUFFERED"] = "1"
    return env


def hardened_env(ws: Workspace, job: AgentJob, *, keep: set[str] | None = None) -> dict[str, str]:
    env = clean_env(keep or set())
    env["GIT_CEILING_DIRECTORIES"] = str(ws.root.parent)
    env.update(job.env)
    return env


def default_mcp_command(ws: Workspace, *, language: str = "", track: str = "", round_index: int | None = None) -> list[str]:
    cmd = [sys.executable, "-m", "codeverse3d.spatial.mcp_server", "--workspace", str(ws.root)]
    if language:
        cmd += ["--language", language]
    if track:
        cmd += ["--track", track]
    if round_index is not None:
        cmd += ["--round", str(round_index)]
    return cmd


def mcp_command_for(ws: Workspace, job: AgentJob) -> list[str]:
    """The 3dcode MCP command for this job, built from the TYPED job.

    It never reads the workspace's ``.mcp.json``: the agent can rewrite it between rounds and
    choose what the next session launches.
    """
    return default_mcp_command(ws, language=job.language, track=job.track, round_index=job.round)


# --------------------------------------------------------------------------- session
#: paths the harness owns; never attributed to an agent session even when git sees them change
HARNESS_OWNED_DIRS = ("artifacts", "trajectories", "stages", "rounds", "_cand", "_assets", ".3dcode", ".gemini", ".claude", ".agents", ".git")
HARNESS_OWNED_FILES = frozenset({"events.jsonl", "run_state.json", "record.json", "AGENTS.md", "CLAUDE.md",
                                 ".mcp.json", ".antigravityignore", ".gitignore"})
#: gitignored control files git cannot revert; byte-snapshotted by :func:`begin_session`, restored by :func:`_enforce_scope`
UNTRACKED_CONTROL_FILES = ("run_state.json", "record.json")
#: the harness appends to the event stream MID-session — exempt from the tamper revert
_CONTROL_EXEMPT = frozenset({"events.jsonl"})


def _hinted(path: str, hints: frozenset[str]) -> bool:
    return any(path == h or path.startswith(h.rstrip("/") + "/") for h in hints)


def attribute_changes(files: list[FileChange], *, write_roots: list[str],
                      only: Iterable[str] | None = None) -> list[FileChange]:
    """The subset of a whole-worktree git diff that belongs to ONE session: inside its
    ``write_roots`` and not harness-owned.

    ``only`` (a ``shared_tree`` job's ``files_hint``): harness writers that take no session lock
    change the same tree meanwhile, so a path outside ``only`` is not the session's.
    """
    roots = tuple(r.strip("/") for r in write_roots if r.strip("/"))
    own = _clean(only) if only is not None else None
    out: list[FileChange] = []
    for f in files:
        parts = Path(f.path).parts
        if not parts or parts[0] in HARNESS_OWNED_DIRS or f.path in HARNESS_OWNED_FILES:
            continue
        if roots and not any(f.path == r or f.path.startswith(r + "/") for r in roots):
            continue
        if own is not None and not _hinted(f.path, own):
            continue
        out.append(f)
    return out


@dataclass
class Session:

    ws: Workspace
    job: AgentJob
    kind: str
    label: str
    round_index: int
    traj: Trajectory
    head_before: str
    t0: float = field(default_factory=time.monotonic)
    notes: list[str] = field(default_factory=list)
    attempt: int = 1
    files_hint: frozenset[str] = frozenset()
    ws_lock: threading.RLock | None = None
    control_snapshot: dict[str, bytes | None] = field(default_factory=dict)


# --------------------------------------------------------------------------- serialisation
#: agent kinds whose sessions are EXCLUSIVE per workspace (every kind): scope is checked post-session,
#: and a late session's ``pre:`` commit (``git add -A``) would absorb a sibling's in-flight writes.
EXCLUSIVE_KINDS = frozenset({"claude-code", "codex", "agy"})

_WS_SESSION_LOCKS: dict[str, threading.RLock] = {}
_WS_SESSION_LOCKS_GUARD = threading.Lock()


def _session_lock(ws: Workspace) -> threading.RLock:
    """One re-entrant lock per workspace root (so a thread that never finishes a session cannot deadlock itself)."""
    with _WS_SESSION_LOCKS_GUARD:
        return _WS_SESSION_LOCKS.setdefault(str(ws.root), threading.RLock())


@contextlib.contextmanager
def exclusive_workspace(ws: Workspace) -> Iterator[None]:
    """Hold ``ws``'s session lock for a HARNESS writer that commits while agent sessions may run.

    A harness commit mid-session (``git add -A``) would absorb the session's in-flight writes and
    break the ``head_before`` that :func:`_enforce_scope` restores to.
    """
    lock = _session_lock(ws)
    with lock:
        yield


def release_session(s: Session) -> None:
    lock, s.ws_lock = s.ws_lock, None
    if lock is not None:
        lock.release()


def _session_label(ws: Workspace, label: str, round_index: int) -> tuple[str, int]:
    """``label`` for attempt 1; ``<label>.a<n>`` when a finished attempt (result.json) already exists."""
    n, cand = 1, label
    while (ws.trajectory_dir(cand, round_index) / "result.json").exists():
        n += 1
        cand = f"{label}.a{n}"
    return cand, n


def begin_session(job: AgentJob, kind: str) -> Session:
    """Create the trajectory dir, write prompt.md and snapshot src/ (``pre:<label>``), taking the
    per-workspace lock first for :data:`EXCLUSIVE_KINDS`."""
    ws = Workspace(job.workspace)
    if not ws.root.is_dir():
        raise FileNotFoundError(f"workspace does not exist: {ws.root}")
    if not (ws.root / ".git").exists():
        ws.create()
    round_index = job.round
    label, attempt = _session_label(ws, job.label or kind, round_index)
    traj = Trajectory(ws.trajectory_dir(label, round_index))
    traj.write_prompt(job.prompt, job.system_append)
    hints = _clean(job.files_hint)
    lock = _session_lock(ws) if kind in EXCLUSIVE_KINDS else None
    if lock is not None:
        lock.acquire()
    try:
        head_before = ws.commit(f"pre:{label}")
    except BaseException:
        if lock is not None:
            lock.release()
        raise
    control = {n: ((ws.root / n).read_bytes() if (ws.root / n).is_file() else None)
               for n in UNTRACKED_CONTROL_FILES}
    return Session(ws=ws, job=job, kind=kind, label=label, round_index=round_index,
                   traj=traj, head_before=head_before, attempt=attempt, files_hint=hints,
                   ws_lock=lock, control_snapshot=control)


def _clean(seq: Any) -> frozenset[str]:
    return frozenset(p for p in (str(x).strip() for x in seq) if p)


def _enforce_scope(s: Session) -> dict[str, str]:
    """Revert a CLI session's out-of-scope writes; returns ``{path: reason}``.

    Layers: ``job.read_only`` and ``job.write_roots`` always; for ``edit_only``, the narrowing to
    ``files_hint`` + ``always_writable`` (new files stay allowed).  Relies on sessions on one
    workspace being serialised, so ``head_before`` holds no sibling's or harness's work.
    """
    roots = tuple(r.strip("/") for r in s.job.write_roots if r.strip("/"))
    frozen = _clean(s.job.read_only)
    narrow = (s.files_hint | _clean(s.job.always_writable)
              if (s.job.edit_only and s.files_hint) else frozenset())
    reverted: dict[str, str] = {}
    restore: list[str] = []
    remove: list[str] = []
    for f in s.ws.changed_files(s.head_before):
        parts = Path(f.path).parts
        if not parts or parts[0] in HARNESS_OWNED_DIRS or f.path in _CONTROL_EXEMPT:
            continue
        if f.path in HARNESS_OWNED_FILES:
            why = "harness control file: never agent-writable"
        elif is_harness_owned(f.path, frozen):
            why = "harness-owned: call its functions, never rewrite it"
        elif roots and not any(f.path == r or f.path.startswith(r + "/") for r in roots):
            why = f"outside write_roots {sorted(roots)}"
        elif not narrow or f.status == "added" or _hinted(f.path, narrow):
            continue
        else:
            why = "outside this task's files (new files stay allowed)"
        reverted[f.path] = why
        (remove if f.status == "added" else restore).append(f.path)
    s.ws.restore_paths(s.head_before, restore)
    for path in remove:  # added files are not in head_before; the next `git add -A` stages the delete
        (s.ws.root / path).unlink(missing_ok=True)
    for name, before in s.control_snapshot.items():  # gitignored control files: bytes are the truth
        p = s.ws.root / name
        now = p.read_bytes() if p.is_file() else None
        if now == before:
            continue
        reverted[name] = "harness control file: never agent-writable"
        if before is None:
            p.unlink(missing_ok=True)
        else:
            p.write_bytes(before)
    return reverted


def finish_session(
    s: Session,
    *,
    ok: bool,
    exit_reason: str,
    text: str,
    usage: Usage,
    tool_calls: int = 0,
    turns: int = 0,
    errors: list[str] | None = None,
    transient: bool = False,
    quota: bool = False,
    stalled: bool = False,
    provider_wait_s: float = 0.0,
    usage_estimated: bool = False,
    effort: str = "",
    **extra: Any,
) -> AgentResult:
    """Commit the agent's work, compute ``files_changed`` via git (attributed to this session),
    write result.json.  ``extra`` lands in result.json only.

    Writes outside ``write_roots`` (or, for ``edit_only``, ``files_hint``) are reverted to the
    session's ``pre:`` commit and the session is failed (:func:`_enforce_scope`).
    """
    try:
        errors = list(errors or [])
        reverted = _enforce_scope(s)
        if reverted:
            ok = False
            msg = ("out-of-scope writes reverted: "
                   + "; ".join(f"{p} ({why})" for p, why in sorted(reverted.items())))
            errors.append(msg)
            s.notes.append(msg)
        s.ws.commit(f"agent:{s.label}")
        files = attribute_changes(s.ws.changed_files(s.head_before), write_roots=s.job.write_roots,
                                  only=s.files_hint if s.job.shared_tree else None)
        duration = round(time.monotonic() - s.t0, 3)
        res = AgentResult(
            ok=ok, exit_reason=exit_reason, text=text, files_changed=files,
            transcript_path=str(s.traj.transcript_path if s.traj.transcript_path.exists() else s.traj.dir),
            usage=usage, duration_s=duration, provider_wait_s=round(min(max(provider_wait_s, 0.0), duration), 3),
            tool_calls=tool_calls, turns=turns, errors=errors, transient=transient and not quota, quota=quota,
            stalled=stalled and not files,
            usage_estimated=usage_estimated, effort=effort,
        )
        s.traj.write_result(res, kind=s.kind, label=s.label, round=s.round_index, attempt=s.attempt, job_label=s.job.label,
                            head_before=s.head_before, head_after=s.ws.head(), notes=s.notes, **extra)
        return res
    finally:
        release_session(s)


def failed(s: Session, reason: str, message: str, usage: Usage | None = None) -> AgentResult:
    return finish_session(s, ok=False, exit_reason=reason, text="", usage=usage or Usage(), errors=[message])


# --------------------------------------------------------------------------- invoke
#: seconds without output or workspace activity before the watchdog kills a CLI agent (read at call time)
IDLE_GRACE_S = 300.0
#: how far past its soft timeout a working session may run: the hard limit is ``timeout_s + HARD_GRACE_S``;
#: a CLI with a clock of its own must be set past it (agy ``--print-timeout``)
HARD_GRACE_S = 300.0

#: how often the watchdog re-reads the activity paths' mtimes (read at call time)
SCAN_S = 5.0

#: watchdog poll period (read at call time so tests can turn it down)
POLL_S = 1.0


def invoke(
    s: Session,
    argv: list[str],
    env: Mapping[str, str],
    *,
    prompt: str,
    stdout_name: str = "stdout.json",
    on_stdout: Callable[[str], None] | None = None,
    on_stderr: Callable[[str], None] | None = None,
    activity: Sequence[Path] = (),
    **invoke_extra: Any,
) -> CompletedProc:
    """Run one CLI agent process under the watchdog and record it in the trajectory.

    The prompt goes to the CLI on STDIN for every backend (never argv or a file the agent must
    read).  Output lines go into the transcript; ``src/`` + ``public/`` (a ``shared_tree`` job's own
    files) plus ``activity`` are the activity dirs.  ``stdout_name`` names the capture;
    ``on_stdout`` / ``on_stderr`` also receive each line; ``invoke_extra`` lands in the ``invoke`` row.
    """
    soft = float(s.job.timeout_s)
    hard = soft + HARD_GRACE_S
    if s.job.hard_deadline_s is not None:
        # runs AFTER begin_session's workspace lock: a queued job must not regain the window it had before waiting
        hard = min(hard, max(0.0, s.job.hard_deadline_s - time.monotonic()))
        soft = min(soft, hard)
    s.traj.append("invoke", argv=list(argv), stdin_bytes=len(prompt.encode("utf-8")),
                  soft_timeout_s=soft, hard_timeout_s=hard, started=hard > 0, **invoke_extra)

    def on_line(stream: str, line: str) -> None:
        s.traj.append("line", stream=stream, text=line[:4000])
        observer = on_stdout if stream == "stdout" else on_stderr
        if observer is not None:
            observer(line)

    if hard <= 0:
        proc = CompletedProc(rc=-9, stdout="", stderr="Run deadline expired before CLI launch.\n",
                             duration_s=0.0, timed_out=True, killed_reason="hard_timeout")
    else:
        proc = run_with_watchdog(
            argv, cwd=s.ws.root, env=env, soft_timeout_s=soft, hard_timeout_s=hard,
            idle_grace_s=IDLE_GRACE_S, poll_s=POLL_S, scan_s=SCAN_S,
            on_line=on_line, stdin=prompt, activity_dirs=activity_paths(s, activity),
        )
    s.traj.write_text(stdout_name, proc.stdout)
    s.traj.write_text("stderr.log", proc.stderr)
    return proc


def activity_paths(s: Session, steps: Sequence[Path] = ()) -> list[Path]:
    """What the watchdog polls as this session's activity: ``src/`` + ``public/``, or a
    ``shared_tree`` job's ``files_hint`` paths alone; plus ``steps``, where the CLI itself records
    each step (agy prints nothing until it exits, so a session that only reads would look idle).
    """
    own = [s.ws.root / h for h in sorted(s.files_hint)] if s.job.shared_tree else [s.ws.src, s.ws.public]
    return own + list(steps)


def watchdog_error(proc: CompletedProc) -> str:
    return f"killed by watchdog ({proc.killed_reason}) after {proc.duration_s:.0f}s"


# --------------------------------------------------------------------------- tool trace
#: The CLI's OWN record of what it called, normalised into ``transcript.jsonl`` by every backend:
#: one :data:`TOOL_CALL_ROW` per call, then ONE :data:`TOOL_TRACE_ROW` saying "this session's calls
#: are all here" (a session without it left no trace, which must not read as "called nothing").
#: Arguments are compacted to :data:`TRACE_ARG_CHARS` per value.
TOOL_CALL_ROW = "tool_call"
TOOL_TRACE_ROW = "tool_trace"
TRACE_ARG_CHARS = 400


@dataclass(frozen=True)
class ToolCall:
    """One tool call as the CLI reported it.  ``skill`` names the bundle when the call was the CLI's
    skill-activation tool; ``failed`` is the CLI's own verdict on the call."""

    tool: str
    args: Mapping[str, Any] = field(default_factory=dict)
    skill: str = ""
    failed: bool = False


def _compact(value: Any, limit: int = TRACE_ARG_CHARS) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _compact(v, limit) for k, v in value.items()}
    if value is None or isinstance(value, bool | int | float):
        return value
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + "…"


def record_tool_calls(s: Session, calls: Iterable[ToolCall], *, source: str,
                      skills_index: Sequence[str] | None = None) -> int:
    """Append one session's tool calls and the closing :data:`TOOL_TRACE_ROW`; returns the count.

    ``skills_index`` is the skill list the CLI reported as available, when it reports one.
    """
    n = 0
    for c in calls:
        row: dict[str, Any] = {"tool": c.tool, "args": _compact(dict(c.args))}
        if c.skill:
            row["skill"] = c.skill
        if c.failed:
            row["failed"] = True
        s.traj.append(TOOL_CALL_ROW, **row)
        n += 1
    extra: dict[str, Any] = {"skills_index": list(skills_index)} if skills_index is not None else {}
    s.traj.append(TOOL_TRACE_ROW, calls=n, source=source, **extra)
    return n


# --------------------------------------------------------------------------- failures
#: the vocabulary for what a CLI says about the call that ended its session.  Transient: a provider
#: failure a retry may get through (capacity, rate limits, dropped transport).  gRPC status names
#: match case-sensitively ("internal" is in every node stack trace).  A plain "timed out" is NOT
#: here: the watchdog's kill and a slow MCP tool say it too, and a retry buys the same outcome.
_TRANSIENT_RE = re.compile(
    r"(?-i:\b(?:UNAVAILABLE|INTERNAL|RESOURCE_EXHAUSTED|DEADLINE_EXCEEDED)\b)|\b(?:429|50[0234]|529)\b|overloaded"
    r"|rate.?limit|empty response|Invalid stream|fetch failed|ECONNRESET|ECONNREFUSED|ETIMEDOUT|EAI_AGAIN"
    r"|socket hang up|connection reset|stream disconnected",
    re.IGNORECASE,
)
#: Quota: the vendor's usage limit or credits are spent.  Not a rate limit: gemini's per-key
#: "quota exceeded" 429s are rotated past by the key pool, so those phrases are not here.
_QUOTA_RE = re.compile(
    r"usage limit|hit your limit|out of (?:extra )?usage|usage credits|credit balance (?:is )?too low"
    r"|purchase more credits|insufficient[_ ]quota|insufficient credits|billing hard limit",
    re.IGNORECASE,
)
_RATE_LIMIT_RE = re.compile(r"\b429\b|(?-i:\bRESOURCE_EXHAUSTED\b)|rate.?limit", re.IGNORECASE)


def is_quota_failure(*texts: str) -> bool:
    return any(_QUOTA_RE.search(t or "") for t in texts)


def is_transient_failure(*texts: str) -> bool:
    """The provider, not the task, ended the session and a retry may get through
    (``AgentResult.transient``); a spent usage limit never is."""
    return any(_TRANSIENT_RE.search(t or "") for t in texts) and not is_quota_failure(*texts)


def is_rate_limited(*texts: str) -> bool:
    return any(_RATE_LIMIT_RE.search(t or "") for t in texts)


@dataclass(frozen=True)
class SessionEnd:
    """How a CLI session ended: ``exit_reason`` (completed | timeout | budget | error) plus the typed
    failure class; ``rate_limited`` is a 429 (agy cools its key in the pool)."""

    exit_reason: str
    transient: bool = False
    quota: bool = False
    rate_limited: bool = False
    stalled: bool = False


def classify_end(proc: CompletedProc, *, ok: bool, text: str, said: Sequence[str] = (),
                 provider_failed: bool = False) -> SessionEnd:
    """The end rule for every CLI backend; each brings only its vendor's evidence: ``said`` (what the
    CLI printed about the call that ended it) and ``provider_failed`` (a typed vendor verdict).

    A watchdog kill is ``timeout``, never ``transient``; ``stalled`` when the watchdog found the
    session IDLE and it produced no text.  Any other failure is ``quota`` / ``transient`` by the
    vocabulary above, and ``budget`` when the usage limit is spent or the call was rate limited.
    """
    if ok:
        return SessionEnd("completed")
    if proc.timed_out:
        return SessionEnd("timeout", stalled=proc.killed_reason == "idle" and not text.strip())
    quota = is_quota_failure(*said)
    limited = is_rate_limited(*said)
    return SessionEnd("budget" if quota or limited else "error",
                      transient=not quota and (provider_failed or is_transient_failure(*said)),
                      quota=quota, rate_limited=limited)


# --------------------------------------------------------------------------- provider wait
def provider_wait(failures: Iterable[tuple[float, float]], end: float,
                  progress: Iterable[float] | None = None) -> float:
    """Seconds a session lost to provider errors, from the CLI's own record of them.

    ``failures`` are ``(t, backoff_s)``; ``progress`` are the times the session demonstrably moved
    on (``None`` when the CLI records none).  Failures with no progress between them form one
    burst, costing from the last progress before it until the CLI retried after it (or until
    ``end`` when nothing followed).  A lower bound wherever the back-off is unannounced.
    """
    fails = sorted(failures)
    prog = sorted(progress) if progress is not None else []
    total, i = 0.0, 0
    while i < len(fails):
        j = i
        while j + 1 < len(fails) and not any(fails[j][0] < p < fails[j + 1][0] for p in prog):
            j += 1
        first, (last, backoff) = fails[i][0], fails[j]
        start = max((p for p in prog if p <= first), default=first)
        resumed = progress is None or any(p > last for p in prog)
        total += max(0.0, (min(last + backoff, end) if resumed else end) - start)
        i = j + 1
    return round(total, 3)


def find_json_object(stdout: str, accept: Callable[[dict[str, Any]], bool]) -> dict[str, Any] | None:
    """The one JSON envelope a CLI printed, possibly among log noise; ``accept`` picks the dict."""
    s = stdout.strip()
    if not s:
        return None
    try:
        whole = json.loads(s)
    except json.JSONDecodeError:
        whole = None
    if isinstance(whole, list):
        hits = [e for e in whole if isinstance(e, dict) and accept(e)]
        return hits[-1] if hits else None
    if isinstance(whole, dict):
        return whole if accept(whole) else None
    for line in reversed(s.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            cand = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(cand, dict) and accept(cand):
            return cand
    return None


def exists_on_path(binary: str) -> bool:
    from shutil import which

    return bool(which(binary)) or Path(binary).is_file()

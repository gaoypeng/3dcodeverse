"""What every vendor-CLI backend (``agents/backends.py``) shares: the trajectory folder
(prompt.md, transcript.jsonl, stdout/stderr, result.json under
``ws.trajectory_dir(label, round)``, one layout whatever the backend), the watchdog'd
session process, the hardened environment, the session's write scope and locks, failure
classification, and the tool-call trace.  (``cli/`` is the harness's own ``3dcode`` CLI.)
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
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

#: per-session transcript caps.  Rows are byte-capped individually but nothing capped
#: the file: a child emitting 100k lines wrote 27 MB, ~406 MB at the per-row cap.  The
#: unabridged (bounded) stream still lands in stdout.json.
LINE_BUDGET_BYTES = 64 * 1024 * 1024
LINE_BUDGET_ROWS = 200_000


class Trajectory:
    """Append-only writer for one agent session's files."""

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
        """Write ``result.json`` = AgentResult fields + any extra diagnostics."""
        write_json_atomic(self.result_path, result.model_dump(mode="json") | extra)

    def read_transcript(self) -> list[dict[str, Any]]:
        return read_jsonl_lenient(self.transcript_path)


# ===================================================================== watchdog
_SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".gemini", ".gemini_home"}


@dataclass
class CompletedProc:
    """Outcome of :func:`run_with_watchdog`.

    ``stdout``/``stderr`` are bounded to ``codeverse3d.proc.STREAM_BUDGET_BYTES`` per
    stream (head + tail kept, truncation marker in between) — streaming consumers
    that must see every line unconditionally use ``on_line``.
    """

    rc: int
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool = False
    killed_reason: str = ""  # "" | idle | hard_timeout


class ActivityTracker:
    """Thread-safe 'last time something happened' clock."""

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
    latest = 0.0
    for d in dirs:
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

    ``on_line(stream, line)`` is called for every line (stream is ``"stdout"``
    or ``"stderr"``).  ``activity_dirs`` (default ``cwd/src``) are polled for
    mtime changes (every ``scan_s``) that also count as activity.

    Since 2026-08-27 the process lifecycle lives in
    :class:`codeverse3d.proc.ManagedProcess`; this function keeps only the clocks.
    Fixed by that move: a KeyboardInterrupt anywhere in the poll loop kills the
    group before propagating (it used to orphan the whole node → chrome tree —
    SIGINT never reaches a ``start_new_session`` child); stdin is written from a
    helper thread, so a child that never reads a >64 KiB prompt (every agent
    CLI gets its prompt on stdin) can no longer wedge the main thread before the
    clocks start; captured output is bounded per stream (head+tail + marker).
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
    """Fail with an argv position and an excerpt instead of a bare "embedded null byte".

    ``subprocess.Popen`` raises ``ValueError: embedded null byte`` naming nothing at all —
    not the argument, not the offset, not the value.  On 2026-08-24 that cost an hour to
    trace back to five ``\\u0000`` escapes a planner had written into plan.json four stages
    earlier (see ``schema_utils.strip_control_chars``, which is the actual cure).  This is
    the backstop: if one ever leaks again, the error says where.
    """
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
    """``os.environ`` minus secrets (the one owner of the patterns: ``proc.scrub_secrets``) but
    for the ``keep`` names (a CLI's own auth var), plus the recursion guard — a vendor CLI's env."""
    env = scrub_secrets(dict(os.environ)) | {k: v for k, v in os.environ.items() if k in keep}
    env["C3D_AGENT_CONTEXT"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def hardened_env(ws: Workspace, job: AgentJob, *, keep: set[str] | None = None) -> dict[str, str]:
    """:func:`clean_env` plus the git ceiling and ``job.env``."""
    env = clean_env(keep or set())
    env["GIT_CEILING_DIRECTORIES"] = str(ws.root.parent)
    env.update(job.env)
    return env


def default_mcp_command(ws: Workspace, *, language: str = "", track: str = "", round_index: int | None = None) -> list[str]:
    """The stdio MCP server command exposing the spatial tool registry for ``ws``."""
    cmd = [sys.executable, "-m", "codeverse3d.spatial.mcp_server", "--workspace", str(ws.root)]
    if language:
        cmd += ["--language", language]
    if track:
        cmd += ["--track", track]
    if round_index is not None:
        cmd += ["--round", str(round_index)]
    return cmd


def mcp_command_for(ws: Workspace, job: AgentJob) -> list[str]:
    """The 3dcode MCP command for this job, from the TYPED job: ``job.mcp_command`` > default.

    It deliberately does NOT read the workspace's ``.mcp.json``.  That file lives where
    the agent works and every CLI backend runs unsandboxed inside it, so an agent that
    rewrote it in round 0 chose what executable the NEXT round's codex/claude launched —
    with provider credentials in the environment, and (for codex) outside the
    ``--sandbox workspace-write`` its own shell obeys.  The command is rebuilt from the
    job every session instead; nothing on disk can redirect it (audit 2026-08-27)."""
    if job.mcp_command:
        return list(job.mcp_command)
    return default_mcp_command(ws, language=job.language, track=job.track, round_index=job.round)


# --------------------------------------------------------------------------- session
#: paths the harness owns; never attributed to an agent session even when git sees them change
#: (`.3dcv` = `.3dcode` in a workspace made before the 2026-09-21 rename, still resumable)
HARNESS_OWNED_DIRS = ("artifacts", "trajectories", "stages", "rounds", "_cand", "_assets", ".3dcode", ".3dcv", ".gemini", ".claude", ".agents", ".git")
HARNESS_OWNED_FILES = frozenset({"events.jsonl", "run_state.json", "record.json", "AGENTS.md", "GEMINI.md", "CLAUDE.md",
                                 ".mcp.json", ".geminiignore", ".aiexclude", ".gitignore"})
#: gitignored control files git cannot revert (downstream trusts both blindly):
#: byte-snapshotted by :func:`begin_session`, compared + restored by :func:`_enforce_scope`
UNTRACKED_CONTROL_FILES = ("run_state.json", "record.json")
#: the harness appends to the event stream MID-session — exempt from the tamper revert
_CONTROL_EXEMPT = frozenset({"events.jsonl"})


def _hinted(path: str, hints: frozenset[str]) -> bool:
    return any(path == h or path.startswith(h.rstrip("/") + "/") for h in hints)


def attribute_changes(files: list[FileChange], *, write_roots: list[str]) -> list[FileChange]:
    """The subset of a whole-worktree git diff that belongs to ONE session: inside its
    ``write_roots`` and not harness-owned.  (Sessions on one workspace are serialised —
    :data:`EXCLUSIVE_KINDS` — so there is no sibling to attribute against.  An ``own_hints``
    parameter sat in this signature unread until 2026-08-30: the edit_only scope is enforced
    by :func:`_enforce_scope`, which REVERTS an out-of-scope write rather than hiding it from
    ``files_changed``, so narrowing here would only have lied about what the session did.)"""
    roots = tuple(r.strip("/") for r in write_roots if r.strip("/"))
    out: list[FileChange] = []
    for f in files:
        parts = Path(f.path).parts
        if not parts or parts[0] in HARNESS_OWNED_DIRS or f.path in HARNESS_OWNED_FILES:
            continue
        if roots and not any(f.path == r or f.path.startswith(r + "/") for r in roots):
            continue
        out.append(f)
    return out


@dataclass
class Session:
    """State for one CLI agent run (created by :func:`begin_session`)."""

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
    #: bytes of :data:`UNTRACKED_CONTROL_FILES` at session start (None = absent)
    control_snapshot: dict[str, bytes | None] = field(default_factory=dict)


# --------------------------------------------------------------------------- serialisation
#: agent kinds whose sessions are EXCLUSIVE per workspace — today, every kind.  No
#: backend has write-time enforcement (scope is checked post-session by
#: :func:`_enforce_scope`), and both session snapshots run ``git add -A`` — a
#: late-starting session's ``pre:`` commit absorbs a sibling's in-flight writes, which
#: makes concurrent provenance (and any per-path rollback) unfixable after the fact.
#: So agent sessions on one workspace are serialised.
EXCLUSIVE_KINDS = frozenset({"claude-code", "codex", "gemini-cli", "agy"})

_WS_SESSION_LOCKS: dict[str, threading.RLock] = {}
_WS_SESSION_LOCKS_GUARD = threading.Lock()


def _session_lock(ws: Workspace) -> threading.RLock:
    """One re-entrant lock per workspace root.  Re-entrant so a thread that begins a
    session it never finishes (argv-building tests do) cannot deadlock itself; real
    sessions begin, finish and release on their own worker thread."""
    with _WS_SESSION_LOCKS_GUARD:
        return _WS_SESSION_LOCKS.setdefault(str(ws.root), threading.RLock())


def release_session(s: Session) -> None:
    """Release ``s``'s workspace lock (idempotent).  ``finish_session`` always calls it;
    the CLI backends also call it in a ``finally`` so a session that crashes between
    begin and finish cannot starve every later session on the same workspace."""
    lock, s.ws_lock = s.ws_lock, None
    if lock is not None:
        lock.release()


def _session_label(ws: Workspace, label: str, round_index: int) -> tuple[str, int]:
    """``label`` for attempt 1; ``<label>.a<n>`` when ``trajectories/<label>_rNN`` already
    holds a finished attempt (result.json) — a silent-bail retry must not overwrite it."""
    n, cand = 1, label
    while (ws.trajectory_dir(cand, round_index) / "result.json").exists():
        n += 1
        cand = f"{label}.a{n}"
    return cand, n


def begin_session(job: AgentJob, kind: str) -> Session:
    """Create the trajectory dir, write prompt.md and snapshot src/ (``pre:<label>``).

    For :data:`EXCLUSIVE_KINDS` the per-workspace session lock is taken BEFORE the
    ``pre:`` commit and held until ``finish_session`` releases it — see the constant's
    comment for why concurrent CLI sessions in one workspace are unfixable."""
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
    """Normalise a path list: str(), strip, drop empties."""
    return frozenset(p for p in (str(x).strip() for x in seq) if p)


def _enforce_scope(s: Session) -> dict[str, str]:
    """Revert a CLI session's out-of-scope writes; returns the reverted paths.

    Post-hoc, because every backend is a vendor CLI and none has a write-time file gate
    (the in-process one that did was deleted 2026-08-28).  Three layers:
    ``job.read_only`` ALWAYS — harness-owned source the agent must call, never rewrite
    (``src/recipes.glsl``); ``job.write_roots`` ALWAYS — a CLI session used to be able
    to write and commit a root-level ``conftest.py``; then, for ``edit_only``, the
    narrowing to ``files_hint`` + ``always_writable`` (a pre-existing file outside it is
    out of scope; new files stay allowed).  Only safe because sessions on one workspace
    are serialised (:data:`EXCLUSIVE_KINDS`): ``head_before`` holds no sibling's
    in-flight work."""
    roots = tuple(r.strip("/") for r in s.job.write_roots if r.strip("/"))
    frozen = _clean(s.job.read_only)
    narrow = (s.files_hint | _clean(s.job.always_writable)
              if (s.job.edit_only and s.files_hint) else frozenset())
    reverted: dict[str, str] = {}
    restore: list[str] = []
    remove: list[str] = []
    # no empty-scope early-out: control files are enforced under ANY job scope
    for f in s.ws.changed_files(s.head_before):
        parts = Path(f.path).parts
        if not parts or parts[0] in HARNESS_OWNED_DIRS or f.path in _CONTROL_EXEMPT:
            continue
        if f.path in HARNESS_OWNED_FILES:  # was a silent skip: the tamper survived AND went unreported
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
    provider_wait_s: float = 0.0,
    **extra: Any,
) -> AgentResult:
    """Commit the agent's work, compute ``files_changed`` via git (attributed to this
    session — see :func:`attribute_changes`), write result.json.  ``turns`` is the
    backend's own count (``AgentResult.turns``); ``transient`` / ``quota`` its typed
    failure class and ``provider_wait_s`` the time its CLI lost to provider errors
    (:func:`provider_wait`); ``extra`` lands in result.json only.

    Write scope is enforced here for the CLI backends (they have no write-time gate):
    writes outside ``write_roots`` (and, for ``edit_only``, outside ``files_hint``) are
    reverted to this session's own ``pre:`` commit and the session is failed
    (:func:`_enforce_scope`)."""
    try:
        errors = list(errors or [])
        reverted = _enforce_scope(s)
        if reverted:
            ok = False
            # each path carries ITS reason: a read_only revert used to be blamed on
            # write_roots ("may only write under ['src']") for a file under src/
            msg = ("out-of-scope writes reverted: "
                   + "; ".join(f"{p} ({why})" for p, why in sorted(reverted.items())))
            errors.append(msg)
            s.notes.append(msg)
        s.ws.commit(f"agent:{s.label}")
        files = attribute_changes(s.ws.changed_files(s.head_before), write_roots=s.job.write_roots)
        duration = round(time.monotonic() - s.t0, 3)
        res = AgentResult(
            ok=ok, exit_reason=exit_reason, text=text, files_changed=files,
            transcript_path=str(s.traj.transcript_path if s.traj.transcript_path.exists() else s.traj.dir),
            usage=usage, duration_s=duration, provider_wait_s=round(min(max(provider_wait_s, 0.0), duration), 3),
            tool_calls=tool_calls, turns=turns, errors=errors, transient=transient and not quota, quota=quota,
        )
        s.traj.write_result(res, kind=s.kind, label=s.label, round=s.round_index, attempt=s.attempt, job_label=s.job.label,
                            head_before=s.head_before, head_after=s.ws.head(), notes=s.notes, **extra)
        return res
    finally:
        release_session(s)


def failed(s: Session, reason: str, message: str, usage: Usage | None = None) -> AgentResult:
    """Shortcut for an ``ok=False`` result with one error line."""
    return finish_session(s, ok=False, exit_reason=reason, text="", usage=usage or Usage(), errors=[message])


# --------------------------------------------------------------------------- invoke
#: seconds without output or workspace activity before the watchdog kills a CLI agent;
#: read at call time by :func:`invoke` so a test can monkeypatch it here.
IDLE_GRACE_S = 300.0

#: watchdog poll period.  A real vendor CLI runs for minutes, so 1 s of latency on
#: noticing it exited is free — but a FAKE cli in a test exits in milliseconds and then
#: waits out the poll, which was ~1 s on every agent.run() in the suite.  Same shape as
#: IDLE_GRACE_S: read at call time so tests can turn it down (tests/agents/conftest.py).
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
    attempt: int = 1,
    soft_timeout_s: float | None = None,
    **invoke_extra: Any,
) -> CompletedProc:
    """Run one CLI agent process under the watchdog and record it in the trajectory.

    The prompt goes to the CLI on STDIN, always, for every backend — never on argv and never
    through a file the agent must read.  argv caps one argument at 128 KiB (``MAX_ARG_STRLEN``),
    and the file stub this replaced (``task_prompt.md`` above 100 kB) handed the prompt to the
    agent's own file tool: gemini-cli's ``read_file`` stops at 2 000 lines, so the last zones of a
    5-zone scene prompt were silently dropped.  Each CLI reads its whole stdin as the prompt,
    byte for byte (checked 2026-09-22 against gemini-cli 0.53, claude-code 2.1.280, codex
    0.155.1 and agy 1.2.8 with a 185 000-character, 2 502-line prompt; agy alone truncates a
    request past ~175 kB, whatever carries it).

    One block every backend shares: the ``invoke`` transcript line, ``run_with_watchdog``
    (workspace cwd, the job's soft timeout, ``IDLE_GRACE_S``, every output line into the
    transcript, ``src/`` + ``public/`` as activity dirs) and the stdout/stderr captures next to
    the transcript.  ``stdout_name`` sets the capture's name (codex streams JSONL →
    ``stdout.jsonl``); ``on_stdout`` / ``on_stderr`` also receive each line as it arrives (the
    backends fold their event streams and retry reports live); ``attempt`` > 1 writes
    ``stdout.<n>.json`` / ``stderr.<n>.log`` so a retry keeps attempt 1's captures.  The
    ``invoke`` line records ``attempt`` and the prompt's size, plus ``invoke_extra`` (gemini-cli
    adds ``model`` and ``key_tail``).
    """
    soft = float(s.job.timeout_s if soft_timeout_s is None else soft_timeout_s)
    hard = soft + 300.0
    if s.job.hard_deadline_s is not None:
        # This runs AFTER begin_session's workspace lock and on every vendor retry.
        # A queued job must not regain the window it had before waiting for a peer.
        hard = min(hard, max(0.0, s.job.hard_deadline_s - time.monotonic()))
        soft = min(soft, hard)
    s.traj.append("invoke", argv=list(argv), attempt=attempt, stdin_bytes=len(prompt.encode("utf-8")),
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
            idle_grace_s=IDLE_GRACE_S, poll_s=POLL_S,
            on_line=on_line, stdin=prompt, activity_dirs=[s.ws.src, s.ws.public],
        )
    suffix = "" if attempt == 1 else f".{attempt}"
    out = Path(stdout_name)
    s.traj.write_text(f"{out.stem}{suffix}{out.suffix}", proc.stdout)
    s.traj.write_text(f"stderr{suffix}.log", proc.stderr)
    return proc


def watchdog_error(proc: CompletedProc) -> str:
    """The error line every CLI backend records when the watchdog killed its process."""
    return f"killed by watchdog ({proc.killed_reason}) after {proc.duration_s:.0f}s"


# --------------------------------------------------------------------------- tool trace
#: The CLI's OWN record of what it called, normalised into ``transcript.jsonl`` by every
#: backend after its session: one :data:`TOOL_CALL_ROW` per call, then ONE
#: :data:`TOOL_TRACE_ROW` saying "this session's calls are all here" — a session without
#: it left no trace, and its silence must not read as "called nothing".  The skill read
#: probe (``skills/telemetry.py``) reads these rows instead of file atimes, which its own
#: control bundle caught "reading" in 27 of 33 sessions (docs/SKILLS.md §9).  Arguments are
#: compacted to :data:`TRACE_ARG_CHARS` per value: a path survives, a file body does not.
TOOL_CALL_ROW = "tool_call"
TOOL_TRACE_ROW = "tool_trace"
TRACE_ARG_CHARS = 400


@dataclass(frozen=True)
class ToolCall:
    """One tool call as the CLI reported it.  ``skill`` names the bundle when the call was
    the CLI's own skill-activation tool (claude-code ``Skill``, gemini-cli ``activate_skill``),
    so a reader of the trace never needs any vendor's tool vocabulary; ``failed`` is the
    CLI's own verdict on the call (a ``sed`` of a mis-expanded skill path read nothing —
    codex 0.155.1 did exactly that on its first try, 2026-09-22)."""

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

    ``source`` says where the backend read them (its event stream, its chat record);
    ``skills_index`` is the skill list the CLI itself reported as available, when it
    reports one (claude-code's ``init`` event) — the proof the index reached the model."""
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
#: THE vocabulary for what a CLI says about the call that ended its session — one place for
#: every backend.  The backends turn it into typed flags (``AgentResult.transient`` / ``.quota``)
#: that ride ``GenerationResult`` into ``RoundFailed``; the round loop never reads a message.
#: Transient: a provider failure a retry may get through — capacity (5xx, UNAVAILABLE,
#: overloaded), rate limits (429, RESOURCE_EXHAUSTED) and the transport (a dropped, refused or
#: reset connection, an empty or broken stream).  The gRPC status names match case-sensitively:
#: lower-case "internal" is in every node stack trace (``node:internal/process/...``), which made
#: any crashed CLI look transient.  A plain "timed out" is NOT here: the watchdog's own kill, a
#: slow MCP tool and a provider timeout all say it, and a retry of the first two buys another
#: full window for the same outcome (provider timeouts arrive typed: gemini-cli's retry lines,
#: ``ModelError(status=408, retryable=True)``).
_TRANSIENT_RE = re.compile(
    r"(?-i:\b(?:UNAVAILABLE|INTERNAL|RESOURCE_EXHAUSTED|DEADLINE_EXCEEDED)\b)|\b(?:429|50[0234]|529)\b|overloaded"
    r"|rate.?limit|empty response|Invalid stream|fetch failed|ECONNRESET|ECONNREFUSED|ETIMEDOUT|EAI_AGAIN"
    r"|socket hang up|connection reset|stream disconnected",
    re.IGNORECASE,
)
#: Quota: the vendor's usage limit or credits are spent and nothing gets through until they reset
#: (codex "You've hit your usage limit", claude "usage limit reached" / "You've hit your limit" /
#: "out of usage credits", OpenAI ``insufficient_quota``).  Not a rate limit: gemini says "Quota
#: exceeded ... per minute" and "You exceeded your current quota" of a per-key 429 the key pool
#: rotates past, so neither phrase is here.
_QUOTA_RE = re.compile(
    r"usage limit|hit your limit|out of (?:extra )?usage|usage credits|credit balance (?:is )?too low"
    r"|purchase more credits|insufficient[_ ]quota|insufficient credits|billing hard limit",
    re.IGNORECASE,
)
_RATE_LIMIT_RE = re.compile(r"\b429\b|(?-i:\bRESOURCE_EXHAUSTED\b)|rate.?limit", re.IGNORECASE)


def is_quota_failure(*texts: str) -> bool:
    """The vendor's usage limit is spent (``AgentResult.quota``): stop, do not retry."""
    return any(_QUOTA_RE.search(t or "") for t in texts)


def is_transient_failure(*texts: str) -> bool:
    """The provider, not the task, ended the session and a retry may get through
    (``AgentResult.transient``); a spent usage limit never is."""
    return any(_TRANSIENT_RE.search(t or "") for t in texts) and not is_quota_failure(*texts)


def is_rate_limited(*texts: str) -> bool:
    """A 429: transient, and the key that got it should cool down (``exit_reason="budget"``)."""
    return any(_RATE_LIMIT_RE.search(t or "") for t in texts)


# --------------------------------------------------------------------------- provider wait
def provider_wait(failures: Iterable[tuple[float, float]], end: float,
                  progress: Iterable[float] | None = None) -> float:
    """Seconds a session lost to provider errors, from the CLI's own record of them.

    ``failures`` are ``(t, backoff_s)``: when the CLI reported a failed API attempt, and the
    back-off it announced before the next one (0 when it does not say — gemini-cli's 5xx
    lines).  ``progress`` are the times the session demonstrably moved on (a model message, a
    tool result); ``None`` when the CLI records none (agy's log).  Failures with no progress
    between them form one burst, which costs from the last progress before it — when the
    request that failed went out; its first failure when nothing precedes it — until the
    CLI tried again after the burst's last failure (``t + backoff``).  A burst no progress
    ever followed costs until ``end``: the CLI gave up, or the watchdog killed it mid-storm.
    A lower bound wherever the back-off is unannounced (≤ 30 s per burst for gemini-cli)."""
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
    """The one JSON envelope a CLI printed, possibly around log noise.

    Tries the whole text; unwraps a top-level list (claude's stream-json array) to
    its LAST accepted element; then ``{``-starting lines from the END (claude / agy
    print one envelope per line after their chatter); then ``json.loads(s[start:])``
    forward from every ``{`` — mandatory, not a fallback: ``gemini --output-format
    json`` prints an INDENTED multi-line object that no single-line scan can parse.
    ``accept`` says which dict is the envelope."""
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
    start = s.find("{")
    while start != -1:
        try:
            obj = json.loads(s[start:])
        except json.JSONDecodeError:
            start = s.find("{", start + 1)
            continue
        return obj if isinstance(obj, dict) and accept(obj) else None
    return None


def exists_on_path(binary: str) -> bool:
    from shutil import which

    return bool(which(binary)) or Path(binary).is_file()

"""Read-only git helpers for the flywheel: file trees at a commit, binary-safe.

The exporter / pairs builder must never touch the working tree of a run
(another process may be resuming it), so everything here reads the object database
of the run's repository (``ls-tree`` / ``cat-file`` / ``diff``).

Read-only is not the same as safe: a run's ``.git/config`` and ``.gitattributes``
were writable by the agent, and git config can name a program to RUN.  Every
invocation therefore goes through the same sanitised argv + environment the
workspace's own git uses (:data:`~codeverse.workspace.GIT_SAFE_FLAGS`,
:func:`~codeverse.workspace.git_safe_env`), and every content-rendering command
adds :data:`~codeverse.workspace.GIT_SAFE_DIFF_FLAGS`.
"""

from __future__ import annotations

import subprocess
from collections.abc import Collection
from pathlib import Path

from codeverse.workspace import GIT_SAFE_DIFF_FLAGS, GIT_SAFE_FLAGS, Workspace, git_safe_env

_GIT_TIMEOUT_S = 60

#: directories of a workspace that hold agent-authored code
CODE_ROOTS: tuple[str, ...] = ("src", "public")
_SKIP_PARTS = {"node_modules", "__pycache__", ".git"}


class GitReadError(RuntimeError):
    """A git read (ls-tree / cat-file / diff) failed or the commit does not exist."""


def _run(ws: Workspace, *args: str, stdin: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
    """One sanitised git invocation; ``stdin`` feeds ``cat-file --batch`` its object list.

    Every way the call can fail — no git binary, a timeout, a non-zero exit — is one
    :class:`GitReadError`, so a caller catches one thing."""
    try:
        proc = subprocess.run(
            ["git", *GIT_SAFE_FLAGS, *args], cwd=ws.root, capture_output=True, check=False,
            env=git_safe_env(), timeout=_GIT_TIMEOUT_S, input=stdin,
        )
    except (OSError, subprocess.SubprocessError) as e:
        raise GitReadError(f"git {' '.join(args)} in {ws.root}: {e}") from e
    if proc.returncode != 0:
        raise GitReadError(f"git {' '.join(args)} failed in {ws.root}: "
                           f"{proc.stderr.decode('utf-8', errors='replace').strip()[:400]}")
    return proc


def commit_by_subject(ws: Workspace, subject: str) -> str | None:
    """SHA of the newest commit whose subject line equals ``subject`` (None = no match)."""
    try:
        out = _run(ws, "log", "--format=%H%x00%s").stdout
    except GitReadError:
        return None
    for line in out.decode("utf-8", errors="replace").splitlines():
        sha, _, subj = line.partition("\0")
        if subj.strip() == subject:
            return sha
    return None


def commit_exists(ws: Workspace, commit: str) -> bool:
    if not commit:
        return False
    try:
        _run(ws, "cat-file", "-e", f"{commit}^{{commit}}")
        return True
    except GitReadError:
        return False


def _keep(rel: str) -> bool:
    parts = Path(rel).parts
    return bool(parts) and parts[0] in CODE_ROOTS and not any(p in _SKIP_PARTS for p in parts)


def read_tree_at(ws: Workspace, commit: str, *, paths: Collection[str] | None = None) -> dict[str, bytes]:
    """``{repo-relative path: bytes}`` for every code file at ``commit`` (binary-safe);
    ``paths`` narrows it to those files, so a caller after two of forty streams two.

    ``ls-tree`` + ``cat-file --batch``, never ``git archive``: archive renders content
    through ``convert_to_working_tree``, so an agent-planted ``filter.<name>.smudge``
    RUNS — and unlike textconv there is no flag to turn it off (``-c
    core.attributesFile=/dev/null`` only silences the GLOBAL attributes file; the
    in-repo ``.gitattributes`` and ``.git/info/attributes`` are still read).
    ``cat-file`` hands back the raw blob and applies no filter unless asked."""
    if not commit_exists(ws, commit):
        raise GitReadError(f"commit {commit!r} not found in {ws.root}")
    entries: list[tuple[str, str]] = []  # (blob sha, path)
    out = _run(ws, "ls-tree", "-r", "-z", commit, "--", *CODE_ROOTS).stdout
    for rec in out.decode("utf-8", errors="replace").split("\0"):
        if not rec:
            continue
        meta, _, path = rec.partition("\t")
        parts = meta.split()
        # mode 120000 is a SYMLINK, stored as a blob whose content is the link target:
        # `git archive` filtered it (tar member, not a file) and this path must too, or
        # `src/link.py -> model.py` comes back as a one-line file saying "model.py".
        if len(parts) < 3 or parts[1] != "blob" or parts[0] == "120000" or not _keep(path):
            continue
        if paths is not None and path not in paths:
            continue
        entries.append((parts[2], path))
    if not entries:
        return {}
    proc = _run(ws, "cat-file", "--batch", stdin=b"".join(f"{sha}\n".encode() for sha, _ in entries))
    files: dict[str, bytes] = {}
    buf, pos = proc.stdout, 0
    for _, path in entries:
        nl = buf.find(b"\n", pos)
        if nl < 0:
            raise GitReadError(f"cat-file --batch ended early reading {path!r} from {ws.root}")
        header = buf[pos:nl].decode("utf-8", errors="replace").split()
        if len(header) != 3:
            raise GitReadError(f"cat-file --batch: unreadable object for {path!r}: {' '.join(header)}")
        size = int(header[2])
        files[path] = buf[nl + 1:nl + 1 + size]
        pos = nl + 1 + size + 1  # the trailing newline git writes after every object
    return files


def diff_between(ws: Workspace, before: str, after: str, *, max_bytes: int | None = None) -> tuple[str, int, bool]:
    """Unified diff of the code roots between two commits: ``(text, total_bytes, truncated)``.

    ``total_bytes`` is what git actually produced, so a capped row still records the
    size it was capped from.  The useful shas are the ones recorded on the rounds,
    never ``HEAD`` — a finished run ends on a "restore best round rNN" commit — and a
    sha the repository no longer holds raises rather than diffing against an empty
    tree: ``git diff`` itself refuses an unknown object, so no pre-check is needed.
    """
    raw = _run(ws, "diff", *GIT_SAFE_DIFF_FLAGS, before, after, "--", *CODE_ROOTS).stdout
    total = len(raw)
    if max_bytes is not None and total > max_bytes:
        head = raw[:max_bytes].decode("utf-8", errors="replace")
        return f"{head}\n... [truncated {total - max_bytes} bytes]\n", total, True
    return raw.decode("utf-8", errors="replace"), total, False


def changed_files_between(ws: Workspace, before: str, after: str) -> list[str]:
    """Sorted code-root paths that differ between two commits."""
    out = _run(ws, "diff", *GIT_SAFE_DIFF_FLAGS, "--name-only", "-z", before, after, "--", *CODE_ROOTS).stdout
    return sorted(p for p in out.decode("utf-8", errors="replace").split("\0") if p and _keep(p))


def read_working_tree(ws: Workspace) -> dict[str, bytes]:
    """Fallback when a round has no commit: the current ``src/`` + ``public/``."""
    files: dict[str, bytes] = {}
    for root in CODE_ROOTS:
        base = ws.root / root
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if p.is_file():
                rel = p.relative_to(ws.root).as_posix()
                if _keep(rel):
                    files[rel] = p.read_bytes()
    return files


def decode_text_files(files: dict[str, bytes], *, max_total: int | None = None) -> tuple[dict[str, str], list[str]]:
    """UTF-8 decode text files; binary files are listed in the second return value.

    ``max_total`` caps the summed decoded length (files are added in sorted path
    order; the first file that does not fit is truncated with a marker and the
    rest are skipped into the ``skipped`` list).
    """
    text: dict[str, str] = {}
    skipped: list[str] = []
    total = 0
    for path in sorted(files):
        raw = files[path]
        if b"\0" in raw[:8000]:
            skipped.append(path)
            continue
        try:
            s = raw.decode("utf-8")
        except UnicodeDecodeError:
            skipped.append(path)
            continue
        if max_total is not None and total + len(s) > max_total:
            room = max_total - total
            if room > 200:
                text[path] = s[:room] + f"\n... [truncated {len(s) - room} chars]"
                total = max_total
            else:
                skipped.append(path)
            continue
        text[path] = s
        total += len(s)
    return text, skipped

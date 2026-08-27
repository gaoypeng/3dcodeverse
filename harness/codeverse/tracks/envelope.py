"""Single-shot envelope: parse the ``=== FILE: ... ===`` format and write files.

Split out of ``tracks/generation.py`` (kept re-exported there) so both modules
stay under the ~400-line law.  ``MultiFileParseError``/``GenerationError`` are
defined here; ``generation`` re-exports them for its callers.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Collection
from pathlib import Path

from codeverse.contracts.agent import FileChange
from codeverse.workspace import Workspace

log = logging.getLogger(__name__)

ALLOWED_ROOTS: tuple[str, ...] = ("src/", "public/")

SINGLE_SHOT_FORMAT = """OUTPUT FORMAT (exactly this, nothing else around it):
For EVERY file you create or fully rewrite, emit one block:

=== FILE: <relative path, e.g. src/parts/seat.js> ===
<complete file contents — the whole file, not a diff>
=== END FILE ===

Rules: paths are relative to the workspace root and must start with src/ (or public/);
emit the COMPLETE contents of each file (no "...rest unchanged"); no prose outside the blocks;
no markdown fences inside a block (plain code).  Files you do not emit are left untouched."""


class GenerationError(RuntimeError):
    """Raised when the generator cannot produce usable files (bad envelope, agent crash)."""


class MultiFileParseError(GenerationError):
    """The single-shot answer did not contain a parseable file envelope."""


_BLOCK = re.compile(
    r"^[ \t]*===\s*FILE:\s*(?P<path>[^\n=]+?)\s*===[ \t]*\r?\n(?P<body>.*?)(?:\r?\n)?^[ \t]*===\s*END FILE\s*===[ \t]*$",
    re.S | re.M,
)
_FENCE = re.compile(r"```[a-zA-Z0-9_+\-]*[ \t]*(?:\r?\n)(?P<body>.*?)```", re.S)
_FENCE_PATH_HINT = re.compile(
    r"(?:^|\n)[^\n]*?(?P<path>(?:src|public)/[A-Za-z0-9_./\-]+\.[a-z]{1,5})[^\n]*\n[ \t]*```", re.S
)


def parse_multifile(text: str, *, expected_files: list[str] | None = None) -> dict[str, str]:
    """Parse ``SINGLE_SHOT_FORMAT`` → ``{path: content}``.

    Tolerant to: a fenced block wrapping a file body; an answer that is ONE fenced
    block when exactly one entry file is expected; fenced blocks preceded by a line
    naming the path.  Raises ``MultiFileParseError`` otherwise.
    """
    files: dict[str, str] = {}
    for m in _BLOCK.finditer(text):
        path = _clean_path(m.group("path"))
        files[path] = _strip_fence(m.group("body"))
    if files:
        return files
    fences = list(_FENCE.finditer(text))
    expected = [_clean_path(p) for p in (expected_files or [])]
    if fences:
        # fenced blocks preceded by a path mention
        for m in fences:
            probe = text[: m.start()][-400:].rstrip(" \t")
            probe += ("" if probe.endswith("\n") else "\n") + "```"
            hint = _FENCE_PATH_HINT.findall(probe)
            if hint:
                files[_clean_path(hint[-1])] = m.group("body").rstrip("\n")
        if files and (not expected or set(files) <= set(expected) or len(files) == len(fences)):
            return files
        if len(expected) == 1:
            largest = max(fences, key=lambda m: len(m.group("body")))
            return {expected[0]: largest.group("body").rstrip("\n")}
    if len(expected) == 1 and _looks_like_code(text):
        return {expected[0]: _strip_envelope_header(text.strip("\n"))}
    raise MultiFileParseError(
        "no '=== FILE: <path> ===' blocks found" + (f"; expected {expected}" if expected else "")
    )


_HEADER_LINE = re.compile(r"^[ \t]*===\s*FILE:\s*[^\n=]+?\s*===[ \t]*\r?\n")


def _strip_envelope_header(body: str) -> str:
    """A truncated answer (cut before ``=== END FILE ===``) reaches the single-file
    fallback with its ``=== FILE: … ===`` header still attached; treat the
    unterminated block as the file's body instead of shipping the header as line 1."""
    m = _HEADER_LINE.match(body)
    return body[m.end():] if m else body


def _clean_path(p: str) -> str:
    p = p.strip().strip("`'\"").replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p.lstrip("/")


def _strip_fence(body: str) -> str:
    s = body.strip("\n")
    m = re.fullmatch(r"```[a-zA-Z0-9_+\-]*[ \t]*\n(.*?)\n?```", s, re.S)
    return (m.group(1) if m else body).rstrip("\n")


def _looks_like_code(text: str) -> bool:
    t = text.strip()
    return bool(t) and ("\n" in t) and not t.lower().startswith(("i ", "here", "sure", "sorry"))


def safe_relpath(path: str, allowed_roots: tuple[str, ...] = ALLOWED_ROOTS) -> str:
    """Validate a model-provided relative path: inside the workspace + allowed roots."""
    p = _clean_path(path)
    if not p or p.startswith("/") or ".." in Path(p).parts or re.match(r"^[A-Za-z]:", p):
        raise GenerationError(f"refusing to write outside the workspace: {path!r}")
    if p.endswith("/"):  # '=== FILE: src/parts/ ===' names a directory, not a file
        raise GenerationError(f"path {path!r} is a directory, not a file")
    if not any(p.startswith(root) for root in allowed_roots):
        raise GenerationError(f"path {p!r} is outside the allowed roots {allowed_roots}")
    return p


def write_files(
    ws: Workspace,
    files: dict[str, str],
    *,
    allowed_roots: tuple[str, ...] = ALLOWED_ROOTS,
    only: Collection[str] | None = None,
    on_skip: Callable[[str, str], None] | None = None,
) -> list[FileChange]:
    """Write parsed files under the workspace; returns git-style FileChange rows.

    A block whose path is unsafe, outside ``allowed_roots`` (flash models love
    to add README.md / package.json despite the format rule) or unwritable is
    SKIPPED — reported via ``on_skip`` — instead of aborting the whole write:
    the valid files were already paid for.

    ``only`` (an ``edit_only`` task's file scope, entry included when owned) skips a
    path that ALREADY EXISTS and is not listed — new files stay allowed, mirroring
    ``FileTools``: the single-shot envelope has no write-time gate, so this is where a
    scoped task is stopped from rewriting a sibling's file."""
    scope = {_clean_path(p) for p in only} if only is not None else None
    changes: list[FileChange] = []
    for raw, content in files.items():
        try:
            rel = safe_relpath(raw, allowed_roots)
        except GenerationError as e:
            if on_skip is not None:
                on_skip(raw, str(e))
            log.warning("skipping out-of-root file from generator: %s", e)
            continue
        dest = ws.root / rel
        existed = dest.exists()
        if scope is not None and existed and rel not in scope:
            reason = f"out of scope: {rel} already exists and is not in this task's file list"
            if on_skip is not None:
                on_skip(raw, reason)
            log.warning("skipping out-of-scope file from generator: %s", rel)
            continue
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content if content.endswith("\n") else content + "\n")
        except OSError as e:
            # Same contract as an out-of-root path: one unwritable block (a path that
            # is already a directory, a name the filesystem rejects) must not throw
            # away the files that DID parse -- the whole answer was already paid for.
            if on_skip is not None:
                on_skip(raw, str(e))
            log.warning("skipping unwritable file from generator: %s: %s", rel, e)
            continue
        changes.append(FileChange(path=rel, status="modified" if existed else "added",
                                  lines_added=content.count("\n") + 1))
    return changes



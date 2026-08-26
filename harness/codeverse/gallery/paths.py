"""Path safety for the local file server.

Two independent gates, both required:

1. the run directory must come from the **index** — a URL can only ever name a
   ``(battery, slug)`` pair the scanner found under a declared root; and
2. :func:`safe_join` resolves the rest of the URL inside that run directory and
   refuses anything that escapes it (``..`` segments, absolute paths, encoded
   separators, symlinks pointing out of the run).

``tests/gallery/test_server.py`` walks the traversal cases.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath


class PathError(ValueError):
    """A requested path is outside the run directory (or is not usable)."""


def safe_join(root: Path | str, rel: str) -> Path:
    """``root / rel`` when ``rel`` stays inside ``root``; :class:`PathError` otherwise.

    ``rel`` is a URL path fragment that has already been percent-decoded."""
    base = Path(root).resolve()
    rel = (rel or "").strip()
    if "\x00" in rel:
        raise PathError("null byte in path")
    if rel.startswith(("/", "\\")) or (len(rel) > 1 and rel[1] == ":"):
        raise PathError(f"absolute path refused: {rel!r}")
    parts = [p for p in PurePosixPath(rel.replace("\\", "/")).parts if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise PathError(f"parent traversal refused: {rel!r}")
    target = base.joinpath(*parts)
    try:
        resolved = target.resolve()
    except OSError as e:  # broken symlink loop, too many levels, …
        raise PathError(f"unresolvable path: {rel!r} ({e})") from e
    if resolved != base and base not in resolved.parents:
        raise PathError(f"path escapes the run directory: {rel!r}")
    return resolved

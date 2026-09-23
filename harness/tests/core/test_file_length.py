"""The 2 000-line cap per source file (law 4, owner 2026-08-28)."""

from __future__ import annotations

import subprocess
from pathlib import Path

HARD_CAP = 2000
SUFFIXES = {".py", ".js", ".mjs", ".cjs"}
EXCLUDE_PARTS = {"node_modules", "vendor"}

ROOT = Path(__file__).resolve().parents[2]


def tracked_sources() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout
    files = []
    for rel in out.split("\0"):
        p = Path(rel)
        if rel and p.suffix in SUFFIXES and not EXCLUDE_PARTS.intersection(p.parts):
            files.append(ROOT / rel)
    return files


def test_no_tracked_source_file_exceeds_the_hard_cap() -> None:
    files = tracked_sources()
    assert len(files) > 400, f"suspiciously few tracked sources ({len(files)}) — is git available?"
    over = []
    for f in files:
        try:
            n = sum(1 for _ in f.open("rb"))
        except FileNotFoundError:  # tracked but deleted in the working tree
            continue
        if n > HARD_CAP:
            over.append(f"{f.relative_to(ROOT)}: {n} lines")
    assert not over, (
        f"files over the {HARD_CAP}-line hard cap (split by responsibility, "
        f"not mechanically):\n" + "\n".join(over)
    )

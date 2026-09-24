"""The tolerant JSONL reader every resumable stage starts from, and the one writer.

The stages rewrite their ``*.jsonl`` whole at the end.  That rewrite truncated the file
first, so a process killed during it lost every row after the tear (re-paid on the next
``--resume``) and left a torn line that a strict ``json.loads`` per line then crashed on.
``write_rows`` publishes the new file in one rename (``codeverse3d.proc.write_text_atomic``);
``read_rows`` still skips a line that does not parse — that row simply runs again."""

from __future__ import annotations

import json
from pathlib import Path

from codeverse3d.proc import write_text_atomic


def read_rows(path: Path) -> list[dict]:
    """Every JSON object line of ``path``, in order; blank and torn lines are skipped."""
    rows = []
    with Path(path).open() as f:
        for line in f:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def write_rows(path: Path, rows: list[dict], *, ensure_ascii: bool = True) -> None:
    """Rewrite ``path`` as one JSON line per row, atomically (the old file stays whole until
    the new one is complete)."""
    write_text_atomic(Path(path), "".join(json.dumps(r, ensure_ascii=ensure_ascii) + "\n" for r in rows))

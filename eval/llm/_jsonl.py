"""The tolerant JSONL reader every resumable stage starts from.

The stages rewrite their ``*.jsonl`` whole at the end; a process killed during that write
leaves a torn last line, and a strict ``json.loads`` per line then crashed every later
``--resume`` of the directory.  A row that does not parse was not finished: the stage
simply runs it again."""

from __future__ import annotations

import json
from pathlib import Path


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

"""The tolerant JSONL reader every resumable stage starts from, and the one writer.

The stages rewrite their ``*.jsonl`` whole at the end.  That rewrite truncated the file
first, so a process killed during it lost every row after the tear (re-paid on the next
``--resume``) and left a torn line that a strict ``json.loads`` per line then crashed on.
``write_rows`` publishes the new file in one rename (``codeverse3d.proc.write_text_atomic``);
``read_rows`` is the harness's lenient reader: a line that does not parse (a torn tail, a bad
byte) is skipped and that row simply runs again; a missing file has no rows."""

from __future__ import annotations

import json
from functools import partial
from pathlib import Path

from codeverse3d.proc import read_jsonl_lenient, write_text_atomic

#: every JSON object line of a file, in order
read_rows = partial(read_jsonl_lenient, dicts_only=True)


def write_rows(path: Path, rows: list[dict], *, ensure_ascii: bool = True) -> None:
    """Rewrite ``path`` as one JSON line per row, atomically (the old file stays whole until
    the new one is complete)."""
    write_text_atomic(Path(path), "".join(json.dumps(r, ensure_ascii=ensure_ascii) + "\n" for r in rows))

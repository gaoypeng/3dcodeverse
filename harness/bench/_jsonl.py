"""One tolerant reader for the append-only ``*.jsonl`` files the bench drivers
resume and report from.

Every one of those files is written a line at a time with a flush per row
(``run_bench.py``, ``compare_backends.py``, ``ab_plan.py``), which is exactly the
shape a SIGKILL truncates mid-append.  Each driver used to validate every line
strictly, so a single half-written last line made the whole file unusable: a killed
battery could not be resumed *at all* and its already-paid rows could not even be
reported — the operator had to hand-edit ``results.jsonl`` or re-buy the battery.

Skip the bad line, keep the rest, say so once — the same contract
``codeverse.cost.ledger.load_ledger`` has always had ("a truncated last line never
loses the rest of the file").
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


def read_jsonl[T: BaseModel](path: Path, model: type[T]) -> list[T]:
    """Parse ``path`` as one ``model`` per line, skipping unparseable lines with a
    warning.  Missing file → ``[]``."""
    if not Path(path).is_file():
        return []
    rows: list[T] = []
    bad = 0
    for i, line in enumerate(Path(path).read_text(encoding="utf-8", errors="replace").splitlines()):
        if not line.strip():
            continue
        try:
            rows.append(model.model_validate_json(line))
        except Exception as e:  # a truncated/corrupt row must not cost us the good ones
            bad += 1
            log.warning("%s:%d unreadable, skipping (%s: %s)", path, i + 1, type(e).__name__, str(e)[:120])
    if bad:
        log.warning("%s: skipped %d unreadable line(s), kept %d", path, bad, len(rows))
    return rows


def seal_for_append(path: Path) -> None:
    """Terminate a partial last line before appending to a journal.

    A SIGKILL leaves ``results.jsonl`` with a last line that has no ``\n``.  Opening
    it ``"a"`` and writing the next row would then glue the new row onto the broken
    one and lose BOTH — the resumed cell would be paid for and then dropped on every
    subsequent read.  One newline keeps the damage to the single row that was
    actually interrupted.
    """
    p = Path(path)
    if not p.is_file() or p.stat().st_size == 0:
        return
    with p.open("rb+") as fh:
        fh.seek(-1, 2)
        if fh.read(1) != b"\n":
            fh.write(b"\n")

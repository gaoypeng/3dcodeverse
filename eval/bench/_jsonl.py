"""The one reader and the one dedup rule for the append-only ``*.jsonl`` journals the
bench drivers resume and report from.

Every one of those files is written a line at a time with a flush per row
(``run_bench.py``, ``compare_backends.py``, ``ab_plan.py``, ``plan_stage_bench.py``),
which is exactly the shape a SIGKILL truncates mid-append.  Readers used to parse every
line strictly, so a single half-written last line made the whole file unusable: a killed
battery could not be resumed *at all* and its already-paid rows could not even be
reported — the operator had to hand-edit ``results.jsonl`` or re-buy the battery.

Skip the bad line, keep the rest, say so — the same contract
``codeverse3d.cost.ledger.load_ledger`` has always had ("a truncated last line never
loses the rest of the file").
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Hashable, Iterable
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from codeverse3d.proc import iter_jsonl_lines

log = logging.getLogger(__name__)


def read_jsonl(path: Path, model: type[BaseModel] | None = None) -> list[Any]:
    """Every row of ``path`` — a ``model`` per line, or a JSON object when no model is
    given — skipping unreadable lines with a warning.  Missing file → ``[]``."""
    rows: list[Any] = []
    bad = 0
    for i, line in iter_jsonl_lines(path):
        try:
            row = model.model_validate_json(line) if model is not None else json.loads(line)
            if not isinstance(row, (BaseModel, dict)):
                raise ValueError(f"a JSON {type(row).__name__}, not an object")
        except Exception as e:  # a truncated/corrupt row must not cost us the good ones
            bad += 1
            log.warning("%s:%d unreadable, skipping (%s: %s)", path, i, type(e).__name__, str(e)[:120])
            continue
        rows.append(row)
    if bad:
        log.warning("%s: skipped %d unreadable line(s), kept %d", path, bad, len(rows))
    return rows


def latest[R](rows: Iterable[R], key: Callable[[R], Hashable] | None = None) -> dict[Hashable, R]:
    """THE dedup rule of every journal: one row per key (``row.natural_key()`` unless
    ``key`` says otherwise), the LAST row winning and the first appearance fixing the
    order.  A dict, so a cell is looked up by its key; ``.values()`` is the journal as it
    stands.

    A resume or a ``--redo-status`` re-run appends a second row for the same key, so a
    reader that skips this rule reads a superseded row: ``ab_plan --report-only`` once
    handed the raw rows to ``render_summary`` while the live driver passed deduped ones,
    and one summary.md printed the variant's mean as 0.400 in the arms table, 0.800 per
    prompt, and advised ``--redo-status infra_failed`` for cells already re-run and scored.
    """
    out: dict[Hashable, R] = {}
    for r in rows:
        out[key(r) if key is not None else r.natural_key()] = r  # type: ignore[attr-defined]
    return out


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

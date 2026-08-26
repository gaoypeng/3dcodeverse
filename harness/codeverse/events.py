"""Append-only JSONL event log per run (one line per stage/tool/model event).

Events are the audit trail that the flywheel, the CLI ``status`` view and
tests read.  Keep payloads small (paths + numbers, not file contents).
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

from codeverse.proc import append_jsonl_line, read_jsonl_lenient

log = logging.getLogger(__name__)


class EventLog:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, event: str, **data: Any) -> None:
        """Append one event.  ``event`` is the event name (e.g. ``round.start``);
        ``data`` may use any keys, including ``kind``."""
        append_jsonl_line(self.path, {"t": round(time.time(), 3), "event": event, **data}, self._lock)

    def read(self) -> list[dict[str, Any]]:
        """Read the log, skipping unparseable lines with a debug log.

        ``emit`` appends one buffered ``write``, so a SIGKILL / OOM kill / reboot
        leaves a partial trailing line — and a killed run is exactly when someone
        types ``3dcv status``.  A truncated last line must never lose the rest of
        the file (same contract as ``cost.ledger.load_ledger``)."""
        return read_jsonl_lenient(self.path, log=log)

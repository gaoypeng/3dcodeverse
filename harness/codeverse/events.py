"""Append-only JSONL event log per run (one line per stage/tool/model event).

Events are the audit trail that the flywheel, the CLI ``status`` view and
tests read.  Keep payloads small (paths + numbers, not file contents).
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any


class EventLog:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, event: str, **data: Any) -> None:
        """Append one event.  ``event`` is the event name (e.g. ``round.start``);
        ``data`` may use any keys, including ``kind``."""
        rec = {"t": round(time.time(), 3), "event": event, **data}
        line = json.dumps(rec, default=str, ensure_ascii=False)
        with self._lock, self.path.open("a") as fh:
            fh.write(line + "\n")

    def read(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]

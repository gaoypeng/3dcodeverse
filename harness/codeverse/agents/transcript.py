"""Trajectory folder helper: prompt.md, transcript.jsonl, stdout/stderr, result.json.

Every CodingAgent backend writes its session here
(``ws.trajectory_dir(label, round)``) so the flywheel and ``3dcv status`` can
read one uniform layout regardless of backend.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel


class Trajectory:
    """Append-only writer for one agent session's files."""

    def __init__(self, directory: Path | str):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ paths
    @property
    def prompt_path(self) -> Path:
        return self.dir / "prompt.md"

    @property
    def transcript_path(self) -> Path:
        return self.dir / "transcript.jsonl"

    @property
    def result_path(self) -> Path:
        return self.dir / "result.json"

    @property
    def stdout_path(self) -> Path:
        return self.dir / "stdout.log"

    @property
    def stderr_path(self) -> Path:
        return self.dir / "stderr.log"

    # ------------------------------------------------------------------ writes
    def write_prompt(self, prompt: str, system: str = "") -> Path:
        body = prompt if not system else f"<!-- system -->\n{system}\n\n<!-- prompt -->\n{prompt}"
        self.prompt_path.write_text(body)
        return self.prompt_path

    def write_text(self, name: str, text: str) -> Path:
        p = self.dir / name
        p.write_text(text)
        return p

    def write_json(self, name: str, data: BaseModel | dict[str, Any] | list[Any]) -> Path:
        p = self.dir / name
        payload = data.model_dump(mode="json") if isinstance(data, BaseModel) else data
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
        tmp.replace(p)
        return p

    def append(self, kind: str, **data: Any) -> None:
        """Append one JSONL turn: ``{"t": epoch, "kind": kind, **data}``."""
        rec = {"t": round(time.time(), 3), "kind": kind, **data}
        line = json.dumps(rec, ensure_ascii=False, default=str)
        with self._lock, self.transcript_path.open("a") as fh:
            fh.write(line + "\n")

    def write_result(self, result: BaseModel, **extra: Any) -> Path:
        """Write ``result.json`` = AgentResult fields + any extra diagnostics."""
        data = result.model_dump(mode="json")
        data.update(extra)
        return self.write_json("result.json", data)

    def read_transcript(self) -> list[dict[str, Any]]:
        if not self.transcript_path.is_file():
            return []
        return [json.loads(ln) for ln in self.transcript_path.read_text().splitlines() if ln.strip()]

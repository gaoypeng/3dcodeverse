"""StageRunner: named, resumable stages with input-hash caching.

A *stage* is ``fn() -> result``.  The runner hashes the declared ``inputs``;
when the hash matches the one recorded in ``RunState`` and the stage's result
file (``<ws>/stages/<name>.json``) exists, the cached result is returned
without running ``fn`` (content-addressed skip — no mtime heuristics).
Results may be pydantic models, lists of models, plain JSON values, or
``Path``s; pass ``model=`` to re-validate a cached JSON into its type.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from codeverse.events import EventLog
from codeverse.orchestrator.state import RunState, StageState
from codeverse.workspace import Workspace

T = TypeVar("T")

_SAFE = re.compile(r"[^0-9A-Za-z._-]+")


def hash_inputs(inputs: Any) -> str:
    """Stable sha256 (12 hex) of arbitrary JSON-able inputs (pydantic ok)."""
    payload = _jsonable(inputs)
    blob = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


def _jsonable(obj: Any) -> Any:
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_jsonable(v) for v in obj]
    return obj


class StageError(RuntimeError):
    """A stage function raised; the original exception is ``__cause__``."""


class StageRunner:
    """Runs stages with resume semantics on top of ``RunState`` + ``EventLog``."""

    def __init__(self, ws: Workspace, events: EventLog, state: RunState | None = None):
        self.ws = ws
        self.events = events
        self.state = state if state is not None else RunState()

    # ----------------------------------------------------------------- paths
    def result_path(self, name: str) -> Path:
        return self.ws.root / "stages" / f"{_SAFE.sub('_', name)}.json"

    # ----------------------------------------------------------------- api
    def stage(
        self,
        name: str,
        fn: Callable[[], T],
        *,
        inputs: Any = "",
        force: bool = False,
        model: type[BaseModel] | None = None,
        list_of: type[BaseModel] | None = None,
    ) -> T:
        """Run ``fn`` unless a cached result for the same ``inputs`` exists."""
        h = hash_inputs(inputs)
        path = self.result_path(name)
        prior = self.state.stages.get(name)
        if not force and prior is not None and prior.inputs_hash == h and path.is_file():
            data = json.loads(path.read_text())
            result = _revive(data, model, list_of)
            self.events.emit("stage.cached", stage=name, inputs_hash=h, path=str(path))
            return result  # type: ignore[return-value]

        self.events.emit("stage.start", stage=name, inputs_hash=h)
        t0 = time.time()
        try:
            result = fn()
        except Exception as e:
            self.events.emit("stage.failed", stage=name, error=f"{type(e).__name__}: {e}",
                             duration_s=round(time.time() - t0, 2))
            raise
        dt = time.time() - t0
        path.parent.mkdir(parents=True, exist_ok=True)
        self.ws.write_json(path, {"stage": name, "inputs_hash": h, "result": _jsonable(result)})
        self.state.stages[name] = StageState(name=name, inputs_hash=h, result_path=str(path), duration_s=dt)
        self.state.save(self.ws)
        self.events.emit("stage.done", stage=name, inputs_hash=h, duration_s=round(dt, 2))
        return result

    def is_done(self, name: str, inputs: Any = "") -> bool:
        prior = self.state.stages.get(name)
        return prior is not None and prior.inputs_hash == hash_inputs(inputs) and self.result_path(name).is_file()

    def invalidate(self, name: str) -> None:
        self.state.stages.pop(name, None)
        p = self.result_path(name)
        if p.exists():
            p.unlink()
        self.state.save(self.ws)


def _revive(data: dict[str, Any], model: type[BaseModel] | None, list_of: type[BaseModel] | None) -> Any:
    result = data.get("result")
    if model is not None and isinstance(result, dict):
        return model.model_validate(result)
    if list_of is not None and isinstance(result, list):
        return [list_of.model_validate(r) for r in result]
    return result

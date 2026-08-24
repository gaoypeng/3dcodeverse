"""Persistent run state (``run_state.json``): what is done, what is best.

The state is the resume anchor.  It records, per stage, the hash of the inputs
it ran with (so ``StageRunner`` can reuse a cached result), the rounds that
completed, and the best round/commit seen so far.  Writes are atomic
(tmp + rename via ``Workspace.write_json``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from codeverse._compat import UTC
from codeverse.contracts.run import RunStatus
from codeverse.workspace import Workspace


class StageState(BaseModel):
    """One completed stage: inputs hash + where its result JSON lives."""

    name: str
    inputs_hash: str
    result_path: str = ""
    finished_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    duration_s: float = 0.0


class RunState(BaseModel):
    """Everything the orchestrator needs to resume a run."""

    status: RunStatus = RunStatus.PLANNING
    stages: dict[str, StageState] = Field(default_factory=dict)
    current_round: int = Field(default=0, description="index of the round in progress / next to run")
    completed_rounds: list[int] = Field(default_factory=list)
    round_commits: dict[int, str] = Field(default_factory=dict)
    best_round: int | None = None
    best_commit: str = ""
    best_score: float | None = None
    materialized_for: str = Field(default="", description="agent kind the workspace was materialised for")
    stop_reason: str = ""
    error: str = ""
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    extra: dict[str, Any] = Field(default_factory=dict)

    # ----------------------------------------------------------------- persistence
    @classmethod
    def load(cls, ws: Workspace) -> RunState | None:
        """Return the saved state or ``None`` when the file is absent."""
        if not ws.state_path.is_file():
            return None
        try:
            return cls.model_validate(ws.read_json(ws.state_path))
        except (ValidationError, ValueError) as e:
            raise StateCorrupt(f"{ws.state_path}: {e}") from e

    @classmethod
    def load_or_new(cls, ws: Workspace, *, resume: bool) -> RunState:
        if resume:
            st = cls.load(ws)
            if st is not None:
                return st
        return cls()

    def save(self, ws: Workspace) -> None:
        self.updated_at = datetime.now(UTC)
        ws.write_json(ws.state_path, self)

    # ----------------------------------------------------------------- helpers
    def mark_round_done(self, index: int, commit: str, ws: Workspace | None = None) -> None:
        if index not in self.completed_rounds:
            self.completed_rounds.append(index)
        self.round_commits[index] = commit
        self.current_round = index + 1
        if ws is not None:
            self.save(ws)

    def update_best(self, index: int, commit: str, score: float | None) -> bool:
        """Record ``index`` as best; returns True when it changed."""
        changed = self.best_round != index
        self.best_round, self.best_commit, self.best_score = index, commit, score
        return changed


class StateCorrupt(RuntimeError):
    """``run_state.json`` exists but does not validate — refuse to guess."""

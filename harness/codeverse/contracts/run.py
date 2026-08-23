"""The run record: one entry per run, the unit the flywheel indexes."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from codeverse.contracts.artifacts import BuildResult, GateReport, Measurement, RenderSet
from codeverse.contracts.common import Usage
from codeverse.contracts.judgment import Judgment
from codeverse.contracts.plan import ArticulatedPlan, GraphicsPlan, ScenePlan, StaticPlan
from codeverse.contracts.spec import Spec


class RunStatus(StrEnum):
    PLANNING = "planning"
    GENERATING = "generating"
    REFINING = "refining"
    PASSED = "passed"
    PLATEAU = "plateau"
    BUDGET = "budget"
    FAILED = "failed"


class RoundRecord(BaseModel):
    index: int
    kind: str = Field(description="baseline | refine | repair | texture | asset:<name> ...")
    commit: str = Field(default="", description="git commit of src/ after this round")
    agent_backend: str = ""
    instructions: list[str] = Field(default_factory=list, description="what the agent was asked to change")
    build: BuildResult | None = None
    gates: list[GateReport] = Field(default_factory=list)
    measurement: Measurement | None = None
    renders: RenderSet | None = None
    judgment: Judgment | None = None
    usage: Usage = Field(default_factory=Usage)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    duration_s: float = 0.0
    notes: str = ""

    @property
    def score(self) -> float | None:
        return self.judgment.overall if self.judgment else None


class RunRecord(BaseModel):
    spec: Spec
    plan: StaticPlan | ArticulatedPlan | ScenePlan | GraphicsPlan | None = None
    workspace: str
    status: RunStatus = RunStatus.PLANNING
    rounds: list[RoundRecord] = Field(default_factory=list)
    best_round: int | None = None
    baseline_score: float | None = None
    final_score: float | None = None
    total_usage: Usage = Field(default_factory=Usage)
    environment: dict[str, str] = Field(default_factory=dict, description="tool versions, git sha, host")
    prompt_hashes: dict[str, str] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    error: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)

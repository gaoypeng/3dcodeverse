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


# --------------------------------------------------------------------------- telemetry (settings + cost)
class RoleSettings(BaseModel):
    """Resolved model settings for ONE role of a run (planner/generator/judge/…).

    ``source`` says where the sampling knobs came from: ``spec`` (frozen on the
    Spec), ``default`` (the harness default, read from the call site's typed
    defaults) or ``unknown`` (the call site hard-codes it per task — the value
    is left empty rather than guessed)."""

    role: str
    model: str = ""
    backend: str = Field(default="", description="gemini | anthropic | api-agent | gemini-cli | codex | …")
    thinking: str = Field(default="", description="off | low | medium | high; '' = not recorded")
    temperature: float | None = None
    n_samples: int | None = Field(default=None, description="judge samples per verdict")
    source: str = "spec"


class SettingsSnapshot(BaseModel):
    """Everything that decided HOW the run ran — the 'key step settings' block."""

    schema_version: int = 1
    roles: list[RoleSettings] = Field(default_factory=list)
    budget: dict[str, Any] = Field(default_factory=dict, description="max_rounds / max_usd / max_minutes / max_repair_attempts")
    candidates: int | None = Field(default=None, description="best-of-N baseline width, resolved")
    texture: bool = False
    seed: int = 0
    rubric: str = ""
    rubric_hash: str = Field(default="", description="content hash of the judge rubric YAML")
    prompt_hashes: dict[str, str] = Field(default_factory=dict, description="contract / cookbook / generate / refine …")
    tool_versions: dict[str, str] = Field(default_factory=dict, description="python / blender / node / three / chrome / harness sha")
    harness_version: str = ""
    harness_git_sha: str = ""
    key_pool_size: int = Field(default=0, description="number of API keys in the rotating pool")
    price_table_version: str = Field(default="", description="content hash of codeverse.models.pricing.PRICES")
    render: dict[str, Any] = Field(default_factory=dict, description="resolved render settings (size, gpu, sheet)")
    limits: dict[str, Any] = Field(default_factory=dict, description="resolved timeouts / parallelism")


class StageCost(BaseModel):
    """Token + money subtotal for one stage of the run (plan / generate / judge / …)."""

    stage: str
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    thoughts_tokens: int = 0
    cost_usd: float = 0.0
    seconds: float = 0.0


class CostSummary(BaseModel):
    """The money ledger: what the run cost, where, and against which budget."""

    schema_version: int = 1
    total_usd: float = 0.0
    budget_usd: float = 0.0
    budget_used_pct: float | None = None
    wall_clock_s: float = 0.0
    max_minutes: float = 0.0
    n_calls: int = 0
    tokens: Usage = Field(default_factory=Usage, description="run totals (record.total_usage)")
    by_stage: list[StageCost] = Field(default_factory=list)
    by_role: dict[str, float] = Field(default_factory=dict, description="planner/generator/judge/… → USD")
    by_model: dict[str, float] = Field(default_factory=dict, description="model → USD")
    by_round: list[dict[str, Any]] = Field(default_factory=list, description="index / kind / cost_usd / seconds / score")
    ledger_usd: float = Field(default=0.0, description="sum of the per-call rows in telemetry/usage.jsonl")
    unattributed_usd: float = Field(
        default=0.0, description="total_usd - ledger_usd when positive: money with no per-call row")
    post_run_usd: float = Field(
        default=0.0, description="ledger_usd - total_usd when positive: priced calls the run total does not "
                                 "include (a texture pass that ran after the loop, …)")


class RunTelemetry(BaseModel):
    """``record.telemetry`` — accounting + settings, kept apart from the deliverable."""

    schema_version: int = 1
    settings: SettingsSnapshot | None = None
    cost: CostSummary | None = None
    environment: dict[str, str] = Field(default_factory=dict)
    files: dict[str, str] = Field(default_factory=dict, description="label → run-relative path under telemetry/")


# --------------------------------------------------------------------------- deliverable
class DeliverableFile(BaseModel):
    """One file handed to the user, with its identity."""

    path: str = Field(description="run-relative path (under deliverable/)")
    role: str = Field(description="code | model | urdf | mesh | sheet | preview | frames | captions | bundle | manifest")
    bytes: int = 0
    sha256: str = ""


class RunDeliverable(BaseModel):
    """``record.deliverable`` — what the run produced, as paths + hashes + sizes."""

    schema_version: int = 1
    dir: str = "deliverable"
    best_round: int | None = None
    commit: str = ""
    code_source: str = Field(default="", description="commit | working_tree")
    entry: str = Field(default="", description="run-relative entry file of the code snapshot")
    files: list[DeliverableFile] = Field(default_factory=list)
    total_bytes: int = 0
    skipped: dict[str, str] = Field(default_factory=dict, description="name → why it is not in deliverable/")
    generated_at: datetime | None = None

    def by_role(self, role: str) -> list[DeliverableFile]:
        return [f for f in self.files if f.role == role]


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
    telemetry: RunTelemetry | None = Field(
        default=None, description="settings snapshot + cost ledger + environment (written by finalize_record)")
    deliverable: RunDeliverable | None = Field(
        default=None, description="what to hand over: paths + hashes + sizes under deliverable/")
    extra: dict[str, Any] = Field(default_factory=dict)

"""The run record (``record.json``) and everything stored inside it: ``RunRecord``, ``RunId``,
per-round skills usage, and the ``telemetry/`` + ``deliverable/`` blocks."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from codeverse3d.contracts.artifacts import (
    BuildResult,
    GateReport,
    Judgment,
    Measurement,
    RenderSet,
)
from codeverse3d.contracts.common import Usage
from codeverse3d.contracts.plan import ArticulatedPlan, GraphicsPlan, ScenePlan, StaticPlan
from codeverse3d.contracts.spec import Spec


class SkillRead(BaseModel):
    """The read probe's verdict for one materialised skill bundle."""

    name: str
    surfaced: bool = Field(default=False, description="transcript: the agent activated the skill or opened its "
                           "SKILL.md; atime fallback: SKILL.md atime > mtime (a discovery scan counts)")
    deep: bool = Field(default=False, description="transcript: the agent opened a file under its references/ (or "
                       "its atime says so while the control's references/ are untouched — a shell read the "
                       "trace cannot parse); atime fallback: a references/*.md atime > mtime")
    deep_measurable: bool = Field(default=True, description="False when the bundle ships no references/ file to probe")
    body_tokens: int = 0
    reason: str = Field(default="", description="which route attached it, and which finding")


class SkillsUsage(BaseModel):
    """``RoundRecord.skills``: skills listed vs read vs what it cost, per round."""

    listed: list[str] = Field(default_factory=list)
    reads: list[SkillRead] = Field(default_factory=list)
    index_tokens: int = 0
    body_tokens_read: int = Field(default=0, description="tokens of the bodies the probe says were read deep")
    inlined: str = Field(default="", description="single-shot: the body inlined into the prompt, '' otherwise")
    control_read: bool = Field(
        default=False,
        description="the never-routed control bundle was 'read' too.  Under atime evidence that "
                    "means the probe was blind (every rate below is an upper bound); under "
                    "transcript evidence it means the AGENT itself opened a bundle that matches "
                    "nothing — a real read, and a sign it reads skills indiscriminately")
    control_present: bool = Field(default=False, description="a control bundle was materialised at all")
    evidence: Literal["atime", "transcript"] = Field(
        default="atime",
        description="where the reads came from: 'transcript' = the CLI's own tool calls, recorded by "
                    "its backend in trajectories/*/transcript.jsonl (exact); 'atime' = the file-access "
                    "probe, the fallback when a session left no tool trace")

    @property
    def surfaced(self) -> list[str]:
        return [r.name for r in self.reads if r.surfaced]

    @property
    def deep(self) -> list[str]:
        return [r.name for r in self.reads if r.deep]

    @property
    def deep_read_rate(self) -> float | None:
        """None when nothing is listed or the atime control shows the probe is blind (git and CLI
        activation touch files nobody chose to read).  Transcript evidence is trusted as is."""
        if not self.listed or (self.evidence == "atime" and self.control_present and self.control_read):
            return None
        return len(self.deep) / len(self.listed)


# ===================================================================== run
# --------------------------------------------------------------------------- identity
#: battery-layout path segments that are pure plumbing, never part of a run's identity
RUN_PATH_NOISE = frozenset({"arms", "cells", "runs"})


class RunId(BaseModel):
    """Where a run directory sits inside its scan root, and the one name to call it.

    ``battery`` labels the scan root; ``rel`` is the run dir's posix path relative to it.
    Only the scanner knows the root, so only it mints one (``record.record.run_id_for``)."""

    model_config = ConfigDict(frozen=True)

    battery: str = Field(description="label of the scan root this run was found under")
    rel: str = Field(description="posix path of the run dir relative to the scan root")

    def _parts(self) -> tuple[str, ...]:
        return PurePosixPath(self.rel).parts

    @property
    def slug(self) -> str:
        """The stable, filesystem-safe name of this run.

        A flat ``rel`` keeps its basename; a nested one joins its meaningful segments with ``__``
        (dropping :data:`RUN_PATH_NOISE` and a trailing ``run``)."""
        parts = self._parts()
        if len(parts) <= 1:
            return parts[0] if parts else self.battery
        meaningful = [p for p in parts if p not in RUN_PATH_NOISE]
        if len(meaningful) > 1 and meaningful[-1] == "run":
            meaningful.pop()  # the literal `run` dir a battery cell wraps its harness run in
        if not meaningful:
            meaningful = [parts[-1]]
        return "__".join(meaningful)

    @property
    def cell(self) -> str | None:
        """The ``cells/<cell>/…`` segment, when the layout has one."""
        parts = self._parts()
        if "cells" in parts:
            i = parts.index("cells")
            if i + 1 < len(parts):
                return parts[i + 1]
        return None

    @property
    def arm(self) -> str | None:
        """The ``arms/<arm>/…`` segment, or the directory between the cell and its ``run``."""
        parts = self._parts()
        if "arms" in parts:
            i = parts.index("arms")
            return parts[i + 1] if i + 1 < len(parts) else None
        if "cells" in parts:
            i = parts.index("cells")
            if i + 2 < len(parts) and parts[i + 2] != "run":
                return parts[i + 2]
        return None


class RunStatus(StrEnum):
    """Where a run is, and once stopped, why it stopped.  Never a verdict."""

    PLANNING = "planning"
    GENERATING = "generating"
    REFINING = "refining"
    MAX_ROUNDS = "max_rounds"                # every round the budget asked for ran
    BUDGET = "budget"                        # the wall clock (``max_minutes``) ran out
    AGENT_QUOTA = "agent_quota"              # the vendor's usage limit, not ours
    NO_CHANGE = "no_change"                  # a refine round's sessions all failed / changed nothing
    NO_REFINE_TASKS = "no_refine_tasks"      # the last round left nothing to ask for
    JUDGE_UNAVAILABLE = "judge_unavailable"  # the last round has no verdict, even after a re-judge
    FAILED = "failed"


class StepTime(BaseModel):
    """One timed step: its wall clock and the part lost to provider errors (503 / 429 / timeouts).

    Parallel work is ONE step, so no second is counted twice."""

    step: str = Field(description="plan | skeleton | assets+env+layouts | zones | assemble | candidates"
                                  " | generate | build | gates | render | judge")
    round: int | None = Field(default=None, description="the round the step belongs to; None = a run-level stage")
    wall_s: float = 0.0
    lost_s: float = Field(default=0.0, description="seconds of wall_s lost to provider errors")

    @property
    def active_s(self) -> float:
        return max(0.0, self.wall_s - self.lost_s)


class RoundRecord(BaseModel):
    index: int
    kind: str = Field(description="baseline | refine | repair | asset:<name> ...")
    commit: str = Field(default="", description="git commit of src/ after this round")
    agent_backend: str = ""
    instructions: list[str] = Field(default_factory=list, description="what the agent was asked to change")
    build: BuildResult | None = None
    gates: list[GateReport] = Field(default_factory=list)
    measurement: Measurement | None = None
    renders: RenderSet | None = None
    judgment: Judgment | None = None
    usage: Usage = Field(default_factory=Usage)
    skills: SkillsUsage | None = Field(
        default=None, description="skills attached to this round's sessions, and which were actually read")
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    duration_s: float = Field(default=0.0, description="the round's clock, provider errors included")
    steps: list[StepTime] = Field(
        default_factory=list, description="the round's timed steps (generate / build / gates / render / judge); "
                                  "empty until timing is recorded")
    notes: str = ""

    @property
    def score(self) -> float | None:
        return self.judgment.overall if self.judgment else None

    @property
    def minutes(self) -> float:
        """The round's timed steps, excluding provider wait."""
        return sum(s.active_s for s in self.steps) / 60.0


# --------------------------------------------------------------------------- telemetry (settings + cost)
class RoleSettings(BaseModel):
    """Resolved model settings for ONE role of a run.

    ``source``: ``run`` (stamped as it ran) or ``spec`` (only the model is known)."""

    role: str
    model: str = ""
    backend: str = Field(default="", description="gemini | anthropic | agy | claude-code | codex | …")
    thinking: str = Field(default="", description="off | low | medium | high; '' = not recorded")
    temperature: float | None = None
    n_samples: int | None = Field(default=None, description="judge samples per verdict")
    source: str = "spec"


class SettingsSnapshot(BaseModel):
    """Everything that decided how the run ran."""

    schema_version: int = 1
    roles: list[RoleSettings] = Field(default_factory=list)
    budget: dict[str, Any] = Field(default_factory=dict, description="max_rounds / max_minutes / max_repair_attempts")
    candidates: int | None = Field(default=None, description="best-of-N baseline width, resolved")
    seed: int = 0
    rubric: str = ""
    rubric_hash: str = Field(default="", description="content hash of the judge rubric YAML")
    prompt_hashes: dict[str, str] = Field(default_factory=dict, description="contract / generate / refine / repair / skill:<name> / judge …")
    tool_versions: dict[str, str] = Field(default_factory=dict, description="python / blender / node / three / chrome / harness sha")
    harness_version: str = ""
    harness_git_sha: str = ""
    key_pool_size: int = Field(default=0, description="number of API keys in the rotating pool")
    price_table_version: str = Field(default="", description="content hash of codeverse3d.models.pricing.PRICES")
    render: dict[str, Any] = Field(default_factory=dict, description="resolved render settings (size, gpu, sheet)")
    limits: dict[str, Any] = Field(default_factory=dict, description="resolved timeouts / parallelism")


class StageCost(BaseModel):

    stage: str
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    thoughts_tokens: int = 0
    cost_usd: float = 0.0
    seconds: float = 0.0


class CostSummary(BaseModel):
    """``telemetry/cost.json``: the run's ledger rows summarised."""

    schema_version: int = 1
    total_usd: float = Field(default=0.0, description="the ledger's sum at list price = record.total_usage")
    minutes: float | None = Field(default=None, description="RunRecord.minutes when the record was written")
    max_minutes: float = 0.0
    n_calls: int = 0
    tokens: Usage = Field(default_factory=Usage, description="run totals (record.total_usage)")
    by_stage: list[StageCost] = Field(default_factory=list)
    by_role: dict[str, float] = Field(default_factory=dict, description="planner/generator/judge/… → USD")
    by_model: dict[str, float] = Field(default_factory=dict, description="model → USD")
    by_round: list[dict[str, Any]] = Field(default_factory=list, description="index / kind / cost_usd / minutes / score")


class RunTelemetry(BaseModel):

    schema_version: int = 1
    settings: SettingsSnapshot | None = None
    cost: CostSummary | None = None


# --------------------------------------------------------------------------- deliverable
class DeliverableFile(BaseModel):

    path: str = Field(description="run-relative path (under deliverable/)")
    role: str = Field(description="code | model | urdf | mesh | sheet | preview | frames | captions | bundle | manifest")
    bytes: int = 0
    sha256: str = ""


class RunDeliverable(BaseModel):
    """``deliverable/manifest.json``: the handed-over round as paths + hashes + sizes."""

    schema_version: int = 1
    dir: str = "deliverable"
    round: int | None = Field(default=None, description="the packaged round")
    commit: str = ""
    code_source: str = Field(default="", description="commit | working_tree")
    entry: str = Field(default="", description="run-relative entry file of the code snapshot")
    files: list[DeliverableFile] = Field(default_factory=list)
    total_bytes: int = 0
    generated_at: datetime | None = None

    def by_role(self, role: str) -> list[DeliverableFile]:
        return [f for f in self.files if f.role == role]


class RunRecord(BaseModel):
    """One run."""

    spec: Spec
    plan: StaticPlan | ArticulatedPlan | ScenePlan | GraphicsPlan | None = None
    workspace: str
    status: RunStatus = RunStatus.PLANNING
    rounds: list[RoundRecord] = Field(default_factory=list)
    total_usage: Usage = Field(default_factory=Usage)
    environment: dict[str, str] = Field(default_factory=dict, description="tool versions, git sha, host")
    prompt_hashes: dict[str, str] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    error: str = ""
    telemetry: RunTelemetry | None = Field(
        default=None, description="settings snapshot + cost summary (written by finalize_record)")
    steps: list[StepTime] = Field(
        default_factory=list, description="the run-level timed steps (plan, the pre-round stages, best-of-N, the part of "
                                  "an aborted round that ran)")
    extra: dict[str, Any] = Field(default_factory=dict)

    @property
    def minutes(self) -> float:
        """The run's minutes: ``wall_s - lost_s`` over every step.  A statistic, not the budget
        clock (``BudgetGuard`` counts provider errors too)."""
        return sum(s.active_s for s in self.steps) / 60.0 + sum(r.minutes for r in self.rounds)

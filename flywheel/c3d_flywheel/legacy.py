"""The v0.0.x run record (``record.json``) and everything stored inside it, read leniently.

The plugin no longer writes runs: these models exist to READ the recorded ones.  Spec, plan and the
artifact blocks are tolerant of what later versions dropped (``Budget``/``Backends`` were removed
from the plugin's contracts; the plan is read as plain data)."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any

from codeverse3d.contracts.artifacts import (
    BuildResult,
    GateReport,
    Judgment,
    Measurement,
    RenderSet,
)
from codeverse3d.contracts.common import ENTRY_FILE, Language, Track
from codeverse3d.contracts.spec import Spec
from pydantic import BaseModel, ConfigDict, Field

# ===================================================================== labels (the plugin keeps only the enums)
TRACK_LABEL: dict[Track, str] = {
    Track.STATIC_OBJECT: "3D Objects",
    Track.ARTICULATED_OBJECT: "Articulated Objects",
    Track.SCENE: "3D Scenes",
    Track.GRAPHICS: "Procedural Graphics",
}

LANGUAGE_LABEL: dict[Language, str] = {
    Language.BLENDER: "Blender Python",
    Language.CADQUERY: "CadQuery (Python)",
    Language.URDF_BLENDER: "URDF + Blender Python",
    Language.THREEJS: "Three.js (ESM)",
    Language.SCENE_THREEJS: "Three.js scene (multi-file ESM + GLSL)",
    Language.SCENE_BLENDER: "Blender scene (multi-file bpy)",
    Language.GLSL_SHADER: "GLSL fragment shader",
    Language.OPENGL_PYTHON: "OpenGL (moderngl Python + GLSL)",
}


def code_file(language: Language) -> str:
    """Top-level dataset copy of the entry file: ``code`` + the entry suffix."""
    entry = ENTRY_FILE[language]
    return "code" + entry[entry.rfind(".") :]


# ===================================================================== usage (one model / agent call)
class Usage(BaseModel):
    """Normalised token/cost accounting for ONE model or agent call, as v0.0.x recorded it."""

    backend: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    thoughts_tokens: int = 0
    tool_calls: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            backend=self.backend or other.backend,
            model=self.model if self.model == other.model else (self.model or other.model),
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cached_tokens=self.cached_tokens + other.cached_tokens,
            thoughts_tokens=self.thoughts_tokens + other.thoughts_tokens,
            tool_calls=self.tool_calls + other.tool_calls,
            cost_usd=self.cost_usd + other.cost_usd,
            latency_ms=self.latency_ms + other.latency_ms,
        )


# ===================================================================== spec + plan (as recorded)
class Budget(BaseModel):
    """The v0.0.x run ceilings (the plugin has no budget)."""

    max_rounds: int = 4
    max_minutes: float = 60.0
    max_repair_attempts: int = 3


class Backends(BaseModel):
    """Who did which job in a v0.0.x run (``<kind>:<model>`` ids)."""

    planner: str = "gemini:gemini-4-argon"
    generator: str = "agy:gemini-4-argon"
    judge: str = "gemini:gemini-4-argon"
    captioner: str = "gemini:gemini-4-argon"


class LegacySpec(Spec):
    budget: Budget = Field(default_factory=Budget)
    backends: Backends = Field(default_factory=Backends)
    tags: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AcceptanceItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    text: str = ""
    how: str = "visual"
    priority: str = "must"


class LegacyPlan(BaseModel):
    """The plan as the gallery and the dataset read it: a name, a part count, the acceptance checklist."""

    model_config = ConfigDict(extra="ignore")

    object_name: str = ""
    title: str = ""
    parts: list[Any] = Field(default_factory=list)
    acceptance: list[AcceptanceItem] = Field(default_factory=list)


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

    step: str = Field(description="plan | skeleton | assets+env+layouts | zones | assemble"
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

    roles: list[RoleSettings] = Field(default_factory=list)
    budget: dict[str, Any] = Field(default_factory=dict, description="max_rounds / max_minutes / max_repair_attempts")
    rubric: str = ""
    rubric_hash: str = Field(default="", description="content hash of the judge rubric YAML")
    prompt_hashes: dict[str, str] = Field(default_factory=dict, description="contract / generate / refine / repair / skill:<name> / judge …")
    tool_versions: dict[str, str] = Field(default_factory=dict, description="python / blender / node / three / chrome / harness sha")
    harness_version: str = ""
    harness_git_sha: str = ""
    key_pool_size: int = Field(default=0, description="number of API keys in the rotating pool")
    price_table_version: str = Field(default="", description="content hash of the price table the run was billed with")
    render: dict[str, Any] = Field(default_factory=dict, description="resolved render settings (size, gpu, sheet)")


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
    """The run's ledger rows summarised (``record.telemetry.cost``)."""

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

    round: int | None = Field(default=None, description="the packaged round")
    commit: str = ""
    entry: str = Field(default="", description="run-relative entry file of the code snapshot")
    files: list[DeliverableFile] = Field(default_factory=list)


class RunRecord(BaseModel):
    """One run."""

    spec: LegacySpec
    plan: LegacyPlan | None = None
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
        """The run's minutes: ``wall_s - lost_s`` over every step (provider errors excluded)."""
        return sum(s.active_s for s in self.steps) / 60.0 + sum(r.minutes for r in self.rounds)

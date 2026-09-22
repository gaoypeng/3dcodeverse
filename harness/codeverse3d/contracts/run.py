"""The run record — ``record.json`` — and everything stored inside it.

``RunRecord`` (rounds, totals, provenance: the flywheel unit), ``RunId`` (where a run
sits in a runs dir or a battery), the per-round skills usage, and the packaged
``telemetry/`` + ``deliverable/`` blocks.  The skills shapes live here rather than in
``codeverse3d/skills`` so ``contracts`` stays a leaf that ``record``, ``addons`` and the
CLI import without the router, and a stored record re-reads on a build with no skill
library at all.
"""

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
    """The read probe's verdict for one materialised bundle (design §6.4)."""

    name: str
    surfaced: bool = Field(default=False, description="transcript: the agent activated the skill or opened its "
                           "SKILL.md; atime fallback: SKILL.md atime > mtime (a discovery scan counts)")
    deep: bool = Field(default=False, description="transcript: the agent opened a file under its references/; "
                       "atime fallback: a references/*.md atime > mtime")
    deep_measurable: bool = Field(default=True, description="False when the bundle ships no references/ file to probe")
    body_tokens: int = 0
    reason: str = Field(default="", description="which route attached it, and which finding")


class SkillsUsage(BaseModel):
    """``RoundRecord.skills`` — listed vs read vs what it cost, per round.

    The metric that replaces "0 of 16 read_cookbook calls" is :attr:`deep_read_rate`.
    """

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
    def probe_trustworthy(self) -> bool:
        """False when the atime probe's control fired — the only honest reading of the numbers below.

        A run computes ``files_changed`` through ``git add -A -N`` + ``git diff``, and git
        reads every untracked file to do it, which bumps atime on the whole bundle tree.
        CLI activation opens ``references/`` too.  Either way the probe says "read" when
        nobody chose to read, and only the control can tell you which session you are in.
        Transcript evidence is the CLI's own record of what it called, so it is trusted as is.
        """
        if self.evidence == "transcript":
            return True
        return self.control_present and not self.control_read

    @property
    def surfaced(self) -> list[str]:
        return [r.name for r in self.reads if r.surfaced]

    @property
    def deep(self) -> list[str]:
        return [r.name for r in self.reads if r.deep]

    @property
    def deep_read_rate(self) -> float | None:
        """None when there is nothing listed, or when the atime control says the probe is blind."""
        if not self.listed or (self.evidence == "atime" and self.control_present and self.control_read):
            return None
        return len(self.deep) / len(self.listed)


# ===================================================================== run
# --------------------------------------------------------------------------- identity
#: battery-layout path segments that are pure plumbing, never part of a run's identity
RUN_PATH_NOISE = frozenset({"arms", "cells", "runs"})


class RunId(BaseModel):
    """Where a run directory sits inside its scan root — and the ONE name to call it.

    Run identity used to be ``ws.root.name`` everywhere, which is right for the flat
    ``runs/<id>`` layout and catastrophically wrong for nested batteries
    (compare_backends ``cells/<id>/<arm>/run``, ab_plan
    ``arms/<arm>/cells/<id>/<slug>/run``): hundreds of distinct runs are all named
    ``run``, so exports rmtree'd each other, the SQLite index tripped its PRIMARY KEY
    and the gallery collapsed whole batteries into one entry.

    ``battery`` labels the scan root the run was found under; ``rel`` is the run
    directory's posix path relative to that root.  Only the scanner still knows the
    root, so only the scanner can mint one of these
    (``record.record.run_id_for``)."""

    model_config = ConfigDict(frozen=True)

    battery: str = Field(description="label of the scan root this run was found under")
    rel: str = Field(description="posix path of the run dir relative to the scan root")

    def _parts(self) -> tuple[str, ...]:
        return PurePosixPath(self.rel).parts

    @property
    def slug(self) -> str:
        """The stable, filesystem-safe name of this run.

        * a flat ``rel`` (no parent directories — today's ``runs/<id>`` layout) keeps
          the basename UNCHANGED, so every existing dataset/index/side-car stays valid;
        * a nested ``rel`` joins the meaningful path segments with ``__``, dropping the
          structural noise (:data:`RUN_PATH_NOISE`) and a trailing ``run`` — e.g.
          ``cells/cmp_easy_stool/harness_api/run`` → ``cmp_easy_stool__harness_api``.
          ``.attemptN`` retry suffixes live on a kept segment and survive.

        Path segments cannot contain ``/``, so the join alone makes the slug a single
        path component; nothing else is mangled."""
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
        """The prompt/cell id segment (``cells/<cell>/…``) when the layout has one."""
        parts = self._parts()
        if "cells" in parts:
            i = parts.index("cells")
            if i + 1 < len(parts):
                return parts[i + 1]
        return None

    @property
    def arm(self) -> str | None:
        """The arm segment: ``arms/<arm>/…`` (ab_plan) or the directory between the
        cell and its ``run`` (compare_backends), when the layout has one."""
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
    """Where a run is — and once it has stopped, only WHY it stopped.  Never a verdict:
    since 2026-09-22 a run is the baseline plus ``max_rounds`` refine rounds, cut short
    only by the clock or a hard failure, and which round to hand over is a reader's
    question (``codeverse3d.addons.select``)."""

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
    #: a run recorded before 2026-09-22 that ended on a judgement stop (``passed`` / ``plateau``)
    STOPPED = "stopped"

    @classmethod
    def _missing_(cls, value: object) -> RunStatus | None:
        return cls.STOPPED if value in ("passed", "plateau") else None


class PairwiseNote(BaseModel):
    """What a tie-break compared and what it concluded.

    Lives on the round record because the comparison is a PAID judge call (~$0.05):
    ``rNN.json`` is the durable artifact, so a resume replays the verdict instead of
    re-ranking on score alone and silently reversing it
    (``tracks.candidates.replay_best_round``)."""

    a: str = Field(description="label of the incumbent (current best)")
    b: str = Field(description="label of the challenger (new round / other candidate)")
    winner: Literal["a", "b", "tie"] = "tie"
    confidence: float = 0.0
    accepted: bool = Field(default=False, description="True when the challenger replaces the incumbent")
    reasons: list[str] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    error: str = ""

    def line(self) -> str:
        verdict = {"a": f"{self.a} wins", "b": f"{self.b} wins", "tie": "tie"}[self.winner]
        return f"pairwise {self.a} vs {self.b}: {verdict} (confidence {self.confidence:.2f}) → {'replace' if self.accepted else 'keep'}"


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
    skills: SkillsUsage | None = Field(
        default=None, description="skills attached to this round's sessions, and which were actually read")
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    duration_s: float = 0.0
    notes: str = ""
    pairwise: PairwiseNote | None = Field(
        default=None, description="the paid tie-break verdict that ranked this round against the "
                                  "incumbent best, when one was bought; None = ranked on score alone")

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
    backend: str = Field(default="", description="gemini | anthropic | gemini-cli | codex | … (api-agent only in records written before 2026-08-28)")
    thinking: str = Field(default="", description="off | low | medium | high; '' = not recorded")
    temperature: float | None = None
    n_samples: int | None = Field(default=None, description="judge samples per verdict")
    source: str = "spec"


class SettingsSnapshot(BaseModel):
    """Everything that decided HOW the run ran — the 'key step settings' block."""

    schema_version: int = 1
    roles: list[RoleSettings] = Field(default_factory=list)
    budget: dict[str, Any] = Field(default_factory=dict, description="max_rounds / max_minutes / max_repair_attempts")
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
    price_table_version: str = Field(default="", description="content hash of codeverse3d.models.pricing.PRICES")
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

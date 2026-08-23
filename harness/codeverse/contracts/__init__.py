"""Typed contracts shared across the harness.  Data only — no behaviour.

Modules:
- ``common``    enums, vectors, usage/cost.
- ``spec``      what the user asked for (track, language, prompt, budget, backends).
- ``plan``      what the planner decided (parts / joints / zones / acceptance).
- ``artifacts`` what tools produced (measurements, renders, gate reports, builds).
- ``judgment``  what judges said (scores, issues, improvement plan).
- ``run``       the run record (rounds, totals, provenance) — the flywheel unit.
- ``chat``      ChatModel request/response shapes.
- ``agent``     CodingAgent job/result shapes.
"""

from codeverse.contracts.agent import AgentJob, AgentResult, FileChange
from codeverse.contracts.artifacts import (
    BuildResult,
    GateFinding,
    GateReport,
    Measurement,
    PartMeasure,
    RenderSet,
    RenderView,
    Severity,
)
from codeverse.contracts.chat import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ImagePart,
    TextPart,
    ToolCallPart,
    ToolResultPart,
    ToolSpec,
)
from codeverse.contracts.common import Backends, Budget, Language, Track, Usage, Vec3
from codeverse.contracts.judgment import ImprovementItem, JudgeIssue, Judgment
from codeverse.contracts.plan import (
    AcceptanceItem,
    ArticulatedPlan,
    AssetPlan,
    BBox,
    CameraPlan,
    EffectPlan,
    GraphicsPlan,
    JointPlan,
    PassPlan,
    PartPlan,
    Plan,
    ScenePlan,
    StaticPlan,
    ZonePlan,
)
from codeverse.contracts.run import RoundRecord, RunRecord, RunStatus
from codeverse.contracts.spec import Constraints, ReferenceImage, Spec

__all__ = [name for name in dir() if not name.startswith("_")]

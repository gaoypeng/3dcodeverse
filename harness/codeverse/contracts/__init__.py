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

from codeverse.contracts.agent import AgentJob, AgentResult, ApiAgentOptions, FileChange
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
from codeverse.contracts.common import (
    ENTRY_FILE,
    LANGUAGE_LABEL,
    TRACK_INFO,
    TRACK_LANGUAGES,
    Backends,
    Budget,
    Language,
    Track,
    TrackInfo,
    Usage,
    Vec3,
    code_file,
)
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
    PartPlan,
    PassPlan,
    Plan,
    ScenePlan,
    StaticPlan,
    ZonePlan,
)
from codeverse.contracts.run import RoundRecord, RunRecord, RunStatus
from codeverse.contracts.spec import Constraints, ReferenceImage, RunOptions, Spec

__all__ = [
    "AcceptanceItem",
    "AgentJob",
    "AgentResult",
    "ApiAgentOptions",
    "ArticulatedPlan",
    "AssetPlan",
    "BBox",
    "Backends",
    "Budget",
    "BuildResult",
    "CameraPlan",
    "ChatMessage",
    "ChatRequest",
    "ChatResponse",
    "Constraints",
    "ENTRY_FILE",
    "EffectPlan",
    "FileChange",
    "GateFinding",
    "GateReport",
    "GraphicsPlan",
    "ImagePart",
    "ImprovementItem",
    "JointPlan",
    "JudgeIssue",
    "Judgment",
    "LANGUAGE_LABEL",
    "Language",
    "Measurement",
    "PartMeasure",
    "PartPlan",
    "PassPlan",
    "Plan",
    "ReferenceImage",
    "RenderSet",
    "RenderView",
    "RoundRecord",
    "RunOptions",
    "RunRecord",
    "RunStatus",
    "ScenePlan",
    "Severity",
    "Spec",
    "StaticPlan",
    "TRACK_INFO",
    "TRACK_LANGUAGES",
    "TextPart",
    "ToolCallPart",
    "ToolResultPart",
    "ToolSpec",
    "Track",
    "TrackInfo",
    "Usage",
    "Vec3",
    "ZonePlan",
    "code_file",
]

"""Judge outputs: rubric scores + structured, targeted improvement plan."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from codeverse.contracts.common import Usage


class JudgeIssue(BaseModel):
    target: str = Field(description="part / zone / joint / asset / 'overall'")
    kind: Literal[
        "geometry", "proportion", "detail", "material", "assembly", "articulation",
        "lighting", "composition", "animation", "effect", "fidelity", "scale", "bug",
    ]
    severity: Literal["critical", "major", "minor"]
    detail: str = Field(description="what is wrong, observable in the renders")
    evidence: str = Field(default="", description="which view(s) / measurement show it")


class ImprovementItem(BaseModel):
    target: str
    kind: Literal["geometry", "material", "assembly", "articulation", "lighting", "composition", "animation", "effect", "bug"]
    instruction: str = Field(description="concrete, actionable change for the builder")
    priority: int = Field(ge=1, le=5, description="1 = do first")
    expected_gain: float = Field(default=0.0, ge=0.0, le=1.0)


class Judgment(BaseModel):
    rubric: str
    judge_backend: str = ""
    scores: dict[str, float] = Field(description="criterion → 0..1")
    overall: float = Field(ge=0.0, le=1.0, description="weighted by the rubric (computed in code)")
    passed: bool
    summary: str = ""
    strengths: list[str] = Field(default_factory=list)
    issues: list[JudgeIssue] = Field(default_factory=list)
    improvement_plan: list[ImprovementItem] = Field(default_factory=list)
    acceptance_results: dict[str, bool] = Field(default_factory=dict, description="acceptance item id → verified")
    n_samples: int = 1
    score_std: float = 0.0
    usage: Usage = Field(default_factory=Usage)
    raw: str = ""

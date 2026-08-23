"""Judge protocol + inputs."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field

from codeverse.contracts.artifacts import GateReport, Measurement, RenderSet
from codeverse.contracts.judgment import Judgment
from codeverse.contracts.plan import AcceptanceItem
from codeverse.contracts.spec import Spec


class JudgeInput(BaseModel):
    spec: Spec
    renders: RenderSet
    measurement: Measurement | None = None
    gates: list[GateReport] = Field(default_factory=list)
    acceptance: list[AcceptanceItem] = Field(default_factory=list)
    plan_summary: str = Field(default="", description="short plan digest (parts/zones/joints) for grounding")
    round_index: int = 0
    previous: Judgment | None = Field(default=None, description="last verdict (for delta framing)")
    extra_context: str = ""


class Judge(Protocol):
    name: str

    def judge(self, inp: JudgeInput) -> Judgment: ...

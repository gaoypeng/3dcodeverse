"""The Spec: everything the user asked for, frozen at run start."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator

from codeverse3d.contracts.common import TRACK_LANGUAGES, Backends, Budget, Language, Track


class ReferenceImage(BaseModel):
    path: str
    role: str = "target"  # target | style | detail
    note: str = ""


class Constraints(BaseModel):
    """Optional hard requirements the user states up front.  Each becomes an
    acceptance item the planner must carry and a gate can verify."""

    dimensions_m: dict[str, float] | None = Field(
        default=None, description="e.g. {'width': 1.2, 'depth': 0.6, 'height': 0.75}"
    )
    style: str = ""
    must_have: list[str] = Field(default_factory=list)
    must_not: list[str] = Field(default_factory=list)


class ProjectCheck(BaseModel):
    """A deterministic check the PROJECT registers (``Spec.checks``): ``entry`` names a function
    (``package.module:function``, importable where the harness runs) that measures each round's GLB against
    what the project knows — survey geometry, catalogue dimensions — and returns ``GateReport``s.  They take part
    exactly like the built-in gates under the name ``project:<name>``: ERRORs become refine tasks, the judge reads
    them, and a rubric ``CapRule(when="gate", gate="project:*")`` caps on them (``tracks/project_checks.py``)."""

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_]*$", description="gate suffix: reports are named project:<name>")
    entry: str = Field(pattern=r"^[A-Za-z_][\w.]*:[A-Za-z_]\w*$", description="'package.module:function'")
    args: dict[str, Any] = Field(default_factory=dict, description="keyword data passed to the check as args=")


class RunOptions(BaseModel):
    """Run-shape options frozen on the spec (distinct from Budget and Backends).

    ``None`` / ``False`` means "not stated": the resolver falls through to its
    ``Settings`` default (``BaseTrack._resolve_candidates``).
    """

    candidates: int | None = Field(default=None, ge=1, description="best-of-N baseline candidates")
    variant: str = Field(default="", description="per-model prompt/skill variant this run was created with "
                         "(codeverse3d/prompts/variants/<name>/, D105); '' = the base prompts and skills; "
                         "frozen here so a resume and the record name the same overlay")


class Spec(BaseModel):
    id: str
    track: Track
    language: Language
    prompt: str
    references: list[ReferenceImage] = Field(default_factory=list)
    constraints: Constraints = Field(default_factory=Constraints)
    # left out of spec.json while empty, so every spec without one serialises exactly as before
    checks: list[ProjectCheck] = Field(default_factory=list, exclude_if=lambda v: not v,
                                       description="project checks run on every round's GLB (ProjectCheck)")
    budget: Budget = Field(default_factory=Budget)
    backends: Backends = Field(default_factory=Backends)
    options: RunOptions = Field(default_factory=RunOptions)
    seed: int = 0
    tags: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def _language_fits_track(self) -> Spec:
        allowed = TRACK_LANGUAGES[self.track]
        if self.language not in allowed:
            raise ValueError(
                f"language {self.language} is not valid for track {self.track}; "
                f"allowed: {[a.value for a in allowed]}"
            )
        return self

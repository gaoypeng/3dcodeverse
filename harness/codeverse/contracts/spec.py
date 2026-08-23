"""The Spec: everything the user asked for, frozen at run start."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field, model_validator

from codeverse.contracts.common import TRACK_LANGUAGES, Backends, Budget, Language, Track


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
    max_triangles: int | None = None
    style: str = ""
    must_have: list[str] = Field(default_factory=list)
    must_not: list[str] = Field(default_factory=list)


class RunOptions(BaseModel):
    """Run-shape options frozen on the spec (distinct from Budget and Backends).

    ``None`` / ``False`` means "not stated": resolution falls through to
    ``run_state.extra`` (legacy runs) and then ``Settings`` defaults.
    """

    candidates: int | None = Field(default=None, ge=1, description="best-of-N baseline candidates")
    texture: bool = Field(default=False, description="run the derived texture pass after the loop")


class Spec(BaseModel):
    id: str
    track: Track
    language: Language
    prompt: str
    references: list[ReferenceImage] = Field(default_factory=list)
    constraints: Constraints = Field(default_factory=Constraints)
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

"""The Spec: everything the user asked for, frozen at run start."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field, model_validator

from codeverse3d.contracts.common import TRACK_LANGUAGES, Backends, Budget, Language, Track, Vec3


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


class FixedPart(BaseModel):
    """A file the PROJECT seeds into ``src/`` that no session may rewrite: measured geometry (a survey shell), the
    data it reads, a shared library.  The planner is told it is given and does not plan it; the agent imports it
    unchanged (an edit is reverted, like a language's ``HARNESS_OWNED_SRC``); the skeleton keeps it; and when it
    builds a part, the ``contract_fixed`` gate measures that part against ``bbox_min``/``bbox_max``.

    Measured, 2026-11-02 (codecity3d dry run 5): told only in the prompt, one agent cut a seeded chimney to pass a
    selfcheck sized to the spec, another rescaled the whole seeded shell 0.9 m to fit the box the planner had
    invented for it — the contract gate rewarded that."""

    path: str = Field(description="workspace-relative file under src/, e.g. 'src/parts/survey_shell.js'")
    name: str = Field(default="", description="the GLB part it builds (a child of the root, e.g. 'SurveyShell'); "
                      "'' for a data or library file")
    export: str = Field(default="", description="what the agent imports from it, e.g. 'buildSurveyShell'")
    bbox_min: Vec3 | None = Field(default=None, description="the built part's box, GLB frame (Y-up, +Z front), m")
    bbox_max: Vec3 | None = None
    note: str = Field(default="", description="one line for the planner and the agent")


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
    fixed_parts: list[FixedPart] = Field(default_factory=list, exclude_if=lambda v: not v,
                                         description="project-seeded files no session may rewrite (FixedPart)")
    budget: Budget = Field(default_factory=Budget)
    backends: Backends = Field(default_factory=Backends)
    options: RunOptions = Field(default_factory=RunOptions)
    seed: int = 0
    tags: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def _fixed_parts_are_sources(self) -> Spec:
        for f in self.fixed_parts:
            parts = f.path.replace("\\", "/").split("/")
            if parts[0] != "src" or ".." in parts or len(parts) < 2:
                raise ValueError(f"fixed part {f.path!r}: must be a file under src/")
            if (f.bbox_min is None) != (f.bbox_max is None):
                raise ValueError(f"fixed part {f.path!r}: give both bbox_min and bbox_max, or neither")
        return self

    @model_validator(mode="after")
    def _language_fits_track(self) -> Spec:
        allowed = TRACK_LANGUAGES[self.track]
        if self.language not in allowed:
            raise ValueError(
                f"language {self.language} is not valid for track {self.track}; "
                f"allowed: {[a.value for a in allowed]}"
            )
        return self

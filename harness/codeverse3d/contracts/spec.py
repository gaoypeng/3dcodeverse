"""The Spec: everything the user asked for, frozen at run start."""

from __future__ import annotations

import posixpath
from datetime import UTC, datetime
from typing import Any

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
    builds a part, the ``contract_fixed`` gate measures that part against ``bbox_min``/``bbox_max``."""

    path: str = Field(description="workspace-relative file under src/, e.g. 'src/parts/survey_shell.js'")
    name: str = Field(default="", description="the GLB part it builds (a child of the root, e.g. 'SurveyShell'); "
                      "'' for a data or library file")
    export: str = Field(default="", description="what the agent imports from it, e.g. 'buildSurveyShell'")
    bbox_min: Vec3 | None = Field(default=None, description="the built part's box, GLB frame (Y-up, +Z front), m")
    bbox_max: Vec3 | None = None
    note: str = Field(default="", description="one line for the planner and the agent")


#: the gates that measure the project's own truth: Spec.checks report as project:<name>, Spec.fixed_parts as contract_fixed
PROJECT_GATE_PREFIX = "project:"
FIXED_PARTS_GATE = "contract_fixed"


class ProjectCheck(BaseModel):
    """A deterministic check the PROJECT registers (``Spec.checks``): ``entry`` names a function
    (``package.module:function``, importable where the harness runs) that measures each round's GLB against
    what the project knows — survey geometry, catalogue dimensions — and returns ``GateReport``s.  They take part
    exactly like the built-in gates under the name ``project:<name>``: ERRORs become refine tasks, the judge reads
    them, and a rubric ``CapRule(when="gate", gate="project:*")`` caps on them (``tracks/project_checks.py``)."""

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_]*$", description="gate suffix: reports are named project:<name>")
    entry: str = Field(pattern=r"^[A-Za-z_][\w.]*:[A-Za-z_]\w*$", description="'package.module:function'")
    args: dict[str, Any] = Field(default_factory=dict, description="keyword data passed to the check as args=")


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
    checks: list[ProjectCheck] = Field(default_factory=list, exclude_if=lambda v: not v,
                                       description="project checks run on every round's GLB (ProjectCheck)")
    budget: Budget = Field(default_factory=Budget)
    backends: Backends = Field(default_factory=Backends)
    tags: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def _fixed_parts_are_sources(self) -> Spec:
        if (self.fixed_parts or self.checks) and self.track not in (Track.STATIC_OBJECT, Track.ARTICULATED_OBJECT):
            raise ValueError(f"fixed_parts and checks are measured on the object tracks only, not {self.track.value}")
        for f in self.fixed_parts:
            f.path = posixpath.normpath(f.path.replace("\\", "/"))
            parts = f.path.split("/")
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

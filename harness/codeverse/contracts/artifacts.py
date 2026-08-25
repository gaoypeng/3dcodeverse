"""What deterministic tools produce: builds, measurements, renders, gates."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from codeverse.contracts.common import Vec3


class Severity(StrEnum):
    ERROR = "error"  # blocks acceptance; routed to a repair task
    WARN = "warn"  # shown to judge/agent; does not block
    INFO = "info"


class GateFinding(BaseModel):
    gate: str
    severity: Severity
    target: str | None = Field(default=None, description="part / zone / joint / file this is about")
    message: str
    fix_hint: str = Field(default="", description="concrete, copyable instruction for the fixer")
    data: dict[str, Any] = Field(default_factory=dict)

    def as_line(
        self,
        *,
        with_gate: bool = False,
        with_severity: bool = False,
        with_target: bool = False,
        with_hint: bool = True,
    ) -> str:
        """One presentation line for prompts/reports — no leading ``- `` (callers
        own their list bullets).  ``message`` is always present; the flags opt in
        to a ``GATE <gate>:`` prefix, a ``[<severity>]`` tag, a `` [<target>]``
        suffix and the `` FIX: <fix_hint>`` suffix (default on, when non-empty)."""
        parts: list[str] = []
        if with_gate:
            parts.append(f"GATE {self.gate}:")
        if with_severity:
            parts.append(f"[{self.severity.value}]")
        parts.append(self.message)
        line = " ".join(parts)
        if with_target and self.target:
            line += f" [{self.target}]"
        if with_hint and self.fix_hint:
            line += f" FIX: {self.fix_hint}"
        return line


class GateReport(BaseModel):
    gate: str
    passed: bool
    findings: list[GateFinding] = Field(default_factory=list)
    duration_ms: int = 0

    @property
    def errors(self) -> list[GateFinding]:
        return [f for f in self.findings if f.severity == Severity.ERROR]


class BuildResult(BaseModel):
    """Result of executing the raw code and exporting the canonical artifact."""

    ok: bool
    language: str
    glb_path: str | None = None
    extra_paths: dict[str, str] = Field(default_factory=dict, description="stl/step/urdf/blend ...")
    stdout_tail: str = ""
    stderr_tail: str = ""
    error_type: str = ""
    error_message: str = ""
    error_file: str = ""
    error_line: int | None = None
    duration_ms: int = 0
    census: dict[str, Any] = Field(default_factory=dict, description="language-native census (objects, materials ...)")


class PartMeasure(BaseModel):
    name: str
    bbox_min: Vec3
    bbox_max: Vec3
    tri_count: int = 0
    islands: int = 1
    volume_m3: float | None = None
    watertight: bool | None = None


class Measurement(BaseModel):
    """Language-agnostic census computed from the canonical GLB (Y-up, meters)."""

    bbox_min: Vec3
    bbox_max: Vec3
    extents: Vec3
    center: Vec3
    tri_count: int
    n_meshes: int
    n_islands: int
    parts: list[PartMeasure] = Field(default_factory=list)
    ground_gap_m: float = Field(default=0.0, description="lowest point minus 0 (negative = below ground)")
    footprint_offset_m: float = Field(default=0.0, description="xz distance of bbox centre from origin")
    materials: int = 0
    frame: str = "y_up_pos_z_front"
    extra: dict[str, Any] = Field(default_factory=dict)


class RenderView(BaseModel):
    name: str
    path: str
    mode: str = "shaded"  # shaded | wire | normals | silhouette | depth | clay
    width: int = 0
    height: int = 0
    camera_position: Vec3 | None = None
    look_at: Vec3 | None = None
    fov: float | None = None
    time_s: float | None = Field(default=None, description="animation time for scenes")
    judge: bool | None = Field(
        default=None,
        description="True/False once the judge subset is stamped at render time; None = not stamped (legacy rounds)",
    )


class RenderSet(BaseModel):
    views: list[RenderView] = Field(default_factory=list)
    contact_sheet: str | None = Field(default=None, description="one labelled grid image of all views")
    turntable: str | None = None
    renderer: str = ""
    duration_ms: int = 0
    console_errors: list[str] = Field(default_factory=list, description="(scenes) JS/WebGL errors seen")
    fps: float | None = None
    out_dir: str = Field(default="", description="directory the views (+ views.json/metrics.json) were written to")

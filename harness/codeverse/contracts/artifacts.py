"""What deterministic tools produce: builds, measurements, renders, gates."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

from codeverse.contracts.common import Usage, Vec3


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


#: every mode the object rig draws (runtime_js/render_glb.mjs MODES, pinned by
#: tests/spatial_tools/test_tools.py).  The tool layer advertised "depth" from the
#: first commit; no renderer ever had it.
RENDER_MODES = ("shaded", "wire", "normals", "silhouette", "clay")


class RenderView(BaseModel):
    name: str
    path: str
    mode: str = "shaded"
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


#: A renderer string that means "no hardware did this".  Same words as
#: `runtime_js/gpu_launch.cjs` SOFTWARE_RE, which is what decides whether a GPU attempt
#: is trusted; kept here because `fps` is only a fact about the scene when hardware
#: measured it.
SOFTWARE_RENDERERS = ("swiftshader", "llvmpipe", "softpipe", "software", "basic render")


class RenderSet(BaseModel):
    views: list[RenderView] = Field(default_factory=list)
    contact_sheet: str | None = Field(default=None, description="one labelled grid image of all views")
    renderer: str = ""
    duration_ms: int = 0
    console_errors: list[str] = Field(default_factory=list, description="(scenes) JS/WebGL errors seen")
    fps: float | None = None
    out_dir: str = Field(default="", description="directory the views (+ views.json/metrics.json) were written to")

    @property
    def software_rendered(self) -> bool:
        """True when these pixels came from a CPU rasteriser.

        Measured on bench/out/scene_baseline (2026-09-05): with the box's eight GPUs at
        ~100 % from other work, the GPU probe's negative verdict is cached for 20 minutes,
        so cells fell back to SwiftShader one at a time and `fps` was measured on a
        DIFFERENT renderer per cell — 11.5 fps on an RTX 6000 Ada for one, 5.1 / 7.1 / 2.0
        on SwiftShader for the next three.  A CPU number is not comparable with a GPU one
        and is not a property of the scene, so nothing may gate or judge on it.
        """
        low = self.renderer.lower()
        return any(word in low for word in SOFTWARE_RENDERERS)

    @property
    def hardware_fps(self) -> float | None:
        """`fps` when hardware measured it, else None — the only form worth reporting."""
        return None if (self.fps is None or self.software_rendered) else self.fps


# ===================================================================== judgment
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

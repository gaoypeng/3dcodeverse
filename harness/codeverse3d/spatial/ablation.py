"""Effect-presence measurement for ``scene_threejs``: what fraction of the
delivered frame the scene's custom shaders actually paint.

The scene track can already tell whether a shader COMPILES (``check_shaders``)
and how many custom materials exist (``census.custom_materials``).  Neither
answers the question the graphics work is actually about: is the effect in the
picture?  A shader that is behind the camera, occluded, alpha-zero or quietly
replaced by a flat material after one compiler error passes both checks and
delivers nothing.

The answer is a counterfactual, measured in code (law 3): render each camera as
authored, and again with every ShaderMaterial / ``onBeforeCompile`` patch
replaced by a neutral material of the same base colour, and diff the pixels.
Zero change means the effect is not there.  Per material — leave one out at a
time — the same diff says WHICH shader carries the picture.

Reproducibility, measured on the starter scene: bit-identical within one process
(three runs, same fractions, same PNG hashes) and stable to ~1e-4 across separate
browser contexts (one camera moved 0.3820 -> 0.3819, 14 pixels of 147 456) — two
orders of magnitude under ``PRESENT_FLOOR``, so no verdict turns on it.

``ablate_scene(ws)`` drives ``runtime_js/ablate_scene.mjs`` (page-side logic in
``runtime_js/lib/host_ablation.mjs``) and returns a typed
:class:`AblationReport`; ``merge_into_census`` folds the compact form into
``artifacts/census.json`` under ``effect_ablation``, where the gates, the judge
context and the agent's own ``check_placement``-style reads already look.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from codeverse3d.proc import read_json_or_none
from codeverse3d.spatial.render_scene import (
    SceneRenderError,
    post_chain_args,
    probe_env_args,
    run_scene_script,
)
from codeverse3d.workspace import Workspace

#: census.json key the compact report lands under
CENSUS_FIELD = "effect_ablation"
#: mid-phase of the animated second the scene track judges (never a synchronised extreme)
DEFAULT_TIME_S = 1.5
#: changed-pixel fraction below which an effect is not in the frame.  0.5 % of a
#: 512x288 readback is ~740 pixels — comfortably above the renderer's own noise
#: floor (measured 0.0000: the leave-one-out pass for a shader placed BEHIND the
#: camera is a full second render and differs from the authored frame in zero
#: pixels) and below any effect worth authoring.
PRESENT_FLOOR = 0.005
#: leave-one-out is linear in materials; past this the answer is already "yes"
MAX_MATERIALS = 8


class CameraAblation(BaseModel):
    """One camera's authored-vs-ablated frame difference."""

    camera: str
    changed_frac: float = Field(description="fraction of pixels the custom shaders change")


class MaterialAblation(BaseModel):
    """One custom material's leave-one-out contribution (``changed_frac`` None = not measured)."""

    material: str
    type: str = ""
    kind: str = Field(default="", description="ShaderMaterial | onBeforeCompile")
    on: str = ""
    meshes: int = 0
    backdrop: str = Field(default="content", description="sky | ground | content (shared backdrop rules)")
    changed_frac: float | None = None
    camera: str = Field(default="", description="the camera the contribution was measured on")


class AblationReport(BaseModel):
    """Typed result of one ablation run over a workspace."""

    ok: bool = False
    custom_materials: int = 0
    cameras: list[CameraAblation] = Field(default_factory=list)
    materials: list[MaterialAblation] = Field(default_factory=list)
    max_changed_frac: float = 0.0
    content_changed_frac: float = 0.0
    per_material_measured: bool = False
    per_material_cameras: list[str] = Field(default_factory=list)
    time_s: float = DEFAULT_TIME_S
    images: list[str] = Field(default_factory=list, description="authored/ablated PNG pairs (absolute paths)")
    console_errors: list[str] = Field(default_factory=list)
    error: str = ""
    duration_ms: int = 0

    @property
    def present(self) -> bool:
        """The custom shaders visibly change the frame in at least one camera."""
        return self.max_changed_frac >= PRESENT_FLOOR

    def top_materials(self, n: int = 5) -> list[MaterialAblation]:
        """Measured materials, largest contribution first."""
        rows = [m for m in self.materials if m.changed_frac is not None]
        return sorted(rows, key=lambda m: (-(m.changed_frac or 0.0), m.material))[:n]

    def census_field(self) -> dict[str, Any]:
        """The compact form written into ``census.json`` (``effect_ablation``)."""
        return {
            "custom_materials": self.custom_materials,
            "max_changed_frac": round(self.max_changed_frac, 4),
            "content_changed_frac": round(self.content_changed_frac, 4),
            "present": self.present,
            "present_floor": PRESENT_FLOOR,
            "time_s": self.time_s,
            "per_camera": {c.camera: c.changed_frac for c in self.cameras},
            "top_materials": [
                {"material": m.material, "kind": m.kind, "backdrop": m.backdrop,
                 "changed_frac": m.changed_frac, "camera": m.camera}
                for m in self.top_materials()
            ],
        }

    def summary_lines(self) -> list[str]:
        """What the agent reads: the headline, then per camera, then per material."""
        if self.error:
            return [f"ablation could not measure the scene: {self.error}"]
        if self.custom_materials == 0:
            return ["no custom shader materials in the scene (no ShaderMaterial, no onBeforeCompile patch) — "
                    "every surface is a stock material, so the effect contributes 0% by construction"]
        head = (f"custom shaders paint {self.max_changed_frac:.1%} of the frame "
                f"({self.custom_materials} custom material(s), t={self.time_s:g} s)")
        if not self.present:
            head += " — NOT VISIBLE: removing every custom shader changes (almost) nothing"
        lines = [head]
        lines.append("per camera: " + ", ".join(f"{c.camera}={c.changed_frac:.1%}" for c in self.cameras))
        if self.per_material_measured:
            lines.append("per material (leave-one-out over "
                         + ", ".join(self.per_material_cameras) + ", best frame each):")
            for m in self.top_materials():
                where = f", best in {m.camera}" if m.camera else ""
                flag = "" if (m.changed_frac or 0.0) >= PRESENT_FLOOR else "   <- in no camera's frame"
                lines.append(f"  {m.changed_frac:.1%}  {m.material} "
                             f"[{m.kind}, {m.backdrop}, {m.meshes} mesh(es){where}]{flag}")
        elif self.custom_materials > MAX_MATERIALS:
            lines.append(f"per-material contribution skipped ({self.custom_materials} materials > {MAX_MATERIALS})")
        return lines


def ablate_scene(
    ws: Workspace,
    *,
    out_dir: Path | None = None,
    time_s: float = DEFAULT_TIME_S,
    frames: bool = True,
    timeout_s: float = 120.0,
) -> AblationReport:
    """Measure how much of the frame the workspace's custom shaders paint.

    ``out_dir`` (default ``artifacts/ablation``) receives ``ablation.json`` and,
    with ``frames``, one authored/ablated PNG pair per camera.  A scene that does
    not boot yields ``ok=False`` with ``error`` set — the build gate is where a
    broken scene is reported, this instrument only declines to measure it.  A
    driver failure raises :class:`SceneRenderError`.
    """
    t0 = time.time()
    out = Path(out_dir) if out_dir else ws.artifacts / "ablation"
    out.mkdir(parents=True, exist_ok=True)
    args = [
        "--ws", str(ws.root), "--out", str(out), "--t", f"{time_s:g}",
        "--max-materials", str(MAX_MATERIALS),
        "--timeout-ms", str(int(timeout_s * 1000)),
    ]
    if frames:
        args.append("--frames")
    args += probe_env_args() + post_chain_args()   # the judge's frames: same repair / settle / post policy
    res = run_scene_script("ablate_scene.mjs", args, timeout_s=timeout_s + 20)
    return _report(res.summary, out, duration_ms=int((time.time() - t0) * 1000))


def _report(summary: dict[str, Any], out_dir: Path, *, duration_ms: int = 0) -> AblationReport:
    """Interpret an ``ablate_scene.mjs`` summary.  Pure over the driver JSON."""
    images: list[str] = []
    for f in summary.get("frame_files") or []:
        for key in ("authored", "ablated"):
            p = out_dir / str(f.get(key) or "")
            if f.get(key) and p.is_file():
                images.append(str(p))
    return AblationReport(
        ok=bool(summary.get("ok")),
        custom_materials=int(summary.get("custom_materials") or 0),
        cameras=[CameraAblation.model_validate(c) for c in summary.get("cameras") or []],
        materials=[MaterialAblation.model_validate(m) for m in summary.get("materials") or []],
        max_changed_frac=float(summary.get("max_changed_frac") or 0.0),
        content_changed_frac=float(summary.get("content_changed_frac") or 0.0),
        per_material_measured=bool(summary.get("per_material_measured")),
        per_material_cameras=[str(c) for c in summary.get("per_material_cameras") or []],
        time_s=float(summary.get("time_s") or DEFAULT_TIME_S),
        images=images,
        console_errors=[str(e)[:400] for e in (summary.get("console_errors") or [])][:10],
        error=str(summary.get("error") or ""),
        duration_ms=duration_ms or int(summary.get("duration_ms") or 0),
    )


def merge_into_census(ws: Workspace, report: AblationReport) -> bool:
    """Write ``report.census_field()`` into ``artifacts/census.json`` under
    ``effect_ablation``.  Returns False when there is no census to merge into (a
    workspace that has not been built) or it could not be rewritten — the
    measurement is still returned to the caller either way."""
    path = ws.artifacts / "census.json"
    census = read_json_or_none(path)
    if not isinstance(census, dict):
        return False
    census[CENSUS_FIELD] = report.census_field()
    try:
        path.write_text(json.dumps(census, indent=1))
    except OSError:
        return False
    return True


__all__ = [
    "CENSUS_FIELD", "DEFAULT_TIME_S", "MAX_MATERIALS", "PRESENT_FLOOR",
    "AblationReport", "CameraAblation", "MaterialAblation",
    "SceneRenderError", "ablate_scene", "merge_into_census",
]

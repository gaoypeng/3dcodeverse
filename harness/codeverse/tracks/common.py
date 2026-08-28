"""Shared track machinery: the run context, lazily-bound services, prompt context.

``Services`` is the single seam between the tracks and the other packages
(models, agents, runtimes, spatial tools, judges, flywheel).  Every method
imports lazily and raises a clear ``ServiceUnavailable`` when the package is
missing; tests subclass it with fakes.  Nothing here touches a network.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from codeverse.config import Settings
from codeverse.contracts.artifacts import GateReport, Measurement, RenderSet, RenderView
from codeverse.contracts.common import Language, Track
from codeverse.contracts.plan import CameraPlan, Plan
from codeverse.contracts.run import RunRecord
from codeverse.conventions import ViewPreset
from codeverse.languages._docs import prompt_dir_for
from codeverse.orchestrator import BudgetGuard, RoundPolicy, RunState
from codeverse.proc import EventLog
from codeverse.prompts import load_text, prompt_hash
from codeverse.tracks.generation import is_single_shot
from codeverse.workspace import Workspace

log = logging.getLogger(__name__)


class ServiceUnavailable(RuntimeError):
    """A sibling package this track depends on is not importable / not configured."""


def _import(path: str, name: str) -> Any:
    import importlib

    try:
        mod = importlib.import_module(path)
    except ImportError as e:
        raise ServiceUnavailable(f"{path} is not available ({e}); cannot call {name}") from e
    try:
        return getattr(mod, name)
    except AttributeError as e:
        raise ServiceUnavailable(f"{path}.{name} does not exist") from e


class Services:
    """Lazy adapters to sibling packages.  Override any method in tests."""

    # ---- models / agents / judges / runtimes
    def chat_model(self, model_id: str) -> Any:
        return _import("codeverse.models", "get_chat_model")(model_id)

    def coding_agent(self, agent_id: str) -> Any:
        return _import("codeverse.agents", "get_coding_agent")(agent_id)

    def judge(self, rubric: str, model_id: str, n_samples: int = 1) -> Any:
        return _import("codeverse.judges.vlm_judge", "VlmJudge")(rubric=rubric, model_id=model_id, n_samples=n_samples)

    def reference_judge(self, model_id: str, n_samples: int = 1, rubric: str = "reference_v1") -> Any:
        """Image-conditioned judge (spec has reference images): renders + references + silhouette IoU."""
        return _import("codeverse.judges.vlm_judge", "ReferenceJudge")(model_id=model_id, n_samples=n_samples, rubric=rubric)

    def likeness_judge(self, model_id: str, n_samples: int = 1, rubric: str = "shader_v2") -> Any:
        """Reference photos beside the frames, no silhouette (graphics / scene with images)."""
        return _import("codeverse.judges.vlm_judge", "LikenessJudge")(model_id=model_id, n_samples=n_samples, rubric=rubric)

    def pairwise(self, model_id: str) -> Any:
        """Position-swapped A/B judge: ``.compare(spec, renders_a, renders_b, rubric=...)``."""
        return _import("codeverse.judges.pairwise", "PairwiseJudge")(model_id)

    def runtime(self, language: Language) -> Any:
        return _import("codeverse.languages", "get_runtime")(language)

    def rubric_threshold(self, rubric: str) -> float | None:
        try:
            r = _import("codeverse.judges.rubrics", "load_rubric")(rubric)
        except ServiceUnavailable:
            return None
        return getattr(r, "pass_threshold", None)

    # ---- spatial
    def measure(self, glb: Path) -> Measurement:
        return _import("codeverse.spatial.measure", "measure_glb")(glb)

    def connectivity(self, glb: Path, language: str = "") -> GateReport:
        """``language`` selects the frame of the fix hints (the author's frame, not the GLB's)."""
        return _import("codeverse.spatial.connectivity", "check_connectivity")(glb, language=language)

    def contract(self, measurement: Measurement, plan: Plan, tol_m: float, language: str = "") -> GateReport:
        return _import("codeverse.spatial.contract", "check_contract")(measurement, plan, language=language, tol_m=tol_m)

    def render_object(self, glb: Path, out_dir: Path, *, views: Sequence[ViewPreset], width: int, height: int) -> RenderSet:
        return _import("codeverse.spatial.render", "render_glb")(glb, out_dir, views=list(views), width=width, height=height, sheet=True)

    def render_scene(self, ws: Workspace, out_dir: Path, *, cameras: list[CameraPlan] | None, times: Sequence[float],
                     width: int, height: int) -> RenderSet:
        return _import("codeverse.spatial.render_scene", "render_scene")(ws, out_dir, cameras=cameras, orbit=True, times=tuple(times),
                                                                        width=width, height=height, sheet=True)

    def render_geometry(self, glb: Path, out_dir: Path, *, views: Sequence[ViewPreset]) -> RenderSet:
        """Clay renders (no materials/textures) exposing holes/intersections for the judge's
        geometry montage — cheap thanks to the render cache."""
        return _import("codeverse.spatial.render", "render_glb")(glb, out_dir, views=list(views), mode="clay", sheet=False)

    def frame_gate(self, renders: RenderSet) -> GateReport:
        """``scene_frames`` gate from a scene RenderSet's metrics.json (missing metrics → passing empty report)."""
        return _import("codeverse.spatial.frame_metrics", "frame_gate_from_renders")(renders)

    def select_judge_views(self, renders: RenderSet, max_n: int = 10) -> RenderSet:
        """The ≤ ``max_n`` scene views a judge should see; identity when the helper is unavailable."""
        try:
            fn = _import("codeverse.spatial.render_scene", "select_judge_views")
        except ServiceUnavailable:
            return renders
        return fn(renders, max_n=max_n)

    def silhouette(self, render_png: Path | str, reference_png: Path | str) -> dict[str, Any]:
        """Outline IoU of a render vs a reference image (``{iou, reliable, ...}``)."""
        return _import("codeverse.spatial.silhouette", "compare_silhouette")(render_png, reference_png)

    def motion_checks(self, ws: Workspace, plan: Plan) -> GateReport | None:
        """Planned joint motion text vs the built URDF's actual motion direction."""
        from codeverse.tracks.articulated_object import default_motion_checks

        return default_motion_checks(ws, plan)

    def joint_sweep(self, ws: Workspace, plan: Plan, out_dir: Path) -> tuple[GateReport, list[RenderView]]:
        """Pose sweep on the built URDF → (gate report, pose views).  See articulated_object."""
        from codeverse.tracks.articulated_object import default_joint_sweep

        return default_joint_sweep(ws, plan, out_dir)

    # ---- agents' workspace materialisation + tool cards
    def materialize(self, ws: Workspace, *, agent_kind: str, contract_md: str, cookbook_rel: str, spatial_tools: bool,
                    mcp_command: list[str]) -> None:
        _import("codeverse.agents.materialize", "materialize_workspace")(
            ws, agent_kind=agent_kind, contract_md=contract_md, cookbook_rel=cookbook_rel,
            spatial_tools=spatial_tools, mcp_command=mcp_command)

    def tool_cards(self, track: str, language: str) -> str:
        try:
            return _import("codeverse.spatial.registry", "tool_cards")(track=track, language=language)
        except Exception as e:  # noqa: BLE001 — tool registry may be empty during bootstrap
            log.warning("tool cards unavailable: %s", e)
            return ""

    # ---- scene assembly + record
    def assemble_scene(self, ws: Workspace, plan: Plan) -> Any:
        fn = _import("codeverse.languages.scene_threejs", "assemble")
        return fn(ws, plan, cameras="plan" if getattr(plan, "cameras", None) else "derive")

    def finalize_record(self, ws: Workspace, record: RunRecord) -> None:
        try:
            fn = _import("codeverse.flywheel.record", "finalize_record")
        except ServiceUnavailable:
            ws.write_json(ws.record_path, record)
            return
        fn(ws, record)


# ----------------------------------------------------------------------------- context
@dataclass
class RunContext:
    """Everything a round needs.  Built once per run by the track."""

    spec: Any
    ws: Workspace
    events: EventLog
    settings: Settings
    budget: BudgetGuard
    runtime: Any
    services: Services
    state: RunState
    policy: RoundPolicy
    track: Track
    rubric: str
    agent_id: str
    plan: Plan | None = None
    judge: Any | None = None
    agent: Any | None = None  # CodingAgent instance (agent path)
    model: Any | None = None  # ChatModel (single-shot path)
    contract_text: str = ""
    cookbook_text: str = ""
    cookbook_rel: str = ""
    tool_cards: str = ""
    prompt_hashes: dict[str, str] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def language(self) -> Language:
        return self.spec.language

    @property
    def single_shot(self) -> bool:
        return is_single_shot(self.agent_id)

    def record_prompt(self, name: str, text: str) -> None:
        self.prompt_hashes[name] = prompt_hash(text)


# ----------------------------------------------------------------------------- prompt helpers
def load_prompt_or(rel: str, fallback: str) -> str:
    """``prompts/<rel>`` if it exists, else ``fallback``."""
    try:
        return load_text(rel)
    except FileNotFoundError:
        return fallback


def language_contract(ctx_language: Language, runtime: Any) -> str:
    """The language authoring contract: prompts/<dir>/contract.md → runtime.contract_doc() → minimal."""
    text = load_prompt_or(f"{prompt_dir_for(ctx_language)}/contract.md", "")
    if text.strip():
        return text
    doc = ""
    if runtime is not None and hasattr(runtime, "contract_doc"):
        try:
            doc = runtime.contract_doc() or ""
        except Exception as e:  # noqa: BLE001
            log.warning("runtime.contract_doc failed: %s", e)
    return doc.strip() or _MINIMAL_CONTRACT.get(ctx_language, "Write raw code in the language's native frame; meters; named parts.")


_MINIMAL_CONTRACT: dict[Language, str] = {
    Language.BLENDER: "src/model.py — pure bpy; Z-up, -Y front, meters; one named object per part (PascalCase); "
                      "no camera/light/render/export calls; do not import anything but bpy, bmesh, math, mathutils, random.",
    Language.CADQUERY: "src/model.py — `import cadquery as cq` only; module-level `result` = cq.Assembly with named parts "
                       "(PascalCase) or a Workplane; Z-up, -Y front, meters.",
    Language.THREEJS: "src/parts/<snake>.js each `export function build<Pascal>(THREE) → THREE.Group` at world pose; "
                      "src/object.js `export function build(THREE) → THREE.Group` adding every part; Y-up, +Z front, meters; "
                      "no DOM, no texture loading, no environment sniffing.",
    Language.URDF_BLENDER: "src/model.py — pure bpy, one object per link named exactly as the link (PascalCase), Z-up, -Y front, "
                           "meters, authored at rest pose in WORLD coordinates; src/robot.urdf — native URDF, meshes as "
                           "meshes/<link>.glb, joint origins/axes in parent-link frames.",
    Language.SCENE_THREEJS: "src/scene.js `export function createScene({THREE, renderer, loaders}) → {scene, cameras, update(t,dt)}`; "
                            "src/env.js, src/zones/*.js, src/assets/*.js (`export function build<Pascal>(THREE, opts)`), "
                            "src/shaders/*.js; Y-up, meters; `import * as THREE from 'three'` only; no DOM, no fetch.",
}


def cookbook_rel_for(language: Language) -> str:
    """prompts/<dir>/cookbook.md.  ``language.value`` is NOT always the directory:
    urdf_blender's prompts live in prompts/urdf/, so this returned a path that does not
    exist and the articulated agent was told "No cookbook is available in this session"
    while its 24 063-character cookbook sat on disk.  A/B'd behind CV3D_URDF_COOKBOOK
    before being made unconditional, because it changes the prompt."""
    if language is Language.URDF_BLENDER and os.environ.get("CV3D_URDF_COOKBOOK", "0") == "0":
        return f"{language.value}/cookbook.md"      # arm A: today's behaviour (misses)
    return f"{prompt_dir_for(language)}/cookbook.md"


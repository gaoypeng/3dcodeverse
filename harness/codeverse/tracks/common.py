"""Shared track machinery: the run context, the services seam, prompt context.

``Services`` is the tracks' injection seam: every call into a sibling package
(models, agents, runtimes, spatial tools, judges, flywheel) goes through one
method here so a test can subclass it with fakes (``tests/orchestrator_tracks/
fakes.py``) and an articulated test double can synthesise joint sweeps from the
plan.  The targets are modules of this same package — never optional — and are
imported lazily only to keep ``tracks`` importable without loading Blender,
Chrome or a model client.  ``ServiceUnavailable`` is what a FAKE raises for a
service the test did not provide.  Nothing here touches a network.
"""

from __future__ import annotations

import importlib
import logging
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
from codeverse.languages import get_runtime
from codeverse.orchestrator import BudgetGuard, RoundPolicy, RunState
from codeverse.proc import EventLog
from codeverse.prompts import load_text, prompt_hash
from codeverse.prompts.catalog import prompt_dir_for
from codeverse.tracks.generation import is_single_shot
from codeverse.workspace import Workspace

log = logging.getLogger(__name__)


class ServiceUnavailable(RuntimeError):
    """A test double has no implementation for this service (``scene._assemble_stage``
    falls back to an agent compose session on it)."""


def _import(path: str, name: str) -> Any:
    return getattr(importlib.import_module(path), name)


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
        return getattr(_import("codeverse.judges.rubrics", "load_rubric")(rubric), "pass_threshold", None)

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
        """The ≤ ``max_n`` scene views a judge should see."""
        return _import("codeverse.spatial.render_scene", "select_judge_views")(renders, max_n=max_n)

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
    def materialize(self, ws: Workspace, *, agent_kind: str, contract_md: str, cookbook_rel: str, spatial_tools: bool) -> None:
        _import("codeverse.agents.materialize", "materialize_workspace")(
            ws, agent_kind=agent_kind, contract_md=contract_md, cookbook_rel=cookbook_rel, spatial_tools=spatial_tools)

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
        _import("codeverse.flywheel.record", "finalize_record")(ws, record)


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

    @property
    def agent_kind(self) -> str:
        """``gemini-cli`` of ``gemini-cli:gemini-3.7-flash``."""
        return self.agent_id.split(":", 1)[0]

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
    """The language authoring contract, as the runtime states it (``LanguageRuntime.contract_doc``
    reads prompts/<dir>/contract.md).  ONE lookup: a per-language prose fallback here restated
    frames/units — conventions.py's job (law 2) — and was unreachable anyway."""
    return (runtime or get_runtime(ctx_language)).contract_doc()


def cookbook_rel_for(language: Language) -> str:
    """prompts/<dir>/cookbook.md.  ``language.value`` is NOT always the directory:
    urdf_blender's prompts live in prompts/urdf/, so this returned a path that does not
    exist and the articulated agent was told "No cookbook is available in this session"
    while its 24 063-character cookbook sat on disk.  Unconditional since 2026-08-29
    (the CV3D_URDF_COOKBOOK A/B switch is gone): every run gets the real cookbook."""
    return f"{prompt_dir_for(language)}/cookbook.md"


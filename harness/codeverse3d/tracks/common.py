"""Shared track machinery: the run context, the services seam, prompt context.

``Services`` is the tracks' injection seam: every call into a sibling package
(models, agents, runtimes, spatial tools, judges, record) goes through one
method here so a test can subclass it with fakes (``tests/orchestrator_tracks/
fakes.py``) and an articulated test double can synthesise joint sweeps from the
plan.  The targets are modules of this same package — never optional — and are
imported lazily only to keep ``tracks`` importable without loading Blender,
Chrome or a model client.  Nothing here touches a network.
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from codeverse3d.config import Settings
from codeverse3d.contracts.artifacts import GateReport, Measurement, RenderSet, RenderView
from codeverse3d.contracts.common import Language, Track
from codeverse3d.contracts.plan import CameraPlan, Plan
from codeverse3d.contracts.run import RunRecord
from codeverse3d.conventions import ViewPreset
from codeverse3d.orchestrator import BudgetGuard, RoundPolicy, RunState
from codeverse3d.proc import EventLog
from codeverse3d.prompts import prompt_hash
from codeverse3d.tracks.generation import (
    SINGLE_SHOT_PREFIX,
    GenerationResult,
    GenerationTask,
    generate,
    is_single_shot,
)
from codeverse3d.workspace import Workspace

log = logging.getLogger(__name__)


def _import(path: str, name: str) -> Any:
    return getattr(importlib.import_module(path), name)


class Services:
    """Lazy adapters to sibling packages.  Override any method in tests."""

    # ---- models / agents / judges / runtimes
    def chat_model(self, model_id: str) -> Any:
        return _import("codeverse3d.models", "get_chat_model")(model_id)

    def coding_agent(self, agent_id: str) -> Any:
        return _import("codeverse3d.agents", "get_coding_agent")(agent_id)

    def judge(self, rubric: str, model_id: str, n_samples: int = 1) -> Any:
        return _import("codeverse3d.judges.vlm_judge", "VlmJudge")(rubric=rubric, model_id=model_id, n_samples=n_samples)

    def reference_judge(self, model_id: str, n_samples: int = 1, rubric: str = "reference_v1") -> Any:
        """Image-conditioned judge (spec has reference images): renders + references + silhouette IoU."""
        return _import("codeverse3d.judges.vlm_judge", "ReferenceJudge")(model_id=model_id, n_samples=n_samples, rubric=rubric)

    def likeness_judge(self, model_id: str, n_samples: int = 1, rubric: str = "shader_v2") -> Any:
        """Reference photos beside the frames, no silhouette (graphics / scene with images)."""
        return _import("codeverse3d.judges.vlm_judge", "LikenessJudge")(model_id=model_id, n_samples=n_samples, rubric=rubric)

    def runtime(self, language: Language) -> Any:
        return _import("codeverse3d.languages", "get_runtime")(language)

    # ---- spatial
    def measure(self, glb: Path) -> Measurement:
        return _import("codeverse3d.spatial.measure", "measure_glb")(glb)

    def connectivity(self, glb: Path, language: str = "", planned_edges: Sequence[tuple[str, str | Sequence[str]]] = ()) -> GateReport:
        """``language`` selects the frame of the fix hints (the author's frame, not the GLB's);
        ``planned_edges`` are the plan's attach_to pairs spelled in GLB part names — the gate
        measures each one and lists it contact/open in its contact ledger (the judge's
        assembly_fit ground truth since 2026-08-30)."""
        return _import("codeverse3d.spatial.connectivity", "check_connectivity")(
            glb, language=language, planned_edges=tuple(planned_edges))

    def contract(self, measurement: Measurement, plan: Plan, tol_m: float, language: str = "") -> GateReport:
        return _import("codeverse3d.spatial.contract", "check_contract")(measurement, plan, language=language, tol_m=tol_m)

    def render_object(self, glb: Path, out_dir: Path, *, views: Sequence[ViewPreset], width: int, height: int) -> RenderSet:
        return _import("codeverse3d.spatial.render", "render_glb")(glb, out_dir, views=list(views), width=width, height=height, sheet=True)

    def render_scene(self, ws: Workspace, out_dir: Path, *, cameras: list[CameraPlan] | None, times: Sequence[float],
                     width: int, height: int) -> RenderSet:
        return _import("codeverse3d.spatial.render_scene", "render_scene")(ws, out_dir, cameras=cameras, orbit=True, times=tuple(times),
                                                                        width=width, height=height, sheet=True)

    def render_geometry(self, glb: Path, out_dir: Path, *, views: Sequence[ViewPreset]) -> RenderSet:
        """Clay renders (no materials/textures) exposing holes/intersections for the judge's
        geometry montage — cheap thanks to the render cache."""
        return _import("codeverse3d.spatial.render", "render_glb")(glb, out_dir, views=list(views), mode="clay", sheet=False)

    def frame_gate(self, renders: RenderSet) -> GateReport:
        """``scene_frames`` gate from a scene RenderSet's metrics.json (missing metrics → passing empty report)."""
        return _import("codeverse3d.spatial.frame_metrics", "frame_gate_from_renders")(renders)

    def silhouette(self, render_png: Path | str, reference_png: Path | str) -> dict[str, Any]:
        """Outline IoU of a render vs a reference image (``{iou, reliable, ...}``)."""
        return _import("codeverse3d.spatial.silhouette", "compare_silhouette")(render_png, reference_png)

    def motion_checks(self, ws: Workspace, plan: Plan) -> GateReport | None:
        """Planned joint motion text vs the built URDF's actual motion direction."""
        from codeverse3d.tracks.articulated_object import default_motion_checks

        return default_motion_checks(ws, plan)

    def joint_sweep(self, ws: Workspace, plan: Plan, out_dir: Path) -> tuple[GateReport, list[RenderView]]:
        """Pose sweep on the built URDF → (gate report, pose views).  See articulated_object."""
        from codeverse3d.tracks.articulated_object import default_joint_sweep

        return default_joint_sweep(ws, plan, out_dir)

    # ---- agents' workspace materialisation + tool cards
    def materialize(self, ws: Workspace, *, agent_kind: str, contract_md: str, cookbook_text: str, spatial_tools: bool) -> None:
        _import("codeverse3d.agents.materialize", "materialize_workspace")(
            ws, agent_kind=agent_kind, contract_md=contract_md, cookbook_text=cookbook_text, spatial_tools=spatial_tools)

    def tool_cards(self, track: str, language: str) -> str:
        try:
            return _import("codeverse3d.spatial.registry", "tool_cards")(track=track, language=language)
        except Exception as e:  # noqa: BLE001 — tool registry may be empty during bootstrap
            log.warning("tool cards unavailable: %s", e)
            return ""

    # ---- scene assembly + record
    def assemble_scene(self, ws: Workspace, plan: Plan) -> Any:
        fn = _import("codeverse3d.languages.scene_threejs", "assemble")
        return fn(ws, plan, cameras="plan" if getattr(plan, "cameras", None) else "derive")

    def finalize_record(self, ws: Workspace, record: RunRecord) -> None:
        _import("codeverse3d.record.record", "finalize_record")(ws, record)


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


# ----------------------------------------------------------------------------- strategy
def single_shot_agent_id(agent_id: str, chat_model_id: str = "") -> str:
    """The single-shot strategy id for this run, or "" when there is no chat model.

    Single-shot is ONE api call that returns the asset file — the cheap path the asset
    stage tries before escalating to a full agent session.  It needs a chat model, and
    the coding agent is always a vendor CLI (which exposes none), so the model comes
    from ``chat_model_id`` — the run's planner backend, which is always an API model.
    Until 2026-08-28 it was derived from the in-process ``api-agent`` generator id;
    that backend is gone."""
    if is_single_shot(agent_id):
        return agent_id
    # a SHAPE check only, deliberately not models.registry.parse_model_id: whether the
    # id resolves is the SERVICES' call (tests run fake providers like "fake:planner"),
    # and single_shot_ctx already degrades to the agent path when chat_model() raises
    return SINGLE_SHOT_PREFIX + chat_model_id if ":" in chat_model_id else ""


def single_shot_ctx(ctx: RunContext) -> RunContext | None:
    """A copy of ``ctx`` bound to the single-shot strategy, or None when the
    generator has no usable chat model (CLI agents, tests with fake services)."""
    cached = ctx.extra.get("_single_shot_ctx")
    if cached is not None:
        return cached or None  # False = known-unavailable
    sid = single_shot_agent_id(ctx.agent_id, ctx.spec.backends.planner)
    sub: RunContext | None = None
    if sid:
        try:
            model = ctx.model if is_single_shot(ctx.agent_id) else ctx.services.chat_model(sid[len(SINGLE_SHOT_PREFIX):])
        except Exception as e:  # noqa: BLE001 — no chat model → keep the agent path
            log.info("single-shot generation unavailable for %s: %s", ctx.agent_id, e)
            model = None
        if model is not None:
            sub = replace(ctx, agent_id=sid, model=model, agent=None)
    ctx.extra["_single_shot_ctx"] = sub or False
    return sub


def generate_for(ctx: RunContext, task: GenerationTask) -> GenerationResult:
    """``generate()`` with everything ``ctx`` knows — the one call every stage makes — plus the
    storm fallback: a session that died on a transient-failure streak and wrote nothing
    (``GenerationResult.storm``) is retried through the single-shot path, one hedged,
    key-rotating API call.  Measured 2026-09-07: 23 of 24 gemini-cli sessions of an evening
    ended ``timeout / 0 turns / $0`` after 8-17 consecutive 503s inside the CLI's own retry
    loop, while every single-shot in the same minutes got through; the stages that lost
    their session shipped the skeleton env and empty zones and judged 0.00-0.14."""
    res = generate(ctx.ws, agent_id=ctx.agent_id, task=task, agent=ctx.agent, model=ctx.model, settings=ctx.settings,
                   budget=ctx.budget, events=ctx.events)
    if not res.storm or ctx.single_shot:
        return res
    sub = single_shot_ctx(ctx)
    if sub is None:
        return res
    ctx.events.emit("generate.storm_fallback", label=task.label, sessions=res.sessions, notes=res.notes[:300])
    again = generate(sub.ws, agent_id=sub.agent_id, task=task, agent=sub.agent, model=sub.model, settings=sub.settings,
                     budget=sub.budget, events=sub.events)
    again.usage = res.usage + again.usage
    again.notes = f"agent session died in a 503 storm ({res.notes[:120]}) → single-shot: {again.notes}"
    # the cheap route did not get through either: the task still died of the storm (the round
    # loop's one retry keys on it — RoundFailed.transient)
    again.transient = again.transient or not again.ok
    return again

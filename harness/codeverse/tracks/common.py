"""Shared track machinery: the run context, lazily-bound services, prompt context.

``Services`` is the single seam between the tracks and the other packages
(models, agents, runtimes, spatial tools, judges, flywheel).  Every method
imports lazily and raises a clear ``ServiceUnavailable`` when the package is
missing; tests subclass it with fakes.  Nothing here touches a network.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from codeverse.config import Settings
from codeverse.contracts.artifacts import GateReport, Measurement, RenderSet, RenderView
from codeverse.contracts.common import Language, Track
from codeverse.contracts.plan import CameraPlan, Plan, StaticPlan
from codeverse.contracts.run import RunRecord
from codeverse.conventions import LANGUAGE_FRAME, Frame, ViewPreset, frame_doc, to_snake
from codeverse.events import EventLog
from codeverse.orchestrator.budget import BudgetGuard
from codeverse.orchestrator.rounds import RoundPolicy
from codeverse.orchestrator.state import RunState
from codeverse.prompts import load_text, prompt_hash
from codeverse.tracks.generation import SINGLE_SHOT_FORMAT, is_single_shot
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

    def connectivity(self, glb: Path) -> GateReport:
        return _import("codeverse.spatial.connectivity", "check_connectivity")(glb)

    def contract(self, measurement: Measurement, plan: Plan, tol_m: float, language: str = "") -> GateReport:
        return _import("codeverse.spatial.contract", "check_contract")(measurement, plan, language=language, tol_m=tol_m)

    def render_object(self, glb: Path, out_dir: Path, *, views: Sequence[ViewPreset], width: int, height: int) -> RenderSet:
        return _import("codeverse.spatial.render", "render_glb")(glb, out_dir, views=list(views), width=width, height=height, sheet=True)

    def render_scene(self, ws: Workspace, out_dir: Path, *, cameras: list[CameraPlan] | None, times: Sequence[float],
                     width: int, height: int) -> RenderSet:
        return _import("codeverse.spatial.render", "render_scene")(ws, out_dir, cameras=cameras, orbit=True, times=tuple(times),
                                                                  width=width, height=height, sheet=True)

    def contact_sheet(self, images: list[tuple[str, Path]], out: Path) -> Path:
        return _import("codeverse.spatial.sheet", "contact_sheet")(images, out)

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
        fn = _import("codeverse.languages.scene_threejs.assemble", "assemble")
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
    """``prompts/<rel>`` if it exists (package K writes those), else ``fallback``."""
    try:
        return load_text(rel)
    except FileNotFoundError:
        return fallback


def language_contract(ctx_language: Language, runtime: Any) -> str:
    """The language authoring contract: prompts/<lang>/contract.md → runtime.contract_doc() → minimal."""
    text = load_prompt_or(f"{ctx_language.value}/contract.md", "")
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
    return f"{language.value}/cookbook.md"


def parts_table(plan: Plan) -> str:
    """Markdown table of parts with exact bboxes (meters, 3 decimals)."""
    parts = getattr(plan, "parts", None)
    if not parts:
        return "(no parts)"
    rows = ["| part | role | bbox centre (x,y,z) m | extents (x,y,z) m | attach_to | inst | material |",
            "|---|---|---|---|---|---|---|"]
    for p in parts:
        c = ", ".join(f"{v:.3f}" for v in p.bbox.center)
        e = ", ".join(f"{v:.3f}" for v in p.bbox.extents)
        rows.append(f"| {p.name} | {p.role} | ({c}) | ({e}) | {p.attach_to or '-'} | {p.instances} | {p.material or '-'} |")
    return "\n".join(rows)


def part_details(plan: Plan) -> str:
    parts = getattr(plan, "parts", None) or []
    return "\n".join(f"- **{p.name}** ({p.symmetry if p.symmetry != 'none' else 'no symmetry'}): {p.description}" for p in parts)


def joints_table(plan: Plan) -> str:
    joints = getattr(plan, "joints", None)
    if not joints:
        return "(no joints)"
    rows = ["| joint | type | parent | child | axis | pivot (world, m) | lower | upper | rest | motion |",
            "|---|---|---|---|---|---|---|---|---|---|"]
    for j in joints:
        ax = ", ".join(f"{v:.3f}" for v in j.axis)
        pv = ", ".join(f"{v:.3f}" for v in j.pivot)
        rows.append(f"| {j.name} | {j.type} | {j.parent} | {j.child} | ({ax}) | ({pv}) | {j.lower:.3f} | {j.upper:.3f} | {j.rest:.3f} | {j.motion} |")
    return "\n".join(rows)


def acceptance_lines(plan: Plan | None) -> str:
    items = getattr(plan, "acceptance", None) or []
    if not items:
        return "(none)"
    return "\n".join(f"- [{a.id}] ({a.priority}, {a.how}) {a.text}" for a in items)


def bbox_line(bbox: Any) -> str:
    c = ", ".join(f"{v:.3f}" for v in bbox.center)
    e = ", ".join(f"{v:.3f}" for v in bbox.extents)
    return f"centre ({c}) m, extents ({e}) m"


def glb_to_plan_frame(v: Sequence[float], language: Language, *, extents: bool = False) -> tuple[float, float, float]:
    """Map a GLB-frame (Y-up, +Z front) vector into the language's authoring frame.
    Blender/CadQuery/URDF plans are Z-up with -Y front: glb (x, y, z) → (x, -z, y)."""
    x, y, z = float(v[0]), float(v[1]), float(v[2])
    if LANGUAGE_FRAME[language.value] is Frame.Z_UP_NEG_Y_FRONT:
        return (x, abs(z) if extents else -z, y)
    return (x, y, z)


def constraints_text(spec: Any) -> str:
    c = spec.constraints
    lines = []
    if c.dimensions_m:
        lines.append("Dimensions (m): " + ", ".join(f"{k}={v:.3f}" for k, v in c.dimensions_m.items()))
    if c.max_triangles:
        lines.append(f"Max triangles: {c.max_triangles}")
    if c.style:
        lines.append(f"Style: {c.style}")
    for m in c.must_have:
        lines.append(f"MUST HAVE: {m}")
    for m in c.must_not:
        lines.append(f"MUST NOT: {m}")
    return "\n".join(lines) or "(none)"


def base_prompt_context(ctx: RunContext, **extra: Any) -> dict[str, Any]:
    """Variables every tracks/*.j2 template may use (StrictUndefined → all present)."""
    plan = ctx.plan
    object_name = getattr(plan, "object_name", None) or getattr(plan, "title", None) or "Object"
    d: dict[str, Any] = {
        "track": ctx.track.value,
        "language": ctx.language.value,
        "frame_doc": frame_doc(LANGUAGE_FRAME[ctx.language.value]),
        "contract": ctx.contract_text,
        "cookbook_rel": ctx.cookbook_rel,
        "cookbook_excerpt": ctx.cookbook_text[:6000],
        "tool_cards": ctx.tool_cards,
        "single_shot": ctx.single_shot,
        "output_format": SINGLE_SHOT_FORMAT if ctx.single_shot else AGENT_OUTPUT_RULES,
        "spec_prompt": ctx.spec.prompt,
        "constraints": constraints_text(ctx.spec),
        "object_name": object_name,
        "plan_summary": getattr(plan, "summary", "") if plan else "",
        "style_notes": getattr(plan, "style_notes", "") if plan else "",
        "overall_bbox": bbox_line(plan.overall_bbox) if isinstance(plan, StaticPlan) else "",
        "parts_table": parts_table(plan) if plan else "",
        "part_details": part_details(plan) if plan else "",
        "joints_table": joints_table(plan) if plan else "",
        "acceptance": acceptance_lines(plan),
        "entry_files": ", ".join(getattr(ctx.runtime, "entry_globs", ()) or ()),
        "n_parts": len(getattr(plan, "parts", []) or []) if plan else 0,
        "root_link": getattr(plan, "root_link", "") if plan else "",
    }
    d.update(extra)
    return d


AGENT_OUTPUT_RULES = """HOW TO FINISH (agent mode): edit files under src/ only (and public/ for compiled assets).
Before you finish you MUST run the `build` tool and fix every error it reports; then run `measure`
(objects) or `scene_probe` (scenes) once and compare the numbers with the plan.  Do not write
reports, READMEs or notes — only the code files.  Stop when the build is clean."""


def file_for_target_factory(ctx: RunContext):
    """Return ``target → [files]`` for the current language, or ``None`` when the
    language is whole-object (one file).  Prefers ``runtime.file_for_part``."""
    rt = ctx.runtime
    plan = ctx.plan
    custom = getattr(rt, "file_for_part", None)
    part_names = {to_snake(p.name): p.name for p in (getattr(plan, "parts", None) or [])}
    lang = ctx.language

    if lang is Language.THREEJS or callable(custom):
        def _threejs(target: str) -> list[str]:
            key = to_snake(target)
            if key in part_names:
                if callable(custom):
                    try:
                        out = custom(part_names[key])
                        return [str(out)] if isinstance(out, (str, Path)) else [str(p) for p in out]
                    except Exception as e:  # noqa: BLE001
                        log.warning("runtime.file_for_part failed for %s: %s", target, e)
                return [f"src/parts/{key}.js"]
            if key in ("overall", "assembly", "object", ""):
                return ["src/object.js"]
            return []
        return _threejs

    if lang is Language.SCENE_THREEJS:
        zones = {to_snake(z.name) for z in (getattr(plan, "zones", None) or [])}
        assets = {to_snake(a.name) for a in (getattr(plan, "assets", None) or [])}
        cameras = {to_snake(c.name) for c in (getattr(plan, "cameras", None) or [])}

        def _scene(target: str) -> list[str]:
            key = to_snake(target)
            if key in zones:
                return [f"src/zones/{key}.js"]
            if key in assets:
                return [f"src/assets/{key}.js"]
            if key in cameras or key in ("camera", "cameras", "composition"):
                return ["src/scene.js"]
            if key in ("env", "environment", "lighting", "sky", "fog", "ground", "water", "light"):
                return ["src/env.js"]
            return []
        return _scene
    return None

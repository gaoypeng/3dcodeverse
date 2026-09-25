"""The prompt + judge-payload manifest: what every model is shown, hashed (permanent: owner, 2026-09-22).

Every track runs offline through the fakes of ``tests/orchestrator_tracks/fakes.py`` in the
scenarios of :data:`SCENARIOS`.  Everything a model would receive is captured, normalised (the
scenario's tmp root becomes ``<TMP>``; timestamps, durations, clock-derived waits and image bytes
are dropped) and stored in ``manifest.json`` as ``kind → {sha256, bytes}``:

* ``job:<label>@rNN/{prompt,system,scope}`` — every coding-agent session (``FakeAgent.jobs``);
* ``chat:<role>:<label>/{system,messages,sampling}`` — every chat call: the ``planner`` (brief,
  plan, re-asks, zone layouts) and the ``model`` (single-shot generation, the scene's single-shot
  assets, a hero's own planner);
* ``judge:<rubric>:<renders dir>/{input,system,messages,sampling}`` — every verdict: the
  ``JudgeInput`` the in-run judge reads (``FakeJudge.calls``) and the request the real
  ``VlmJudge`` builds from it;
* ``context:<workspace>/files`` — what a session reads in its workspace, as materialised
  (AGENTS/GEMINI/CLAUDE.md, the cookbook copy, the gemini settings, the ignore files);
* ``skills:<workspace>:<kind>@rNN/routed`` — the skill bundles routed to a round;
* ``mcp_tools:<track>/<language>`` — the MCP tool list a session is offered;

and every cached stage's key under ``stage_keys``.  A code-only change leaves all of it
byte-identical.  A change that means to alter what a model sees re-blesses it in the same commit,
saying why::

    python -m tests.prompts.manifest               # compare this tree with manifest.json
    python -m tests.prompts.manifest --bless       # rewrite manifest.json from this tree
    python -m tests.prompts.manifest --dump DIR    # every payload as a file: diff two trees' dumps

Not covered (the fakes cannot drive it): reference-image runs (reference and likeness judges, the
silhouette gate), the texture pack's own planner (``texturing/scene_pack.md``: the scene is handed
a canned pack) and the texture pass (``texturing/material_plan.md``), replays (``3dcode judge``),
the budget-stop salvage (the clock is frozen), the node asset checker (a python stand-in answers)
and each vendor CLI's own framing of a session (only gemini-cli's workspace files are built).
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import logging
import os
import re
import sys
import tempfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest import mock

import numpy as np
from PIL import Image
from pydantic import BaseModel

from codeverse3d import config
from codeverse3d.config import Settings
from codeverse3d.contracts.artifacts import BuildResult
from codeverse3d.contracts.chat import ChatRequest, TextPart
from codeverse3d.contracts.common import TRACK_LANGUAGES, Backends, Budget, Language, Track
from codeverse3d.contracts.plan import BBox, PartPlan, Plan, ScenePlan, StaticPlan, ZoneLayout
from codeverse3d.contracts.spec import Constraints, Spec
from codeverse3d.conventions import to_pascal, to_snake
from codeverse3d.cost import ledger
from codeverse3d.judges.vlm_judge import VlmJudge
from codeverse3d.languages import get_runtime
from codeverse3d.languages._gl_common import finish_build, judge_times
from codeverse3d.languages.glsl_shader import GlslShaderRuntime
from codeverse3d.languages.opengl_python import OpenGLPythonRuntime
from codeverse3d.languages.scene_threejs import asset_file, zone_file
from codeverse3d.models.base import ModelError
from codeverse3d.orchestrator import BudgetGuard
from codeverse3d.spatial.gl_render import GlFrame, GlResult, gif_times
from codeverse3d.spatial.registry import list_tools
from codeverse3d.texturing.plan import PackEntry, ScenePack
from codeverse3d.tracks import get_track, scene_assets
from codeverse3d.tracks.common import Services
from codeverse3d.tracks.planner import plan_example
from codeverse3d.tracks.scene_assets import AssetCheck
from codeverse3d.tracks.zone_layout import validate_layout
from codeverse3d.workspace import Workspace, WorkspaceGitError
from tests.orchestrator_tracks.fakes import (
    FAIL_MARK,
    FakeAgent,
    FakeChatModel,
    FakeJudge,
    FakeRuntime,
    FakeServices,
    small_scene,
)

MANIFEST = Path(__file__).with_name("manifest.json")
AGENT = "gemini-cli:gemini-3.7-flash"                 # the default generator (a vendor CLI; FakeAgent answers)
SINGLE_SHOT = "single-shot:gemini:gemini-3.7-flash"
#: what each track is asked for
REQUESTS = {Track.STATIC_OBJECT: ("a mid-century wooden dining chair", Constraints(dimensions_m={"height": 0.82}, must_have=["armrests"])),
          Track.ARTICULATED_OBJECT: ("a bedside cabinet with one drawer", Constraints(must_have=["a round knob"])),
          Track.SCENE: ("a small harbour at dusk", Constraints(style="weathered, hand-painted", must_have=["a crate stack"])),
          Track.GRAPHICS: ("neon rain on a window with bokeh city lights", Constraints(must_have=["bokeh lights"]))}
#: the canned engineering brief (object tracks expand one before planning)
BRIEF = {"object_name": "DiningChair", "reference": "a Danish oak dining chair", "one_line": "a four-legged oak chair",
         "dimensions_m": [{"name": "height", "meters": 0.82}, {"name": "seat_height", "meters": 0.45}],
         "sub_assemblies": [{"name": "frame", "purpose": "carries the load", "parts": ["legs", "rails"], "material": "oak"}],
         "mechanism": "the legs carry the seat", "visible_from_outside": ["seat", "legs", "backrest"],
         "signature_features": ["tapered legs", "curved backrest"], "materials": ["seat: oiled oak"]}
CONTEXT_FILES = ("AGENTS.md", "GEMINI.md", "CLAUDE.md", ".3dcode/cookbook.md", ".gemini/settings.json", ".geminiignore", ".aiexclude")
VOLATILE = frozenset({"duration_ms", "duration_s", "latency_ms", "max_wait_s", "timeout_s", "hard_deadline_s", "workspace", "data_b64"})
SHADER = ("void mainImage(out vec4 fragColor, in vec2 fragCoord) {{\n  vec2 uv = fragCoord / u_resolution.xy;\n"
          "  // {tag}\n  fragColor = vec4(uv, 0.5 + 0.5 * sin(u_time), 1.0);\n}}\n")
PROGRAM = ("import moderngl  # {tag}\n\n\ndef setup(ctx, width, height):\n    return {{}}\n\n\n"
           "def render(ctx, state, t, frame, fbo):\n    fbo.clear(0.1, 0.2, 0.3 + 0.1 * t % 0.5, 1.0)\n")


# ============================================================================ the world a scenario runs in
@contextlib.contextmanager
def isolated(root: Path) -> Iterator[None]:
    """The environment of every scenario, whoever runs it (pytest, ``--bless``): none of this
    machine's ``C3D_*`` variables or config files, a wall clock frozen at 0 (a stage's window is
    part of its prompt), caches and ledgers under ``root``, and the node asset checker answered in
    python (``_asset_check``)."""
    env, cwd = dict(os.environ), os.getcwd()
    for k in [k for k in os.environ if k.startswith(("C3D_", "CV3D_"))]:
        del os.environ[k]
    os.environ["C3D_CACHE_DIR"] = str(root / "cache")
    with contextlib.ExitStack() as stack:
        for target, name, value in ((config, "_USER_CONFIG", root / "absent.yaml"),
                                    (config, "_LEGACY_USER_CONFIG", root / "absent.yaml"),
                                    (BudgetGuard, "elapsed_minutes", lambda self: 0.0),
                                    (ledger, "_fallback", ledger.CostLedger(root / "process.jsonl")),
                                    (scene_assets, "check_threejs_asset", _asset_check),
                                    (Workspace, "_git", _git_through_short_reads)):
            stack.enter_context(mock.patch.object(target, name, value))
        os.chdir(root)       # no ./3dcodeverse.yaml
        config.get_settings.cache_clear()
        try:
            yield
        finally:
            os.chdir(cwd)
            os.environ.clear()
            os.environ.update(env)
            config.get_settings.cache_clear()


_GIT = Workspace._git


def _git_through_short_reads(ws: Workspace, *args: str, **kw: Any) -> Any:
    """``Workspace._git``, retried when ``git add`` met a file mid-rewrite ("short read").

    A production race, not a payload: the scene's parallel stages commit (``git add -A``) while a
    sibling rewrites a tracked file — ``skills.materialize.write_index`` rewrites AGENTS/GEMINI/CLAUDE.md
    in place, a session writes its module — and git fails the stage with ``short read while indexing
    GEMINI.md`` (seen here 2026-09-22; ``_git`` retries only ``index.lock``)."""
    for _ in range(3):
        try:
            return _GIT(ws, *args, **kw)
        except WorkspaceGitError as e:
            if "short read" not in str(e):
                raise
    return _GIT(ws, *args, **kw)


def _asset_check(ctx: Any, rel: str, pascal: str, *, timeout_s: float = 60.0,
                 expected_size_m: tuple[float, float, float] | None = None) -> AssetCheck:
    """``scene_assets.check_threejs_asset`` without node: a module passes when it exports its builder."""
    path = ctx.ws.root / rel
    if path.is_file() and f"export function build{pascal}(" in path.read_text():
        return AssetCheck(ok=True, ran=True, size_m=expected_size_m, tris=480, meshes=3, materials=2)
    return AssetCheck(ok=False, ran=True, fatal=True,
                      errors=[f"missing export: this file must `export function build{pascal}(THREE, opts = {{}})`"])


class StableJudge(FakeJudge):
    """A verdict scored by what it judges (round, candidate), never by call order: the candidates,
    the assets and the scene stages judge from threads."""

    def __init__(self, rubric: str, scores: tuple[float, ...], targets: tuple[str, ...]):
        super().__init__(scores=scores, targets=targets)
        self.rubric = rubric

    def score_for(self, inp: Any) -> float:
        s = self.scores[min(inp.round_index, len(self.scores) - 1)]
        parts = Path(inp.renders.views[0].path).parts if inp.renders.views else ()
        cand = parts[parts.index("_cand") + 1] if "_cand" in parts else ""
        return round(s + 0.05 * int(cand[1:]), 3) if cand else s


class ManifestServices(FakeServices):
    """The fakes, with the real text-producing adapters (workspace materialisation, tool cards), a
    judge per rubric, and the rig's chat model for the stages that ask for one."""

    def __init__(self, rig: Rig, **kw: Any):
        super().__init__(**kw)
        self.rig = rig
        self.judges: list[StableJudge] = []
        self.contexts: list[tuple[Path, dict[str, str]]] = []

    def chat_model(self, model_id: str) -> Any:
        return self.rig.model

    def judge(self, rubric: str, model_id: str, n_samples: int = 1) -> Any:
        self.judges.append(StableJudge(rubric, self.rig.scores, self.rig.targets))
        return self.judges[-1]

    def tool_cards(self, track: str, language: str) -> str:
        return Services.tool_cards(self, track, language)

    def materialize(self, ws: Workspace, *, agent_kind: str, contract_md: str, cookbook_text: str, spatial_tools: bool) -> None:
        Services.materialize(self, ws, agent_kind=agent_kind, contract_md=contract_md, cookbook_text=cookbook_text,
                             spatial_tools=spatial_tools)
        self.materialized.append(agent_kind)
        self.contexts.append((ws.root, {n: (ws.root / n).read_text() for n in CONTEXT_FILES if (ws.root / n).is_file()}))


def gl_runtime(language: Language) -> Any:
    """The language's real GL runtime (skeleton, lint, layout) with a build that draws synthetic frames."""
    base = GlslShaderRuntime if language is Language.GLSL_SHADER else OpenGLPythonRuntime

    class FakeGl(base):
        def build(self, ws: Workspace, *, timeout_s: int | None = None, **kw: Any) -> BuildResult:
            entry = get_runtime(language).expected_files(None)[0]
            if FAIL_MARK in "".join(p.read_text() for p in sorted(ws.src.rglob("*")) if p.is_file()):
                res = GlResult(ok=False, mode="shader", stage="compile", error_type="GlslCompileError",
                               error_message=f"{entry}:3: error: `nope' undeclared")
                return finish_build(ws, res, language=language.value, error_file=entry, error_line=3)
            frames = []
            for i, t in enumerate(judge_times(8.0) + gif_times(8.0, 4)):
                p = ws.artifacts / "frames" / f"f{i:02d}_t{t:06.2f}.png"
                p.parent.mkdir(parents=True, exist_ok=True)
                x = (np.arange(64, dtype=np.uint16)[None, :].repeat(36, 0) * 4 + 16 * i) % 256
                Image.fromarray(np.stack([x, 255 - x, np.full_like(x, 96)], -1).astype(np.uint8), "RGB").save(p)
                frames.append(GlFrame(index=i, time=t, path=str(p), judge=i < 5))
            return finish_build(ws, GlResult(ok=True, mode="shader", stage="render", renderer="fake-gl", frames=frames),
                                language=language.value, census={"convention": "mainImage"})

    return FakeGl()


# ============================================================================ one scenario's rig
@dataclass
class Rig:
    """One scenario: its tmp root, its plan, the fakes every run of it shares — and what they saw."""

    root: Path
    track: Track
    language: Language
    plan: Plan
    scores: tuple[float, ...] = (0.55, 0.7, 0.85)
    targets: tuple[str, ...] = ()
    fail: frozenset[str] = frozenset()             # session labels whose code does not build
    broken_assets: frozenset[str] = frozenset()    # assets whose single-shot answers lack the builder export
    services_kw: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.agent = FakeAgent(lambda job, ws: self.files(job.label, job.round, job.files_hint, single_shot=False))
        self.planner = FakeChatModel(self.answer)
        self.model = FakeChatModel(self.answer)
        self.services = ManifestServices(self, **self.services_kw)
        self.runtime = gl_runtime(self.language) if self.track is Track.GRAPHICS else FakeRuntime(self.language)
        self.settings = Settings(runs_dir=self.root / "runs", cache_dir=self.root / "cache")
        self.ws = Workspace(self.root / "runs" / "run")

    def run(self, *, rounds: int, resume: bool = False, single_shot: bool = False, candidates: int | None = None) -> None:
        prompt, constraints = REQUESTS[self.track]
        spec = Spec(id="manifest", track=self.track, language=self.language, prompt=prompt, constraints=constraints,
                    budget=Budget(max_rounds=rounds, max_minutes=60, max_repair_attempts=2),
                    backends=Backends(planner="fake:planner", generator=SINGLE_SHOT if single_shot else AGENT, judge="fake:judge"))
        track = get_track(self.track, services=self.services, agent=None if single_shot else self.agent,
                          model=self.model if single_shot else None, planner_model=self.planner, settings=self.settings,
                          runtime=self.runtime, n_candidates=candidates)
        track.run(spec, self.ws, resume=resume)

    # ------------------------------------------------------------------ what the fakes answer
    def answer(self, req: ChatRequest) -> Any:
        """Planner-shaped requests get a plan / brief / layout; everything else a file envelope."""
        title = (req.response_schema or {}).get("title", "")
        if title == "EngineeringBrief":
            return BRIEF
        if title == "ZoneLayout":
            return self.layout(req.messages[-1].text)
        if title:     # a plan: this scenario's, or the static one a scene hero plans itself
            return self.plan.model_dump(mode="json") if title == type(self.plan).__name__ else plan_example(Track.STATIC_OBJECT)
        label, rnd = req.label.rsplit(":r", 1)
        files = self.files(label, int(rnd), self.single_shot_files(label), single_shot=True)
        return "".join(f"=== FILE: {rel} ===\n{body}=== END FILE ===\n" for rel, body in files.items())

    def single_shot_files(self, label: str) -> list[str]:
        """The files a single-shot answer for ``label`` returns (an agent job names them in ``files_hint``)."""
        if isinstance(self.plan, ScenePlan):
            if label == "env":
                return ["src/env.js"]
            if label.startswith("zone"):
                return [zone_file(z.name) for z in self.plan.zones]
            if label.startswith("asset_"):
                asset = self.asset(label)
                if asset.kind == "threejs":
                    return [asset_file(asset)]
                return get_runtime(Language.BLENDER).expected_files(StaticPlan.model_validate(plan_example(Track.STATIC_OBJECT)))
        return get_runtime(self.language).expected_files(self.plan)

    def asset(self, label: str) -> Any:
        return next(a for a in self.plan.assets if label[len("asset_"):].startswith(to_snake(a.name)))  # type: ignore[attr-defined]

    def files(self, label: str, rnd: int, rels: list[str], *, single_shot: bool) -> dict[str, str]:
        """What a session writes: every file it owns, each naming the session (a round must change code)."""
        tag = f"{label} r{rnd}" + (f" {FAIL_MARK}" if label in self.fail else "")
        out = {}
        for rel in rels:
            if rel.startswith("src/assets/") and rel.endswith(".js"):
                name = to_pascal(Path(rel).stem)
                export = "build" if single_shot and name in self.broken_assets else f"build{name}"
                out[rel] = f"// {tag}\nexport function {export}(THREE, opts = {{}}) {{ return new THREE.Group(); }}\n"
            elif rel.endswith(".frag"):
                out[rel] = SHADER.format(tag=tag)
            elif rel.endswith(".glsl"):
                out[rel] = f"// {tag}\n"
            elif rel == "src/program.py":
                out[rel] = PROGRAM.format(tag=tag)
            elif rel.endswith(".urdf"):
                out[rel] = f"<robot name='x'/>\n<!-- {tag} -->\n"
            elif rel.endswith(".py"):
                out[rel] = f"import bpy  # {tag}\n"
            else:
                out[rel] = f"// {tag}\nexport function build(THREE) {{ return new THREE.Group(); }}\n"
        return out

    def layout(self, text: str) -> dict[str, Any]:
        """A layout the validator accepts — except the first zone's first answer, rejected so the re-ask is covered."""
        plan: ScenePlan = self.plan  # type: ignore[assignment]
        zone = next(z for z in plan.zones if text.startswith(f"Lay out zone {z.name}."))
        if zone is plan.zones[0] and "was rejected" not in text:
            return {"zone": zone.name, "placements": [{"asset": c, "count": 1, "cluster": [1e3, 1e3], "spread_m": 0.0}
                                                      for c in zone.contents]}
        lo, hi = zone.bbox.min, zone.bbox.max
        grid = [(lo[0] + (hi[0] - lo[0]) * i / 4, lo[2] + (hi[2] - lo[2]) * j / 4) for i in range(1, 4) for j in range(1, 4)]
        placed: list[dict[str, Any]] = []
        for c in zone.contents:
            for x, z in grid:
                row = {"asset": c, "count": 2, "cluster": [round(x, 1), round(z, 1)], "spread_m": 0.5}
                done = [p["asset"] for p in placed] + [c]
                trial = ZoneLayout(zone=zone.name, placements=[*placed, row])
                if not validate_layout(trial, zone.model_copy(update={"contents": done}), plan):
                    placed.append(row)
                    break
        return {"zone": zone.name, "placements": placed, "mid_props": 4, "small_props": 8, "ground_cover": 20}


# ============================================================================ the scenarios
def machine() -> StaticPlan:
    """An 11-part object: past the scoped-baseline threshold, with a real attachment tree."""
    parts = [PartPlan(name="Body", role="main mass", description="the shell", bbox=BBox(center=(0, 0.5, 0), extents=(0.6, 1.0, 0.4)))]
    parts += [PartPlan(name=f"Fitting{i}", role=f"fitting {i}", description="a fitting with a bevel", attach_to="Body",
                       bbox=BBox(center=(0.05 * i, 0.2 + 0.05 * i, 0.1), extents=(0.05, 0.05, 0.05))) for i in range(10)]
    return StaticPlan(object_name="Machine", summary="A machine, 0.6 x 0.4 x 1.0 m.", parts=parts, acceptance=[],
                      overall_bbox=BBox(center=(0, 0.5, 0), extents=(0.6, 1.0, 0.4)))


def _example(track: Track) -> Plan:
    return get_track(track).plan_model.model_validate(plan_example(track))


REBUILD = frozenset({"baseline", "r00_baseline_repair1", "r00_baseline_repair2"})   # round 0 never builds


def _textures(scene_plan: Any, out_dir: Path, image_model: Any, model_id: str = "", **kw: Any) -> ScenePack:
    """The texture pack's generator, canned: the env and zone prompts see a two-texture pack."""
    entries = {n: PackEntry(name=n, material_family=fam, subject=n, tile_size_m=1.2, role=role, file=f"{n}.png")
               for n, fam, role in (("cobblestone", "stone", "path"), ("pond_bed", "stone", "water_bed"))}
    return ScenePack(out_dir=str(out_dir), entries=entries, source="default")


def _switch(name: str, value: str) -> None:
    os.environ[name] = value           # isolated() restores the environment
    config.get_settings.cache_clear()


def _scene_textures(rig: Rig) -> None:
    _switch("C3D_SCENE_TEXTURES", "on")
    with mock.patch("codeverse3d.texturing.plan.scene_texture_pack", _textures), mock.patch("codeverse3d.reference.get_image_model"):
        rig.run(rounds=0)
        rig.run(rounds=1, resume=True)


def _two_runs(first: int, then: int) -> Callable[[Rig], None]:
    def go(rig: Rig) -> None:
        rig.run(rounds=first)
        rig.run(rounds=then, resume=True)
    return go


def _switched(name: str, value: str, rounds: int) -> Callable[[Rig], None]:
    def go(rig: Rig) -> None:
        _switch(name, value)
        rig.run(rounds=rounds)
    return go


S, A, SC, G = Track.STATIC_OBJECT, Track.ARTICULATED_OBJECT, Track.SCENE, Track.GRAPHICS
#: name → (track, language, rig options, what runs).  Names read track.language.generator.what.
SCENARIOS: dict[str, tuple[Track, Language, dict[str, Any], Callable[[Rig], None]]] = {
    "static_object.threejs.agent.fresh": (S, Language.THREEJS, {"services_kw": {"contract_errors": 1}},
                                          lambda r: r.run(rounds=2)),
    "static_object.threejs.agent.resume": (S, Language.THREEJS, {}, _two_runs(0, 1)),
    "static_object.threejs.agent.scoped": (S, Language.THREEJS, {"plan": machine(), "targets": ("Body", "Fitting0", "Fitting5")},
                                           lambda r: r.run(rounds=1)),
    "static_object.blender.agent.best_of_2": (S, Language.BLENDER, {}, lambda r: r.run(rounds=1, candidates=2)),
    "static_object.cadquery.agent.rebuild": (S, Language.CADQUERY, {"fail": REBUILD, "services_kw": {"contract_errors": 2}},
                                             lambda r: r.run(rounds=2)),
    "static_object.threejs.single_shot.repair": (S, Language.THREEJS, {"fail": frozenset({"baseline"})},
                                                 lambda r: r.run(rounds=1, single_shot=True)),
    "articulated_object.urdf_blender.agent.fresh": (A, Language.URDF_BLENDER, {"targets": ("Drawer",), "services_kw": {"sweep_errors": 1}},
                                                    lambda r: r.run(rounds=1)),
    "articulated_object.urdf_blender.single_shot.fresh": (A, Language.URDF_BLENDER, {"targets": ("Drawer",)},
                                                          lambda r: r.run(rounds=1, single_shot=True)),
    "scene.scene_threejs.agent.fresh": (SC, Language.SCENE_THREEJS, {"plan": small_scene(), "broken_assets": frozenset({"Bollard"})},
                                        _two_runs(1, 2)),
    "scene.scene_threejs.agent.textures": (SC, Language.SCENE_THREEJS, {"plan": small_scene()}, _scene_textures),
    "scene.scene_threejs.agent.no_layouts": (SC, Language.SCENE_THREEJS, {"plan": small_scene()}, _switched("C3D_ZONE_LAYOUTS", "off", 0)),
    "scene.scene_threejs.agent.hero": (SC, Language.SCENE_THREEJS, {"scores": (0.6, 0.7)}, lambda r: r.run(rounds=0)),
    # the zones never build again: repairs and the rebuild own scene.js/env.js, not the zone files
    "scene.scene_threejs.agent.rebuild": (SC, Language.SCENE_THREEJS, {"plan": small_scene(), "fail": frozenset({"zones_quay_water"})},
                                          lambda r: r.run(rounds=1)),
    "scene.scene_threejs.single_shot.fresh": (SC, Language.SCENE_THREEJS, {"plan": small_scene(), "broken_assets": frozenset({"Bollard"})},
                                              lambda r: r.run(rounds=1, single_shot=True)),
    "graphics.glsl_shader.agent.fresh": (G, Language.GLSL_SHADER, {}, _two_runs(1, 2)),
    "graphics.glsl_shader.agent.best_of_2": (G, Language.GLSL_SHADER, {}, lambda r: r.run(rounds=0, candidates=2)),
    "graphics.glsl_shader.agent.rebuild": (G, Language.GLSL_SHADER, {"fail": REBUILD}, lambda r: r.run(rounds=1)),
    "graphics.glsl_shader.single_shot.fresh": (G, Language.GLSL_SHADER, {}, lambda r: r.run(rounds=1, single_shot=True)),
    "graphics.opengl_python.agent.fresh": (G, Language.OPENGL_PYTHON, {}, lambda r: r.run(rounds=1)),
}
DEFAULT_TARGETS = {S: ("Seat", "FrontLeg", "Backrest"), SC: ("Quay", "Water"), G: ("RainDrops", "CityBokeh")}


# ============================================================================ capture → normalise → hash
@dataclass
class Result:
    payloads: dict[str, str]           # kind → the normalised payload text
    stage_keys: dict[str, str]         # <workspace>:<stage> → inputs_hash


def run_scenario(name: str, root: Path) -> Result:
    """Run one scenario under ``root`` and capture every payload (``mcp_tools`` runs nothing)."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    if name == "mcp_tools":
        with isolated(root):
            return Result(mcp_tools(), {})
    track, language, opts, go = SCENARIOS[name]
    opts = {"plan": _example(track), "targets": DEFAULT_TARGETS.get(track, ()), **opts}
    with isolated(root):
        rig = Rig(root, track, language, **opts)
        go(rig)
        return Result(capture(rig), stage_keys(rig))


def mcp_tools() -> dict[str, str]:
    """The tool list an MCP session is offered, per (track, language)."""
    return {f"mcp_tools:{t.value}/{lang.value}": _json([{"name": d.name, "cost": d.cost_hint, "description": d.description,
                                                         "schema": d.schema()} for d in list_tools(track=t.value, language=lang.value)])
            for t, langs in TRACK_LANGUAGES.items() for lang in langs}


def capture(rig: Rig) -> dict[str, str]:
    roots = sorted({str(rig.root), str(rig.root.resolve())}, key=len, reverse=True)

    def norm(obj: Any) -> Any:
        if isinstance(obj, BaseModel):
            obj = obj.model_dump(mode="json")
        if isinstance(obj, dict):
            return {k: norm(v) for k, v in obj.items() if k not in VOLATILE and not k.endswith("_at")}
        if isinstance(obj, (list, tuple)):
            return [norm(v) for v in obj]
        if isinstance(obj, str):
            for r in roots:
                obj = obj.replace(r, "<TMP>")
        return obj

    def rel(p: Path | str) -> str:
        return norm(str(p)).removeprefix("<TMP>/")

    items: list[tuple[str, dict[str, str]]] = []
    for job in rig.agent.jobs:
        items.append((f"job:{job.label}@r{job.round:02d}", {
            "prompt": norm(job.prompt), "system": norm(job.system_append),
            "scope": _json(norm(job.model_dump(exclude={"prompt", "system_append", "env", "mcp_command"})))}))
    for role, model in (("planner", rig.planner), ("model", rig.model)):
        items += [(f"chat:{role}:{req.label}", chat_payloads(req, norm)) for req in model.requests]
    for judge in rig.services.judges:
        for inp in judge.calls:
            where = rel(Path(inp.renders.views[0].path).parent) if inp.renders.views else "-"
            items.append((f"judge:{judge.rubric}:{where}", {"input": _json(norm(inp)),
                                                            **judge_request(judge.rubric, inp, rig.root, norm)}))
    for ws_root, files in rig.services.contexts:
        items.append((f"context:{rel(ws_root)}", {"files": _json(norm(files))}))
    for log in sorted(p for p in rig.root.rglob("events.jsonl") if not p.is_symlink()):   # telemetry/ links the log
        for line in log.read_text().splitlines():
            e = json.loads(line)
            if e.get("event") == "skills.attached":   # the bundles the session finds in its workspace
                items.append((f"skills:{rel(log.parent)}:{e['kind']}@r{e['round']:02d}",
                              {"routed": _json({"skills": e.get("skills"), "inlined": e.get("inlined")})}))
    return number(items)


def number(items: list[tuple[str, dict[str, str]]]) -> dict[str, str]:
    """``kind/field → text``; a kind seen twice (a retried session, a re-judged round) gets ``#k`` in
    the order of its payloads' hashes — thread order never decides a name."""
    by_kind: dict[str, list[dict[str, str]]] = {}
    for kind, fields in items:
        by_kind.setdefault(kind, []).append(fields)
    out: dict[str, str] = {}
    for kind, group in by_kind.items():
        group.sort(key=lambda f: _sha(_json(f)))
        for k, fields in enumerate(group):
            for fname, text in fields.items():
                out[f"{kind}{f'#{k}' if len(group) > 1 else ''}/{fname}"] = text
    return out


def chat_payloads(req: ChatRequest, norm: Callable[[Any], Any]) -> dict[str, str]:
    """A chat request as a model receives it: system, messages (text and image labels, never the
    pixels or a cache path) and what it is sampled with (the response schema included)."""
    messages = [{"role": m.role, "parts": [p.text if isinstance(p, TextPart) else {"image": p.label, "mime": p.mime} for p in m.parts]}
                for m in req.messages]
    sampling = {"schema": req.response_schema, "temperature": req.temperature, "thinking": req.thinking,
                "max_output_tokens": req.max_output_tokens}
    return {"system": norm(req.system), "messages": _json(norm(messages)), "sampling": _json(norm(sampling))}


class _Capture:
    """A chat model that records the one request and refuses it (the verdict is not needed)."""

    def __init__(self) -> None:
        self.requests: list[ChatRequest] = []

    def generate(self, req: ChatRequest) -> Any:
        self.requests.append(req)
        raise ModelError("manifest capture: the request is all that is needed", retryable=False)


def judge_request(rubric: str, inp: Any, root: Path, norm: Callable[[Any], Any]) -> dict[str, str]:
    """The request the real ``VlmJudge`` of ``rubric`` sends for ``inp`` (montages, rig text, schema)."""
    model = _Capture()
    quiet = logging.getLogger("codeverse3d.judges.vlm_judge")
    level, quiet.level = quiet.level, logging.ERROR     # the refusal is expected: no warning per verdict
    try:
        VlmJudge(rubric=rubric, model_id="fake:judge", chat_model=model, cache_dir=root / "judge_cache").judge(inp)
    finally:
        quiet.setLevel(level)
    return chat_payloads(model.requests[0], norm)


def stage_keys(rig: Rig) -> dict[str, str]:
    """``<workspace>:<stage> → inputs_hash`` of every cached stage."""
    return {f"{d.parent.relative_to(rig.root)}:{p.stem}": h for d in sorted(rig.root.rglob("stages"))
            if d.is_dir() and not d.is_symlink()                      # telemetry/stages links it
            for p in sorted(d.glob("*.json")) if (h := json.loads(p.read_text()).get("inputs_hash"))}


def _json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, indent=1, default=str)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def digest(result: Result) -> dict[str, Any]:
    return {"payloads": {k: {"sha256": _sha(v), "bytes": len(v.encode())} for k, v in sorted(result.payloads.items())},
            "stage_keys": dict(sorted(result.stage_keys.items()))}


# ============================================================================ compare / bless
PAYLOAD_HINT = ("A code-only change leaves every payload byte-identical.  To read what changed: "
                "`python -m tests.prompts.manifest --dump DIR <scenario>` here and on the base commit, then `diff -r`.  "
                "A change a model is meant to see (a bug fix): re-bless in the same commit and say why — "
                "`python -m tests.prompts.manifest --bless`.")
STAGE_HINT = ("A stage key names a stage's inputs: when it moves, a resumed run re-runs that stage (and every recorded "
              "run dir with it).  The default run's keys (textures off, zone layouts on) must never move; any other move is "
              "re-blessed on purpose (`python -m tests.prompts.manifest --bless`), saying why.")


def load() -> dict[str, Any]:
    """The stored manifest's scenarios (``{}`` before the first bless)."""
    return json.loads(MANIFEST.read_text())["scenarios"] if MANIFEST.is_file() else {}


def diff(got: dict[str, Any], want: dict[str, Any]) -> list[str]:
    """One line per entry that changed, appeared or went (empty = identical)."""
    def show(v: Any) -> str:
        return "-" if v is None else f"{v['bytes']} bytes" if isinstance(v, dict) else str(v)

    return [f"  {'changed' if k in got and k in want else 'added' if k in got else 'removed':8} {k}  "
            f"({show(want.get(k))} -> {show(got.get(k))})" for k in sorted(set(got) | set(want)) if got.get(k) != want.get(k)]


def run_all(names: list[str]) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="c3d-manifest-") as tmp:
        return {name: digest(run_scenario(name, Path(tmp) / name)) for name in names}


def write(scenarios: dict[str, Any]) -> None:
    """Sorted, one line per payload, so a re-bless diff names exactly what changed."""
    text = json.dumps({"scenarios": scenarios}, indent=1, sort_keys=True)
    MANIFEST.write_text(re.sub(r'\{\n\s*"bytes": (\d+),\n\s*"sha256": ("\w+")\n\s*\}', r'{"bytes": \1, "sha256": \2}', text) + "\n")


ALL = sorted([*SCENARIOS, "mcp_tools"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tests.prompts.manifest", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bless", action="store_true", help=f"rewrite {MANIFEST.name} from this tree")
    ap.add_argument("--dump", type=Path, metavar="DIR", help="write every normalised payload to DIR/<scenario>/<kind>")
    ap.add_argument("scenarios", nargs="*", help="only these (default: all)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.ERROR)    # the runs' own warnings (skipped writes, re-asks) are expected
    names = args.scenarios or ALL
    if args.dump:
        with tempfile.TemporaryDirectory(prefix="c3d-manifest-") as tmp:
            for name in names:
                for kind, text in run_scenario(name, Path(tmp) / name).payloads.items():
                    p = args.dump / name / kind.replace(":", "_").replace("@", "_")
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(text)
        print(f"wrote {args.dump}")
        return 0
    got, stored = run_all(names), load()
    changed = False
    for name in names:
        if name not in stored:
            changed = True
            print(f"{name}: a new scenario ({len(got[name]['payloads'])} payloads)")
            continue
        lines = diff(got[name]["payloads"], stored[name]["payloads"]) + diff(got[name]["stage_keys"], stored[name]["stage_keys"])
        if lines:
            changed = True
            print(f"{name}:\n" + "\n".join(lines))
    if args.bless:
        write({**stored, **got} if args.scenarios else got)
        print(f"blessed {MANIFEST} ({len(names)} scenario(s))")
        return 0
    return 1 if changed else 0


if __name__ == "__main__":
    sys.exit(main())

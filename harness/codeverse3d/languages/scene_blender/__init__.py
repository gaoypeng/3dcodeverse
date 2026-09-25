"""scene_blender: multi-file bpy scenes on the ``scene`` track (DESIGN 2026-09-24 §3, phase 1).

The agent writes ``src/env.py`` / ``src/zones/<snake>.py`` / ``src/assets/<snake>.py`` in the Blender
frame (``conventions.LANGUAGE_FRAME``: Z up, −Y front, metres); the harness assembles ``src/scene.py``
(data only: which zones / assets / heroes, and the plan's cameras converted to that frame) and builds
it in ONE Blender process (``languages/wrappers/run_bpy_scene.py``): every builder isolated, a zone
that raises dropped with its ``file:line``.  The geometry gates are the JS ones, run on the
harness-written census GLB (``wrappers/_census_glb.py`` → ``runtime_js/lib/glb_scene.mjs``) — one
implementation of census, placement and camera fitting for both scene languages.  The plan stays in
the GLB frame (D12): only the files the agent reads are converted.

This module: lint (the object track's bpy traps + a scene policy table), layout, skeleton, assembler,
and the ``SceneRuntime``.  Rendering (``render_scene``) is DESIGN phase 2 and not here yet.
"""

from __future__ import annotations

import ast
import json
import time
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from codeverse3d.config import Settings, get_settings
from codeverse3d.contracts.artifacts import (
    BuildResult,
    GateFinding,
    GateReport,
    LintKind,
    RenderSet,
    Severity,
)
from codeverse3d.contracts.common import ENTRY_FILE, Language
from codeverse3d.contracts.plan import AssetPlan, CameraPlan, Plan, ScenePlan, ZonePlan
from codeverse3d.conventions import fmt3, glb_basis, to_authoring_frame, to_pascal, to_snake
from codeverse3d.languages._ast_lint import (
    BASE_FORBIDDEN_IMPORTS,
    check_imports,
    describe_parse_failure,
    dotted,
    safe_parse,
)
from codeverse3d.languages._common import (
    compose_build_result,
    missing_entry,
    strip_blender_noise,
    target_file,
    target_line,
    ws_rel,
)
from codeverse3d.languages.blender import (
    BlenderNotFoundError,
    BpyCollector,
    blender_env,
    call_traps,
    finish_for,
    source_traps,
    store_traps,
    use_nodes_trap,
)
from codeverse3d.proc import read_json_or_none, run_subprocess
from codeverse3d.workspace import Workspace

if TYPE_CHECKING:
    from codeverse3d.spatial.probes import SceneProbeResult

LANGUAGE = Language.SCENE_BLENDER
ENTRY_REL = ENTRY_FILE[LANGUAGE]   # "src/scene.py"
GATE = "lint:scene_blender"
#: the harness's frame clock: ``frame(t) = 1 + round(t * FPS)`` — the wrapper sets it, the render
#: driver samples it (t = 0 → frame 1, t = 1.5 → frame 46)
FPS = 30
TIMES: tuple[float, ...] = (0.0, 1.5)


def frame_of(t: float) -> int:
    """The Blender frame that shows animation time ``t`` (seconds)."""
    return 1 + round(t * FPS)


# ===================================================================== layout
#: what a whole-scene session edits: the entry and the environment (zones and assets have their own)
SCENE_FILES: tuple[str, ...] = (ENTRY_REL, "src/env.py")
#: refine targets that are the environment's, whatever the plan calls its zones
ENV_WORDS = frozenset({"env", "environment", "lighting", "sky", "fog", "ground", "water", "light", "world", "sun"})


def zone_file(name: str) -> str:
    """``src/zones/<snake>.py`` — the module that builds zone ``name``."""
    return f"src/zones/{to_snake(name)}.py"


def asset_file(asset: AssetPlan) -> str:
    """A procedural asset's module (``bpy``; a three.js-kind asset in a Blender plan is one too); a
    hero's compiled GLB."""
    snake = to_snake(asset.name)
    return f"public/assets/{snake}.glb" if asset.kind == "blender_glb" else f"src/assets/{snake}.py"


def scene_files_for(plan: Plan | None, target: str, *, alias: dict[str, str] | None = None) -> list[str]:
    """The files that own a scene refine target — the same routing as scene_threejs's
    (``languages.scene_threejs.scene_files_for``), over this language's file table."""
    zone_plans = list(getattr(plan, "zones", None) or [])
    zones = {to_snake(z.name) for z in zone_plans}
    assets = {to_snake(a.name): a for a in (getattr(plan, "assets", None) or [])}
    survivor = {to_snake(k): v for k, v in (alias or {}).items()}
    key = to_snake(target)
    if key in zones:
        return [zone_file(key)]
    if key in assets:
        if assets[key].kind != "blender_glb":
            return [asset_file(assets[key].model_copy(update={"name": survivor.get(key, key)}))]
        return [zone_file(z.name) for z in zone_plans if key in {to_snake(c) for c in z.contents}]
    if key in {to_snake(c.name) for c in (getattr(plan, "cameras", None) or [])} or key in ("camera", "cameras", "composition"):
        return [ENTRY_REL]
    if key in ENV_WORDS:
        return ["src/env.py"]
    if "/" in target and to_snake(target.split("/", 1)[0]) in zones:
        return [zone_file(target.split("/", 1)[0])]
    return []


def module_files(ws: Workspace, sub: str) -> list[Path]:
    """``src/<sub>/*.py`` (sorted; ``_`` helper modules excluded)."""
    d = ws.src / sub
    return sorted(p for p in d.glob("*.py") if not p.name.startswith("_")) if d.is_dir() else []


def hero_files(ws: Workspace) -> list[Path]:
    d = ws.public / "assets"
    return sorted(d.glob("*.glb")) if d.is_dir() else []


# ===================================================================== lint
ALLOWED_IMPORTS = frozenset({
    "bpy", "bmesh", "mathutils", "math", "random", "numpy", "itertools", "functools", "collections", "typing",
    "dataclasses", "colorsys", "statistics", "operator", "enum", "copy", "__future__", "bpy_extras",
    "env", "zones", "assets", "lib",   # the workspace's own packages (src/ is on sys.path)
})
#: the object track's floor + everything that reads a clock or the file system: a scene is
#: deterministic by construction and the harness owns every file
FORBIDDEN_IMPORTS = frozenset(BASE_FORBIDDEN_IMPORTS | {"os", "sys", "pathlib", "time", "datetime", "tempfile",
                                                        "glob", "io", "shlex"})
#: numpy's random module is fine only through a seeded generator
_SEEDED_NP = ("np.random.default_rng", "numpy.random.default_rng", "np.random.RandomState", "numpy.random.RandomState")
#: the scene policy table: dotted-call prefix → (severity, kind, message, hint)
CALL_POLICY: tuple[tuple[str, Severity, LintKind, str, str], ...] = (
    ("bpy.ops.render.", Severity.ERROR, LintKind.HARNESS_OWNED, "rendering is the harness's", "delete every bpy.ops.render.* call"),
    ("bpy.ops.wm.", Severity.ERROR, LintKind.HARNESS_OWNED, "file open/save/append/link is the harness's",
     "delete it; the harness saves the .blend"),
    ("bpy.ops.export_", Severity.ERROR, LintKind.HARNESS_OWNED, "export is the harness's", "delete it"),
    ("bpy.ops.import_", Severity.ERROR, LintKind.HARNESS_OWNED, "imports are the harness's",
     "a hero GLB is already imported: place ctx.assets['<snake>'] as a collection instance"),
    ("bpy.ops.image.save", Severity.ERROR, LintKind.SANDBOX, "no image file writes", "delete it"),
    ("bpy.data.libraries.", Severity.ERROR, LintKind.SANDBOX, "no linking/appending external .blend data",
     "build it procedurally"),
    ("bpy.data.cameras.new", Severity.ERROR, LintKind.HARNESS_OWNED, "cameras are the harness's (the plan's cameras)",
     "delete camera creation"),
    ("bpy.ops.object.camera_add", Severity.ERROR, LintKind.HARNESS_OWNED, "cameras are the harness's (the plan's cameras)",
     "delete camera creation"),
    ("bpy.app.timers.", Severity.ERROR, LintKind.HARNESS_OWNED, "timers never run in a headless build", "delete it"),
    ("subprocess.", Severity.ERROR, LintKind.SANDBOX, "no subprocesses", "delete it"),
    ("os.", Severity.ERROR, LintKind.SANDBOX, "no file-system or process access", "delete it"),
    ("shutil.", Severity.ERROR, LintKind.SANDBOX, "no file IO", "delete it"),
    ("sys.exit", Severity.ERROR, LintKind.SANDBOX, "do not exit; the harness runs every builder", "delete it"),
    ("bpy.ops.view3d.", Severity.WARN, LintKind.API_TRAP, "view3d operators fail in background mode (no 3D viewport)", "delete it"),
    ("bpy.ops.screen.", Severity.WARN, LintKind.API_TRAP, "screen operators fail in background mode", "delete it"),
)
#: scene settings the harness owns: an assigned dotted target that CONTAINS one of these (a
#: material's ``cycles`` or a modifier's ``render_levels`` are the agent's, so the dots matter)
_STORE_OWNED_IN: tuple[tuple[str, str], ...] = (
    ("scene.render.", "render settings"), ("scene.cycles.", "Cycles settings"), ("scene.eevee.", "EEVEE settings"),
    ("scene.view_settings.", "colour management"), ("scene.display_settings.", "colour management"),
)
#: ... or ENDS with one of these
_STORE_OWNED_END: tuple[tuple[str, str], ...] = (
    ("scene.camera", "the active camera"), ("scene.frame_start", "the frame range"), ("scene.frame_end", "the frame range"),
    ("scene.frame_current", "the current frame"), ("scene.render", "render settings"),
)
#: Blender's simple-expression driver subset (verified on 5.0.1: ``driver.is_simple_expression``);
#: anything else runs Python, which a ``--factory-startup`` build silently evaluates to 0 (D10)
SIMPLE_DRIVER_FUNCS = frozenset({
    "radians", "degrees", "abs", "fabs", "floor", "ceil", "trunc", "round", "int", "sin", "cos", "tan", "asin",
    "acos", "atan", "atan2", "exp", "log", "sqrt", "smoothstep", "fmod", "pow", "min", "max", "clamp", "lerp",
})
_SIMPLE_NODES = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Compare, ast.BoolOp, ast.IfExp, ast.Call, ast.Name,
                 ast.Constant, ast.Load, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.USub, ast.UAdd, ast.Not, ast.And,
                 ast.Or, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE)


def simple_driver(expr: str) -> bool:
    """Is ``expr`` inside Blender's simple-expression subset (no Python needed to evaluate it)?"""
    try:
        tree = ast.parse(expr.strip(), mode="eval")
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return False
    for node in ast.walk(tree):
        if not isinstance(node, _SIMPLE_NODES):
            return False
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float, bool)):
            return False
        if isinstance(node, ast.Call) and not (isinstance(node.func, ast.Name) and node.func.id in SIMPLE_DRIVER_FUNCS
                                               and not node.keywords):
            return False
    return True


class _SceneFacts(ast.NodeVisitor):
    """What the scene policy needs beyond the object-track collector: primitive operators inside
    loops, handler access, driver expressions, unseeded randomness, links into the scene root."""

    def __init__(self) -> None:
        self.loop_ops: list[tuple[str, int]] = []
        self.handlers: list[int] = []
        self.expressions: list[tuple[str, int]] = []
        self.use_self: list[int] = []
        self.random_calls: list[tuple[str, int]] = []
        self.random_names: list[tuple[str, int]] = []
        self.root_links: list[int] = []
        self._loops = 0

    def _loop(self, node: ast.For | ast.While | ast.comprehension) -> None:
        self._loops += 1
        self.generic_visit(node)
        self._loops -= 1

    visit_For = visit_While = visit_ListComp = visit_GeneratorExp = _loop  # type: ignore[assignment]

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if dotted(node) == "bpy.app.handlers" or dotted(node).startswith("bpy.app.handlers."):
            self.handlers.append(node.lineno)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if (node.module or "") == "random":
            self.random_names += [(a.name, node.lineno) for a in node.names if a.name != "Random"]
        if (node.module or "").startswith("bpy.app") and any(a.name == "handlers" for a in node.names):
            self.handlers.append(node.lineno)

    def visit_Call(self, node: ast.Call) -> None:
        name = dotted(node.func)
        if self._loops and name.startswith("bpy.ops.mesh.primitive_"):
            self.loop_ops.append((name, node.lineno))
        if name.startswith("random.") and not (name == "random.Random" and node.args):
            self.random_calls.append((name, node.lineno))
        if name.startswith(("np.random.", "numpy.random.")) and not (name in _SEEDED_NP and node.args):
            self.random_calls.append((name, node.lineno))
        if name.endswith("scene.collection.objects.link") or name.endswith("scene.collection.children.link"):
            self.root_links.append(node.lineno)
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        for t in node.targets:
            d = dotted(t)
            if d.endswith(".expression") and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                self.expressions.append((node.value.value, node.lineno))
            if d.endswith(".use_self"):
                self.use_self.append(node.lineno)
        self.generic_visit(node)


def _f(kind: LintKind, sev: Severity, msg: str, line: int | None, hint: str, target: str) -> GateFinding:
    data: dict[str, object] = {"kind": kind.value}
    if line:
        data["line"] = line
    return GateFinding(gate=GATE, severity=sev, target=f"{target}:{line}" if line else target, message=msg,
                       fix_hint=hint, data=data)


def _regate(findings: list[GateFinding], target: str) -> list[GateFinding]:
    """The object track's bpy traps, re-stamped as this gate's findings on ``target``."""
    out = []
    for f in findings:
        line = f.data.get("line")
        out.append(f.model_copy(update={"gate": GATE, "target": f"{target}:{line}" if line else target}))
    return out


def _policy(c: BpyCollector, facts: _SceneFacts, *, target: str, role: str) -> list[GateFinding]:
    out: list[GateFinding] = []
    E, W = Severity.ERROR, Severity.WARN

    def _import_finding(kind: str, mod: str, line: int) -> GateFinding:
        if kind == "forbidden":
            return _f(LintKind.FORBIDDEN_IMPORT, E, f"forbidden import `{mod}`", line,
                      "a scene uses bpy / bmesh / mathutils / math / numpy and ctx.rng(seed); no clock, no files", target)
        return _f(LintKind.BAD_IMPORT, W, f"unexpected import `{mod}` (not available in the build)", line,
                  "use bpy, bmesh, mathutils, math, numpy and your own src/ modules", target)

    out.extend(check_imports(c.imports, forbidden=FORBIDDEN_IMPORTS, allowed=ALLOWED_IMPORTS, make_finding=_import_finding))
    for name, call in c.calls:
        for prefix, sev, kind, msg, hint in CALL_POLICY:
            if name.startswith(prefix):
                out.append(_f(kind, sev, f"`{name}`: {msg}", call.lineno, hint, target))
        if name == "open":
            out.append(_f(LintKind.SANDBOX, E, "file IO via open() is forbidden", call.lineno, "the harness writes every file", target))
        if name.endswith(".frame_set"):
            out.append(_f(LintKind.HARNESS_OWNED, E, f"`{name}`: the harness samples the frames", call.lineno,
                          "animate with keyframes / simple drivers; never set the frame", target))
        if role != "env" and name.startswith("bpy.data.worlds.new"):
            out.append(_f(LintKind.HARNESS_OWNED, E, "the world is src/env.py's", call.lineno, "build the world in build_env(ctx)", target))
        out.extend(_regate(call_traps(c, name, call), target))
    for store, line in c.attr_stores:
        out.extend(_regate(store_traps(store, line), target))
        owned = next((what for key, what in _STORE_OWNED_IN if key in store), None) or next(
            (what for key, what in _STORE_OWNED_END if store.endswith(key)), None)
        if owned:
            out.append(_f(LintKind.HARNESS_OWNED, E, f"`{store}`: {owned} are the harness's", line, "delete the assignment", target))
        if role != "env" and store.endswith(".world"):
            out.append(_f(LintKind.HARNESS_OWNED, E, f"`{store}`: the world is src/env.py's", line, "build the world in build_env(ctx)", target))
        out.extend(_regate(use_nodes_trap(store, line), target))
    for const, line in c.constants:
        if const == "NISHITA":
            out.append(_f(LintKind.API_TRAP, E, "sky_type 'NISHITA' was renamed in Blender 5.0 (it raises)", line,
                          "sky.sky_type = 'MULTIPLE_SCATTERING'", target))
    for line in facts.handlers[:1]:
        out.append(_f(LintKind.HARNESS_OWNED, E, "bpy.app.handlers: a frame hook does not travel with the .blend", line,
                      "animate with keyframes, simple drivers on `frame` or the geometry-nodes Scene Time node", target))
    for expr, line in facts.expressions:
        if not simple_driver(expr):
            out.append(_f(LintKind.API_TRAP, E, f"driver expression {expr!r} needs Python, which the build never runs (it evaluates to 0)",
                          line, "keyframes, or a simple expression of `frame` (sin/cos/min/max/clamp/lerp, + - * /, "
                                "comparisons; no attribute access, no ** or %)", target))
    for line in facts.use_self[:1]:
        out.append(_f(LintKind.API_TRAP, E, "driver.use_self needs Python, which the build never runs", line,
                      "use a driver variable or keyframes", target))
    for name, line in facts.random_calls[:1] + facts.random_names[:1]:
        out.append(_f(LintKind.NONDETERMINISM, E, f"`{name}`: unseeded randomness", line,
                      "rng = ctx.rng(seed); rng.uniform(a, b) — the only randomness a scene uses", target))
    for name, line in facts.loop_ops[:1]:
        out.append(_f(LintKind.HANG_RISK, W, f"`{name}` inside a loop: every operator call rebuilds the scene state", line,
                      "build meshes with bmesh / bpy.data, or model once and place collection instances", target))
    if role == "zone":
        for line in facts.root_links[:1]:
            out.append(_f(LintKind.LAYOUT, W, "linked into the scene root: it will not belong to this zone", line,
                          "ctx.collection.objects.link(obj)", target))
    return out


def lint_scene_source(source: str, *, target: str, role: str) -> list[GateFinding]:
    """Lint one scene module; ``role`` is ``entry`` / ``env`` / ``zone`` / ``asset`` / ``helper``."""
    tree, exc = safe_parse(source, target)
    if tree is None:
        msg, hint, line = describe_parse_failure(exc)  # type: ignore[arg-type]
        return [_f(LintKind.SYNTAX, Severity.ERROR, msg, line, hint, target)]
    c = BpyCollector()
    c.visit(tree)
    facts = _SceneFacts()
    facts.visit(tree)
    out = _policy(c, facts, target=target, role=role)
    out.extend(_regate(source_traps(c, source), target))
    out.extend(_exports(tree, target=target, role=role))
    return out


def _exports(tree: ast.Module, *, target: str, role: str) -> list[GateFinding]:
    defs = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    stem = Path(target).stem
    need = {"zone": ("build", "build(ctx) -> bpy.types.Collection: link everything into ctx.collection and return it"),
            "asset": (f"build_{stem}", f"build_{stem}(ctx, variant=0) -> bpy.types.Collection, built at the origin, base on z = 0"),
            "env": ("build_env", "build_env(ctx) -> dict: world, sun, ground, atmosphere")}.get(role)
    out = []
    if need and need[0] not in defs:
        out.append(_f(LintKind.LAYOUT, Severity.ERROR, f"{target} defines no `{need[0]}`", None, f"define {need[1]}", target))
    if role == "env" and "height_at" not in defs:
        out.append(_f(LintKind.LAYOUT, Severity.WARN, "src/env.py defines no height_at(x, y)", None,
                      "define height_at(x, y) -> z (the ground the zones seat on; the harness uses z = 0 without it)", target))
    return out


def _role(ws: Workspace, p: Path) -> str:
    if p == ws.root / ENTRY_REL:
        return "entry"
    if p == ws.src / "env.py":
        return "env"
    if p.parent == ws.src / "zones" and not p.name.startswith("_"):
        return "zone"
    if p.parent == ws.src / "assets" and not p.name.startswith("_"):
        return "asset"
    return "helper"


def lint(ws: Workspace) -> GateReport:
    """Every ``src/**.py`` except the harness's own library (``src/lib/``, syntax only); passed iff no ERROR."""
    t0 = time.monotonic()
    findings: list[GateFinding] = []
    if not (ws.root / ENTRY_REL).is_file():
        findings.append(_f(LintKind.MISSING_FILE, Severity.ERROR, f"{ENTRY_REL} is missing", None,
                           "the harness assembles src/scene.py; re-run the assemble step", ENTRY_REL))
    for p in sorted(ws.src.rglob("*.py")) if ws.src.is_dir() else []:
        rel = ws_rel(ws, p)
        source = p.read_text(errors="replace")
        if p.is_relative_to(ws.src / "lib"):
            tree, exc = safe_parse(source, rel)
            if tree is None:
                msg, hint, line = describe_parse_failure(exc)  # type: ignore[arg-type]
                findings.append(_f(LintKind.SYNTAX, Severity.ERROR, msg, line, hint, rel))
            continue
        role = _role(ws, p)
        if role in ("zone", "asset") and p.stem != to_snake(p.stem):
            findings.append(_f(LintKind.NAMING, Severity.ERROR, f"module name '{p.name}' is not snake_case", None,
                               f"rename it to {to_snake(p.stem)}.py", rel))
        findings.extend(lint_scene_source(source, target=rel, role=role))
    return GateReport.of(GATE, findings, duration_ms=int((time.monotonic() - t0) * 1000))


# ===================================================================== skeleton
def _v(v: Sequence[float], *, extents: bool = False) -> tuple[float, float, float]:
    """A plan (GLB-frame) vector in this language's frame (D12: prompts and skeleton convert)."""
    return tuple(round(c, 3) for c in to_authoring_frame(v, LANGUAGE.value, extents=extents))  # type: ignore[return-value]


def _env_source(plan: ScenePlan) -> str:
    c, e = _v(plan.bounds.center), _v(plan.bounds.extents, extents=True)
    span = max(e[0], e[1], 40.0)
    ground = round(max(120.0, span * 2.5), 1)
    fog_h = round(max(e[2] * 2.0, 20.0), 1)
    interior = "True" if plan.interior else "False"
    return f'''"""src/env.py — the environment: world (sky), sun, ground, atmosphere{", enclosure" if plan.interior else ""}.

ENV PLAN: {plan.environment.strip()}
setting: {plan.setting.strip()}   mood: {plan.mood.strip()}
Frame: Z up, -Y front, metres.  BOUNDS below are the plan's, in this frame.
CONTRACT: height_at(x, y) -> z is THE ground: the terrain mesh and every zone's seat use it.
  build_env(ctx) -> dict builds the world, lamps, ground and atmosphere into ctx.collection and returns
  what zones may share (materials, water level, sun direction ...) — they read it as ctx.env.
  Rewrite the sky / sun / ground / fog below to match the plan; keep build_env and height_at.
"""
import math

import bmesh
import bpy
from mathutils import Vector

BOUNDS = {{"center": ({fmt3(c)}), "extents": ({fmt3(e)})}}
INTERIOR = {interior}
GROUND_SIZE = {ground}
SUN_AZIMUTH_DEG = 45.0     # compass angle of the sun: 0 = +X, 90 = +Y
SUN_ELEVATION_DEG = 35.0


def height_at(x, y):
    """z of the ground at (x, y) — flat until you shape it (then the ground mesh follows)."""
    return 0.0


def _material(name, rgb, roughness=0.8, metallic=0.0):
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat


def _ground(ctx, material):
    me = bpy.data.meshes.new("Ground")
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=96, y_segments=96, size=GROUND_SIZE / 2)
    for v in bm.verts:
        v.co.z = height_at(v.co.x, v.co.y)
    bm.to_mesh(me)
    bm.free()
    me.materials.append(material)
    ob = bpy.data.objects.new("Ground", me)
    ctx.collection.objects.link(ob)
    return ob


def _sky(ctx):
    world = bpy.data.worlds.new("Sky")
    ctx.scene.world = world
    nodes, links = world.node_tree.nodes, world.node_tree.links
    sky = nodes.new("ShaderNodeTexSky")
    sky.sky_type = "MULTIPLE_SCATTERING"
    sky.sun_elevation = math.radians(SUN_ELEVATION_DEG)
    sky.sun_rotation = math.radians(SUN_AZIMUTH_DEG)
    links.new(sky.outputs["Color"], nodes["Background"].inputs["Color"])
    nodes["Background"].inputs["Strength"].default_value = 0.4


def _sun(ctx):
    az, el = math.radians(SUN_AZIMUTH_DEG), math.radians(SUN_ELEVATION_DEG)
    towards_sun = Vector((math.cos(el) * math.cos(az), math.cos(el) * math.sin(az), math.sin(el)))
    lamp = bpy.data.lights.new("Sun", "SUN")
    lamp.energy = 3.0
    ob = bpy.data.objects.new("Sun", lamp)
    ob.rotation_euler = (-towards_sun).to_track_quat("-Z", "Y").to_euler()
    ctx.collection.objects.link(ob)
    return towards_sun


def _atmosphere(ctx):
    """A bounded fog volume over the scene (a world volume over a large scene swallows the sun)."""
    cx, cy, _ = BOUNDS["center"]
    me = bpy.data.meshes.new("Atmosphere")
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(GROUND_SIZE, GROUND_SIZE, {fog_h}), verts=bm.verts)
    bmesh.ops.translate(bm, vec=(cx, cy, {fog_h / 2}), verts=bm.verts)
    bm.to_mesh(me)
    bm.free()
    mat = bpy.data.materials.new("AtmosphereFog")
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    nodes.remove(nodes["Principled BSDF"])
    fog = nodes.new("ShaderNodeVolumePrincipled")
    fog.inputs["Density"].default_value = {0.001 if plan.interior else 0.002}
    links.new(fog.outputs["Volume"], nodes["Material Output"].inputs["Volume"])
    me.materials.append(mat)
    ctx.collection.objects.link(bpy.data.objects.new("Atmosphere", me))


def _enclosure(ctx, material):
    """Floor, walls and ceiling on the bounds' faces (an interior's shell; cut the openings)."""
    (cx, cy, cz), (ex, ey, ez) = BOUNDS["center"], BOUNDS["extents"]
    t = 0.2
    slabs = {{
        "Floor": ((cx, cy, cz - ez / 2 - t / 2), (ex, ey, t)),
        "Ceiling": ((cx, cy, cz + ez / 2 + t / 2), (ex, ey, t)),
        "WallBack": ((cx, cy + ey / 2 + t / 2, cz), (ex, t, ez)),
        "WallLeft": ((cx - ex / 2 - t / 2, cy, cz), (t, ey, ez)),
        "WallRight": ((cx + ex / 2 + t / 2, cy, cz), (t, ey, ez)),
    }}
    for name, (centre, size) in slabs.items():
        me = bpy.data.meshes.new(name)
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=size, verts=bm.verts)
        bmesh.ops.translate(bm, vec=centre, verts=bm.verts)
        bm.to_mesh(me)
        bm.free()
        me.materials.append(material)
        ctx.collection.objects.link(bpy.data.objects.new(name, me))
    lamp = bpy.data.lights.new("CeilingLight", "AREA")
    lamp.energy = 40.0 * ex * ey
    lamp.size = max(0.5, min(ex, ey) * 0.5)
    ob = bpy.data.objects.new("CeilingLight", lamp)
    ob.location = (cx, cy, cz + ez / 2 - 0.05)
    ctx.collection.objects.link(ob)


def build_env(ctx):
    _sky(ctx)
    sun = _sun(ctx)
    ground_mat = _material("GroundMat", (0.28, 0.33, 0.22))
    _ground(ctx, ground_mat)
    if INTERIOR:
        _enclosure(ctx, _material("WallMat", (0.72, 0.68, 0.6)))
    _atmosphere(ctx)
    return {{"ground_material": ground_mat, "sun_direction": tuple(sun)}}
'''


def _asset_source(a: AssetPlan) -> str:
    snake, pascal = to_snake(a.name), to_pascal(a.name)
    w, h, d = (max(0.05, float(v)) for v in a.approx_size_m)   # plan order: width, height, depth
    rgb, rough, metal = finish_for(a.description)
    return f'''"""src/assets/{snake}.py — asset "{pascal}": {a.description.strip()}

approx size {w:g} x {d:g} x {h:g} m (x = width, y = depth, z = height); expected instances: ~{a.instances_hint}
CONTRACT: build_{snake}(ctx, variant=0) -> bpy.types.Collection — link every part into ctx.collection
  (named '{pascal}'), built ONCE at the origin with its base on z = 0, front facing -Y.  Zones place
  collection instances of it (ctx.assets['{snake}']); never link it anywhere else.
"""
import bmesh
import bpy


def build_{snake}(ctx, variant=0):
    mat = bpy.data.materials.new("{pascal}Mat")
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = ({fmt3(rgb)}, 1.0)
    bsdf.inputs["Roughness"].default_value = {rough}
    bsdf.inputs["Metallic"].default_value = {metal}
    # PLACEHOLDER blockout at the planned size — REPLACE with the real construction (several parts).
    me = bpy.data.meshes.new("{pascal}Body")
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=({w:g}, {d:g}, {h:g}), verts=bm.verts)
    bmesh.ops.translate(bm, vec=(0.0, 0.0, {h / 2:g}), verts=bm.verts)
    bm.to_mesh(me)
    bm.free()
    me.materials.append(mat)
    ctx.collection.objects.link(bpy.data.objects.new("{pascal}Body", me))
    return ctx.collection
'''


def _zone_source(z: ZonePlan, assets: dict[str, AssetPlan], counters: dict[str, int]) -> str:
    """A zone stub placing each planned asset once; ``counters`` numbers the placed copies across
    zones, because a Blender object name is global (a second ``Lantern_0`` would be ``Lantern_0.001``)."""
    snake, pascal = to_snake(z.name), to_pascal(z.name)
    c, e = _v(z.bbox.center), _v(z.bbox.extents, extents=True)
    places = []
    for i, name in enumerate(z.contents):
        a = assets.get(to_snake(name))
        if a is None:
            continue
        ox = (-e[0] / 4 + (i % 3) * e[0] / 4) if len(z.contents) > 1 else 0.0
        oy = (-e[1] / 4 + (i // 3) * e[1] / 4) if len(z.contents) > 3 else 0.0
        n = counters.get(to_snake(a.name), 0)
        counters[to_snake(a.name)] = n + 1
        places.append(f'    place(ctx, "{to_snake(a.name)}", "{to_pascal(a.name)}_{n}", {round(c[0] + ox, 2)}, {round(c[1] + oy, 2)})')
    body = "\n".join(places) or "    pass  # TODO: build this zone's content (seat everything with ctx.height_at(x, y))"
    return f'''"""src/zones/{snake}.py — zone "{pascal}": {z.description.strip()}

PLAN bbox (Z up, -Y front, metres): centre ({fmt3(c)}) extents ({fmt3(e)}).  Contents: {", ".join(z.contents) or "-"}
CONTRACT: build(ctx) -> bpy.types.Collection — ctx.collection is this zone's (named '{pascal}', active,
  so bpy.ops primitives land in it); every PLACED thing is ONE root object in it, named <Name>_<i>
  (object names are global in Blender: never reuse one another zone uses).  Seat things on
  ctx.height_at(x, y); keep them inside the bbox.  Assets: a collection-instance Empty of
  ctx.assets['<snake>'].  Animation is data: keyframes, or simple drivers on `frame`.
"""
import bpy


def place(ctx, key, name, x, y, rot_z=0.0):
    """One placed copy of asset `key`: a collection-instance Empty seated on the ground."""
    source = ctx.assets.get(key)
    if source is None:
        return None
    ob = bpy.data.objects.new(name, None)
    ob.instance_type = "COLLECTION"
    ob.instance_collection = source
    ob.location = (x, y, ctx.height_at(x, y))
    ob.rotation_euler[2] = rot_z
    ctx.collection.objects.link(ob)
    return ob


def build(ctx):
{body}
    return ctx.collection
'''


def _cam(c: CameraPlan) -> str:
    return (f'    {{"name": "{to_snake(c.name)}", "location": ({fmt3(_v(c.position))}), '
            f'"look_at": ({fmt3(_v(c.look_at))}), "fov": {c.fov:g}}},')


def render_scene_py(zones: Sequence[str], assets: Sequence[str], heroes: Sequence[str], cameras: Sequence[CameraPlan],
                    *, title: str = "", summary: str = "") -> str:
    """Deterministic ``src/scene.py``: data the wrapper reads, nothing to execute."""
    def table(keys: Sequence[str]) -> str:
        return "{\n" + "".join(f'    "{k}": "{to_pascal(k)}",\n' for k in keys) + "}" if keys else "{}"

    plan_line = f"PLAN: {title} — {summary.strip()}\n" if title else ""
    return f'''"""src/scene.py — ASSEMBLED BY THE HARNESS (codeverse3d assembler).  Edit env / zones / assets
instead; re-assembly overwrites this file.
{plan_line}
The harness builds the scene in one Blender process: env.build_env(ctx); every hero GLB and asset
module (build_<snake>(ctx)); then every zone's build(ctx) — each on its own (a zone that raises is
dropped and reported with its file:line); then the CAMERAS below (Z up, -Y front, metres; fov is
the VERTICAL field of view in degrees).  The census is the t = 0 pose, frame 1.
"""

ZONES = {table(zones)}
ASSETS = {table(assets)}
HEROES = {table(heroes)}
CAMERAS = [
{chr(10).join(_cam(c) for c in cameras)}
]
'''


def write_skeleton(ws: Workspace, plan: Plan | None) -> list[Path]:
    """Starter files for a scene plan (env, one module per zone and per procedural asset, the
    assembled entry); ``public/assets/`` for the heroes."""
    if not isinstance(plan, ScenePlan):
        raise TypeError(f"scene_blender needs a ScenePlan, got {type(plan).__name__}")
    written: list[Path] = []

    def put(rel: str, text: str) -> None:
        path = ws.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        written.append(path)

    put("src/env.py", _env_source(plan))
    assets = {to_snake(a.name): a for a in plan.assets}
    for a in plan.assets:
        if a.kind != "blender_glb":
            put(asset_file(a), _asset_source(a))
    counters: dict[str, int] = {}
    for z in plan.zones:
        put(zone_file(z.name), _zone_source(z, assets, counters))
    put(ENTRY_REL, render_scene_py([to_snake(z.name) for z in plan.zones],
                                   [to_snake(a.name) for a in plan.assets if a.kind != "blender_glb"], [],
                                   list(plan.cameras[:6]), title=plan.title, summary=plan.summary))
    (ws.public / "assets").mkdir(parents=True, exist_ok=True)
    return written


# ===================================================================== build
WRAPPER = Path(__file__).resolve().parent.parent / "wrappers" / "run_bpy_scene.py"
#: what a build (re)writes under ``artifacts/`` — wiped first, so nothing of a previous build reads as current
BUILD_OUTPUTS = ("build.json", "census.json", "census.glb", "scene.blend", "bpy_build.json", "bpy_census.json",
                 "scene_probe.json")


class WrapperRun(BaseModel):
    """One Blender process over the workspace: the wrapper's report as a BuildResult (typed
    timeout / crash / script error), its raw report and the bpy facts."""

    result: BuildResult
    report: dict[str, Any] = Field(default_factory=dict)
    facts: dict[str, Any] = Field(default_factory=dict)


def _fallback_camera(plan: ScenePlan | None) -> CameraPlan:
    if plan is None:
        return CameraPlan(name="overview", position=(30.0, 18.0, 30.0), look_at=(0.0, 1.0, 0.0), fov=50.0)
    c, e = plan.bounds.center, plan.bounds.extents
    r = max(e[0], e[2], 10.0)
    return CameraPlan(name="overview", position=(c[0] + 0.8 * r, c[1] + 0.5 * r, c[2] + 0.8 * r), look_at=c, fov=50.0,
                      purpose="fallback: the plan names no camera")


class AssembleResult(BaseModel):
    scene_path: str
    zones_included: list[str] = Field(default_factory=list)
    zones_failed: dict[str, str] = Field(default_factory=dict)
    env_ok: bool = True
    cameras: list[CameraPlan] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    census: dict[str, Any] = Field(default_factory=dict)


class SceneBlenderRuntime:
    """``SceneRuntime`` for ``Language.SCENE_BLENDER``."""

    language = LANGUAGE
    entry_globs: tuple[str, ...] = (ENTRY_REL, "src/zones/*.py", "src/assets/*.py", "src/env.py")

    zone_file = staticmethod(zone_file)
    asset_file = staticmethod(asset_file)

    def __init__(self, *, blender: str | None = None, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._blender = blender

    # ------------------------------------------------------------------ layout
    def expected_files(self, plan: Plan | None) -> list[str]:
        return list(SCENE_FILES)

    def files_for(self, plan: Plan | None, target: str, *, alias: dict[str, str] | None = None) -> list[str]:
        return scene_files_for(plan, target, alias=alias)

    def skeleton(self, ws: Workspace, plan: Plan | None) -> list[Path]:
        return write_skeleton(ws, plan)

    def lint(self, ws: Workspace) -> GateReport:
        return lint(ws)

    # ------------------------------------------------------------------ the Blender process
    def blender_binary(self) -> str:
        b = self._blender or self._settings.resolve_blender()
        if not b or not Path(b).exists():
            raise BlenderNotFoundError("Blender binary not found; set C3D_BINARIES__BLENDER=/path/to/blender")
        return b

    def run_wrapper(self, ws: Workspace, out_dir: Path, *, zones: dict[str, str] | None = None, blend: bool = True,
                    timeout_s: float | None = None) -> WrapperRun:
        """Build the workspace in Blender, outputs into ``out_dir`` (``bpy_build.json``,
        ``bpy_census.json``, ``census.glb``, ``scene.blend``); ``zones`` overrides the entry's ZONES."""
        out_dir.mkdir(parents=True, exist_ok=True)
        basis = ",".join(f"{v:g}" for row in glb_basis(LANGUAGE.value) for v in row)
        cmd = [self.blender_binary(), "-b", "--factory-startup", "--python", str(WRAPPER), "--",
               "--scene", str(ws.root / ENTRY_REL), "--out", str(out_dir), "--basis", basis,
               "--fps", str(FPS), "--frames", f"{frame_of(TIMES[0])},{frame_of(TIMES[-1])}",
               "--rlimit-gb", str(self._settings.limits.bpy_rlimit_gb), "--seed", "0"]
        if zones is not None:
            cmd += ["--zones", json.dumps(zones)]
        if not blend:
            cmd.append("--no-blend")
        report_path, facts_path = out_dir / "bpy_build.json", out_dir / "bpy_census.json"
        for p in (report_path, facts_path):
            p.unlink(missing_ok=True)
        proc = run_subprocess(cmd, cwd=ws.root, env=blender_env(),
                              timeout_s=float(timeout_s or self._settings.limits.build_timeout_s))
        report = read_json_or_none(report_path) or {}
        result = compose_build_result(language=LANGUAGE.value, proc=proc, build_json=report_path, census_json=facts_path,
                                      glb_path=out_dir / "census.glb", extra_paths={}, output_filter=strip_blender_noise,
                                      publish=False)
        if report.get("error_type") == "CensusGlbError":
            result = result.model_copy(update={"harness_failure": True})
        facts = read_json_or_none(facts_path) or {}
        return WrapperRun(result=result, report=report, facts=facts)

    def _measure(self, ws: Workspace, *, timeout_s: float | None, keep: tuple[str, ...] = ()
                 ) -> tuple[WrapperRun, GateReport | None, dict[str, Any]]:
        """Wrapper + the census-GLB probe: (the Blender run, the ``scene_probe`` gate or ``None``
        when nothing was built, the merged census).  Every output but ``keep`` is wiped first."""
        from codeverse3d.spatial.probes import PROBE_GATE, run_probe

        ws.artifacts.mkdir(parents=True, exist_ok=True)
        ws.stage_artifacts(*[n for n in BUILD_OUTPUTS if n not in keep]).invalidate()
        run = self.run_wrapper(ws, ws.artifacts, timeout_s=timeout_s)
        if not run.result.ok:
            return run, None, {}
        probe, _, census = run_probe(ws, glb=ws.artifacts / "census.glb", facts=run.facts, timeout_s=timeout_s)
        findings = stage_findings(run.report) + probe.findings
        gate = GateReport.of(PROBE_GATE, findings, duration_ms=probe.duration_ms)
        ws.write_json(ws.artifacts / "gates" / "scene_probe.json", gate)
        if census:
            census["language"] = LANGUAGE.value   # the placement / frame hints speak bpy for it
            ws.write_json(ws.artifacts / "census.json", census)
        return run, gate, census

    # ------------------------------------------------------------------ protocol
    def build(self, ws: Workspace, *, timeout_s: int | None = None) -> BuildResult:
        """Blender build + the census-GLB probe; ok iff the scene built and ``scene_probe`` passed
        (a dropped zone, an env that raised or a Python driver fail it, with their file:line)."""
        t0 = time.time()
        ws.artifacts.mkdir(parents=True, exist_ok=True)
        if (missing := missing_entry(ws, self.language)) is not None:
            ws.stage_artifacts(*[n for n in BUILD_OUTPUTS if n != "build.json"]).invalidate()
            return missing
        run, gate, census = self._measure(ws, timeout_s=timeout_s)
        if gate is None:
            res = run.result.model_copy(update={"duration_ms": int((time.time() - t0) * 1000)})
            ws.write_json(ws.artifacts / "build.json", res)
            return res
        errors = sorted(gate.errors, key=lambda f: 0 if target_line(f.target) else 1)
        first = errors[0] if errors else None
        res = BuildResult(
            ok=gate.passed, language=self.language.value, glb_path=None,
            extra_paths={k: str(ws.artifacts / v) for k, v in (("census", "census.json"), ("census_glb", "census.glb"),
                                                               ("blend", "scene.blend"), ("scene_probe", "gates/scene_probe.json"))
                         if (ws.artifacts / v).is_file()},
            stdout_tail=_summary_text(gate), stderr_tail=run.result.stderr_tail,
            error_type=(first.data.get("kind") or first.gate) if first else "",
            error_message=first.message if first else "",
            error_file=target_file(first.target) if first else "",
            error_line=target_line(first.target) if first else None,
            duration_ms=int((time.time() - t0) * 1000), census=census, gates=[gate],
            harness_failure=bool(first is not None and first.data.get("harness_failure")),
        )
        ws.write_json(ws.artifacts / "build.json", res)
        return res

    def probe(self, ws: Workspace, *, timeout_s: float = 60.0) -> SceneProbeResult:
        """The build's own measurement (Blender run + census-GLB probe), without touching
        ``build.json``: an offline scene has no cheaper way to load."""
        from codeverse3d.spatial.probes import PROBE_GATE, probe_result

        if not (ws.root / ENTRY_REL).is_file():
            gate = GateReport.of(PROBE_GATE, [GateFinding(gate=PROBE_GATE, severity=Severity.ERROR, target=ENTRY_REL,
                                                          message=f"{ENTRY_REL} does not exist", fix_hint="re-run the assemble step")])
            return probe_result(gate, {})
        run, gate, census = self._measure(ws, timeout_s=max(timeout_s, float(self._settings.limits.build_timeout_s)),
                                          keep=("build.json",))
        if gate is None:
            r = run.result
            finding = GateFinding(gate=PROBE_GATE, severity=Severity.ERROR,
                                  target=f"{r.error_file}:{r.error_line}" if r.error_line else (r.error_file or ENTRY_REL),
                                  message=f"the scene did not build: {r.error_type}: {r.error_message}"[:1500],
                                  data={"harness_failure": True} if r.harness_failure else {})
            return probe_result(GateReport.of(PROBE_GATE, [finding]), {})
        return probe_result(gate, census)

    def assemble(self, ws: Workspace, plan: ScenePlan) -> AssembleResult:
        """Build every zone module on disk once (isolated, no .blend) and write ``src/scene.py``
        with the zones that built and the plan's cameras — scene_threejs's assembler semantics."""
        return assemble(self, ws, plan)

    def render_scene(self, ws: Workspace, out_dir: Path, *, cameras: list[CameraPlan] | None = None, orbit: bool = True,
                     times: Sequence[float] = TIMES, width: int = 1024, height: int = 576, sheet: bool = True) -> RenderSet:
        raise NotImplementedError(
            "scene_blender rendering is DESIGN phase 2 (render_bpy_scene.py + spatial/render_blender.py), not landed yet: "
            "a scene_blender workspace builds and passes its geometry gates but cannot be rendered or judged")


def stage_findings(report: dict[str, Any]) -> list[GateFinding]:
    """The wrapper's isolated-stage failures as ``scene_probe`` findings: a zone / asset / env that
    raised is an ERROR on its ``file:line`` (the zone is gone from the scene); a hero GLB that
    would not import or is missing is a WARN on the file."""
    from codeverse3d.spatial.probes import PROBE_GATE

    out: list[GateFinding] = []
    what = {"zone": "zone {name} raised {error_type}: {error_message} — it is dropped from the scene",
            "asset": "asset {name} raised {error_type}: {error_message} — zones placing it get nothing",
            "env": "src/env.py raised {error_type}: {error_message} — the scene built without its env (flat ground at z = 0)",
            "scene": "create_scene raised {error_type}: {error_message}",
            "hero": "hero {name} did not import: {error_type}: {error_message}"}
    for e in report.get("errors") or []:
        stage = str(e.get("stage"))
        line = e.get("error_line")
        target = f"{e.get('error_file')}:{line}" if line else str(e.get("error_file") or ENTRY_REL)
        msg = what.get(stage, "{name} raised {error_type}: {error_message}").format(
            name=e.get("name"), error_type=e.get("error_type"), error_message=str(e.get("error_message"))[:600])
        src = str(e.get("error_source") or "")
        out.append(GateFinding(gate=PROBE_GATE, severity=Severity.WARN if stage == "hero" else Severity.ERROR, target=target,
                               message=msg + (f"  at: {src}" if src else ""), fix_hint="fix the quoted line; the rest of "
                               "the scene built without it" if stage != "hero" else "rebuild the hero asset",
                               data={"kind": f"{stage}_error", "stage": stage, "name": e.get("name"), "line": line}))
    for w in report.get("warnings") or []:
        out.append(GateFinding(gate=PROBE_GATE, severity=Severity.WARN, target="scene", message=str(w)[:500],
                               data={"kind": "build_warning"}))
    return out


# ===================================================================== assemble
_ASSEMBLE_OUT = "assemble_probe"


def assemble(runtime: SceneBlenderRuntime, ws: Workspace, plan: ScenePlan | None, *, probe: bool = True,
             timeout_s: float | None = None) -> AssembleResult:
    zones = [p.stem for p in module_files(ws, "zones")]
    if not zones:
        raise FileNotFoundError(f"no zone modules under {ws.src / 'zones'}")
    assets = [p.stem for p in module_files(ws, "assets")]
    heroes = [p.stem for p in hero_files(ws)]
    cams = list(plan.cameras[:6]) if plan is not None and plan.cameras else [_fallback_camera(plan)]
    warnings: list[str] = []
    env_ok = (ws.src / "env.py").is_file()
    if not env_ok:
        warnings.append("src/env.py missing: scene assembled without env (flat ground at z = 0, no world or lamps)")
    healthy, failed = list(zones), {}
    census: dict[str, Any] = {}
    out = ws.root / ENTRY_REL
    if probe:
        # the probe run needs an entry that lists every asset and hero; its zones come from --zones
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_scene_py([], assets, heroes, cams))
        run = runtime.run_wrapper(ws, ws.artifacts / _ASSEMBLE_OUT, zones={z: to_pascal(z) for z in zones}, blend=False,
                                  timeout_s=timeout_s)
        if run.result.ok or run.report.get("zones_ok") is not None:
            healthy = [z for z in zones if z in set(run.report.get("zones_ok") or [])]
            failed = {z: str(m) for z, m in (run.report.get("zones_failed") or {}).items()}
        else:
            warnings.append(f"assembler probe did not run: {run.result.error_type}: {run.result.error_message}"[:300])
        for e in run.report.get("errors") or []:
            if e.get("stage") == "env":
                env_ok = False
                warnings.append(f"env.py failed in probe: {e.get('error_type')}: {e.get('error_message')}"[:300])
        census = {k: run.facts[k] for k in ("lights", "fog", "background", "unique_tris", "instanced_tris") if k in run.facts}
    if not healthy:
        warnings.append("no zone module survived probing; scene.py assembled with zero zones")
    out.write_text(render_scene_py(healthy, assets, heroes, cams, title=getattr(plan, "title", ""),
                                   summary=getattr(plan, "summary", "")))
    result = AssembleResult(scene_path=str(out), zones_included=healthy, zones_failed=failed, env_ok=env_ok,
                            cameras=cams, warnings=warnings, census=census)
    ws.write_json(ws.artifacts / "assemble.json", result)
    return result


# ===================================================================== helpers
def _summary_text(gate: GateReport) -> str:
    lines = [f"scene_probe: {'ok' if gate.passed else 'FAILED'} ({len(gate.errors)} errors)"]
    lines += [f"  - {f.target or ''}: {f.message}"[:400] for f in gate.findings if f.severity != Severity.INFO][:12]
    return "\n".join(lines)


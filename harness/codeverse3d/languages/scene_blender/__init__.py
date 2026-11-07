"""scene_blender: multi-file bpy scenes on the ``scene`` track.

The agent writes ``src/env.py`` / ``src/zones/<snake>.py`` / ``src/assets/<snake>.py`` in the Blender
frame; the harness assembles ``src/scene.py`` and builds it in one Blender process
(``wrappers/run_bpy_scene.py``).  Geometry gates run on the harness-written census GLB.
This module holds lint, layout, skeleton, assembler and the ``SceneRuntime``."""

from __future__ import annotations

import ast
import json
import logging
import re
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
from codeverse3d.conventions import (
    SCENE_TIMES,
    ZONE_ROOT_KEY,
    fmt3,
    glb_basis,
    to_authoring_frame,
    to_pascal,
    to_snake,
)
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
from codeverse3d.languages.base import AssembleResult, scene_files_for
from codeverse3d.languages.blender import (
    BlenderNotFoundError,
    BpyCollector,
    blender_binary,
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
log = logging.getLogger(__name__)
ENTRY_REL = ENTRY_FILE[LANGUAGE]
GATE = "lint:scene_blender"
#: the harness frame clock: ``frame(t) = 1 + round(t * FPS)``
FPS = 30


# ===================================================================== layout
#: what a whole-scene session edits
SCENE_FILES: tuple[str, ...] = (ENTRY_REL, "src/env.py")
#: refine targets that are the environment's, whatever the plan calls its zones
ENV_WORDS = frozenset({"env", "environment", "lighting", "sky", "fog", "ground", "water", "light", "world", "sun"})


def zone_file(name: str) -> str:
    return f"src/zones/{to_snake(name)}.py"


def asset_file(asset: AssetPlan) -> str:
    snake = to_snake(asset.name)
    return f"public/assets/{snake}.glb" if asset.kind == "blender_glb" else f"src/assets/{snake}.py"


def module_files(ws: Workspace, sub: str) -> list[Path]:
    d = ws.src / sub
    return sorted(p for p in d.glob("*.py") if not p.name.startswith("_")) if d.is_dir() else []


def hero_files(ws: Workspace) -> list[Path]:
    d = ws.public / "assets"
    return sorted(d.glob("*.glb")) if d.is_dir() else []


# ===================================================================== lint
ALLOWED_IMPORTS = frozenset({
    "bpy", "bmesh", "mathutils", "math", "random", "numpy", "itertools", "functools", "collections", "typing",
    "dataclasses", "colorsys", "statistics", "operator", "enum", "copy", "__future__", "bpy_extras",
    "env", "zones", "assets",
})
#: the object track's floor + everything that reads a clock or the file system
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
#: scene settings the harness owns: an assigned dotted target that CONTAINS one of these
_STORE_OWNED_IN: tuple[tuple[str, str], ...] = (
    ("scene.render.", "render settings"), ("scene.cycles.", "Cycles settings"), ("scene.eevee.", "EEVEE settings"),
    ("scene.view_settings.", "colour management"), ("scene.display_settings.", "colour management"),
)
#: ... or ENDS with one of these
_STORE_OWNED_END: tuple[tuple[str, str], ...] = (
    ("scene.camera", "the active camera"), ("scene.frame_start", "the frame range"), ("scene.frame_end", "the frame range"),
    ("scene.frame_current", "the current frame"), ("scene.render", "render settings"),
)
#: Blender's simple-expression driver subset; anything else evaluates to 0 under ``--factory-startup``
SIMPLE_DRIVER_FUNCS = frozenset({
    "radians", "degrees", "abs", "fabs", "floor", "ceil", "trunc", "round", "int", "sin", "cos", "tan", "asin",
    "acos", "atan", "atan2", "exp", "log", "sqrt", "smoothstep", "fmod", "pow", "min", "max", "clamp", "lerp",
})
_SIMPLE_NODES = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Compare, ast.BoolOp, ast.IfExp, ast.Call, ast.Name,
                 ast.Constant, ast.Load, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.USub, ast.UAdd, ast.Not, ast.And,
                 ast.Or, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE)


def simple_driver(expr: str) -> bool:
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

    visit_For = visit_While = visit_ListComp = visit_GeneratorExp = _loop

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
    tree, exc = safe_parse(source, target)
    if tree is None:
        msg, hint, line = describe_parse_failure(exc)
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
            "env": ("build_env", "build_env(ctx) -> dict: the environment the plan describes (world / lighting / ground or "
                                 "floor / atmosphere as the brief needs); zones read the returned dict as ctx.env")}.get(role)
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
    t0 = time.monotonic()
    findings: list[GateFinding] = []
    if not (ws.root / ENTRY_REL).is_file():
        findings.append(_f(LintKind.MISSING_FILE, Severity.ERROR, f"{ENTRY_REL} is missing", None,
                           "the harness assembles src/scene.py; re-run the assemble step", ENTRY_REL))
    for p in sorted(ws.src.rglob("*.py")) if ws.src.is_dir() else []:
        rel = ws_rel(ws, p)
        source = p.read_text(errors="replace")
        role = _role(ws, p)
        if role in ("zone", "asset") and p.stem != to_snake(p.stem):
            findings.append(_f(LintKind.NAMING, Severity.ERROR, f"module name '{p.name}' is not snake_case", None,
                               f"rename it to {to_snake(p.stem)}.py", rel))
        findings.extend(lint_scene_source(source, target=rel, role=role))
    return GateReport.of(GATE, findings, duration_ms=int((time.monotonic() - t0) * 1000))


ENV_FILE = "src/env.py"


#: the env check's data entry (deleted after use): only build_env runs
ENV_CHECK_REL = "src/_c3d_env_check.py"


_TB_FRAME_RE = re.compile(r'File "(?P<file>[^"]+)", line (?P<line>\d+)')


def env_frame(traceback_text: str) -> str:
    """``src/env.py:N`` of the deepest traceback frame in the env module (with its source line), or ``""``."""
    lines = traceback_text.splitlines()
    found = ""
    for i, text in enumerate(lines):
        m = _TB_FRAME_RE.search(text)
        if m is None or not m.group("file").replace("\\", "/").endswith("/" + ENV_FILE):
            continue
        code = lines[i + 1].strip() if i + 1 < len(lines) and not _TB_FRAME_RE.search(lines[i + 1]) else ""
        found = f"{ENV_FILE}:{m.group('line')}" + (f" `{code[:160]}`" if code else "")
    return found


def _put(path: Path, text: str, written: list[Path]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    written.append(path)


# ===================================================================== skeleton
def _v(v: Sequence[float], *, extents: bool = False) -> tuple[float, float, float]:
    return tuple(round(c, 3) for c in to_authoring_frame(v, LANGUAGE.value, extents=extents))


def _env_source(plan: ScenePlan) -> str:
    """``src/env.py`` as an interface stub: the plan's data, the two exports the wrapper calls and a
    placeholder ground plate.  No world, lamp or atmosphere, so it fails the probe until the author writes them."""
    from codeverse3d.languages.scene_threejs import PLACEHOLDER_GROUND_SPANS, sun_from_plan

    c, e = _v(plan.bounds.center), _v(plan.bounds.extents, extents=True)
    span = max(e[0], e[1], 1.0)
    ground = round(span * PLACEHOLDER_GROUND_SPANS, 1)
    interior = "True" if plan.interior else "False"
    glazed = bool(getattr(plan, "glazed", False))
    az, el = sun_from_plan(plan)
    az_line = (f"SUN_AZIMUTH_DEG = {az:g}      # the plan's light: compass 0 = +X, 90 = +Y (the sky and the lamp share it; zones may import it)"
               if az is not None else
               "SUN_AZIMUTH_DEG = None     # the plan states no azimuth: set the brief's (compass 0 = +X, 90 = +Y; zones may import it)")
    el_line = (f"SUN_ELEVATION_DEG = {el:g}" if el is not None else "SUN_ELEVATION_DEG = None   # the plan states none: set the brief's")
    return f'''"""src/env.py — the environment: the ground every zone seats on, the light, the sky / backdrop, the
atmosphere{" and the enclosure" if plan.interior else ""}.

ENV PLAN: {plan.environment.strip()}
setting: {plan.setting.strip()}   mood: {plan.mood.strip()}
Frame: Z up, -Y front, metres.  BOUNDS below are the plan's, in this frame: the extent of the PLANNED
content and the cameras' subject, not the edge of the world — land, backdrop and scenery continue beyond
it as the brief implies.
CONTRACT (the wrapper calls exactly these two):
  height_at(x, y) -> z: the ONE ground function — the ground mesh samples it and every zone seats on it
    (measured: scene_placement floating > 0.05 m outdoors / 0.02 m indoors, sunken > 0.10 m); the plan's
    cameras expect the ground near their look_at at the height the plan states, and each eye above it
    (camera_underground, cap 0.50).
  build_env(ctx) -> dict: builds the world, the lamps, the ground and the atmosphere the plan describes into
    ctx.collection and returns what zones may share (materials, water level, sun direction ...) — ctx.env to them.
MEASURED: a world exists and an atmosphere exists — a world Volume or a bounded fog volume (no_fog, ERROR) —
  with 1 / density >= the plan span / 1.5 (fog_short); the judged frames sit in the exposure band
  (scene_frames: dark_frame / blown_frame / flat_frame); whatever the zones seat on is named Ground... /
  Terrain... (the census finds the ground by name and shape); backdrop geometry is named Sky...; no world
  edge in any authored frame (judged; the census: standing geometry past 1.25 x the bounds' half-extent).
PLACEHOLDER below: ONE level plate named Ground past the plan span, so the census has a surface — and NO
  world, NO lamp, NO atmosphere: as written this module fails the probe and renders black until build_env
  builds the brief's.  The landform, the sky, the light and the atmosphere are yours.  Keep build_env and height_at.
"""
import bmesh
import bpy

BOUNDS = {{"center": ({fmt3(c)}), "extents": ({fmt3(e)})}}
INTERIOR = {interior}      # the plan's: an interior needs the enclosure the brief describes (openings, materials as
                           # named; it reads black without openings or practical lights)
GLAZED = {glazed}          # the plan says the enclosure is glazed; its construction is the brief's
GROUND_SIZE = {ground}     # the placeholder plate's side (m): 3 x the plan span, past the reach the fog gate measures
{az_line}
{el_line}
SEED = 0


def height_at(x, y):
    """z of the ground at (x, y): the one function every zone seats on and the ground mesh follows.  PLACEHOLDER: level."""
    return 0.0


def build_env(ctx):
    # PLACEHOLDER ground: a level plate following height_at — REPLACE with the brief's land (named Ground... / Terrain...)
    ground_mat = bpy.data.materials.new("GroundMat")
    bsdf = ground_mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (0.159, 0.144, 0.117, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.95
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=32, y_segments=32, size=GROUND_SIZE / 2)
    for v in bm.verts:
        v.co.z = height_at(v.co.x, v.co.y)
    me = bpy.data.meshes.new("Ground")
    bm.to_mesh(me)
    bm.free()
    me.materials.append(ground_mat)
    ctx.collection.objects.link(bpy.data.objects.new("Ground", me))
    # NO world, lamp or atmosphere here: the brief's go in this function (the docstring says what is measured)
    return {{"ground_material": ground_mat}}
'''


_ASSET_BODY = """import bmesh
import bpy


def build_{snake}(ctx, variant=0):
    mat = bpy.data.materials.new("{pascal}Mat")
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = ({rgb}, 1.0)
    bsdf.inputs["Roughness"].default_value = {rough}
    bsdf.inputs["Metallic"].default_value = {metal}
    # PLACEHOLDER blockout at the planned size — REPLACE with the real thing (a single box fails the asset check);
    # base on z = 0, front facing -Y.
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=({w}, {d}, {h}), verts=bm.verts)
    bmesh.ops.translate(bm, vec=(0.0, 0.0, {h2}), verts=bm.verts)
    me = bpy.data.meshes.new("{pascal}")
    bm.to_mesh(me)
    bm.free()
    me.materials.append(mat)
    ctx.collection.objects.link(bpy.data.objects.new("{pascal}", me))
    return ctx.collection
"""


def _asset_source(a: AssetPlan) -> str:
    snake, pascal = to_snake(a.name), to_pascal(a.name)
    w, h, d = (max(0.05, float(v)) for v in a.approx_size_m)
    rgb, rough, metal = finish_for(a.description)
    return f'''"""src/assets/{snake}.py — asset "{pascal}": {a.description.strip()}

approx size {w:g} x {d:g} x {h:g} m (x = width, y = depth, z = height); expected instances: ~{a.instances_hint}
CONTRACT: build_{snake}(ctx, variant=0) -> bpy.types.Collection — link every part into ctx.collection
  (named '{pascal}'), built ONCE at the origin with its base on z = 0, front facing -Y.  Zones place
  collection instances of it (ctx.assets['{snake}']); link it nowhere else.  It must read as the plan
  describes it at the judge's camera distance (judged) and measure about the size above (the asset check);
  parts, materials and construction are yours.
"""
{_ASSET_BODY.format(snake=snake, pascal=pascal, rgb=fmt3(rgb), rough=rough, metal=metal,
                    w=f"{w:g}", d=f"{d:g}", h=f"{h:g}", h2=f"{h / 2:g}")}'''


def _zone_source(z: ZonePlan, assets: dict[str, AssetPlan]) -> str:
    snake, pascal = to_snake(z.name), to_pascal(z.name)
    c, e = _v(z.bbox.center), _v(z.bbox.extents, extents=True)
    todo = []
    for name in z.contents:
        a = assets.get(to_snake(name))
        if a is None:
            todo.append(f"  - {name}: not an asset of the plan — built here")
        elif a.kind == "blender_glb":
            todo.append(f"  - {a.name}: ctx.assets['{to_snake(a.name)}'] — the hero GLB public/assets/{to_snake(a.name)}.glb imported by the harness")
        else:
            todo.append(f"  - {a.name}: ctx.assets['{to_snake(a.name)}'] — built by src/assets/{to_snake(a.name)}.py; ~{a.instances_hint} planned")
    todo_block = "\n".join(todo) if todo else "  (the plan lists no contents)"
    return f'''"""src/zones/{snake}.py — zone "{pascal}": {z.description.strip()}

PLAN bbox (Z up, -Y front, metres): centre ({fmt3(c)}) extents ({fmt3(e)}).
CONTRACT: build(ctx) -> bpy.types.Collection — ctx.collection is this zone's (named '{pascal}', active): link
  everything into it and return it.  Every PLACED thing is ONE root object in it, named <Name>_<i> (object
  names are global in Blender: never reuse one another zone uses).  Seat things on ctx.height_at(x, y)
  (measured: scene_placement floating > 0.05 m outdoors / 0.02 m indoors, sunken > 0.10 m).  The planned
  contents are measured against the plan bounds and this bbox (scene_placement: present, at the plan's size,
  not outside the bounds); scenery may continue past the bbox as the brief implies.
  Assets: ctx.assets['<snake>'] is a Collection — place collection-instance Empties of it (instance_type
  'COLLECTION', seated on ctx.height_at).  Animation: keyframes or simple drivers (the build samples frames 1 and
  46 = t 0 / 1.5 s; motion between them is measured).
TODO — this zone's planned contents, none placed yet:
{todo_block}
"""


def build(ctx):
    # TODO: build the contents listed above (seat everything with ctx.height_at)
    return ctx.collection
'''


def _cam(c: CameraPlan) -> str:
    return (f'    {{"name": "{to_snake(c.name)}", "location": ({fmt3(_v(c.position))}), '
            f'"look_at": ({fmt3(_v(c.look_at))}), "fov": {c.fov:g}}},')


def render_scene_py(zones: Sequence[str], assets: Sequence[str], heroes: Sequence[str], cameras: Sequence[CameraPlan],
                    *, title: str = "", summary: str = "") -> str:
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


def write_skeleton(ws: Workspace, plan: Plan | None = None) -> list[Path]:
    """Starter files for a scene plan; none without one."""
    if plan is None:
        return []
    if not isinstance(plan, ScenePlan):
        raise TypeError(f"scene_blender needs a ScenePlan, got {type(plan).__name__}")
    written: list[Path] = []
    _put(ws.root / ENV_FILE, _env_source(plan), written)
    assets = {to_snake(a.name): a for a in plan.assets}
    for a in plan.assets:
        if a.kind != "blender_glb":
            _put(ws.root / asset_file(a), _asset_source(a), written)
    for z in plan.zones:
        _put(ws.root / zone_file(z.name), _zone_source(z, assets), written)
    _put(ws.root / ENTRY_REL, render_scene_py([to_snake(z.name) for z in plan.zones],
                                              [to_snake(a.name) for a in plan.assets if a.kind != "blender_glb"], [],
                                              list(plan.cameras[:6]), title=plan.title, summary=plan.summary), written)
    (ws.public / "assets").mkdir(parents=True, exist_ok=True)
    return written


# ===================================================================== build
WRAPPER = Path(__file__).resolve().parent.parent / "wrappers" / "run_bpy_scene.py"
#: what a build (re)writes under ``artifacts/``; wiped first
BUILD_OUTPUTS = ("build.json", "census.json", "census.glb", "scene.blend", "bpy_build.json", "bpy_census.json",
                 "scene_probe.json")


class WrapperRun(BaseModel):

    result: BuildResult
    report: dict[str, Any] = Field(default_factory=dict)
    facts: dict[str, Any] = Field(default_factory=dict)


class SceneBlenderRuntime:

    language = LANGUAGE
    entry_globs: tuple[str, ...] = (ENTRY_REL, "src/zones/*.py", "src/assets/*.py", "src/env.py")

    zone_file = staticmethod(zone_file)
    asset_file = staticmethod(asset_file)

    @property
    def env_file(self) -> str:
        return ENV_FILE

    def __init__(self, *, blender: str | None = None, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._blender = blender

    # ------------------------------------------------------------------ layout
    def expected_files(self, plan: Plan | None) -> list[str]:
        return list(SCENE_FILES)

    def files_for(self, plan: Plan | None, target: str) -> list[str]:
        return scene_files_for(plan, target, zone_file=zone_file, asset_file=asset_file, entry=ENTRY_REL, env_file="src/env.py",
                               env_words=ENV_WORDS)

    def skeleton(self, ws: Workspace, plan: Plan | None) -> list[Path]:
        return write_skeleton(ws, plan)

    def lint(self, ws: Workspace) -> GateReport:
        return lint(ws)

    def lib_interface(self, ws: Workspace) -> str:
        return ""

    def check_env(self, ws: Workspace) -> str:
        """``""`` when ``src/env.py`` lints clean, runs ``build_env`` alone and lights what it builds;
        else the first failure, quoted.  A Blender that cannot run gives ``""``."""
        from codeverse3d.spatial.probes import unlit

        env = ws.root / ENV_FILE
        if not env.is_file():
            return f"{ENV_FILE} does not exist"
        own = [f for f in lint_scene_source(env.read_text(errors="replace"), target=ENV_FILE, role="env")
               if f.severity == Severity.ERROR]
        if own:
            return "\n".join(f"{f.target}: {f.message}" + (f" ({f.fix_hint})" if f.fix_hint else "") for f in own[:6])
        entry = ws.root / ENV_CHECK_REL
        entry.write_text(render_scene_py([], [], [], []))
        try:
            run = self.run_wrapper(ws, ws.artifacts / "env_check", zones={}, blend=False, entry=ENV_CHECK_REL)
        except BlenderNotFoundError as e:
            log.warning("env check could not run: %s", e)
            return ""
        finally:
            entry.unlink(missing_ok=True)
        raised = [e for e in run.report.get("errors") or [] if e.get("stage") == "env"]
        if raised:
            e = raised[0]
            at = f"{e.get('error_file') or ENV_FILE}:{e.get('error_line')}" if e.get("error_line") else ENV_FILE
            if (e.get("error_file") or ENV_FILE) != ENV_FILE and (own_at := env_frame(str(e.get("traceback") or ""))):
                # the raise surfaced in a helper the env called: lead with the env's own call line
                at = f"{own_at} (raised in {at})"
            return f"{at}: build_env raised {e.get('error_type')}: {str(e.get('error_message'))[:600]}"
        if not run.result.ok:
            if run.result.harness_failure:
                return ""
            return f"{ENV_FILE}: the env-only build failed: {run.result.error_type}: {run.result.error_message}"[:900]
        facts = run.facts
        dark = unlit({"totals": {"lights": int(facts.get("lights") or 0)}, "background": facts.get("background")}, offline=True)
        return f"{ENV_FILE}: {dark.message}" if dark is not None else ""

    # ------------------------------------------------------------------ the Blender process
    def run_wrapper(self, ws: Workspace, out_dir: Path, *, zones: dict[str, str] | None = None, blend: bool = True,
                    timeout_s: float | None = None, entry: str = ENTRY_REL) -> WrapperRun:
        """Build the workspace in Blender into ``out_dir``; ``zones`` overrides ZONES, ``entry`` is another data entry."""
        out_dir.mkdir(parents=True, exist_ok=True)
        basis = ",".join(f"{v:g}" for row in glb_basis(LANGUAGE.value) for v in row)
        cmd = [blender_binary(self._blender, self._settings), "-b", "--factory-startup", "--python", str(WRAPPER), "--",
               "--scene", str(ws.root / entry), "--out", str(out_dir), "--basis", basis, "--zone-key", ZONE_ROOT_KEY,
               "--fps", str(FPS), "--times", ",".join(f"{t:g}" for t in SCENE_TIMES),
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
        """Wrapper + census-GLB probe: (Blender run, ``scene_probe`` gate or None, merged census)."""
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
            census["language"] = LANGUAGE.value
            ws.write_json(ws.artifacts / "census.json", census)
        return run, gate, census

    # ------------------------------------------------------------------ protocol
    def build(self, ws: Workspace, *, timeout_s: int | None = None) -> BuildResult:
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

    def probe(self, ws: Workspace, *, timeout_s: float | None = None) -> SceneProbeResult:
        from codeverse3d.spatial.probes import PROBE_GATE, probe_result

        if not (ws.root / ENTRY_REL).is_file():
            gate = GateReport.of(PROBE_GATE, [GateFinding(gate=PROBE_GATE, severity=Severity.ERROR, target=ENTRY_REL,
                                                          message=f"{ENTRY_REL} does not exist", fix_hint="re-run the assemble step")])
            return probe_result(gate, {})
        run, gate, census = self._measure(ws, timeout_s=max(timeout_s or 0.0, float(self._settings.limits.build_timeout_s)),
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
        return assemble(self, ws, plan)

    def render_scene(self, ws: Workspace, out_dir: Path, *, cameras: list[CameraPlan] | None = None, orbit: bool = True,
                     times: Sequence[float] = SCENE_TIMES, width: int = 1024, height: int = 576, sheet: bool = True) -> RenderSet:
        from codeverse3d.spatial.render_blender import render_blender_scene

        return render_blender_scene(ws, out_dir, cameras=cameras, orbit=orbit, times=times, width=width,
                                    height=height, sheet=sheet)


def stage_findings(report: dict[str, Any]) -> list[GateFinding]:
    """The wrapper's isolated-stage failures as ``scene_probe`` findings (ERROR for a dropped zone / asset / env, WARN for a bad hero GLB)."""
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


def assemble(runtime: SceneBlenderRuntime, ws: Workspace, plan: ScenePlan, *, timeout_s: float | None = None) -> AssembleResult:
    zones = [p.stem for p in module_files(ws, "zones")]
    if not zones:
        raise FileNotFoundError(f"no zone modules under {ws.src / 'zones'}")
    assets = [p.stem for p in module_files(ws, "assets")]
    heroes = [p.stem for p in hero_files(ws)]
    cams = list(plan.cameras[:6])
    warnings: list[str] = []
    env_ok = (ws.src / "env.py").is_file()
    if not env_ok:
        warnings.append("src/env.py missing: scene assembled without env (flat ground at z = 0, no world or lamps)")
    healthy, failed = list(zones), {}
    census: dict[str, Any] = {}
    out = ws.root / ENTRY_REL
    # the probe run's entry lists every asset and hero; zones come from --zones
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
    out.write_text(render_scene_py(healthy, assets, heroes, cams, title=plan.title,
                                   summary=plan.summary))
    result = AssembleResult(scene_path=str(out), zones_included=healthy, zones_failed=failed, env_ok=env_ok,
                            warnings=warnings, census=census)
    ws.write_json(ws.artifacts / "assemble.json", result)
    return result


# ===================================================================== helpers
def _summary_text(gate: GateReport) -> str:
    lines = [f"scene_probe: {'ok' if gate.passed else 'FAILED'} ({len(gate.errors)} errors)"]
    lines += [f"  - {f.target or ''}: {f.message}"[:400] for f in gate.findings if f.severity != Severity.INFO][:12]
    return "\n".join(lines)


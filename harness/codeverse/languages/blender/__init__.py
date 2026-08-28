"""Blender (bpy) language: runtime, lint, layout, skeleton; wrappers run inside Blender."""

from __future__ import annotations

import ast
import math
import os
import re
import time
from pathlib import Path

from codeverse.config import Settings, get_settings
from codeverse.contracts.artifacts import BuildResult, GateFinding, GateReport, Severity
from codeverse.contracts.common import ENTRY_FILE, Language
from codeverse.contracts.plan import BBox, PartPlan, Plan, StaticPlan
from codeverse.conventions import MAX_TRIS_OBJECT, to_pascal, to_snake
from codeverse.languages._ast_lint import (  # noqa: F401 — dotted re-exported
    BASE_FORBIDDEN_IMPORTS,
    check_imports,
    describe_parse_failure,
    dotted,
    safe_parse,
)
from codeverse.languages._common import (
    compose_build_result,
    run_subprocess,
    strip_blender_noise,
)
from codeverse.proc import scrub_secrets
from codeverse.prompts import PROMPTS_DIR
from codeverse.workspace import Workspace

# ===================================================================== lint
# (merged from codeverse/languages/blender/lint.py, 2026-08-28)
GATE = "lint:blender"

ALLOWED_IMPORTS = {
    "bpy", "bmesh", "mathutils", "math", "random", "numpy", "np", "itertools", "functools",
    "collections", "typing", "dataclasses", "colorsys", "statistics", "operator", "enum", "copy",
    # os/sys are deliberately ALLOWED here (unlike cadquery/opengl): headless bpy scripts
    # touch them legitimately and the forbidden-call rules below catch the harmful uses.
    "sys", "os", "__future__", "bpy_extras",
    "parts",  # the workspace's own src/parts/<snake>.py modules (multi-file layout)
}
FORBIDDEN_IMPORTS = frozenset(BASE_FORBIDDEN_IMPORTS | {"pathlib"})
# dotted-name prefixes of calls that violate the contract (harness owns them)
FORBIDDEN_CALL_PREFIXES: tuple[tuple[str, str], ...] = (
    ("bpy.ops.render.", "rendering is done by the harness; delete all bpy.ops.render.* calls"),
    ("bpy.ops.export_", "export is done by the harness; delete all bpy.ops.export_* calls"),
    ("bpy.ops.import_", "no imports of external files; build geometry procedurally"),
    ("bpy.ops.wm.", "no bpy.ops.wm.* (file open/save/append/link/quit); the harness owns the file"),
    ("bpy.ops.image.save", "no image file writes"),
    ("bpy.data.libraries.", "no linking/appending external .blend data"),
    ("os.system", "no shell commands"),
    ("os.remove", "no file IO"), ("os.unlink", "no file IO"), ("os.rmdir", "no file IO"),
    ("os.makedirs", "no file IO"), ("os.mkdir", "no file IO"), ("os.rename", "no file IO"),
    ("subprocess.", "no subprocesses"),
    ("urllib.", "no network"), ("requests.", "no network"),
    ("shutil.", "no file IO"),
)
WARN_CALL_PREFIXES: tuple[tuple[str, str], ...] = (
    ("bpy.data.cameras.new", "the harness owns cameras; remove camera creation"),
    ("bpy.ops.object.camera_add", "the harness owns cameras; remove camera creation"),
    ("bpy.data.lights.new", "the harness owns lighting; remove light creation"),
    ("bpy.ops.object.light_add", "the harness owns lighting; remove light creation"),
    ("bpy.data.worlds.new", "the harness owns the world/background; remove world code"),
    ("bpy.ops.view3d.", "view3d operators fail in background mode (no 3D viewport)"),
    ("bpy.ops.screen.", "screen operators fail in background mode"),
    ("sys.exit", "do not exit; let the script fall off the end"),
)
# Principled BSDF inputs that raise KeyError in Blender 4.x/5.x → their replacement.
# Verified against Blender 5.0.1: 'Anisotropic' and 'Specular Tint' STILL EXIST (do not list them).
REMOVED_BSDF_INPUTS = {
    "Specular": "Specular IOR Level", "Subsurface": "Subsurface Weight", "Transmission": "Transmission Weight",
    "Emission": "Emission Color (+ 'Emission Strength')", "Subsurface Color": "Base Color (subsurface tint removed)",
    "Clearcoat": "Coat Weight", "Clearcoat Roughness": "Coat Roughness", "Sheen": "Sheen Weight",
    "Transmission Roughness": "(removed) use Roughness",
}
KNOWN_BINDINGS = ("Vector", "Matrix", "Euler", "Quaternion", "bmesh", "math", "random", "np", "numpy", "bpy")
PASCAL_RE = re.compile(r"^[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]*)*(?:_\d+)?$")


class _Collector(ast.NodeVisitor):
    """One pass over the tree; rules read the collected facts."""

    def __init__(self) -> None:
        self.imports: dict[str, int] = {}  # top-level module → line
        self.bound: set[str] = set()
        self.calls: list[tuple[str, ast.Call]] = []
        self.attr_stores: list[tuple[str, int]] = []  # dotted target of assignments
        self.name_loads: list[tuple[str, int]] = []
        self.bm_names: set[str] = set()  # names bound from bmesh.new() / bmesh.from_edit_mesh()
        self.bm_subscripts: list[tuple[str, int]] = []  # (base name, line) of <name>.verts/edges/faces[i]
        self.bsdf_inputs: list[tuple[str, int]] = []
        self.names_assigned: list[str] = []  # string constants assigned to .name / name=
        self.constants: list[tuple[str, int]] = []
        self.has_temp_override = False
        self.keywords: list[tuple[str, int]] = []

    def visit_Import(self, node: ast.Import) -> None:
        for a in node.names:
            top = a.name.split(".")[0]
            self.imports.setdefault(top, node.lineno)
            self.bound.add((a.asname or a.name).split(".")[0])

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        top = (node.module or "").split(".")[0]
        self.imports.setdefault(top, node.lineno)
        for a in node.names:
            self.bound.add(a.asname or a.name)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.bound.add(node.name)
        for a in node.args.args + node.args.kwonlyargs + node.args.posonlyargs:
            self.bound.add(a.arg)
        if node.args.vararg:
            self.bound.add(node.args.vararg.arg)
        if node.args.kwarg:
            self.bound.add(node.args.kwarg.arg)
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.bound.add(node.name)
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            self.name_loads.append((node.id, node.lineno))
        else:
            self.bound.add(node.id)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self.bound.add(node.name)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        name = dotted(node.func)
        self.calls.append((name, node))
        if name.endswith("temp_override"):
            self.has_temp_override = True
        for a in node.args:
            if isinstance(a, ast.Constant) and isinstance(a.value, str):
                self.names_assigned.append(a.value)
        for kw in node.keywords:
            if kw.arg:
                self.keywords.append((kw.arg, node.lineno))
            if kw.arg == "name" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                self.names_assigned.append(kw.value.value)
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        for t in node.targets:
            d = dotted(t)
            if d:
                self.attr_stores.append((d, node.lineno))
        v = node.value
        if isinstance(v, ast.Call) and dotted(v.func) in ("bmesh.new", "bmesh.from_edit_mesh"):
            self.bm_names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        if isinstance(v, ast.Constant) and isinstance(v.value, str):
            self.names_assigned.append(v.value)
        elif isinstance(v, ast.JoinedStr) and v.values and isinstance(v.values[0], ast.Constant):
            self.names_assigned.append(str(v.values[0].value).rstrip("_"))
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if isinstance(node.value, ast.Attribute):
            # only <Name>.verts/edges/faces[i] — e.verts[0] (BMEdge) / me.edges[i] (Mesh) are
            # filtered later against the names actually bound to a bmesh (BMElemSeq).
            if (node.value.attr in ("verts", "edges", "faces") and not isinstance(node.slice, ast.Slice)
                    and isinstance(node.value.value, ast.Name)):
                self.bm_subscripts.append((node.value.value.id, node.lineno))
            if node.value.attr == "inputs" and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
                self.bsdf_inputs.append((node.slice.value, node.lineno))
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str):
            self.constants.append((node.value, node.lineno))


def _f(sev: Severity, msg: str, line: int | None = None, hint: str = "", target: str = "src/model.py") -> GateFinding:
    data = {"line": line} if line else {}
    return GateFinding(gate=GATE, severity=sev, target=target, message=msg, fix_hint=hint, data=data)


def _rules(c: _Collector, source: str, *, target: str, expect_names: bool, expect_bpy: bool) -> list[GateFinding]:
    out: list[GateFinding] = []
    E, W, I = Severity.ERROR, Severity.WARN, Severity.INFO  # noqa: E741
    if expect_bpy and "bpy" not in c.imports:
        out.append(_f(E, f"{target} never imports bpy", 1, "start the file with `import bpy`"))

    def _import_finding(kind: str, mod: str, line: int) -> GateFinding:
        if kind == "forbidden":
            return _f(E, f"forbidden import `{mod}`", line, "only bpy/bmesh/mathutils/math/random/numpy (+stdlib data helpers) are allowed")
        return _f(W, f"unexpected import `{mod}` (not available / not allowed in the build sandbox)", line, "use only bpy, bmesh, mathutils, math, random, numpy")

    out.extend(check_imports(c.imports, forbidden=FORBIDDEN_IMPORTS, allowed=ALLOWED_IMPORTS, make_finding=_import_finding))
    for name, call in c.calls:
        for prefix, hint in FORBIDDEN_CALL_PREFIXES:
            if name.startswith(prefix):
                out.append(_f(E, f"forbidden call `{name}`", call.lineno, hint))
        for prefix, hint in WARN_CALL_PREFIXES:
            if name.startswith(prefix):
                out.append(_f(W, f"`{name}` — {hint}", call.lineno, hint))
        if name == "open":
            out.append(_f(E, "file IO via open() is forbidden", call.lineno, "no file reads/writes; the harness exports for you"))
        if name.startswith("bpy.ops.") and call.args and isinstance(call.args[0], ast.Dict):
            out.append(_f(E, f"`{name}({{...}})` context-dict override was removed in Blender 4.0", call.lineno,
                          "use `with bpy.context.temp_override(object=obj, active_object=obj, selected_objects=[obj]): bpy.ops....()`"))
        if name.endswith("primitive_cube_add"):
            kws = {k.arg for k in call.keywords}
            if "scale" in kws and "size" not in kws:
                out.append(_f(W, "primitive_cube_add(scale=...) without size=1 makes a 2 m cube × scale (extents doubled)", call.lineno,
                              "pass `size=1, scale=(sx, sy, sz)` then `bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)`"))
        if name.endswith("transform_apply"):
            kws = {k.arg for k in call.keywords}
            if kws and not {"location", "rotation", "scale"} <= kws:
                out.append(_f(W, "transform_apply with partial kwargs: unspecified ones default to True (bakes location into mesh data)", call.lineno,
                              "always pass all three: transform_apply(location=False, rotation=False, scale=True)"))
        if name.endswith("modifier_apply") and not c.has_temp_override:
            out.append(_f(W, "bpy.ops.object.modifier_apply needs the object active+selected in OBJECT mode", call.lineno,
                          "either leave modifiers unapplied (the exporter applies them) or wrap: `with bpy.context.temp_override(object=obj): bpy.ops.object.modifier_apply(modifier=mod.name)`"))
        if name.endswith("bpy.ops.object.join") or name == "bpy.ops.object.join":
            out.append(_f(W, "bpy.ops.object.join needs all parts selected + an active object; the result inherits the ACTIVE object's transform", call.lineno,
                          "select all, set `bpy.context.view_layer.objects.active = main_obj` (identity transform) before join — or keep parts separate (preferred: named parts)"))
        if name.endswith("shade_smooth") and any(k.arg == "use_auto_smooth" for k in call.keywords):
            out.append(_f(E, "shade_smooth(use_auto_smooth=...) was removed in 4.1", call.lineno, "use `bpy.ops.object.shade_smooth_by_angle(angle=0.523599)` or `bpy.ops.object.shade_auto_smooth()`"))
        if name.endswith(".calc_normals"):
            out.append(_f(E, "Mesh.calc_normals() was removed in 4.0 (normals are computed automatically)", call.lineno, "delete the call (use me.update() if needed)"))
        if name == "bpy.ops.wm.redraw_timer":
            pass
    for target, line in c.attr_stores:
        if target.endswith(".use_auto_smooth") or target.endswith(".auto_smooth_angle"):
            out.append(_f(E, f"`{target}` was removed in Blender 4.1", line, "use bpy.ops.object.shade_smooth_by_angle(angle=...) or mark sharp edges; or just shade_smooth()"))
        if ".render." in target or target.endswith(".render"):
            out.append(_f(W, f"render settings touched: `{target}`", line, "the harness owns rendering; delete render/engine/resolution code"))
        if target.endswith("scene.camera") or target.endswith(".world"):
            out.append(_f(W, f"`{target}` assignment — cameras/world are owned by the harness", line, "remove camera/world code"))
        if target.endswith(".use_nodes"):
            out.append(_f(I, "`use_nodes = True` is a no-op in Blender 5.x (materials always have a node tree); harmless", line, ""))
    for key, line in c.bsdf_inputs:
        if key in REMOVED_BSDF_INPUTS:
            out.append(_f(E, f"Principled BSDF input '{key}' does not exist in Blender 4.x/5.x (KeyError at runtime)", line,
                          f"use inputs['{REMOVED_BSDF_INPUTS[key]}']"))
        elif key == "Specular Tint":
            out.append(_f(I, "'Specular Tint' is an RGBA colour in Blender 4.x/5.x (a float raises TypeError)", line,
                          'assign a 4-tuple: inputs["Specular Tint"].default_value = (r, g, b, 1.0)'))
    has_lookup = any(n.endswith("ensure_lookup_table") for n, _ in c.calls)
    # only subscripts on a BMESH object need ensure_lookup_table (BMElemSeq); e.verts[0]
    # (BMEdge/BMFace tuples) and me.edges[i] (Mesh collections) are always fine.
    bm_subs = [line for base, line in c.bm_subscripts if base in c.bm_names or base == "bm"]
    if "bmesh" in c.imports and bm_subs and not has_lookup:
        out.append(_f(E, "bmesh verts/edges/faces indexed with [] but ensure_lookup_table() is never called → IndexError", bm_subs[0],
                      "after bm.from_mesh()/any topology change call `bm.verts.ensure_lookup_table(); bm.edges.ensure_lookup_table(); bm.faces.ensure_lookup_table()` before indexing"))
    for name, line in c.name_loads:
        if name in KNOWN_BINDINGS and name not in c.bound:
            hint = {"Vector": "from mathutils import Vector", "Matrix": "from mathutils import Matrix", "Euler": "from mathutils import Euler",
                    "Quaternion": "from mathutils import Quaternion", "np": "import numpy as np", "numpy": "import numpy"}.get(name, f"import {name}")
            out.append(_f(E, f"`{name}` is used but never imported (NameError at runtime)", line, hint))
            break
    if "bpy.context.selected_objects" in source:
        out.append(_f(W, "bpy.context.selected_objects is unreliable in background mode (often empty)", None,
                      "keep references to the objects you create (`obj = bpy.context.object` right after primitive_*_add) instead of re-reading the selection"))
    for const, line in c.constants:
        if const == "BLENDER_EEVEE_NEXT":
            out.append(_f(W, "'BLENDER_EEVEE_NEXT' is not a valid engine id in Blender 5.0", line, "render settings are owned by the harness; delete the line"))
    pascal = [n for n in c.names_assigned if PASCAL_RE.match(n) and n not in ("Cube", "Cylinder", "Sphere", "Plane")]
    if pascal:
        out.append(_f(I, f"named objects: {sorted(set(pascal))[:12]}", None, ""))
    elif expect_names:
        out.append(_f(W, "no PascalCase object names found (e.g. obj.name = 'SeatCushion')", None,
                      "name every visible mesh after its part: `obj.name = 'SeatCushion'`; instances `Leg_0..Leg_3`"))
    return out


def lint_blender_source(
    source: str, *, target: str = "src/model.py", expect_names: bool = True, expect_bpy: bool = True,
) -> GateReport:
    """Lint one bpy script; returns ``GateReport(gate='lint:blender')``.

    ``expect_names=False`` silences the PascalCase-name warning (multi-file entry files
    only import and call builders); ``expect_bpy=False`` allows helper modules that do
    not touch bpy.  :func:`codeverse.languages.blender.lint_workspace` lints a
    whole ``src/`` tree with these set per file.
    """
    t0 = time.monotonic()
    findings: list[GateFinding] = []
    tree, exc = safe_parse(source, target)
    if tree is None:
        msg, hint, line = describe_parse_failure(exc)  # type: ignore[arg-type]
        findings.append(_f(Severity.ERROR, msg, line, hint, target))
        return GateReport(gate=GATE, passed=False, findings=findings, duration_ms=int((time.monotonic() - t0) * 1000))
    c = _Collector()
    c.visit(tree)
    findings = _rules(c, source, target=target, expect_names=expect_names, expect_bpy=expect_bpy)
    for f in findings:
        f.target = target
    passed = not any(f.severity == Severity.ERROR for f in findings)
    return GateReport(gate=GATE, passed=passed, findings=findings, duration_ms=int((time.monotonic() - t0) * 1000))


def lint_blender_file(path: Path, *, target: str = "src/model.py") -> GateReport:
    if not path.is_file():
        return GateReport(gate=GATE, passed=False, findings=[_f(Severity.ERROR, f"{target} is missing", None, "create src/model.py (see the skeleton)", target)])
    return lint_blender_source(path.read_text(), target=target)


# ===================================================================== layout
# (merged from codeverse/languages/blender/layout.py, 2026-08-28)
ENTRY_REL = ENTRY_FILE[Language.BLENDER]  # "src/model.py"
PARTS_DIR = "parts"
PARTS_PKG = "parts"


def part_file_rel(part_name: str) -> str:
    """``'Seat Cushion'`` → ``'src/parts/seat_cushion.py'`` (workspace-relative)."""
    return f"src/{PARTS_DIR}/{to_snake(part_name)}.py"


def build_fn_name(part_name: str) -> str:
    """``'SeatCushion'`` → ``'build_seat_cushion'`` (the function a part file must export)."""
    return f"build_{to_snake(part_name)}"


def part_files(ws: Workspace) -> list[Path]:
    """Part modules present in the workspace (sorted; underscore helper modules excluded)."""
    d = ws.src / PARTS_DIR
    if not d.is_dir():
        return []
    return sorted(p for p in d.glob("*.py") if not p.name.startswith("_"))


def helper_files(ws: Workspace) -> list[Path]:
    """Underscore-prefixed shared modules under ``src/parts/`` (linted, not required to export)."""
    d = ws.src / PARTS_DIR
    return sorted(d.glob("_*.py")) if d.is_dir() else []


def source_files(ws: Workspace) -> list[Path]:
    """Every agent-authored python file under ``src/`` (entry first), including nested dirs."""
    entry = ws.src / "model.py"
    rest = sorted(p for p in ws.src.rglob("*.py") if p != entry)
    return ([entry] if entry.is_file() else []) + rest


def _rel(ws: Workspace, p: Path) -> str:
    return p.relative_to(ws.root).as_posix()


def _finding(sev: Severity, target: str, msg: str, hint: str, line: int | None = None) -> GateFinding:
    return GateFinding(gate=GATE, severity=sev, target=target, message=msg, fix_hint=hint,
                       data={"line": line} if line else {})


def _exported_functions(tree: ast.Module) -> set[str]:
    return {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _imported_part_modules(tree: ast.Module) -> set[str]:
    """snake names of ``parts.<snake>`` modules the entry references via import statements."""
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            dotted = node.module.split(".")
            if dotted[0] == PARTS_PKG:
                if len(dotted) > 1:
                    out.add(dotted[1])
                else:  # ``from parts import seat, leg``
                    out.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            for a in node.names:
                dotted = a.name.split(".")
                if dotted[0] == PARTS_PKG and len(dotted) > 1:
                    out.add(dotted[1])
    return out


def _layout_rules(ws: Workspace, parts: list[Path], entry_tree: ast.Module | None) -> list[GateFinding]:
    """Cross-file rules: naming, exported builders, import-time side effects, unused part files."""
    out: list[GateFinding] = []
    E, W = Severity.ERROR, Severity.WARN
    imported = _imported_part_modules(entry_tree) if entry_tree is not None else set()
    for p in parts:
        target = _rel(ws, p)
        stem = p.stem
        if stem != to_snake(stem):
            out.append(_finding(E, target, f"part file name '{p.name}' is not snake_case",
                                f"rename to src/{PARTS_DIR}/{to_snake(stem)}.py (the harness maps plan part "
                                f"'{stem}' → that file) and fix the import in model.py"))
            continue
        try:
            tree = ast.parse(p.read_text(), filename=target)
        except SyntaxError:
            continue  # reported by the per-file lint
        fn = f"build_{stem}"
        if fn not in _exported_functions(tree):
            out.append(_finding(E, target, f"{target} does not define `def {fn}()`",
                                f"every part file exports `def {fn}() -> bpy.types.Object` returning the named object "
                                f"at its world pose; model.py calls it (`from {PARTS_PKG}.{stem} import {fn}`)"))
        for node in tree.body:
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                name = getattr(node.value.func, "id", "")
                if name.startswith("build_") or name == "main":
                    out.append(_finding(W, target, f"{target} calls `{name}()` at import time",
                                        "part files only DEFINE builders; model.py calls them once (a module-level "
                                        "call here builds the part twice → auto-suffixed 'Name.001')", node.lineno))
        if entry_tree is not None and stem not in imported:
            out.append(_finding(W, target, f"{target} is never imported by src/model.py → its part is not built",
                                f"add `from {PARTS_PKG}.{stem} import {fn}` to model.py and call `{fn}()` in main()"))
    return out


def lint_workspace(ws: Workspace) -> GateReport:
    """Lint every python file under ``src/`` + the multi-file layout rules (one merged report)."""
    t0 = time.monotonic()
    entry = ws.src / "model.py"
    if not entry.is_file():
        return GateReport(gate=GATE, passed=False, duration_ms=0, findings=[_finding(
            Severity.ERROR, ENTRY_REL, f"{ENTRY_REL} is missing",
            "create src/model.py (entry: imports src/parts/<snake>.py builders and calls them; see the skeleton)")])
    parts = part_files(ws)
    findings: list[GateFinding] = []
    for p in source_files(ws):
        target = _rel(ws, p)
        is_entry = p == entry
        is_helper = p.name.startswith("_")
        rep = lint_blender_source(p.read_text(), target=target,
                                  expect_names=not (is_helper or (is_entry and bool(parts))),
                                  expect_bpy=not is_helper)
        findings.extend(rep.findings)
    entry_tree: ast.Module | None
    try:
        entry_tree = ast.parse(entry.read_text(), filename=ENTRY_REL)
    except SyntaxError:
        entry_tree = None
    findings.extend(_layout_rules(ws, parts, entry_tree))
    passed = not any(f.severity == Severity.ERROR for f in findings)
    return GateReport(gate=GATE, passed=passed, findings=findings, duration_ms=int((time.monotonic() - t0) * 1000))


# ===================================================================== skeleton
# (merged from codeverse/languages/blender/skeleton.py, 2026-08-28)
# keyword → (rgb, roughness, metallic) placeholder finishes so the first render is not all-grey
_FINISHES: tuple[tuple[tuple[str, ...], tuple[float, float, float], float, float], ...] = (
    (("wood", "oak", "walnut", "pine", "timber", "birch"), (0.55, 0.36, 0.20), 0.55, 0.0),
    (("steel", "chrome", "aluminium", "aluminum", "metal", "iron", "brass"), (0.75, 0.75, 0.78), 0.35, 1.0),
    (("fabric", "cloth", "cushion", "upholster", "velvet", "linen"), (0.45, 0.30, 0.30), 0.9, 0.0),
    (("leather",), (0.30, 0.16, 0.10), 0.6, 0.0),
    (("glass", "acrylic"), (0.85, 0.9, 0.95), 0.05, 0.0),
    (("rubber", "tire", "tyre"), (0.05, 0.05, 0.05), 0.9, 0.0),
    (("plastic", "abs", "nylon"), (0.9, 0.9, 0.9), 0.4, 0.0),
    (("black",), (0.05, 0.05, 0.05), 0.5, 0.0),
    (("white",), (0.9, 0.9, 0.9), 0.5, 0.0),
    (("red",), (0.7, 0.1, 0.1), 0.5, 0.0),
    (("blue",), (0.1, 0.2, 0.7), 0.5, 0.0),
    (("green",), (0.1, 0.5, 0.2), 0.5, 0.0),
)


def finish_for(text: str) -> tuple[tuple[float, float, float], float, float]:
    """Placeholder (rgb, roughness, metallic) from material words; neutral grey otherwise."""
    low = text.lower()
    for words, rgb, rough, metal in _FINISHES:
        if any(w in low for w in words):
            return rgb, rough, metal
    return (0.6, 0.6, 0.6), 0.5, 0.0


def instance_centers(bbox: BBox, n: int, symmetry: str) -> list[tuple[float, float, float]]:
    """Placeholder centres for ``n`` instances of a part using the plan's symmetry hint."""
    cx, cy, cz = bbox.center
    if n <= 1:
        return [(cx, cy, cz)]
    if n == 2 and symmetry == "mirror_x":
        return [(abs(cx), cy, cz), (-abs(cx), cy, cz)]
    if n == 2 and symmetry == "mirror_y":
        return [(cx, abs(cy), cz), (cx, -abs(cy), cz)]
    if n == 4 and symmetry in ("mirror_x", "mirror_y"):
        ax, ay = abs(cx), abs(cy)
        return [(ax, -ay, cz), (-ax, -ay, cz), (-ax, ay, cz), (ax, ay, cz)]
    if symmetry == "radial":
        r = math.hypot(cx, cy)
        a0 = math.atan2(cy, cx)
        return [(r * math.cos(a0 + 2 * math.pi * i / n), r * math.sin(a0 + 2 * math.pi * i / n), cz) for i in range(n)]
    return [(cx, cy, cz) for _ in range(n)]


def _fmt(v: tuple[float, float, float]) -> str:
    return "(" + ", ".join(f"{x:.3f}" for x in v) + ")"


def _bevel(bbox: BBox) -> float:
    return max(0.0, min(0.01, 0.08 * min(bbox.extents)))


# ----------------------------------------------------------------------------- shared text blocks
IMPORTS = '''import math
import random

import bpy
import bmesh  # noqa: F401  (available for custom meshes: bm = bmesh.new(); ... bm.to_mesh(me))
from mathutils import Vector, Matrix  # noqa: F401

random.seed(0)
'''

HELPERS = '''
# ----------------------------------------------------------------------------- helpers (plain bpy)
def make_material(name, rgb, roughness=0.5, metallic=0.0):
    """Principled BSDF material with flat PBR values (what the GLB keeps)."""
    mat = bpy.data.materials.new(name)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat


def add_box(name, center, extents, material=None, bevel=0.0):
    """Axis-aligned box: size=1 cube scaled to `extents`, scale baked into the mesh."""
    bpy.ops.mesh.primitive_cube_add(size=1, location=center, scale=extents)
    obj = bpy.context.object
    obj.name = name
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    if material is not None:
        obj.data.materials.append(material)
    if bevel > 0:
        mod = obj.modifiers.new("Bevel", "BEVEL")
        mod.width = bevel
        mod.segments = 3
        mod.limit_method = "ANGLE"
    return obj

'''

SELFCHECK = '''
def _selfcheck():
    """What the harness checks first: meshes exist, no auto-suffixed names, stands on z=0."""
    bpy.context.view_layer.update()
    meshes = [o for o in bpy.data.objects if o.type == "MESH"]
    assert meshes, "no mesh objects built"
    for o in meshes:
        assert "." not in o.name, f"auto-suffixed name {o.name!r}: give every instance its own name"
    z_min = min((o.matrix_world @ Vector(c)).z for o in meshes for c in o.bound_box)
    assert abs(z_min) < 0.002, f"lowest point z={z_min:.4f}: the object must stand on z=0"
    print(f"[selfcheck] {len(meshes)} mesh objects, z_min={z_min:.4f}")

'''


def _constants(p: PartPlan) -> str:
    mn, mx = p.bbox.min, p.bbox.max
    pre = to_snake(p.name).upper()
    return (
        f"# ---- plan numbers (metres) for {to_pascal(p.name)} — keep the finished part inside this bbox\n"
        f"{pre}_CENTER = {_fmt(p.bbox.center)}\n"
        f"{pre}_EXTENTS = {_fmt(p.bbox.extents)}\n"
        f"{pre}_MIN = {_fmt(mn)}\n"
        f"{pre}_MAX = {_fmt(mx)}\n"
        f"{pre}_INSTANCES = {p.instances}\n"
    )


def _part_function(p: PartPlan) -> str:
    """``def build_<snake>() -> bpy.types.Object`` (placeholder box; instances looped inside)."""
    pascal, snake, fn = to_pascal(p.name), to_snake(p.name), build_fn_name(p.name)
    pre = snake.upper()
    rgb, rough, metal = finish_for(f"{p.material} {p.description}")
    mn, mx = p.bbox.min, p.bbox.max
    lines = [
        f"def {fn}():",
        f'    """{pascal} — {p.role}',
        f"    {p.description}",
        f"    Material: {p.material or 'n/a'}",
        f"    Plan bbox: center {_fmt(p.bbox.center)} extents {_fmt(p.bbox.extents)}",
        f"      x in [{mn[0]:.3f}, {mx[0]:.3f}]  y in [{mn[1]:.3f}, {mx[1]:.3f}]  z in [{mn[2]:.3f}, {mx[2]:.3f}]",
    ]
    if p.attach_to:
        lines.append(f"    Attaches to: {to_pascal(p.attach_to)} (surfaces must touch, no gap)")
    if p.instances > 1:
        lines.append(f"    Instances: {p.instances} ({p.symmetry}) -> named {pascal}_0..{pascal}_{p.instances - 1}, "
                     f"each TOP-LEVEL (no parent Empty — it would merge them into one measured part); returns the list")
    lines += ['    Returns the object(s) at WORLD pose (Z up, -Y front, metres).', '    """']
    lines.append(f"    mat = make_material(\"{pascal}Mat\", {_fmt(rgb)}, roughness={rough}, metallic={metal})")
    if p.instances == 1:
        lines += [
            "    # TODO: replace this placeholder box with the real geometry (keep the name + bbox)",
            f"    return add_box(\"{pascal}\", {pre}_CENTER, {pre}_EXTENTS, mat, bevel={_bevel(p.bbox):.4f})",
        ]
    else:
        centers = [tuple(round(x, 4) for x in c) for c in instance_centers(p.bbox, p.instances, p.symmetry)]
        lines += [
            "    objs = []",
            f"    for i, c in enumerate({centers}):  # TODO: exact placement",
            "        # TODO: replace this placeholder box with the real geometry (keep the names + bbox)",
            f"        objs.append(add_box(f\"{pascal}_{{i}}\", c, {pre}_EXTENTS, mat, bevel={_bevel(p.bbox):.4f}))",
            "    return objs",
        ]
    return "\n".join(lines) + "\n"


def _plan_header(plan: StaticPlan, *, multi_file: bool) -> str:
    ob = plan.overall_bbox
    parts_doc = "\n".join(
        f"  - {to_pascal(p.name)}{'' if p.instances == 1 else f' x{p.instances}'}: {p.role}; bbox center {_fmt(p.bbox.center)} "
        f"extents {_fmt(p.bbox.extents)}" + (f"  [{part_file_rel(p.name)}]" if multi_file else "")
        for p in plan.parts
    )
    accept = "\n".join(f"  - [{a.id}] {a.text}" for a in plan.acceptance) or "  (none listed)"
    layout = (
        f"  * LAYOUT: this file is the ENTRY. Each part lives in src/{PARTS_DIR}/<snake>.py and exports\n"
        f"    build_<snake>() -> bpy.types.Object; main() below imports and calls them in order.\n"
        "    Edit geometry in the part files; keep this file to imports + calls + self-check.\n"
        if multi_file else
        "  * LAYOUT: single file (small object). One build_<snake>() per part, called from main().\n"
    )
    return f'''"""{plan.object_name} — Blender (bpy) model.

{plan.summary}
Style: {plan.style_notes or "n/a"}

CONTRACT (the harness runs this file in an EMPTY scene with `blender -b --factory-startup`):
  * Z is up, -Y is the FRONT, units are METERS. Object stands on z=0, footprint centred on Z.
  * Overall bbox: center {_fmt(ob.center)} extents {_fmt(ob.extents)}
    -> x in [{ob.min[0]:.3f}, {ob.max[0]:.3f}]  y in [{ob.min[1]:.3f}, {ob.max[1]:.3f}]  z in [{ob.min[2]:.3f}, {ob.max[2]:.3f}]
  * One mesh object per part, named EXACTLY as below (PascalCase); instances Name_0..Name_N-1,
    each TOP-LEVEL (never parented under an Empty — that merges them into ONE measured part).
{layout}  * Materials: Principled BSDF (Base Color / Roughness / Metallic). GLB keeps flat PBR + image
    textures only (procedural node textures are NOT exported) — rely on geometry + flat PBR.
  * Modifiers may stay unapplied (the exporter applies them). Keep < 500k triangles, < 120 s.
  * NEVER: cameras, lights, world, render settings, export/import, file IO, bpy.ops.wm.*.
  * Only bpy / bmesh / mathutils / math / random (seeded). No other imports.

PARTS:
{parts_doc}

ACCEPTANCE:
{accept}
"""
'''


# ----------------------------------------------------------------------------- public renderers
def part_file_source(p: PartPlan) -> str:
    """Complete ``src/parts/<snake>.py`` text for one plan part (self-contained, runnable)."""
    pascal, fn = to_pascal(p.name), build_fn_name(p.name)
    doc = (
        f'"""{pascal} — {p.role} (part module; imported by src/model.py).\n\n'
        f"{p.description}\n"
        f"Material: {p.material or 'n/a'}.  Instances: {p.instances}"
        + (f" ({p.symmetry})" if p.instances > 1 else "")
        + (f".  Attaches to: {to_pascal(p.attach_to)} (must touch, no gap)" if p.attach_to else "")
        + f".\n\nExports `{fn}() -> bpy.types.Object`: builds the part at its WORLD pose (Z up, -Y front,\n"
        "metres) with the exact object name(s) and returns it (list of TOP-LEVEL objects when\n"
        "instanced — never parent instances under an Empty).  Do NOT call it here — model.py does.\n"
        "Keep this file self-contained (its own helpers); only bpy/bmesh/mathutils/math/random.\n"
        '"""\n'
    )
    return doc + IMPORTS + "\n" + _constants(p) + HELPERS + "\n# ----------------------------------------------------------------------------- part\n" + _part_function(p)


def model_file_source(plan: StaticPlan) -> str:
    """Complete multi-file entry ``src/model.py``: imports, ordered calls, self-check."""
    imports = "\n".join(f"from {PARTS_PKG}.{to_snake(p.name)} import {build_fn_name(p.name)}" for p in plan.parts)
    calls = "\n".join(f"    {build_fn_name(p.name)}()" for p in plan.parts)
    return (
        _plan_header(plan, multi_file=True)
        + "import bpy\nfrom mathutils import Vector\n\n"
        + imports + "\n\n"
        + SELFCHECK
        + "\ndef main():\n    # build every part (order = plan order); parts are placed at world pose by their builders\n"
        + calls + "\n    _selfcheck()\n\n\nmain()\n"
    )


def blender_skeleton_source(plan: StaticPlan) -> str:
    """The same model as ONE ``src/model.py`` (small objects may use a single file)."""
    body = "\n# ----------------------------------------------------------------------------- parts\n"
    for p in plan.parts:
        body += _constants(p) + "\n\n" + _part_function(p) + "\n\n"
    calls = "\n".join(f"    {build_fn_name(p.name)}()" for p in plan.parts)
    return (
        _plan_header(plan, multi_file=False) + IMPORTS + HELPERS + body + SELFCHECK
        + "\ndef main():\n" + calls + "\n    _selfcheck()\n\n\nmain()\n"
    )


def write_blender_skeleton(ws: Workspace, plan: StaticPlan, *, multi_file: bool = True) -> list[Path]:
    """Write the starter files (overwrites) and return the written paths (entry first)."""
    ws.src.mkdir(parents=True, exist_ok=True)
    entry = ws.src / "model.py"
    if not multi_file:
        entry.write_text(blender_skeleton_source(plan))
        return [entry]
    entry.write_text(model_file_source(plan))
    written = [entry]
    for p in plan.parts:
        path = ws.root / part_file_rel(p.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(part_file_source(p))
        written.append(path)
    return written


# ===================================================================== runtime
# (merged from codeverse/languages/blender/runtime.py, 2026-08-28)
_PKG_DIR = Path(__file__).resolve().parent
WRAPPER = _PKG_DIR / "wrappers" / "run_bpy.py"


class BlenderNotFoundError(RuntimeError):
    """No usable Blender binary (configure ``CV3D_BINARIES__BLENDER`` or put blender on PATH)."""


def blender_env() -> dict[str, str]:
    """Environment for a headless Blender child: keep the user's env but make sure the
    host python (conda) cannot leak into Blender's bundled interpreter, and strip
    credential-shaped vars (:func:`codeverse.proc.scrub_secrets`) — the model-authored
    ``model.py`` executes inside this process and must never see API keys."""
    env = scrub_secrets(dict(os.environ))
    for k in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"):
        env.pop(k, None)
    env["PYTHONNOUSERSITE"] = "1"
    return env


class BlenderRuntime:
    """LanguageRuntime for ``Language.BLENDER``."""

    language = Language.BLENDER
    entry_globs: tuple[str, ...] = (ENTRY_REL, "src/parts/*.py")

    def __init__(self, *, blender: str | None = None, settings: Settings | None = None):
        self._settings = settings or get_settings()
        self._blender = blender

    # ------------------------------------------------------------------ helpers
    def blender_binary(self) -> str:
        b = self._blender or self._settings.resolve_blender()
        if not b or not Path(b).exists():
            raise BlenderNotFoundError("Blender binary not found; set CV3D_BINARIES__BLENDER=/path/to/blender")
        return b

    def entry_file(self, ws: Workspace) -> Path:
        return ws.root / ENTRY_REL

    @staticmethod
    def file_for_part(part_name: str) -> str:
        """Workspace-relative file that owns a plan part: ``src/parts/<snake>.py``
        (the tracks call this via ``getattr`` to fan out per-part refinement)."""
        return part_file_rel(part_name)

    def part_file(self, ws: Workspace, part_name: str) -> Path:
        return ws.root / self.file_for_part(part_name)

    @staticmethod
    def file_for_target(target: str) -> list[str]:
        """Refine target → files: whole-object targets (``overall``/``assembly``/``object``/'')
        map to the entry ``src/model.py``; anything else is treated as a part name."""
        if target.strip().lower() in ("", "overall", "assembly", "object", "model"):
            return [ENTRY_REL]
        return [part_file_rel(target)]

    def build_command(
        self, ws: Workspace, *, stl: bool = True, blend: bool = False, seed: int = 0,
        tri_limit: int = MAX_TRIS_OBJECT, rlimit_gb: float | None = None,
    ) -> list[str]:
        cmd = [
            self.blender_binary(), "-b", "--factory-startup", "--python", str(WRAPPER), "--",
            "--script", str(self.entry_file(ws)), "--out", str(ws.artifacts),
            "--rlimit-gb", str(rlimit_gb if rlimit_gb is not None else self._settings.limits.bpy_rlimit_gb),
            "--tri-limit", str(tri_limit), "--seed", str(seed),
        ]
        if stl:
            cmd.append("--stl")
        if blend:
            cmd.append("--blend")
        return cmd

    # ------------------------------------------------------------------ protocol
    def skeleton(self, ws: Workspace, plan: Plan) -> list[Path]:
        if not isinstance(plan, StaticPlan):  # ArticulatedPlan is a StaticPlan subclass → allowed
            raise TypeError(f"BlenderRuntime.skeleton needs a StaticPlan/ArticulatedPlan, got {type(plan).__name__}")
        return write_blender_skeleton(ws, plan)

    def lint(self, ws: Workspace) -> GateReport:
        """Lint every python file under ``src/`` + the multi-file layout rules."""
        return lint_workspace(ws)

    def build(
        self, ws: Workspace, *, timeout_s: int | None = None, stl: bool = True, blend: bool = False,
        seed: int = 0, tri_limit: int = MAX_TRIS_OBJECT,
    ) -> BuildResult:
        """Run the wrapper; never raises for agent-code failures (typed BuildResult instead)."""
        ws.artifacts.mkdir(parents=True, exist_ok=True)
        build_json = ws.artifacts / "build.json"
        census_json = ws.artifacts / "census.json"
        glb = ws.artifacts / "object.glb"
        stl_path = ws.artifacts / "object.stl"
        blend_path = ws.artifacts / "object.blend"
        # invalidate BEFORE the missing-entry early return: a deleted model.py must not
        # leave the previous round's object.glb + build.json (ok: true) looking current
        ws.stage_artifacts("build.json", "census.json", "object.glb", "object.stl", "object.blend").invalidate()
        entry = self.entry_file(ws)
        if not entry.is_file():
            result = BuildResult(ok=False, language=self.language.value, error_type="MissingEntryFile",
                                 error_message=f"{ENTRY_REL} does not exist", error_file=ENTRY_REL)
            ws.write_json(build_json, result)
            return result
        cmd = self.build_command(ws, stl=stl, blend=blend, seed=seed, tri_limit=tri_limit)
        proc = run_subprocess(
            cmd, cwd=ws.root, env=blender_env(),
            timeout_s=timeout_s or self._settings.limits.build_timeout_s,
        )
        return compose_build_result(
            language=self.language.value, proc=proc, build_json=build_json, census_json=census_json,
            glb_path=glb, extra_paths={"stl": stl_path, "blend": blend_path}, output_filter=strip_blender_noise,
        )

    def contract_doc(self) -> str:
        p = PROMPTS_DIR / "blender" / "contract.md"
        if p.is_file():
            return p.read_text()
        return (_PKG_DIR / "CONTRACT.md").read_text()

    def cookbook_path(self) -> Path:
        return PROMPTS_DIR / "blender" / "cookbook.md"

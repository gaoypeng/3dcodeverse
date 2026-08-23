"""Static lint for agent-authored bpy scripts (``src/model.py`` and ``src/parts/*.py``).

AST based, offline, milliseconds.  Catches what the build would only reveal
after a 2-10 s Blender round-trip — and the contract violations the build
cannot see (file IO, render calls).  Findings carry a concrete ``fix_hint``.

Severity policy: ERROR = will fail at runtime or violates the contract;
WARN = fragile in headless Blender / likely wrong; INFO = advice.
"""

from __future__ import annotations

import ast
import re
import time
from pathlib import Path

from codeverse.contracts.artifacts import GateFinding, GateReport, Severity

GATE = "lint:blender"

ALLOWED_IMPORTS = {
    "bpy", "bmesh", "mathutils", "math", "random", "numpy", "np", "itertools", "functools",
    "collections", "typing", "dataclasses", "colorsys", "statistics", "operator", "enum", "copy",
    "sys", "os", "__future__", "bpy_extras",
    "parts",  # the workspace's own src/parts/<snake>.py modules (multi-file layout)
}
FORBIDDEN_IMPORTS = {
    "subprocess", "urllib", "requests", "socket", "http", "shutil", "ctypes", "pickle",
    "multiprocessing", "threading", "webbrowser", "ftplib", "smtplib", "importlib", "pathlib",
}
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


def dotted(node: ast.AST) -> str:
    """``bpy.ops.render.render`` for an Attribute/Name chain; '' otherwise."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    if parts:  # method on an expression result, e.g. ``wp.box(1).rotate`` → ``<expr>.rotate``
        return "<expr>." + ".".join(reversed(parts))
    return ""


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
    for mod, line in c.imports.items():
        if mod in FORBIDDEN_IMPORTS:
            out.append(_f(E, f"forbidden import `{mod}`", line, "only bpy/bmesh/mathutils/math/random/numpy (+stdlib data helpers) are allowed"))
        elif mod not in ALLOWED_IMPORTS:
            out.append(_f(W, f"unexpected import `{mod}` (not available / not allowed in the build sandbox)", line, "use only bpy, bmesh, mathutils, math, random, numpy"))
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
    not touch bpy.  :func:`codeverse.languages.blender.layout.lint_workspace` lints a
    whole ``src/`` tree with these set per file.
    """
    t0 = time.monotonic()
    findings: list[GateFinding] = []
    try:
        tree = ast.parse(source, filename=target)
    except SyntaxError as e:
        findings.append(_f(Severity.ERROR, f"SyntaxError: {e.msg}", e.lineno, f"fix the syntax near line {e.lineno}: {(e.text or '').strip()!r}", target))
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

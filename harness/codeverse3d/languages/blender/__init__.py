"""Blender (bpy) language: runtime, lint, layout, skeleton; wrappers run inside Blender."""

from __future__ import annotations

import ast
import os
import time
from pathlib import Path

from codeverse3d.config import Settings, get_settings
from codeverse3d.contracts.artifacts import BuildResult, GateFinding, GateReport, LintKind, Severity
from codeverse3d.contracts.common import ENTRY_FILE, Language
from codeverse3d.conventions import PASCAL_RE, to_snake
from codeverse3d.languages._ast_lint import (
    BASE_FORBIDDEN_IMPORTS,
    check_imports,
    describe_parse_failure,
    dotted,
    safe_parse,
)
from codeverse3d.languages._common import run_wrapper_build, strip_blender_noise, ws_rel
from codeverse3d.languages.base import RuntimeLayout
from codeverse3d.proc import scrub_secrets
from codeverse3d.workspace import Workspace

# ===================================================================== lint
GATE = "lint:blender"

ALLOWED_IMPORTS = {
    "bpy", "bmesh", "mathutils", "math", "random", "numpy", "itertools", "functools",
    "collections", "typing", "dataclasses", "colorsys", "statistics", "operator", "enum", "copy",
    "sys", "os", "__future__", "bpy_extras",
    "parts",  # the workspace's own src/parts/<snake>.py modules (multi-file layout)
}
FORBIDDEN_IMPORTS = frozenset(BASE_FORBIDDEN_IMPORTS | {"pathlib"})
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
WARN_CALL_PREFIXES: tuple[tuple[str, str, LintKind], ...] = (
    ("bpy.data.cameras.new", "the harness owns cameras; remove camera creation", LintKind.HARNESS_OWNED),
    ("bpy.ops.object.camera_add", "the harness owns cameras; remove camera creation", LintKind.HARNESS_OWNED),
    ("bpy.data.lights.new", "the harness owns lighting; remove light creation", LintKind.HARNESS_OWNED),
    ("bpy.ops.object.light_add", "the harness owns lighting; remove light creation", LintKind.HARNESS_OWNED),
    ("bpy.data.worlds.new", "the harness owns the world/background; remove world code", LintKind.HARNESS_OWNED),
    ("bpy.ops.view3d.", "view3d operators fail in background mode (no 3D viewport)", LintKind.API_TRAP),
    ("bpy.ops.screen.", "screen operators fail in background mode", LintKind.API_TRAP),
    ("sys.exit", "do not exit; let the script fall off the end", LintKind.SANDBOX),
)
# Principled BSDF inputs that raise KeyError in Blender 4.x/5.x → their replacement.
# Verified on Blender 5.2.2: 'Anisotropic' and 'Specular Tint' still exist (do not list them).
REMOVED_BSDF_INPUTS = {
    "Specular": "Specular IOR Level", "Subsurface": "Subsurface Weight", "Transmission": "Transmission Weight",
    "Emission": "Emission Color (+ 'Emission Strength')", "Subsurface Color": "Base Color (subsurface tint removed)",
    "Clearcoat": "Coat Weight", "Clearcoat Roughness": "Coat Roughness", "Sheen": "Sheen Weight",
    "Transmission Roughness": "(removed) use Roughness",
}
KNOWN_BINDINGS = ("Vector", "Matrix", "Euler", "Quaternion", "bmesh", "math", "random", "np", "numpy", "bpy")


class BpyCollector(ast.NodeVisitor):

    def __init__(self) -> None:
        self.imports: dict[str, int] = {}
        self.bound: set[str] = set()
        self.calls: list[tuple[str, ast.Call]] = []
        self.attr_stores: list[tuple[str, int]] = []
        self.name_loads: list[tuple[str, int]] = []
        self.bm_names: set[str] = set()
        self.bm_subscripts: list[tuple[str, int]] = []
        self.bsdf_inputs: list[tuple[str, int, bool]] = []  # (key, line, inside `if '<key>' in x.inputs:`)
        self._guarded: list[frozenset[str]] = []
        self.names_assigned: list[str] = []
        self.constants: list[tuple[str, int]] = []
        self.gn_item_stores: list[tuple[str, int]] = []  # (key, line) of <x>["Socket_N"] = … (pre-5.2 GN inputs)
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

    @staticmethod
    def _input_guards(test: ast.expr) -> frozenset[str]:
        """Keys ``k`` a test asserts present: ``'k' in x.inputs`` (also inside an ``and``)."""
        if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And):
            return frozenset().union(*(BpyCollector._input_guards(v) for v in test.values))
        if (isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], ast.In)
                and isinstance(test.left, ast.Constant) and isinstance(test.left.value, str)
                and isinstance(test.comparators[0], ast.Attribute) and test.comparators[0].attr == "inputs"):
            return frozenset({test.left.value})
        return frozenset()

    def visit_If(self, node: ast.If | ast.IfExp) -> None:
        self.visit(node.test)
        self._guarded.append(self._input_guards(node.test))
        for child in node.body if isinstance(node.body, list) else [node.body]:
            self.visit(child)
        self._guarded.pop()
        for child in node.orelse if isinstance(node.orelse, list) else [node.orelse]:
            self.visit(child)

    visit_IfExp = visit_If  # type: ignore[assignment]

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if isinstance(node.value, ast.Attribute):
            if (node.value.attr in ("verts", "edges", "faces") and not isinstance(node.slice, ast.Slice)
                    and isinstance(node.value.value, ast.Name)):
                self.bm_subscripts.append((node.value.value.id, node.lineno))
            if node.value.attr == "inputs" and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
                key = node.slice.value
                self.bsdf_inputs.append((key, node.lineno, any(key in g for g in self._guarded)))
        if (isinstance(node.ctx, ast.Store) and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str)
                and node.slice.value.startswith(("Socket_", "Input_"))):
            self.gn_item_stores.append((node.slice.value, node.lineno))
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str):
            self.constants.append((node.value, node.lineno))


def _f(kind: LintKind, sev: Severity, msg: str, line: int | None = None, hint: str = "",
       target: str = "src/model.py") -> GateFinding:
    data: dict[str, object] = {"kind": kind.value}
    if line:
        data["line"] = line
    return GateFinding(gate=GATE, severity=sev, target=target, message=msg, fix_hint=hint, data=data)


# API traps of bpy itself, shared by the object lint and the scene lint.
def call_traps(c: BpyCollector, name: str, call: ast.Call) -> list[GateFinding]:
    out: list[GateFinding] = []
    E, W = Severity.ERROR, Severity.WARN
    if name.startswith("bpy.ops.") and call.args and isinstance(call.args[0], ast.Dict):
        out.append(_f(LintKind.API_TRAP, E, f"`{name}({{...}})` context-dict override was removed in Blender 4.0", call.lineno,
                      "use `with bpy.context.temp_override(object=obj, active_object=obj, selected_objects=[obj]): bpy.ops....()`"))
    if name.endswith("primitive_cube_add"):
        kws = {k.arg for k in call.keywords}
        if "scale" in kws and "size" not in kws:
            out.append(_f(LintKind.API_TRAP, W, "primitive_cube_add(scale=...) without size=1 makes a 2 m cube × scale (extents doubled)", call.lineno,
                          "pass `size=1, scale=(sx, sy, sz)` then `bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)`"))
    if name.endswith("transform_apply"):
        kws = {k.arg for k in call.keywords}
        if kws and not {"location", "rotation", "scale"} <= kws:
            out.append(_f(LintKind.API_TRAP, W, "transform_apply with partial kwargs: unspecified ones default to True (bakes location into mesh data)", call.lineno,
                          "always pass all three: transform_apply(location=False, rotation=False, scale=True)"))
    if name.endswith("modifier_apply") and not c.has_temp_override:
        out.append(_f(LintKind.API_TRAP, W, "bpy.ops.object.modifier_apply needs the object active+selected in OBJECT mode", call.lineno,
                      "either leave modifiers unapplied (the exporter applies them) or wrap: `with bpy.context.temp_override(object=obj): bpy.ops.object.modifier_apply(modifier=mod.name)`"))
    if (name.endswith("bpy.ops.object.join") or name == "bpy.ops.object.join") and not any(
            t.endswith("view_layer.objects.active") for t, _ in c.attr_stores):
        out.append(_f(LintKind.API_TRAP, W, "bpy.ops.object.join needs all parts selected + an active object; the result inherits the ACTIVE object's transform", call.lineno,
                      "select all, set `bpy.context.view_layer.objects.active = main_obj` (identity transform) before join — or keep parts separate (preferred: named parts)"))
    if name.endswith("shade_smooth") and any(k.arg == "use_auto_smooth" for k in call.keywords):
        out.append(_f(LintKind.API_TRAP, E, "shade_smooth(use_auto_smooth=...) was removed in 4.1", call.lineno, "use `bpy.ops.object.shade_smooth_by_angle(angle=0.523599)` or `bpy.ops.object.shade_auto_smooth()`"))
    if name.endswith(".calc_normals"):
        out.append(_f(LintKind.API_TRAP, E, "Mesh.calc_normals() was removed in 4.0 (normals are computed automatically)", call.lineno, "delete the call (use me.update() if needed)"))
    return out


def store_traps(target: str, line: int) -> list[GateFinding]:
    if target.endswith(".use_auto_smooth") or target.endswith(".auto_smooth_angle"):
        return [_f(LintKind.API_TRAP, Severity.ERROR, f"`{target}` was removed in Blender 4.1", line, "use bpy.ops.object.shade_smooth_by_angle(angle=...) or mark sharp edges; or just shade_smooth()")]
    return []


def use_nodes_trap(target: str, line: int) -> list[GateFinding]:
    if target.endswith(".use_nodes"):
        return [_f(LintKind.API_TRAP, Severity.INFO, "`use_nodes = True` is deprecated in Blender 5.x (removed in 6.0): materials, worlds and lights "
                   "always have a node tree; harmless", line, "delete the line")]
    return []


def gn_input_traps(c: BpyCollector) -> list[GateFinding]:
    """Geometry-nodes modifier inputs set the pre-5.2 way (``mod["Socket_2"] = v``) raise TypeError on 5.2."""
    return [_f(LintKind.API_TRAP, Severity.ERROR, f"`[...][{key!r}] = ...`: geometry-nodes modifier inputs are RNA properties in Blender 5.2 "
               "(TypeError at runtime)", line,
               f"getattr(mod.properties.inputs, {key!r}).value = <value>  (identifiers: mod.properties.inputs.keys(); "
               "an attribute-driven input: `.type = 'ATTRIBUTE'` + `.attribute_name = ...`)")
            for key, line in c.gn_item_stores]


def solver_traps(c: BpyCollector, source: str) -> list[GateFinding]:
    """``solver = 'FAST'``: renamed; 5.2 has FLOAT / EXACT / MANIFOLD."""
    lines = source.splitlines()
    return [_f(LintKind.API_TRAP, Severity.ERROR, "boolean solver 'FAST' does not exist in Blender 5.x (TypeError at runtime)", line,
               "solver = 'MANIFOLD' (closed meshes) or 'EXACT'; 'FLOAT' is the old FAST")
            for const, line in c.constants if const == "FAST" and 0 < line <= len(lines) and ".solver" in lines[line - 1]]


def source_traps(c: BpyCollector, source: str) -> list[GateFinding]:
    """Whole-file traps: removed Principled inputs, bmesh lookup tables, undefined names, the
    background-mode selection, invalid engine ids."""
    out: list[GateFinding] = []
    E, W, I = Severity.ERROR, Severity.WARN, Severity.INFO  # noqa: E741
    for key, line, guarded in c.bsdf_inputs:
        if key in REMOVED_BSDF_INPUTS and not guarded:  # `elif 'Specular' in bsdf.inputs:` is version-proof
            out.append(_f(LintKind.API_TRAP, E, f"Principled BSDF input '{key}' does not exist in Blender 4.x/5.x (KeyError at runtime)", line,
                          f"use inputs['{REMOVED_BSDF_INPUTS[key]}']"))
        elif key == "Specular Tint":
            out.append(_f(LintKind.API_TRAP, I, "'Specular Tint' is an RGBA colour in Blender 4.x/5.x (a float raises TypeError)", line,
                          'assign a 4-tuple: inputs["Specular Tint"].default_value = (r, g, b, 1.0)'))
    has_lookup = any(n.endswith("ensure_lookup_table") for n, _ in c.calls)
    bm_subs = [line for base, line in c.bm_subscripts if base in c.bm_names or base == "bm"]
    if "bmesh" in c.imports and bm_subs and not has_lookup:
        out.append(_f(LintKind.API_TRAP, E, "bmesh verts/edges/faces indexed with [] but ensure_lookup_table() is never called → IndexError", bm_subs[0],
                      "after bm.from_mesh()/any topology change call `bm.verts.ensure_lookup_table(); bm.edges.ensure_lookup_table(); bm.faces.ensure_lookup_table()` before indexing"))
    for name, line in c.name_loads:
        if name in KNOWN_BINDINGS and name not in c.bound:
            hint = {"Vector": "from mathutils import Vector", "Matrix": "from mathutils import Matrix", "Euler": "from mathutils import Euler",
                    "Quaternion": "from mathutils import Quaternion", "np": "import numpy as np", "numpy": "import numpy"}.get(name, f"import {name}")
            out.append(_f(LintKind.UNDEFINED_NAME, E, f"`{name}` is used but never imported (NameError at runtime)", line, hint))
            break
    if "bpy.context.selected_objects" in source:
        out.append(_f(LintKind.API_TRAP, W, "bpy.context.selected_objects is unreliable in background mode (often empty)", None,
                      "keep references to the objects you create (`obj = bpy.context.object` right after primitive_*_add) instead of re-reading the selection"))
    for const, line in c.constants:
        if const == "BLENDER_EEVEE_NEXT":
            out.append(_f(LintKind.HARNESS_OWNED, W, "'BLENDER_EEVEE_NEXT' is not a valid engine id in Blender 5.x", line, "render settings are owned by the harness; delete the line"))
    out.extend(gn_input_traps(c))
    out.extend(solver_traps(c, source))
    return out


def _rules(c: BpyCollector, source: str, *, target: str, expect_names: bool) -> list[GateFinding]:
    # a file that uses `bpy` without importing it is caught by the KNOWN_BINDINGS rule in source_traps;
    # an entry that only imports and calls its part builders needs no `import bpy`
    out: list[GateFinding] = []
    E, W = Severity.ERROR, Severity.WARN

    def _import_finding(kind: str, mod: str, line: int) -> GateFinding:
        if kind == "forbidden":
            return _f(LintKind.FORBIDDEN_IMPORT, E, f"forbidden import `{mod}`", line, "only bpy/bmesh/mathutils/math/random/numpy (+stdlib data helpers) are allowed")
        return _f(LintKind.BAD_IMPORT, W, f"unexpected import `{mod}` (not available / not allowed in the build sandbox)", line, "use only bpy, bmesh, mathutils, math, random, numpy")

    out.extend(check_imports(c.imports, forbidden=FORBIDDEN_IMPORTS, allowed=ALLOWED_IMPORTS, make_finding=_import_finding))
    for name, call in c.calls:
        for prefix, hint in FORBIDDEN_CALL_PREFIXES:
            if name.startswith(prefix):
                out.append(_f(LintKind.SANDBOX, E, f"forbidden call `{name}`", call.lineno, hint))
        for prefix, hint, kind in WARN_CALL_PREFIXES:
            if name.startswith(prefix):
                out.append(_f(kind, W, f"`{name}` — {hint}", call.lineno, hint))
        if name == "open":
            out.append(_f(LintKind.SANDBOX, E, "file IO via open() is forbidden", call.lineno, "no file reads/writes; the harness exports for you"))
        out.extend(call_traps(c, name, call))
    for target, line in c.attr_stores:
        out.extend(store_traps(target, line))
        if ".render." in target or target.endswith(".render"):
            out.append(_f(LintKind.HARNESS_OWNED, W, f"render settings touched: `{target}`", line, "the harness owns rendering; delete render/engine/resolution code"))
        if target.endswith("scene.camera") or target.endswith(".world"):
            out.append(_f(LintKind.HARNESS_OWNED, W, f"`{target}` assignment — cameras/world are owned by the harness", line, "remove camera/world code"))
        out.extend(use_nodes_trap(target, line))
    out.extend(source_traps(c, source))
    pascal = [n for n in c.names_assigned if PASCAL_RE.match(n) and n not in ("Cube", "Cylinder", "Sphere", "Plane")]
    if expect_names and not pascal:
        out.append(_f(LintKind.NAMING, W, "no PascalCase object names found (e.g. obj.name = 'SeatCushion')", None,
                      "name every visible mesh after its part: `obj.name = 'SeatCushion'`; instances `Leg_0..Leg_3`"))
    return out


def lint_blender_source(
    source: str, *, target: str = "src/model.py", expect_names: bool = True,
) -> GateReport:
    """Lint one bpy script; returns ``GateReport(gate='lint:blender')``.

    ``expect_names=False`` relaxes the PascalCase-name check for entry and helper files."""
    t0 = time.monotonic()
    findings: list[GateFinding] = []
    tree, exc = safe_parse(source, target)
    if tree is None:
        msg, hint, line = describe_parse_failure(exc)  # type: ignore[arg-type]
        findings.append(_f(LintKind.SYNTAX, Severity.ERROR, msg, line, hint, target))
        return GateReport.of(GATE, findings, duration_ms=int((time.monotonic() - t0) * 1000))
    c = BpyCollector()
    c.visit(tree)
    findings = _rules(c, source, target=target, expect_names=expect_names)
    for f in findings:
        f.target = target
    return GateReport.of(GATE, findings, duration_ms=int((time.monotonic() - t0) * 1000))


# ===================================================================== layout
ENTRY_REL = ENTRY_FILE[Language.BLENDER]  # "src/model.py"
PARTS_DIR = "parts"


def part_file_rel(part_name: str) -> str:
    return f"src/{PARTS_DIR}/{to_snake(part_name)}.py"


def build_fn_name(part_name: str) -> str:
    return f"build_{to_snake(part_name)}"


def part_files(ws: Workspace) -> list[Path]:
    d = ws.src / PARTS_DIR
    if not d.is_dir():
        return []
    return sorted(p for p in d.glob("*.py") if not p.name.startswith("_"))


def source_files(ws: Workspace) -> list[Path]:
    entry = ws.src / "model.py"
    rest = sorted(p for p in ws.src.rglob("*.py") if p != entry)
    return ([entry] if entry.is_file() else []) + rest


def _exported_functions(tree: ast.Module) -> set[str]:
    return {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _imported_part_modules(tree: ast.Module) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            dotted = node.module.split(".")
            if dotted[0] == PARTS_DIR:
                if len(dotted) > 1:
                    out.add(dotted[1])
                else:  # ``from parts import seat, leg``
                    out.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            for a in node.names:
                dotted = a.name.split(".")
                if dotted[0] == PARTS_DIR and len(dotted) > 1:
                    out.add(dotted[1])
    return out


def _layout_rules(ws: Workspace, parts: list[Path], entry_tree: ast.Module | None) -> list[GateFinding]:
    out: list[GateFinding] = []
    E, W = Severity.ERROR, Severity.WARN
    imported = _imported_part_modules(entry_tree) if entry_tree is not None else set()
    for p in parts:
        target = ws_rel(ws, p)
        stem = p.stem
        if stem != to_snake(stem):
            out.append(_f(LintKind.NAMING, E, f"part file name '{p.name}' is not snake_case", target=target,
                          hint=f"rename to src/{PARTS_DIR}/{to_snake(stem)}.py (the harness maps plan part "
                               f"'{stem}' → that file) and fix the import in model.py"))
            continue
        tree, _ = safe_parse(p.read_text(), target)
        if tree is None:
            continue  # reported by the per-file lint
        fn = f"build_{stem}"
        if fn not in _exported_functions(tree):
            out.append(_f(LintKind.LAYOUT, E, f"{target} does not define `def {fn}()`", target=target,
                          hint=f"every part file exports `def {fn}() -> bpy.types.Object` returning the named object "
                               f"at its world pose; model.py calls it (`from {PARTS_DIR}.{stem} import {fn}`)"))
        for node in tree.body:
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                name = getattr(node.value.func, "id", "")
                if name.startswith("build_") or name == "main":
                    out.append(_f(LintKind.LAYOUT, W, f"{target} calls `{name}()` at import time", node.lineno, target=target,
                                  hint="part files only DEFINE builders; model.py calls them once (a module-level "
                                       "call here builds the part twice → auto-suffixed 'Name.001')"))
        if entry_tree is not None and stem not in imported:
            out.append(_f(LintKind.PART_NOT_IMPORTED, W, f"{target} is never imported by src/model.py → its part is not built", target=target,
                          hint=f"add `from {PARTS_DIR}.{stem} import {fn}` to model.py and call `{fn}()` in main()"))
    return out


def lint_workspace(ws: Workspace) -> GateReport:
    t0 = time.monotonic()
    entry = ws.root / ENTRY_REL
    if not entry.is_file():
        return GateReport.of(GATE, [_f(
            LintKind.MISSING_FILE, Severity.ERROR, f"{ENTRY_REL} is missing", target=ENTRY_REL,
            hint="create src/model.py (entry: imports src/parts/<snake>.py builders and calls them; see the skeleton)")])
    parts = part_files(ws)
    findings: list[GateFinding] = []
    for p in source_files(ws):
        target = ws_rel(ws, p)
        is_entry = p == entry
        is_helper = p.name.startswith("_")
        rep = lint_blender_source(p.read_text(), target=target,
                                  expect_names=not (is_helper or (is_entry and bool(parts))))
        findings.extend(rep.findings)
    entry_tree, _ = safe_parse(entry.read_text(), ENTRY_REL)
    findings.extend(_layout_rules(ws, parts, entry_tree))
    return GateReport.of(GATE, findings, duration_ms=int((time.monotonic() - t0) * 1000))


# ===================================================================== skeleton
_COLOUR_WORDS: tuple[tuple[str, tuple[float, float, float]], ...] = (
    ("black", (0.05, 0.05, 0.05)), ("white", (0.9, 0.9, 0.9)), ("red", (0.7, 0.1, 0.1)),
    ("blue", (0.1, 0.2, 0.7)), ("green", (0.1, 0.5, 0.2)),
)


# ----------------------------------------------------------------------------- shared text blocks


# ----------------------------------------------------------------------------- public renderers


# ===================================================================== runtime
WRAPPER = Path(__file__).resolve().parent.parent / "wrappers" / "run_bpy.py"


class BlenderNotFoundError(RuntimeError):
    """No usable Blender binary (configure ``C3D_BINARIES__BLENDER`` or put blender on PATH)."""


def blender_binary(override: str | None, settings: Settings) -> str:
    b = override or settings.resolve_blender()
    if not b or not Path(b).exists():
        raise BlenderNotFoundError("Blender binary not found; set C3D_BINARIES__BLENDER=/path/to/blender")
    return b


def blender_env() -> dict[str, str]:
    """Environment for a headless Blender child: host python cannot leak in, credential-shaped vars are stripped."""
    env = scrub_secrets(dict(os.environ))
    for k in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"):
        env.pop(k, None)
    env["PYTHONNOUSERSITE"] = "1"
    return env


class BlenderRuntime(RuntimeLayout):
    """LanguageRuntime for ``Language.BLENDER``."""

    language = Language.BLENDER
    entry_globs: tuple[str, ...] = (ENTRY_REL, "src/parts/*.py")
    part_file = staticmethod(part_file_rel)

    def __init__(self, *, blender: str | None = None, settings: Settings | None = None):
        self._settings = settings or get_settings()
        self._blender = blender

    # ------------------------------------------------------------------ helpers
    def entry_file(self, ws: Workspace) -> Path:
        return ws.root / ENTRY_REL


    def lint(self, ws: Workspace) -> GateReport:
        return lint_workspace(ws)

    def build(self, ws: Workspace, *, timeout_s: int | None = None) -> BuildResult:
        def argv() -> list[str]:
            return [blender_binary(self._blender, self._settings), "-b", "--factory-startup", "--python", str(WRAPPER), "--",
                    "--script", str(self.entry_file(ws)), "--out", str(ws.artifacts),
                    "--rlimit-gb", str(self._settings.limits.bpy_rlimit_gb), "--stl"]

        return run_wrapper_build(
            ws, language=self.language, extras={"stl": "object.stl"}, argv=argv,
            env=blender_env(), timeout_s=timeout_s or self._settings.limits.build_timeout_s, output_filter=strip_blender_noise)

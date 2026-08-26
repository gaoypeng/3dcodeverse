"""Static lint for the ``urdf_blender`` language: ``src/robot.urdf`` (URDF rules)
and ``src/model.py`` (bpy pitfalls + link-name coverage).  Gate ``lint:urdf``.

Every finding carries a copyable ``fix_hint``.  Errors block the build; warnings
are shown to the agent/judge.
"""

from __future__ import annotations

import ast
import math
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from codeverse.contracts.artifacts import GateFinding, GateReport, Severity
from codeverse.conventions import to_snake
from codeverse.languages._ast_lint import describe_parse_failure, safe_parse
from codeverse.spatial.joints_model import JOINT_TYPES, MOVABLE_TYPES, RESERVED_LINK_NAMES
from codeverse.workspace import Workspace

GATE = "lint:urdf"
URDF_REL = "src/robot.urdf"
MODEL_REL = "src/model.py"
_STATE_WORDS = ("open", "closed", "opened", "extended", "retracted", "raised", "lowered", "folded", "unfolded")
#: link names double as Blender object names and ``meshes/<link>.glb`` stems: plain
#: identifiers only (``door``, ``handle_left``, ``DoorHandle``) — never ``Door.001`` / spaces.
_IDENT = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")

#: attribute chains that must not appear in model.py (harness owns these)
FORBIDDEN_BPY_PREFIXES: tuple[tuple[str, str], ...] = (
    ("bpy.ops.wm.", "file/session operators (open/save/quit/read_factory_settings) — the harness owns the session"),
    ("bpy.ops.render.", "rendering — the harness renders"),
    ("bpy.ops.export_scene.", "exporting — the harness exports meshes/<link>.glb"),
    ("bpy.ops.export_mesh.", "exporting — the harness exports"),
    ("bpy.ops.import_scene.", "importing external files — build geometry procedurally"),
    ("bpy.ops.import_mesh.", "importing external files — build geometry procedurally"),
)
FORBIDDEN_MODULES: tuple[str, ...] = ("subprocess", "socket", "urllib", "requests", "http", "shutil", "ctypes", "multiprocessing")
FORBIDDEN_CALLS: dict[str, str] = {
    "os.system": "shell access", "os.remove": "file deletion", "os.unlink": "file deletion", "os.rmdir": "file deletion",
    "sys.exit": "exits Blender before the export — just return/raise instead", "exit": "exits Blender", "quit": "exits Blender",
    "input": "blocks headless Blender forever",
}
WARN_CALLS: dict[str, str] = {
    "bpy.ops.object.camera_add": "cameras are ignored (and stripped) by the wrapper",
    "bpy.ops.object.light_add": "lights are ignored (and stripped) by the wrapper",
    "time.sleep": "pointless in a build script",
}


def _f(sev: Severity, msg: str, *, target: str | None = None, fix: str = "", **data) -> GateFinding:
    return GateFinding(gate=GATE, severity=sev, target=target, message=msg, fix_hint=fix, data=data)


def _floats(text: str | None) -> list[float] | None:
    try:
        return [float(v) for v in (text or "").split()]
    except ValueError:
        return None


# ------------------------------------------------------------------ URDF
def lint_urdf_text(text: str, *, label: str = URDF_REL) -> tuple[list[GateFinding], list[str]]:
    """Lint URDF source.  Returns (findings, link_names)."""
    out: list[GateFinding] = []
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        line = getattr(e, "position", (None, None))[0]
        out.append(_f(Severity.ERROR, f"{label} is not well-formed XML: {e}", target=label,
                      fix="Fix the XML (every <tag> closed, attributes quoted, one <robot> root element).", line=line))
        return out, []
    if root.tag != "robot":
        out.append(_f(Severity.ERROR, f"root element must be <robot>, got <{root.tag}>", target=label,
                      fix='Wrap everything in <robot name="..."> ... </robot>.'))
        return out, []
    if not root.get("name"):
        out.append(_f(Severity.WARN, "<robot> has no name attribute", target=label, fix='<robot name="my_object">'))

    links = root.findall("link")
    joints = root.findall("joint")
    link_names: list[str] = []
    for el in links:
        name = el.get("name", "")
        if not name:
            out.append(_f(Severity.ERROR, "<link> without name", target=label, fix='<link name="base">'))
            continue
        if name in link_names:
            out.append(_f(Severity.ERROR, f"duplicate link name '{name}'", target=name, fix="Link names must be unique."))
        link_names.append(name)
        _lint_link(el, name, out)
    if not link_names:
        out.append(_f(Severity.ERROR, "URDF has no <link> elements", target=label, fix="Add one <link> per mesh object in model.py."))
        return out, []

    joint_names: list[str] = []
    parent_of: dict[str, str] = {}
    for el in joints:
        jname = el.get("name", "")
        if not jname:
            out.append(_f(Severity.ERROR, "<joint> without name", target=label, fix='<joint name="hinge" type="revolute">'))
            continue
        if jname in joint_names:
            out.append(_f(Severity.ERROR, f"duplicate joint name '{jname}'", target=jname, fix="Joint names must be unique."))
        joint_names.append(jname)
        _lint_joint(el, jname, link_names, parent_of, out)

    # tree structure
    children = set(parent_of)
    roots = [n for n in link_names if n not in children]
    if len(roots) != 1:
        out.append(_f(Severity.ERROR, f"expected exactly one root link (no parent joint), found {roots}", target=label,
                      fix="Every link except the root must be the <child> of exactly one joint; connect extra roots with a fixed joint."))
    else:
        for link in link_names:
            seen, cur = set(), link
            while cur in parent_of and cur not in seen:
                seen.add(cur)
                cur = parent_of[cur]
            if cur != roots[0]:
                out.append(_f(Severity.ERROR, f"link '{link}' does not reach the root '{roots[0]}' (cycle or detached)", target=link,
                              fix="Joints must form a single tree rooted at the base link."))
    return out, link_names


def _lint_link(el: ET.Element, name: str, out: list[GateFinding]) -> None:
    if name in RESERVED_LINK_NAMES:
        out.append(_f(Severity.ERROR, f"link name '{name}' is reserved by the GLB scene graph (glTF readers use it as the base frame)",
                      target=name, fix="Rename the link (e.g. 'base' or the part's name) in BOTH robot.urdf and model.py."))
    if not _IDENT.match(name):
        out.append(_f(Severity.WARN, f"link name '{name}' is not a plain identifier (letters/digits/underscore)", target=name,
                      fix=f"Rename to '{to_snake(name)}' in BOTH robot.urdf and model.py (object names are case-sensitive; "
                          "Blender's auto-suffix '.001' means two objects shared a name)."))
    if any(w in to_snake(name).split("_") for w in _STATE_WORDS):
        out.append(_f(Severity.WARN, f"link name '{name}' contains a state word — links are parts, states come from joints", target=name,
                      fix="Name the part (door, drawer, lid), not its state."))
    visuals = el.findall("visual")
    expected = f"meshes/{name}.glb"
    if len(visuals) != 1:
        out.append(_f(Severity.ERROR, f"link '{name}' has {len(visuals)} <visual> elements; exactly one is required", target=name,
                      fix=f'<visual><origin xyz="..." rpy="0 0 0"/><geometry><mesh filename="{expected}"/></geometry></visual>'))
    for vis in visuals:
        geom = vis.find("geometry")
        mesh = geom.find("mesh") if geom is not None else None
        if geom is None or mesh is None:
            kinds = [c.tag for c in geom] if geom is not None else []
            out.append(_f(Severity.ERROR, f"link '{name}': visual geometry must be a <mesh> (found {kinds or 'nothing'})", target=name,
                          fix=f'Build the shape in model.py and reference <mesh filename="{expected}"/>.'))
        else:
            fn = mesh.get("filename", "")
            if fn != expected:
                out.append(_f(Severity.ERROR, f"link '{name}': mesh filename '{fn}' must be '{expected}'", target=name,
                              fix=f'<mesh filename="{expected}"/>'))
            sc = _floats(mesh.get("scale")) if mesh.get("scale") else None
            if sc is not None and any(abs(s - 1) > 1e-9 for s in sc):
                out.append(_f(Severity.WARN, f"link '{name}': mesh scale {sc} — model in meters in model.py instead", target=name,
                              fix="Drop the scale attribute and size the geometry in model.py."))
        _lint_origin(vis.find("origin"), f"link '{name}' visual", name, out)
        col = el.find("collision")
        if col is None:
            out.append(_f(Severity.WARN, f"link '{name}' has no <collision> twin of its visual", target=name,
                          fix="Copy the <visual> block as <collision> (same origin + geometry)."))
        else:
            if _sig(col.find("geometry")) != _sig(geom) or _origin_sig(col.find("origin")) != _origin_sig(vis.find("origin")):
                out.append(_f(Severity.WARN, f"link '{name}': <collision> differs from <visual>; the harness collides the VISUAL mesh", target=name,
                              fix="Make <collision> an identical copy of <visual>."))


def _sig(el: ET.Element | None) -> str:
    return "" if el is None else ET.tostring(el).decode().strip()


def _origin_sig(o: ET.Element | None) -> tuple[tuple[float, ...], tuple[float, ...]]:
    if o is None:
        return ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    return (tuple(_floats(o.get("xyz")) or (0.0, 0.0, 0.0)), tuple(_floats(o.get("rpy")) or (0.0, 0.0, 0.0)))


def _lint_origin(o: ET.Element | None, what: str, target: str, out: list[GateFinding]) -> None:
    if o is None:
        return
    for attr in ("xyz", "rpy"):
        if o.get(attr) is None:
            continue
        vals = _floats(o.get(attr))
        if vals is None or len(vals) != 3 or any(not math.isfinite(v) for v in vals):
            out.append(_f(Severity.ERROR, f"{what}: origin {attr}='{o.get(attr)}' must be 3 finite numbers", target=target,
                          fix=f'<origin {attr}="0 0 0"/>'))


def _lint_joint(el: ET.Element, jname: str, link_names: list[str], parent_of: dict[str, str], out: list[GateFinding]) -> None:
    jtype = el.get("type", "")
    if jtype not in JOINT_TYPES:
        out.append(_f(Severity.ERROR, f"joint '{jname}': type '{jtype}' must be one of {JOINT_TYPES}", target=jname,
                      fix='type="revolute" (hinge) | "prismatic" (slide) | "continuous" (wheel) | "fixed"'))
    p = el.find("parent")
    c = el.find("child")
    parent = p.get("link", "") if p is not None else ""
    child = c.get("link", "") if c is not None else ""
    for role, lk in (("parent", parent), ("child", child)):
        if not lk:
            out.append(_f(Severity.ERROR, f"joint '{jname}': missing <{role} link=...>", target=jname, fix=f'<{role} link="base"/>'))
        elif lk not in link_names:
            out.append(_f(Severity.ERROR, f"joint '{jname}': {role} link '{lk}' does not exist (links: {link_names})", target=jname,
                          fix="Reference an existing <link name>."))
    if parent and child and parent == child:
        out.append(_f(Severity.ERROR, f"joint '{jname}': parent == child", target=jname, fix="A joint connects two different links."))
    if child:
        if child in parent_of:
            out.append(_f(Severity.ERROR, f"link '{child}' is the child of two joints ('{jname}' and another)", target=jname,
                          fix="Each link has exactly one parent joint (tree)."))
        parent_of[child] = parent
    _lint_origin(el.find("origin"), f"joint '{jname}'", jname, out)
    o = el.find("origin")
    if o is not None and o.get("rpy") and any(abs(v) > 1e-9 for v in (_floats(o.get("rpy")) or [])):
        out.append(_f(Severity.INFO, f"joint '{jname}' uses a rotated origin (rpy); allowed, but point the <axis> instead where possible", target=jname))
    ax = el.find("axis")
    if jtype in MOVABLE_TYPES:
        if ax is None or ax.get("xyz") is None:
            out.append(_f(Severity.WARN, f"joint '{jname}': no <axis>; URDF defaults to '1 0 0'", target=jname, fix='<axis xyz="0 0 1"/>'))
        else:
            vals = _floats(ax.get("xyz"))
            if vals is None or len(vals) != 3:
                out.append(_f(Severity.ERROR, f"joint '{jname}': axis '{ax.get('xyz')}' must be 3 numbers", target=jname, fix='<axis xyz="0 0 1"/>'))
            else:
                n = math.sqrt(sum(v * v for v in vals))
                if n < 1e-9:
                    out.append(_f(Severity.ERROR, f"joint '{jname}': zero axis", target=jname, fix='<axis xyz="0 0 1"/>'))
                elif abs(n - 1) > 1e-3:
                    unit = " ".join(f"{v / n:.6g}" for v in vals)
                    out.append(_f(Severity.WARN, f"joint '{jname}': axis not unit length (|a|={n:.4g}); auto-normalised", target=jname,
                                  fix=f'<axis xyz="{unit}"/>'))
    lim = el.find("limit")
    if jtype in ("revolute", "prismatic"):
        lo = _floats(lim.get("lower")) if lim is not None and lim.get("lower") is not None else None
        hi = _floats(lim.get("upper")) if lim is not None and lim.get("upper") is not None else None
        if lim is None or not lo or not hi:
            unit = "rad" if jtype == "revolute" else "m"
            out.append(_f(Severity.ERROR, f"joint '{jname}': {jtype} joints need <limit lower upper effort velocity> ({unit})", target=jname,
                          fix='<limit lower="0" upper="1.57" effort="10" velocity="1"/>'))
        else:
            if hi[0] < lo[0]:
                out.append(_f(Severity.ERROR, f"joint '{jname}': upper {hi[0]} < lower {lo[0]}", target=jname,
                              fix="Swap lower/upper; if the motion should go the other way, negate the <axis> instead."))
            if abs(hi[0] - lo[0]) < 1e-9:
                out.append(_f(Severity.WARN, f"joint '{jname}': lower == upper (joint cannot move)", target=jname,
                              fix="Give the joint a range, or make it type=fixed."))
            if jtype == "revolute" and hi[0] - lo[0] > 2 * math.pi + 1e-6:
                out.append(_f(Severity.WARN, f"joint '{jname}': revolute range > 2π — use type=continuous", target=jname))
        if lim is not None and (lim.get("effort") is None or lim.get("velocity") is None):
            out.append(_f(Severity.WARN, f"joint '{jname}': <limit> should carry effort and velocity", target=jname,
                          fix='effort="10" velocity="1"'))
    elif jtype == "continuous" and lim is not None and (lim.get("lower") is not None or lim.get("upper") is not None):
        out.append(_f(Severity.WARN, f"joint '{jname}': continuous joints have no lower/upper (ignored)", target=jname,
                      fix='<limit effort="10" velocity="1"/> or use type=revolute'))
    if el.find("mimic") is not None:
        out.append(_f(Severity.WARN, f"joint '{jname}': <mimic> is ignored by the harness (sweeps move it independently)", target=jname))


# ------------------------------------------------------------------ model.py
class _Visitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.findings: list[GateFinding] = []
        self.has_bpy = False
        self.strings: set[str] = set()

    def _chain(self, node: ast.AST) -> str:
        parts: list[str] = []
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if isinstance(node, ast.Name):
            parts.append(node.id)
        return ".".join(reversed(parts))

    def visit_Import(self, node: ast.Import) -> None:
        for a in node.names:
            top = a.name.split(".")[0]
            if top == "bpy":
                self.has_bpy = True
            if top in FORBIDDEN_MODULES:
                self.findings.append(_f(Severity.ERROR, f"line {node.lineno}: import of '{a.name}' is not allowed in model.py",
                                        target=MODEL_REL, fix="Build geometry with bpy/bmesh/mathutils/math only.", line=node.lineno))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        top = (node.module or "").split(".")[0]
        if top == "bpy":
            self.has_bpy = True
        if top in FORBIDDEN_MODULES:
            self.findings.append(_f(Severity.ERROR, f"line {node.lineno}: import from '{node.module}' is not allowed in model.py",
                                    target=MODEL_REL, fix="Build geometry with bpy/bmesh/mathutils/math only.", line=node.lineno))
        if top == "codeverse":
            self.findings.append(_f(Severity.ERROR, f"line {node.lineno}: model.py must not import the harness", target=MODEL_REL,
                                    fix="Raw bpy only.", line=node.lineno))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        chain = self._chain(node.func)
        for prefix, why in FORBIDDEN_BPY_PREFIXES:
            if chain.startswith(prefix):
                self.findings.append(_f(Severity.ERROR, f"line {node.lineno}: {chain}() is forbidden: {why}", target=MODEL_REL,
                                        fix="Delete this call; the harness owns sessions/exports/renders.", line=node.lineno))
        if chain in FORBIDDEN_CALLS:
            self.findings.append(_f(Severity.ERROR, f"line {node.lineno}: {chain}() is forbidden: {FORBIDDEN_CALLS[chain]}",
                                    target=MODEL_REL, fix="Remove the call.", line=node.lineno))
        if chain in WARN_CALLS:
            self.findings.append(_f(Severity.WARN, f"line {node.lineno}: {chain}(): {WARN_CALLS[chain]}", target=MODEL_REL, line=node.lineno))
        if chain == "open" and len(node.args) > 1 and isinstance(node.args[1], ast.Constant) and "w" in str(node.args[1].value):
            self.findings.append(_f(Severity.WARN, f"line {node.lineno}: writing files from model.py is ignored by the harness",
                                    target=MODEL_REL, line=node.lineno))
        self.generic_visit(node)

    def visit_While(self, node: ast.While) -> None:
        if isinstance(node.test, ast.Constant) and node.test.value is True:
            self.findings.append(_f(Severity.WARN, f"line {node.lineno}: 'while True' in a build script risks a hang", target=MODEL_REL, line=node.lineno))
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str):
            self.strings.add(node.value)


def lint_model_text(text: str, link_names: list[str], *, label: str = MODEL_REL) -> list[GateFinding]:
    """AST lint of model.py: forbidden APIs + every URDF link name appears as a string literal."""
    tree, exc = safe_parse(text, label)
    if tree is None:
        msg, hint, line = describe_parse_failure(exc)  # type: ignore[arg-type]
        return [_f(Severity.ERROR, f"{label}:{line or '?'}: {msg}", target=label, fix=hint, line=line)]
    v = _Visitor()
    v.visit(tree)
    out = v.findings
    if not v.has_bpy:
        out.append(_f(Severity.ERROR, f"{label} never imports bpy", target=label, fix="import bpy"))
    for link in link_names:
        if link not in v.strings and not any(link in s for s in v.strings):
            out.append(_f(Severity.WARN, f"link '{link}' never appears as a string in {label} — the wrapper looks for an object named exactly '{link}'",
                          target=link, fix=f'obj.name = "{link}"'))
    return out


# ------------------------------------------------------------------ workspace
def lint_workspace(ws: Workspace) -> GateReport:
    t0 = time.time()
    findings: list[GateFinding] = []
    urdf_p = ws.root / URDF_REL
    model_p = ws.root / MODEL_REL
    link_names: list[str] = []
    if not urdf_p.is_file():
        findings.append(_f(Severity.ERROR, f"missing {URDF_REL}", target=URDF_REL, fix="Write src/robot.urdf (see the contract)."))
    else:
        f, link_names = lint_urdf_text(urdf_p.read_text())
        findings.extend(f)
    if not model_p.is_file():
        findings.append(_f(Severity.ERROR, f"missing {MODEL_REL}", target=MODEL_REL, fix="Write src/model.py (pure bpy, one object per link)."))
    else:
        findings.extend(lint_model_text(model_p.read_text(), link_names))
    passed = not any(f.severity == Severity.ERROR for f in findings)
    return GateReport(gate=GATE, passed=passed, findings=findings, duration_ms=int((time.time() - t0) * 1000))


def lint_files(urdf_path: Path, model_path: Path | None = None) -> GateReport:
    """Lint arbitrary file paths (CLI / tests)."""
    t0 = time.time()
    findings, links = lint_urdf_text(Path(urdf_path).read_text(), label=str(urdf_path))
    if model_path is not None:
        findings.extend(lint_model_text(Path(model_path).read_text(), links, label=str(model_path)))
    return GateReport(gate=GATE, passed=not any(f.severity == Severity.ERROR for f in findings), findings=findings,
                      duration_ms=int((time.time() - t0) * 1000))

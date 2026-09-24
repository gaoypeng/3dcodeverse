"""``urdf_blender``: bpy link meshes + hand-written URDF — lint, skeleton, FK ↔ geometry
consistency and the ``UrdfBlenderRuntime`` around ``languages/wrappers/run_bpy_links.py``."""

from __future__ import annotations

import ast
import contextlib
import math
import os
import re
import shutil
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np

from codeverse3d.config import Settings, get_settings
from codeverse3d.contracts.artifacts import BuildResult, GateFinding, GateReport, Severity
from codeverse3d.contracts.common import ENTRY_FILE, Language, MimicSpec, mimic_issues
from codeverse3d.contracts.plan import ArticulatedPlan, JointPlan, PartPlan, Plan
from codeverse3d.conventions import to_snake
from codeverse3d.languages._ast_lint import (
    BASE_FORBIDDEN_IMPORTS,
    ImportCollector,
    check_imports,
    describe_parse_failure,
    dotted,
    safe_parse,
)
from codeverse3d.languages._common import compose_build_result, missing_entry, strip_blender_noise
from codeverse3d.languages.base import RuntimeLayout
from codeverse3d.proc import ProcResult, run_subprocess
from codeverse3d.spatial.joints_export import urdf_to_glb
from codeverse3d.spatial.joints_model import (
    JOINT_TYPES,
    MOVABLE_TYPES,
    RESERVED_LINK_NAMES,
    Robot,
    UrdfError,
    fk,
    invert_transform,
    load_urdf,
    matrix_to_rpy,
    parse_floats,
    parse_joint,
    parse_origin,
)
from codeverse3d.spatial.joints_sweep import sweep_collisions, sweep_findings
from codeverse3d.workspace import ArtifactStage, Workspace

# ===================================================================== consistency
#: this gate's own name — a bare ``GATE`` here was shadowed by the lint section's
#: ``GATE = "lint:urdf"`` 59 lines below when the package became one module (2026-08-28),
#: so every FK finding went out mislabelled until 2026-08-28.
FK_GATE = "fk_consistency"


def check_fk_consistency(robot: Robot, census_links: dict[str, dict[str, Any]], *, tol_m: float = 0.001) -> list[GateFinding]:
    """Compare every link's FK-posed mesh bbox at rest with the authored census bbox.

    Returns one ERROR finding per inconsistent link carrying the corrected visual
    origin (``fix_hint`` is copy-pasteable XML) and an INFO summary otherwise."""
    T = fk(robot, {})
    out: list[GateFinding] = []
    for name, link in robot.links.items():
        if link.mesh is None:
            continue
        row = census_links.get(name)
        if row is None:
            out.append(GateFinding(gate=FK_GATE, severity=Severity.ERROR, target=name,
                                   message=f"link '{name}' has no authored mesh in census (wrapper exported nothing for it)",
                                   fix_hint=f"Create a mesh object named exactly '{name}' in model.py."))
            continue
        pts = np.asarray(link.mesh.vertices) @ T[name][:3, :3].T + T[name][:3, 3]
        fk_min, fk_max = pts.min(axis=0), pts.max(axis=0)
        au_min, au_max = np.asarray(row["bbox_min"], dtype=float), np.asarray(row["bbox_max"], dtype=float)
        err = float(max(np.abs(fk_min - au_min).max(), np.abs(fk_max - au_max).max()))
        if err <= tol_m:
            continue
        T_fix = invert_transform(T[name])
        xyz_fix = T_fix[:3, 3]
        rpy_fix = matrix_to_rpy(T_fix[:3, :3])
        cur_xyz = link.visual_origin[:3, 3]
        cur_rpy = matrix_to_rpy(link.visual_origin[:3, :3])
        frame_xyz = T[name][:3, 3]
        out.append(GateFinding(
            gate=FK_GATE, severity=Severity.ERROR, target=name,
            message=(f"link '{name}': FK at q=0 puts the mesh at bbox [{_vec(fk_min)}]..[{_vec(fk_max)}] but model.py authored it at "
                     f"[{_vec(au_min)}]..[{_vec(au_max)}] (max error {err*1000:.1f} mm). Its link frame is at world "
                     f"[{_vec(frame_xyz)}] so the visual origin must be the inverse: xyz=\"{_vec(xyz_fix)}\" "
                     f"(you wrote xyz=\"{_vec(cur_xyz)}\" rpy=\"{_vec(cur_rpy)}\")."),
            fix_hint=(f"In robot.urdf, link '{name}': set BOTH <visual> and <collision> to "
                      f"<origin xyz=\"{_vec(xyz_fix)}\" rpy=\"{_vec(rpy_fix)}\"/>  "
                      f"(= -(joint pivot world) when joints have rpy=0; or move the joint origin so the pivot is where you meant)."),
            data={"error_m": err, "fk_bbox": [fk_min.tolist(), fk_max.tolist()], "authored_bbox": [au_min.tolist(), au_max.tolist()],
                  "corrected_visual_origin": {"xyz": [float(v) for v in xyz_fix], "rpy": [float(v) for v in rpy_fix]},
                  "current_visual_origin": {"xyz": [float(v) for v in cur_xyz], "rpy": [float(v) for v in cur_rpy]},
                  "link_frame_world_xyz": [float(v) for v in frame_xyz]},
        ))
    return out


# ===================================================================== lint
GATE = "lint:urdf"
URDF_REL = "src/robot.urdf"
MODEL_REL = ENTRY_FILE[Language.URDF_BLENDER]
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
#: the shared floor (subprocess/network/pickle/threading/importlib …): the wrapper execs
#: model.py inside the same Blender interpreter as the blender language, so no looser list
FORBIDDEN_IMPORTS: frozenset[str] = frozenset(BASE_FORBIDDEN_IMPORTS)
#: model.py runs inside the SAME Blender python as blender/model.py, so the pure-computation
#: stdlib it allows is allowed here too; anything else is an "unexpected" WARN (os/sys stay out:
#: a link-mesh script has no business in the filesystem, and FORBIDDEN_CALLS catches the uses)
ALLOWED_IMPORTS: frozenset[str] = frozenset({
    "bpy", "bmesh", "mathutils", "math", "random", "numpy", "np", "bpy_extras", "__future__",
    "typing", "dataclasses", "itertools", "functools", "collections", "enum", "copy",
    "colorsys", "statistics", "operator",
})
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

    _lint_mimics(joints, out)

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


def _lint_mimics(joints: list[ET.Element], out: list[GateFinding]) -> None:
    """``<mimic>`` couplings, over the whole file: a declaration may name a joint that is
    written later, and a cycle is only visible on the graph.  The RULES are
    ``contracts.common.mimic_issues`` — shared with the plan validator and the URDF
    loader, which rejected different multipliers from this lint until 2026-09-03."""
    # EVERY named joint becomes a spec, coupled or not: an uncoupled joint is what the
    # coupled ones are allowed to name, and mimic_issues resolves targets against the set
    # it is given.
    specs: list[MimicSpec] = []
    for el in joints:
        jname = el.get("name", "")
        if not jname:
            continue
        movable = el.get("type", "") != "fixed"
        lim = el.find("limit")
        lo = hi = None
        if lim is not None:
            with contextlib.suppress(TypeError, ValueError):
                lo, hi = float(lim.get("lower")), float(lim.get("upper"))
        mim = el.find("mimic")
        src = (mim.get("joint") or "").strip() if mim is not None else ""
        mult, off = 1.0, 0.0
        if src:   # a <mimic> with no joint= or non-numeric factors is parse_joint's ERROR (_lint_joint)
            try:
                mult = float(mim.get("multiplier", 1) or 1)
                off = float(mim.get("offset", 0) or 0)
            except ValueError:
                src = ""
        specs.append(MimicSpec(key=jname, name=jname, movable=movable, target=src or None,
                               multiplier=mult, offset=off, lower=lo, upper=hi))
    known = sorted({el.get("name", "") for el in joints if el.get("name")})
    # NOT checked here: whether a follower of `<driver>_1` should also follow `_2..n`.
    # Only the skeleton knows which `_N` names are instances of one plan joint (an agent
    # names joints `hinge_1` / `hinge_2` by hand all the time), so that note is written
    # where the instancing happens — compute_urdf_frames — as a comment in the file.
    for i in mimic_issues(specs):
        j, t = i.joint, i.target
        if i.kind == "immobile":
            out.append(_f(Severity.ERROR, f"joint '{j}': a fixed joint has nothing to mimic", target=j,
                          fix="give it a type and a limit, or drop the <mimic>"))
        elif i.kind == "zero_multiplier":
            out.append(_f(Severity.ERROR, f"joint '{j}': <mimic multiplier=\"0\"> — the joint cannot move",
                          target=j, fix="use type=fixed, or a non-zero multiplier"))
        elif i.kind == "self":
            out.append(_f(Severity.ERROR, f"joint '{j}': <mimic> names itself", target=j))
        elif i.kind == "unknown_target":
            out.append(_f(Severity.ERROR, f"joint '{j}': <mimic joint=\"{t}\"> names no joint in this file",
                          target=j, fix=f"one of: {', '.join(known)}"))
        elif i.kind == "immobile_target":
            out.append(_f(Severity.ERROR, f"joint '{j}': mimics '{t}', which is fixed and never moves", target=j,
                          fix="mimic a joint that moves, or give that joint a type and a limit"))
        elif i.kind == "out_of_range":
            out.append(_f(Severity.WARN, f"joint '{j}': following '{t}' drives it over {i.detail}, "
                                         "outside its own <limit>", target=j,
                          fix="Make the limits and the multiplier agree: multiplier * driver range "
                              "+ offset is the range this joint really has."))
        else:
            out.append(_f(Severity.ERROR, f"joint '{j}': <mimic> chain is a cycle through '{i.detail or j}'",
                          target=j, fix="one joint drives the chain; the rest follow it, directly or in a line"))


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
            try:
                sc = parse_floats(mesh.get("scale"), 3, f"link {name} mesh scale") if mesh.get("scale") else None
            except UrdfError as e:
                out.append(_f(Severity.ERROR, str(e), target=name, fix="Drop the scale attribute and size the geometry in model.py."))
                sc = None
            if sc is not None and any(abs(s - 1) > 1e-9 for s in sc):
                out.append(_f(Severity.WARN, f"link '{name}': mesh scale {list(sc)} — model in meters in model.py instead", target=name,
                              fix="Drop the scale attribute and size the geometry in model.py."))
        try:
            parse_origin(vis, f"link {name} visual")
        except UrdfError as e:
            out.append(_f(Severity.ERROR, str(e), target=name, fix='<origin xyz="0 0 0" rpy="0 0 0"/>'))
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


def _lint_joint(el: ET.Element, jname: str, link_names: list[str], parent_of: dict[str, str],
                out: list[GateFinding]) -> None:
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
    if jtype not in JOINT_TYPES or not parent or not child:
        return
    # the numbers (origin, axis, limit, mimic) and the per-joint rules are the loader's own:
    # the build parses this element through the same function, so lint-clean never raises there
    try:
        j = parse_joint(el)
    except UrdfError as e:
        out.append(_f(Severity.ERROR, str(e), target=jname))
        return
    rpy = matrix_to_rpy(j.origin[:3, :3])
    if any(abs(v) > 1e-9 for v in rpy):
        out.append(_f(Severity.INFO, f"joint '{jname}' uses a rotated origin (rpy); allowed, but point the <axis> instead where possible", target=jname))
    ax = el.find("axis")
    if jtype in MOVABLE_TYPES:
        if ax is None or ax.get("xyz") is None:
            out.append(_f(Severity.WARN, f"joint '{jname}': no <axis>; URDF defaults to '1 0 0'", target=jname, fix='<axis xyz="0 0 1"/>'))
        else:
            n = math.sqrt(sum(v * v for v in parse_floats(ax.get("xyz"), 3, "axis")))
            if abs(n - 1) > 1e-3:
                unit = " ".join(f"{v:.6g}" for v in j.axis)
                out.append(_f(Severity.WARN, f"joint '{jname}': axis not unit length (|a|={n:.4g}); auto-normalised", target=jname,
                              fix=f'<axis xyz="{unit}"/>'))
    lim = el.find("limit")
    if jtype in ("revolute", "prismatic") and j.lower is not None and j.upper is not None:
        if abs(j.upper - j.lower) < 1e-9:
            out.append(_f(Severity.WARN, f"joint '{jname}': lower == upper (joint cannot move)", target=jname,
                          fix="Give the joint a range, or make it type=fixed."))
        if jtype == "revolute" and j.upper - j.lower > 2 * math.pi + 1e-6:
            out.append(_f(Severity.WARN, f"joint '{jname}': revolute range > 2π — use type=continuous", target=jname))
        if lim is not None and (lim.get("effort") is None or lim.get("velocity") is None):
            out.append(_f(Severity.WARN, f"joint '{jname}': <limit> should carry effort and velocity", target=jname,
                          fix='effort="10" velocity="1"'))
    elif jtype == "continuous" and lim is not None and (lim.get("lower") is not None or lim.get("upper") is not None):
        out.append(_f(Severity.WARN, f"joint '{jname}': continuous joints have no lower/upper (ignored)", target=jname,
                      fix='<limit effort="10" velocity="1"/> or use type=revolute'))


# ------------------------------------------------------------------ model.py
class _Visitor(ImportCollector):
    def __init__(self) -> None:
        super().__init__()
        self.findings: list[GateFinding] = []
        self.strings: set[str] = set()

    def visit_Call(self, node: ast.Call) -> None:
        chain = dotted(node.func)
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

    def _import_finding(kind: str, mod: str, line: int) -> GateFinding:
        if mod == "codeverse3d":
            return _f(Severity.ERROR, f"line {line}: model.py must not import the harness", target=label, fix="Raw bpy only.", line=line)
        if kind == "forbidden":
            return _f(Severity.ERROR, f"line {line}: import of '{mod}' is not allowed in model.py", target=label,
                      fix="Build geometry with bpy/bmesh/mathutils/math only.", line=line)
        return _f(Severity.WARN, f"line {line}: import of '{mod}' is unexpected in model.py (outside the contract's list)",
                  target=label, fix="Build geometry with bpy/bmesh/mathutils/math only.", line=line)

    out.extend(check_imports(v.imports, forbidden=FORBIDDEN_IMPORTS, allowed=ALLOWED_IMPORTS, make_finding=_import_finding))
    if "bpy" not in v.imports:
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
    return GateReport.of(GATE, findings, duration_ms=int((time.time() - t0) * 1000))


# ===================================================================== skeleton
DEFAULT_EFFORT = 10.0
DEFAULT_VELOCITY = 1.0


@dataclass(frozen=True)
class LinkFrame:
    """World placement of one link frame at rest + its plan bbox."""

    name: str
    frame_xyz: tuple[float, float, float]
    bbox_center: tuple[float, float, float]
    bbox_extents: tuple[float, float, float]
    parent_joint: str | None


@dataclass(frozen=True)
class JointRow:
    name: str
    type: str
    parent: str
    child: str
    origin_xyz: tuple[float, float, float]
    axis: tuple[float, float, float]
    lower: float | None
    upper: float | None
    rest: float = 0.0  # plan rest value the limits were shifted by (0 → limits == plan limits)
    mimic: tuple[str, float, float] | None = None  # (driving joint, multiplier, offset)
    mimic_note: str = ""  # written beside the <mimic> as a comment (an instanced driver's other copies)


@dataclass
class UrdfFrames:
    robot_name: str
    root: str
    links: dict[str, LinkFrame]
    joints: list[JointRow]


def _fmt(v: float) -> str:
    s = f"{v:.6f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def _vec(v: tuple[float, float, float]) -> str:
    return " ".join(_fmt(x) for x in v)


def _expand_parts(plan: ArticulatedPlan) -> list[tuple[str, PartPlan, tuple[float, float, float]]]:
    """(link_name, part, bbox_center) — parts with ``instances > 1`` become ``<name>_1..n``
    spread along +x so the baseline has no overlaps (the agent repositions them)."""
    out = []
    for p in plan.parts:
        base = to_snake(p.name)
        if p.instances <= 1:
            out.append((base, p, tuple(p.bbox.center)))
            continue
        cx, cy, cz = p.bbox.center
        step = p.bbox.extents[0] * 1.5
        for i in range(p.instances):
            out.append((f"{base}_{i + 1}", p, (cx + i * step, cy, cz)))
    return out


def compute_urdf_frames(plan: ArticulatedPlan) -> UrdfFrames:
    """Pure function: plan → link frames + joint rows in the enforced convention."""
    parts = _expand_parts(plan)
    base_names = {to_snake(p.name) for p in plan.parts}
    root = to_snake(plan.root_link)
    links: dict[str, LinkFrame] = {}
    pivot_of: dict[str, tuple[float, float, float]] = {}
    parent_of: dict[str, str] = {}
    joints: list[JointRow] = []

    offsets = {n: tuple(c - o for c, o in zip(center, p.bbox.center, strict=True)) for n, p, center in parts}

    def add_joint(j: JointPlan, child: str, parent: str, suffix: str = "") -> None:
        pivot_of[child] = tuple(float(v) + o for v, o in zip(j.pivot, offsets[child], strict=True))
        parent_of[child] = parent
        lower = upper = None
        if j.type in ("revolute", "prismatic"):
            lower, upper = j.lower - j.rest, j.upper - j.rest
        mim = None
        if j.mimic is not None:
            # every copy of an instanced follower follows the SAME driver; the driver's own
            # name is resolved after emission, when the instance suffixes are known
            mim = (to_snake(j.mimic.joint), float(j.mimic.multiplier), float(j.mimic.offset))
        joints.append(JointRow(name=to_snake(j.name) + suffix, type=j.type, parent=parent, child=child,
                               origin_xyz=(0.0, 0.0, 0.0), axis=tuple(float(v) for v in j.axis), lower=lower, upper=upper,
                               rest=float(j.rest) if lower is not None else 0.0, mimic=mim))

    for j in plan.joints:
        parent, child = to_snake(j.parent), to_snake(j.child)
        if parent in base_names and parent not in {n for n, _, _ in parts}:
            parent = f"{parent}_1"  # joint to an instanced parent → first instance
        # one instance link by its own name, else the part and every instance link it expanded to
        children = [n for n, p, _ in parts if n == child or to_snake(p.name) == child]
        for k, c in enumerate(children):
            add_joint(j, c, parent, "" if len(children) == 1 else f"_{k + 1}")

    # A driver whose child is instanced exists only as <name>_1..._n, so the plan's name
    # names no joint in the file: follow the first instance, the rule an instanced PARENT
    # link already uses above.  The plan validator guarantees the target is a plan joint,
    # so anything unresolved here is a bug in this function, not in the plan.
    emitted = {jr.name for jr in joints}
    for i, jr in enumerate(joints):
        if jr.mimic is None or jr.mimic[0] in emitted:
            continue
        target, mult, off = jr.mimic
        if f"{target}_1" not in emitted:
            raise ValueError(f"joint {jr.name}: mimic target {target!r} was not emitted; joints are {sorted(emitted)}")
        # the other instances stay independent inputs the sweep drives on their own; say so
        # in the file, here where the instancing is known, rather than guessing from names
        others = sorted(n for n in emitted if n.startswith(f"{target}_") and n != f"{target}_1")
        joints[i] = replace(jr, mimic=(f"{target}_1", mult, off),
                            mimic_note=f"follows {target}_1 only; {', '.join(others)} stay independent inputs "
                                       "(add a <mimic> per instance if they move together)")

    # link frames: root at origin, others at their pivot
    for name, p, center in parts:
        frame = (0.0, 0.0, 0.0) if name == root else pivot_of.get(name, (0.0, 0.0, 0.0))
        pj = next((jr.name for jr in joints if jr.child == name), None)
        links[name] = LinkFrame(name=name, frame_xyz=frame, bbox_center=center,
                                bbox_extents=tuple(float(v) for v in p.bbox.extents), parent_joint=pj)
    # joint origins relative to the parent link frame
    fixed: list[JointRow] = []
    for jr in joints:
        pf = links[jr.parent].frame_xyz
        cf = links[jr.child].frame_xyz
        fixed.append(JointRow(name=jr.name, type=jr.type, parent=jr.parent, child=jr.child,
                              origin_xyz=tuple(c - p for c, p in zip(cf, pf, strict=True)), axis=jr.axis,
                              lower=jr.lower, upper=jr.upper, rest=jr.rest, mimic=jr.mimic,
                              mimic_note=jr.mimic_note))
    return UrdfFrames(robot_name=to_snake(plan.object_name), root=root, links=links, joints=fixed)


# ------------------------------------------------------------------ text renderers
def _limit_note(jr: JointRow) -> str:
    """Comment explaining a rest-shifted limit (empty when the plan's rest is 0)."""
    if jr.lower is None or jr.upper is None or abs(jr.rest) < 1e-12:
        return ""
    return (f"  <!-- plan lower={_fmt(jr.lower + jr.rest)} upper={_fmt(jr.upper + jr.rest)} rest={_fmt(jr.rest)}: "
            f"the mesh is authored at rest, so q=0 = plan {_fmt(jr.rest)} and the limits are shifted by -rest -->")


def render_urdf(frames: UrdfFrames) -> str:
    lines = ['<?xml version="1.0"?>', f'<robot name="{frames.robot_name}">']
    lines.append("  <!-- link frames: root at the world origin; every other link frame sits at its joint pivot (world, rest pose). -->")
    lines.append("  <!-- visual/collision origin = -(link frame world) because meshes/<link>.glb hold WORLD coordinates at rest. -->")
    lines.append("  <!-- q=0 is the authored pose (the plan's rest pose); limits are the plan's lower-rest .. upper-rest. -->")
    for name, lf in frames.links.items():
        vis = tuple(-v for v in lf.frame_xyz)
        lines.append(f'  <link name="{name}">  <!-- frame at world {_vec(lf.frame_xyz)} -->')
        for tag in ("visual", "collision"):
            lines.append(f'    <{tag}><origin xyz="{_vec(vis)}" rpy="0 0 0"/><geometry><mesh filename="meshes/{name}.glb"/></geometry></{tag}>')
        lines.append("  </link>")
    for jr in frames.joints:
        lines.append(f'  <joint name="{jr.name}" type="{jr.type}">')
        lines.append(f'    <parent link="{jr.parent}"/>')
        lines.append(f'    <child link="{jr.child}"/>')
        lines.append(f'    <origin xyz="{_vec(jr.origin_xyz)}" rpy="0 0 0"/>  <!-- pivot_world(child) - frame_world(parent) -->')
        if jr.type != "fixed":
            lines.append(f'    <axis xyz="{_vec(jr.axis)}"/>')
            lim = f'effort="{_fmt(DEFAULT_EFFORT)}" velocity="{_fmt(DEFAULT_VELOCITY)}"'
            if jr.lower is not None and jr.upper is not None:
                lim = f'lower="{_fmt(jr.lower)}" upper="{_fmt(jr.upper)}" ' + lim
            lines.append(f"    <limit {lim}/>" + _limit_note(jr))
            if jr.mimic is not None:
                src, mult, off = jr.mimic
                lines.append(f'    <mimic joint="{src}" multiplier="{_fmt(mult)}" offset="{_fmt(off)}"/>'
                             f"  <!-- driven: the sweep moves the driver, this joint follows"
                             f"{'; ' + jr.mimic_note if jr.mimic_note else ''} -->")
        lines.append("  </joint>")
    lines.append("</robot>")
    return "\n".join(lines) + "\n"


def render_model_py(plan: ArticulatedPlan, frames: UrdfFrames) -> str:
    parts_by_link = {n: p for n, p, _ in _expand_parts(plan)}
    head = f'''"""{plan.object_name} — link meshes for robot.urdf (pure bpy, Z-up, -Y front, meters).

CONTRACT: build ONE mesh object per URDF link, named EXACTLY like the link, placed
at its REST-POSE WORLD position (= URDF q=0, the pose the plan's bboxes describe).
Extra helper objects must be parented under a link object (they are joined into
it).  No cameras / lights / render / export calls.
Replace every placeholder box below with real, detailed geometry; keep the names.
"""
import bpy
import bmesh  # noqa: F401  (handy for detail)
import math   # noqa: F401
from mathutils import Vector  # noqa: F401


def box(name, center, size):
    """Axis-aligned box helper: center/size in world meters; returns the object."""
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=center)
    ob = bpy.context.active_object
    ob.name = name
    ob.scale = size
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    return ob


'''
    body = []
    for name, lf in frames.links.items():
        p = parts_by_link[name]
        role = (p.role or "").replace("\n", " ")
        desc = (p.description or "").replace("\n", " ")
        body.append(f"# link '{name}': {role}\n#   {desc}")
        if lf.parent_joint:
            jr = next(j for j in frames.joints if j.name == lf.parent_joint)
            note = f"  (plan rest={_fmt(jr.rest)} → this pose is URDF q=0)" if abs(jr.rest) > 1e-12 else ""
            body.append(f"#   joint '{lf.parent_joint}' pivot (world) = {lf.frame_xyz}{note}")
        body.append(f"{name} = box({name!r}, center={tuple(round(v, 4) for v in lf.bbox_center)}, "
                    f"size={tuple(round(v, 4) for v in lf.bbox_extents)})\n")
    tail = f"\n# Sanity: every link object exists\nfor _n in {list(frames.links)!r}:\n    assert _n in bpy.data.objects, _n\n"
    return head + "\n".join(body) + tail


def write_skeleton(ws: Workspace, plan: ArticulatedPlan) -> list[Path]:
    """Write src/model.py + src/robot.urdf; returns the written paths."""
    frames = compute_urdf_frames(plan)
    ws.src.mkdir(parents=True, exist_ok=True)
    model = ws.src / "model.py"
    urdf = ws.src / "robot.urdf"
    model.write_text(render_model_py(plan, frames))
    urdf.write_text(render_urdf(frames))
    return [model, urdf]


# ===================================================================== runtime
WRAPPER = Path(__file__).resolve().parent.parent / "wrappers" / "run_bpy_links.py"
REST_PENETRATION_MAX_M = 0.005
FK_TOL_M = 0.001

#: every canonical artifact this build owns — staged together so a failed build
#: can never leave a previous round's output looking current
STAGED_OUTPUTS = ("build.json", "census.json", "meshes", "robot.urdf", "object.glb")


class UrdfBlenderRuntime(RuntimeLayout):
    language = Language.URDF_BLENDER
    entry_globs = (ENTRY_FILE[Language.URDF_BLENDER], "src/robot.urdf")
    extra_files = ("src/robot.urdf",)

    # ------------------------------------------------------------ protocol
    def skeleton(self, ws: Workspace, plan: Plan) -> list[Path]:
        if not isinstance(plan, ArticulatedPlan):
            raise TypeError(f"urdf_blender skeleton needs an ArticulatedPlan, got {type(plan).__name__}")
        return write_skeleton(ws, plan)

    def lint(self, ws: Workspace) -> GateReport:
        return lint_workspace(ws)


    # ------------------------------------------------------------ build
    def build(self, ws: Workspace, *, timeout_s: int | None = None) -> BuildResult:
        """All five canonical outputs go through one :class:`ArtifactStage`: entering
        it invalidates them BEFORE any early return can leak a previous round's
        files, everything is written to staging, and only an ``ok=True`` build
        promotes the full set.  Every exit path publishes the FINAL BuildResult as
        ``artifacts/build.json`` — the wrapper's own build.json used to stay on disk
        saying ``ok: true`` while a post-wrapper check (UrdfError / FkInconsistent /
        RestPenetration) failed the build."""
        t0 = time.time()
        settings = get_settings()
        timeout_s = timeout_s or settings.limits.build_timeout_s
        ws.artifacts.mkdir(parents=True, exist_ok=True)
        with ws.stage_artifacts(*STAGED_OUTPUTS) as stage:
            return self._build_staged(ws, stage, t0=t0, settings=settings, timeout_s=timeout_s)

    def _build_staged(self, ws: Workspace, stage: ArtifactStage, *, t0: float, settings: Settings,
                      timeout_s: int) -> BuildResult:
        art = ws.artifacts

        def finish(res: BuildResult) -> BuildResult:
            # disk never contradicts the returned result: build.json is ALWAYS the
            # final BuildResult; the rest of the set is published only on ok
            ws.write_json(stage.path("build.json"), res)
            if res.ok:
                stage.promote()
            elif stage.path("census.json").is_file():
                stage.promote("build.json", "census.json")
            else:
                stage.promote("build.json")
            return res

        def fail(error_type: str, message: str, *, file: str = "src/robot.urdf", line: int | None = None,
                 census: dict[str, Any] | None = None, **kw: Any) -> BuildResult:
            return finish(BuildResult(ok=False, language=self.language.value, error_type=error_type,
                                      error_message=message, error_file=file, error_line=line,
                                      duration_ms=int((time.time() - t0) * 1000), census=census or {}, **kw))

        if (missing := missing_entry(ws, self.language, write=False)) is not None:
            return finish(missing)
        # 1. lint (cheap, no Blender)
        lint = self.lint(ws)
        census: dict[str, Any] = {"lint": [f.model_dump(mode="json") for f in lint.findings]}
        if not lint.passed:
            errs = lint.errors
            msg = "\n".join(f"- {f.message}" + (f"  → {f.fix_hint}" if f.fix_hint else "") for f in errs[:12])
            return fail("LintError", f"{len(errs)} lint error(s):\n{msg}", file=str(errs[0].target or "src/robot.urdf"),
                        line=errs[0].data.get("line"), census=census)

        # 2. Blender wrapper (writes build.json / census.json / meshes/ into the staging dir),
        #    read like every other wrapper's report
        blender = settings.resolve_blender()
        if not blender:
            return fail("BlenderNotFound", "no Blender binary (set C3D_BINARIES__BLENDER)", file="", census=census)
        proc = _run_blender(blender, ws, stage.staging_dir, timeout_s, settings.limits.bpy_rlimit_gb)
        wrapped = compose_build_result(language=self.language.value, proc=proc, build_json=stage.path("build.json"),
                                       census_json=stage.path("census.json"), glb_path=None, extra_paths={},
                                       output_filter=strip_blender_noise)
        census.update(wrapped.census)
        if not wrapped.ok:
            hints = "\n".join(f"  hint: {h}" for h in (census.get("hints") or {}).values())
            return fail(wrapped.error_type or "ScriptError", wrapped.error_message + ("\n" + hints if hints else ""),
                        file=wrapped.error_file or "src/model.py", line=wrapped.error_line, census=census,
                        stdout_tail=wrapped.stdout_tail, stderr_tail=wrapped.stderr_tail)

        # 3. URDF copy + load (staged files; extra_paths name the canonical homes)
        urdf_staged = stage.path("robot.urdf")
        shutil.copyfile(ws.root / "src" / "robot.urdf", urdf_staged)
        extra = {"urdf": str(art / "robot.urdf"), "meshes_dir": str(art / "meshes")}
        try:
            robot = load_urdf(urdf_staged, stage.path("meshes"))
        except UrdfError as e:
            return fail("UrdfError", str(e), census=census)

        # 4. FK consistency
        fk_findings = check_fk_consistency(robot, census.get("links") or {}, tol_m=FK_TOL_M)
        census["fk_check"] = [f.model_dump(mode="json") for f in fk_findings]
        if fk_findings:
            msg = "\n".join(f"- {f.as_line()}" for f in fk_findings)
            return fail("FkInconsistent", f"URDF frames do not reproduce the authored geometry:\n{msg}", census=census)

        # 5. the rest pose — the only pose that decides the build (D17); every other pose is
        #    the round's joint_sweep gate (spatial.joints_sweep.sweep_gate), which the tool reports too
        report = sweep_collisions(robot, [{}], volumes=False)
        findings = sweep_findings(report, rest_max_m=REST_PENETRATION_MAX_M)

        # 6. canonical GLB at rest
        urdf_to_glb(robot, stage.path("object.glb"), None)
        extra["object_glb"] = str(art / "object.glb")
        res = BuildResult(ok=True, language=self.language.value, glb_path=str(art / "object.glb"), extra_paths=extra,
                          stdout_tail=wrapped.stdout_tail, stderr_tail=wrapped.stderr_tail,
                          duration_ms=int((time.time() - t0) * 1000), census=census)
        if report.summary.rest_max_penetration_m > REST_PENETRATION_MAX_M:
            worst = [f for f in findings if f.severity == "error"]
            res.ok = False
            res.error_type = "RestPenetration"
            res.error_file = "src/model.py"
            res.error_message = (f"links interpenetrate by {report.summary.rest_max_penetration_m*1000:.1f} mm at the rest pose "
                                 f"(max {REST_PENETRATION_MAX_M*1000:.0f} mm):\n" +
                                 "\n".join(f"- {f.as_line()}" for f in worst[:8]))
            res.glb_path = None      # the fresh GLB is NOT published on a failed build
            res.extra_paths = {}
        return finish(res)


def _run_blender(blender: str, ws: Workspace, art: Path, timeout_s: int, rlimit_gb: int) -> ProcResult:
    cmd = [blender, "-b", "--factory-startup", "--python", str(WRAPPER), "--",
           "--script", str(ws.root / "src" / "model.py"), "--urdf", str(ws.root / "src" / "robot.urdf"),
           "--out", str(art), "--rlimit-gb", str(rlimit_gb)]
    # whitelist env (no inherited PYTHONPATH, user config pinned into artifacts) — deliberately
    # stricter than blender.runtime.blender_env(); keep it that way
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", str(ws.root)),
           "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1", "BLENDER_USER_CONFIG": str(art / ".blender_config")}
    return run_subprocess(cmd, cwd=ws.root, env=env, timeout_s=timeout_s)

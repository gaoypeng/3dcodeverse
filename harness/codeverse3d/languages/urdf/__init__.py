"""``urdf_blender``: bpy link meshes + hand-written URDF — lint, skeleton, FK ↔ geometry
consistency and the ``UrdfBlenderRuntime`` around ``languages/wrappers/run_bpy_links.py``."""

from __future__ import annotations

import ast
import contextlib
import math
import os
import shutil
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import numpy as np

from codeverse3d.config import Settings, get_settings
from codeverse3d.contracts.artifacts import BuildResult, GateFinding, GateReport, LintKind, Severity
from codeverse3d.contracts.common import ENTRY_FILE, Language, MimicSpec, mimic_issues
from codeverse3d.conventions import LINK_NAME_RE, to_snake
from codeverse3d.languages._ast_lint import dotted, safe_parse
from codeverse3d.languages._common import compose_build_result, missing_entry, strip_blender_noise
from codeverse3d.languages.base import RuntimeLayout
from codeverse3d.languages.blender import lint_blender_source
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
from codeverse3d.spatial.joints_sweep import (
    REST_PENETRATION_MAX_M,
    sweep_collisions,
    sweep_findings,
)
from codeverse3d.workspace import ArtifactStage, Workspace

# ===================================================================== consistency
#: this gate's own name
FK_GATE = "fk_consistency"


def _vec(v: tuple[float, float, float]) -> str:
    return " ".join(f"{x:g}" if abs(x) >= 1e-9 else "0" for x in v)


def check_fk_consistency(robot: Robot, census_links: dict[str, dict[str, Any]], *, tol_m: float = 0.001) -> list[GateFinding]:
    """Compare every link's FK-posed mesh bbox at rest with the authored census bbox; ERROR per inconsistent link with a corrected origin."""
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

#: model.py runs in Blender's python, so it is linted by the blender language's rules; these are the extras a link script adds.
LINK_SCRIPT_CALLS: dict[str, tuple[Severity, str, LintKind]] = {
    "exit": (Severity.ERROR, "exits Blender before the links are exported", LintKind.SANDBOX),
    "quit": (Severity.ERROR, "exits Blender before the links are exported", LintKind.SANDBOX),
    "input": (Severity.ERROR, "blocks headless Blender forever", LintKind.HANG_RISK),
    "time.sleep": (Severity.WARN, "pointless in a build script", LintKind.HANG_RISK),
}


def _f(kind: LintKind, sev: Severity, msg: str, *, target: str | None = None, fix: str = "", **data) -> GateFinding:
    return GateFinding(gate=GATE, severity=sev, target=target, message=msg, fix_hint=fix, data={"kind": kind.value, **data})


def _floats(text: str | None) -> list[float] | None:
    try:
        return [float(v) for v in (text or "").split()]
    except ValueError:
        return None


# ------------------------------------------------------------------ URDF
def lint_urdf_text(text: str, *, label: str = URDF_REL) -> tuple[list[GateFinding], list[str]]:
    out: list[GateFinding] = []
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        line = getattr(e, "position", (None, None))[0]
        out.append(_f(LintKind.URDF_STRUCTURE, Severity.ERROR, f"{label} is not well-formed XML: {e}", target=label,
                      fix="Fix the XML (every <tag> closed, attributes quoted, one <robot> root element).", line=line))
        return out, []
    if root.tag != "robot":
        out.append(_f(LintKind.URDF_STRUCTURE, Severity.ERROR, f"root element must be <robot>, got <{root.tag}>", target=label,
                      fix='Wrap everything in <robot name="..."> ... </robot>.'))
        return out, []
    if not root.get("name"):
        out.append(_f(LintKind.URDF_STRUCTURE, Severity.WARN, "<robot> has no name attribute", target=label, fix='<robot name="my_object">'))

    links = root.findall("link")
    joints = root.findall("joint")
    link_names: list[str] = []
    for el in links:
        name = el.get("name", "")
        if not name:
            out.append(_f(LintKind.URDF_STRUCTURE, Severity.ERROR, "<link> without name", target=label, fix='<link name="base">'))
            continue
        if name in link_names:
            out.append(_f(LintKind.URDF_STRUCTURE, Severity.ERROR, f"duplicate link name '{name}'", target=name, fix="Link names must be unique."))
        link_names.append(name)
        _lint_link(el, name, out)
    if not link_names:
        out.append(_f(LintKind.URDF_STRUCTURE, Severity.ERROR, "URDF has no <link> elements", target=label, fix="Add one <link> per mesh object in model.py."))
        return out, []

    joint_names: list[str] = []
    parent_of: dict[str, str] = {}
    for el in joints:
        jname = el.get("name", "")
        if not jname:
            out.append(_f(LintKind.URDF_STRUCTURE, Severity.ERROR, "<joint> without name", target=label, fix='<joint name="hinge" type="revolute">'))
            continue
        if jname in joint_names:
            out.append(_f(LintKind.URDF_STRUCTURE, Severity.ERROR, f"duplicate joint name '{jname}'", target=jname, fix="Joint names must be unique."))
        joint_names.append(jname)
        _lint_joint(el, jname, link_names, parent_of, out)

    _lint_mimics(joints, out)

    children = set(parent_of)
    roots = [n for n in link_names if n not in children]
    if len(roots) != 1:
        out.append(_f(LintKind.URDF_STRUCTURE, Severity.ERROR, f"expected exactly one root link (no parent joint), found {roots}", target=label,
                      fix="Every link except the root must be the <child> of exactly one joint; connect extra roots with a fixed joint."))
    else:
        for link in link_names:
            seen, cur = set(), link
            while cur in parent_of and cur not in seen:
                seen.add(cur)
                cur = parent_of[cur]
            if cur != roots[0]:
                out.append(_f(LintKind.URDF_STRUCTURE, Severity.ERROR, f"link '{link}' does not reach the root '{roots[0]}' (cycle or detached)", target=link,
                              fix="Joints must form a single tree rooted at the base link."))
    return out, link_names


def _lint_mimics(joints: list[ET.Element], out: list[GateFinding]) -> None:
    """``<mimic>`` couplings over the whole file; the rules are ``contracts.common.mimic_issues``."""
    # every named joint becomes a spec; mimic_issues resolves targets against that set
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
    # not checked here: whether a follower of `<driver>_1` also follows `_2..n` (only the skeleton knows the instances)
    for i in mimic_issues(specs):
        j, t = i.joint, i.target
        if i.kind == "immobile":
            out.append(_f(LintKind.MIMIC, Severity.ERROR, f"joint '{j}': a fixed joint has nothing to mimic", target=j,
                          fix="give it a type and a limit, or drop the <mimic>"))
        elif i.kind == "zero_multiplier":
            out.append(_f(LintKind.MIMIC, Severity.ERROR, f"joint '{j}': <mimic multiplier=\"0\"> — the joint cannot move",
                          target=j, fix="use type=fixed, or a non-zero multiplier"))
        elif i.kind == "self":
            out.append(_f(LintKind.MIMIC, Severity.ERROR, f"joint '{j}': <mimic> names itself", target=j))
        elif i.kind == "unknown_target":
            out.append(_f(LintKind.MIMIC, Severity.ERROR, f"joint '{j}': <mimic joint=\"{t}\"> names no joint in this file",
                          target=j, fix=f"one of: {', '.join(known)}"))
        elif i.kind == "immobile_target":
            out.append(_f(LintKind.MIMIC, Severity.ERROR, f"joint '{j}': mimics '{t}', which is fixed and never moves", target=j,
                          fix="mimic a joint that moves, or give that joint a type and a limit"))
        elif i.kind == "out_of_range":
            out.append(_f(LintKind.MIMIC, Severity.WARN, f"joint '{j}': following '{t}' drives it over {i.detail}, "
                                         "outside its own <limit>", target=j,
                          fix="Make the limits and the multiplier agree: multiplier * driver range "
                              "+ offset is the range this joint really has."))
        else:
            out.append(_f(LintKind.MIMIC, Severity.ERROR, f"joint '{j}': <mimic> chain is a cycle through '{i.detail or j}'",
                          target=j, fix="one joint drives the chain; the rest follow it, directly or in a line"))


def _lint_link(el: ET.Element, name: str, out: list[GateFinding]) -> None:
    if name in RESERVED_LINK_NAMES:
        out.append(_f(LintKind.NAMING, Severity.ERROR, f"link name '{name}' is reserved by the GLB scene graph (glTF readers use it as the base frame)",
                      target=name, fix="Rename the link (e.g. 'base' or the part's name) in BOTH robot.urdf and model.py."))
    if not LINK_NAME_RE.match(name):
        out.append(_f(LintKind.NAMING, Severity.WARN, f"link name '{name}' is not a plain identifier (letters/digits/underscore)", target=name,
                      fix=f"Rename to '{to_snake(name)}' in BOTH robot.urdf and model.py (object names are case-sensitive; "
                          "Blender's auto-suffix '.001' means two objects shared a name)."))
    if any(w in to_snake(name).split("_") for w in _STATE_WORDS):
        out.append(_f(LintKind.NAMING, Severity.WARN, f"link name '{name}' contains a state word — links are parts, states come from joints", target=name,
                      fix="Name the part (door, drawer, lid), not its state."))
    visuals = el.findall("visual")
    expected = f"meshes/{name}.glb"
    if len(visuals) != 1:
        out.append(_f(LintKind.URDF_LINK, Severity.ERROR, f"link '{name}' has {len(visuals)} <visual> elements; exactly one is required", target=name,
                      fix=f'<visual><origin xyz="..." rpy="0 0 0"/><geometry><mesh filename="{expected}"/></geometry></visual>'))
    for vis in visuals:
        geom = vis.find("geometry")
        mesh = geom.find("mesh") if geom is not None else None
        if geom is None or mesh is None:
            kinds = [c.tag for c in geom] if geom is not None else []
            out.append(_f(LintKind.URDF_LINK, Severity.ERROR, f"link '{name}': visual geometry must be a <mesh> (found {kinds or 'nothing'})", target=name,
                          fix=f'Build the shape in model.py and reference <mesh filename="{expected}"/>.'))
        else:
            fn = mesh.get("filename", "")
            if fn != expected:
                out.append(_f(LintKind.URDF_LINK, Severity.ERROR, f"link '{name}': mesh filename '{fn}' must be '{expected}'", target=name,
                              fix=f'<mesh filename="{expected}"/>'))
            try:
                sc = parse_floats(mesh.get("scale"), 3, f"link {name} mesh scale") if mesh.get("scale") else None
            except UrdfError as e:
                out.append(_f(LintKind.URDF_LINK, Severity.ERROR, str(e), target=name, fix="Drop the scale attribute and size the geometry in model.py."))
                sc = None
            if sc is not None and any(abs(s - 1) > 1e-9 for s in sc):
                out.append(_f(LintKind.URDF_LINK, Severity.WARN, f"link '{name}': mesh scale {list(sc)} — model in meters in model.py instead", target=name,
                              fix="Drop the scale attribute and size the geometry in model.py."))
        try:
            parse_origin(vis, f"link {name} visual")
        except UrdfError as e:
            out.append(_f(LintKind.URDF_LINK, Severity.ERROR, str(e), target=name, fix='<origin xyz="0 0 0" rpy="0 0 0"/>'))
        col = el.find("collision")
        if col is None:
            out.append(_f(LintKind.URDF_LINK, Severity.WARN, f"link '{name}' has no <collision> twin of its visual", target=name,
                          fix="Copy the <visual> block as <collision> (same origin + geometry)."))
        else:
            if _sig(col.find("geometry")) != _sig(geom) or _origin_sig(col.find("origin")) != _origin_sig(vis.find("origin")):
                out.append(_f(LintKind.URDF_LINK, Severity.WARN, f"link '{name}': <collision> differs from <visual>; the harness collides the VISUAL mesh", target=name,
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
        out.append(_f(LintKind.URDF_JOINT, Severity.ERROR, f"joint '{jname}': type '{jtype}' must be one of {JOINT_TYPES}", target=jname,
                      fix='type="revolute" (hinge) | "prismatic" (slide) | "continuous" (wheel) | "fixed"'))
    p = el.find("parent")
    c = el.find("child")
    parent = p.get("link", "") if p is not None else ""
    child = c.get("link", "") if c is not None else ""
    for role, lk in (("parent", parent), ("child", child)):
        if not lk:
            out.append(_f(LintKind.URDF_JOINT, Severity.ERROR, f"joint '{jname}': missing <{role} link=...>", target=jname, fix=f'<{role} link="base"/>'))
        elif lk not in link_names:
            out.append(_f(LintKind.URDF_JOINT, Severity.ERROR, f"joint '{jname}': {role} link '{lk}' does not exist (links: {link_names})", target=jname,
                          fix="Reference an existing <link name>."))
    if parent and child and parent == child:
        out.append(_f(LintKind.URDF_JOINT, Severity.ERROR, f"joint '{jname}': parent == child", target=jname, fix="A joint connects two different links."))
    if child:
        if child in parent_of:
            out.append(_f(LintKind.URDF_JOINT, Severity.ERROR, f"link '{child}' is the child of two joints ('{jname}' and another)", target=jname,
                          fix="Each link has exactly one parent joint (tree)."))
        parent_of[child] = parent
    if jtype not in JOINT_TYPES or not parent or not child:
        return
    # the numbers (origin, axis, limit, mimic) and the per-joint rules are the loader's own:
    # the build parses this element through the same function, so lint-clean never raises there
    try:
        j = parse_joint(el)
    except UrdfError as e:
        out.append(_f(LintKind.URDF_JOINT, Severity.ERROR, str(e), target=jname))
        return
    rpy = matrix_to_rpy(j.origin[:3, :3])
    if any(abs(v) > 1e-9 for v in rpy):
        out.append(_f(LintKind.URDF_JOINT, Severity.INFO, f"joint '{jname}' uses a rotated origin (rpy); allowed, but point the <axis> instead where possible", target=jname))
    ax = el.find("axis")
    if jtype in MOVABLE_TYPES:
        if ax is None or ax.get("xyz") is None:
            out.append(_f(LintKind.URDF_JOINT, Severity.WARN, f"joint '{jname}': no <axis>; URDF defaults to '1 0 0'", target=jname, fix='<axis xyz="0 0 1"/>'))
        else:
            n = math.sqrt(sum(v * v for v in parse_floats(ax.get("xyz"), 3, "axis")))
            if abs(n - 1) > 1e-3:
                unit = " ".join(f"{v:.6g}" for v in j.axis)
                out.append(_f(LintKind.URDF_JOINT, Severity.WARN, f"joint '{jname}': axis not unit length (|a|={n:.4g}); auto-normalised", target=jname,
                              fix=f'<axis xyz="{unit}"/>'))
    lim = el.find("limit")
    if jtype in ("revolute", "prismatic") and j.lower is not None and j.upper is not None:
        if abs(j.upper - j.lower) < 1e-9:
            out.append(_f(LintKind.URDF_JOINT, Severity.WARN, f"joint '{jname}': lower == upper (joint cannot move)", target=jname,
                          fix="Give the joint a range, or make it type=fixed."))
        if jtype == "revolute" and j.upper - j.lower > 2 * math.pi + 1e-6:
            out.append(_f(LintKind.URDF_JOINT, Severity.WARN, f"joint '{jname}': revolute range > 2π — use type=continuous", target=jname))
        if lim is not None and (lim.get("effort") is None or lim.get("velocity") is None):
            out.append(_f(LintKind.URDF_JOINT, Severity.WARN, f"joint '{jname}': <limit> should carry effort and velocity", target=jname,
                          fix='effort="10" velocity="1"'))
    elif jtype == "continuous" and lim is not None and (lim.get("lower") is not None or lim.get("upper") is not None):
        out.append(_f(LintKind.URDF_JOINT, Severity.WARN, f"joint '{jname}': continuous joints have no lower/upper (ignored)", target=jname,
                      fix='<limit effort="10" velocity="1"/> or use type=revolute'))


# ------------------------------------------------------------------ model.py
def lint_model_text(text: str, link_names: list[str], *, label: str = MODEL_REL) -> list[GateFinding]:
    """model.py: the blender language's lint + LINK_SCRIPT_CALLS, ``while True`` and URDF link names as string literals."""
    out = [f.model_copy(update={"gate": GATE})
           for f in lint_blender_source(text, target=label, expect_names=False).findings]
    tree, _ = safe_parse(text, label)
    if tree is None:
        return out      # the blender lint already reported the parse failure
    strings: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            strings.add(node.value)
        elif isinstance(node, ast.Call) and (rule := LINK_SCRIPT_CALLS.get(dotted(node.func))):
            sev, why, kind = rule
            out.append(_f(kind, sev, f"line {node.lineno}: {dotted(node.func)}(): {why}", target=label,
                          fix="Remove the call." if sev == Severity.ERROR else "", line=node.lineno))
        elif isinstance(node, ast.While) and isinstance(node.test, ast.Constant) and node.test.value is True:
            out.append(_f(LintKind.HANG_RISK, Severity.WARN, f"line {node.lineno}: 'while True' in a build script risks a hang",
                          target=label, line=node.lineno))
    for link in link_names:
        if not any(link in s for s in strings):
            out.append(_f(LintKind.LINK_NAME, Severity.WARN, f"link '{link}' never appears as a string in {label} — the wrapper looks for an object named exactly '{link}'",
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
        findings.append(_f(LintKind.MISSING_FILE, Severity.ERROR, f"missing {URDF_REL}", target=URDF_REL, fix="Write src/robot.urdf (see the contract)."))
    else:
        f, link_names = lint_urdf_text(urdf_p.read_text())
        findings.extend(f)
    if not model_p.is_file():
        findings.append(_f(LintKind.MISSING_FILE, Severity.ERROR, f"missing {MODEL_REL}", target=MODEL_REL, fix="Write src/model.py (pure bpy, one object per link)."))
    else:
        findings.extend(lint_model_text(model_p.read_text(), link_names))
    return GateReport.of(GATE, findings, duration_ms=int((time.time() - t0) * 1000))


# ===================================================================== skeleton


# ------------------------------------------------------------------ text renderers


# ===================================================================== runtime
WRAPPER = Path(__file__).resolve().parent.parent / "wrappers" / "run_bpy_links.py"
FK_TOL_M = 0.001

#: every canonical artifact this build owns, staged together
STAGED_OUTPUTS = ("build.json", "census.json", "meshes", "robot.urdf", "object.glb")


class UrdfBlenderRuntime(RuntimeLayout):
    language = Language.URDF_BLENDER
    entry_globs = (ENTRY_FILE[Language.URDF_BLENDER], "src/robot.urdf")
    extra_files = ("src/robot.urdf",)


    def lint(self, ws: Workspace) -> GateReport:
        return lint_workspace(ws)


    # ------------------------------------------------------------ build
    def build(self, ws: Workspace, *, timeout_s: int | None = None) -> BuildResult:
        """All canonical outputs go through one :class:`ArtifactStage`: invalidated on entry, promoted only on ``ok=True``;
        ``artifacts/build.json`` is always the final BuildResult."""
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
        lint = self.lint(ws)
        census: dict[str, Any] = {}
        if not lint.passed:
            errs = lint.errors
            msg = "\n".join(f"- {f.message}" + (f"  → {f.fix_hint}" if f.fix_hint else "") for f in errs[:12])
            return fail("LintError", f"{len(errs)} lint error(s):\n{msg}", file=str(errs[0].target or "src/robot.urdf"),
                        line=errs[0].data.get("line"), census=census)

        # 1. Blender wrapper (writes build.json / census.json / meshes/ into the staging dir),
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

        urdf_staged = stage.path("robot.urdf")
        shutil.copyfile(ws.root / "src" / "robot.urdf", urdf_staged)
        extra = {"urdf": str(art / "robot.urdf"), "meshes_dir": str(art / "meshes")}
        try:
            robot = load_urdf(urdf_staged, stage.path("meshes"))
        except UrdfError as e:
            return fail("UrdfError", str(e), census=census)

        fk_findings = check_fk_consistency(robot, census.get("links") or {}, tol_m=FK_TOL_M)
        if fk_findings:
            msg = "\n".join(f"- {f.as_line()}" for f in fk_findings)
            return fail("FkInconsistent", f"URDF frames do not reproduce the authored geometry:\n{msg}", census=census)

        # 2. the rest pose decides the build; other poses are the joint_sweep gate
        report = sweep_collisions(robot, [{}])
        findings = sweep_findings(report, rest_max_m=REST_PENETRATION_MAX_M)

        urdf_to_glb(robot, stage.path("object.glb"), None)
        extra["object_glb"] = str(art / "object.glb")
        res = BuildResult(ok=True, language=self.language.value, glb_path=str(art / "object.glb"), extra_paths=extra,
                          stdout_tail=wrapped.stdout_tail, stderr_tail=wrapped.stderr_tail,
                          duration_ms=int((time.time() - t0) * 1000), census=census)
        if report.rest_max_penetration_m > REST_PENETRATION_MAX_M:
            worst = [f for f in findings if f.severity == "error"]
            res.ok = False
            res.error_type = "RestPenetration"
            res.error_file = "src/model.py"
            res.error_message = (f"links interpenetrate by {report.rest_max_penetration_m*1000:.1f} mm at the rest pose "
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
    # stricter than languages.blender.blender_env(); keep it that way
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", str(ws.root)),
           "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1", "BLENDER_USER_CONFIG": str(art / ".blender_config")}
    return run_subprocess(cmd, cwd=ws.root, env=env, timeout_s=timeout_s)

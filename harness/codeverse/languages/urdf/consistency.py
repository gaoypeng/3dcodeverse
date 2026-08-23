"""FK ↔ authored-geometry consistency check for ``urdf_blender`` builds.

``model.py`` authors every link mesh at its rest-pose WORLD placement; the
wrapper exports those world coordinates into ``meshes/<link>.glb`` and records
the world bbox per link in ``census.json``.  The URDF must then place each mesh
back exactly where it was authored at ``q = 0``:

    T_link_rest(q=0) · T_visual · mesh_world  ==  mesh_world
    ⇒  T_visual == inv(T_link_rest)

We verify this numerically (bbox of the FK-posed mesh vs the census bbox, 1 mm)
and report the exact corrected ``<origin>`` when it is wrong.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from codeverse.contracts.artifacts import GateFinding, Severity
from codeverse.spatial.joints_model import Robot, fk, invert_transform, matrix_to_rpy

GATE = "fk_consistency"


def _fmt(v: float) -> str:
    s = f"{v:.6f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def _vec(v) -> str:
    return " ".join(_fmt(float(x)) for x in v)


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
            out.append(GateFinding(gate=GATE, severity=Severity.ERROR, target=name,
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
            gate=GATE, severity=Severity.ERROR, target=name,
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

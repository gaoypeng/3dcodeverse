"""Articulation tools on URDF robots (facade).

Import from here::

    from codeverse.spatial.joints import load_urdf, fk, pose_samples, sweep_collisions, urdf_to_glb

* :mod:`codeverse.spatial.joints_model`  — ``Robot``/``Link``/``Joint``, ``load_urdf``,
  ``fk``, ``pose_samples``, ``limit_poses``, URDF frame math.
* :mod:`codeverse.spatial.joints_sweep`  — ``sweep_collisions`` (FCL + trimesh),
  ``sweep_findings`` (→ GateFindings), ``motion_direction_check``.
* :mod:`codeverse.spatial.joints_export` — ``urdf_to_glb`` (hierarchical GLB with joint
  extras), ``render_poses`` (+ ``articulation_sheet.png``).
"""

from codeverse.spatial.joints_export import (
    ARTICULATION_SHEET_NAME,
    ZUP_TO_YUP,
    render_poses,
    robot_scene,
    urdf_to_glb,
)
from codeverse.spatial.joints_model import (
    Joint,
    Link,
    Robot,
    UrdfError,
    fk,
    link_world_meshes,
    load_urdf,
)
from codeverse.spatial.joints_poses import limit_poses, pose_label, pose_samples
from codeverse.spatial.joints_sweep import (
    MotionCheck,
    Overlap,
    SweepReport,
    SweepSummary,
    motion_direction_check,
    summary_text,
    sweep_collisions,
    sweep_findings,
)

__all__ = [
    "ARTICULATION_SHEET_NAME", "ZUP_TO_YUP", "Joint", "Link", "MotionCheck", "Overlap", "Robot", "SweepReport",
    "SweepSummary", "UrdfError", "fk", "limit_poses",
    "link_world_meshes", "load_urdf", "motion_direction_check", "pose_label", "pose_samples", "render_poses",
    "robot_scene", "summary_text", "sweep_collisions", "sweep_findings", "urdf_to_glb",
]

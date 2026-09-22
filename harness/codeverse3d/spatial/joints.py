"""Articulation tools on URDF robots (facade).

Import from here::

    from codeverse3d.spatial.joints import load_urdf, fk, pose_samples, sweep_collisions, urdf_to_glb

* :mod:`codeverse3d.spatial.joints_model`  — ``Robot``/``Link``/``Joint``, ``load_urdf``,
  ``fk``, ``pose_samples``, ``limit_poses``, URDF frame math.
* :mod:`codeverse3d.spatial.joints_sweep`  — ``sweep_collisions`` (FCL + trimesh),
  ``sweep_findings`` (→ GateFindings), ``sweep_gate`` (THE ``joint_sweep`` verdict),
  ``motion_direction_check``.
* :mod:`codeverse3d.spatial.joints_export` — ``urdf_to_glb`` (hierarchical GLB with joint
  extras), ``render_poses`` (+ ``articulation_sheet.png``).
"""

from codeverse3d.spatial.joints_export import (
    ARTICULATION_SHEET_NAME,
    ZUP_TO_YUP,
    render_poses,
    robot_scene,
    urdf_to_glb,
)
from codeverse3d.spatial.joints_model import (
    Joint,
    Link,
    Robot,
    UrdfError,
    fk,
    link_world_meshes,
    load_urdf,
)
from codeverse3d.spatial.joints_poses import limit_poses, pose_label, pose_samples
from codeverse3d.spatial.joints_sweep import (
    SWEEP_GATE,
    MotionCheck,
    Overlap,
    SweepReport,
    SweepSummary,
    aggregate_findings,
    buried_links,
    find_urdf,
    motion_direction_check,
    sweep_collisions,
    sweep_findings,
    sweep_gate,
)

__all__ = [
    "ARTICULATION_SHEET_NAME", "SWEEP_GATE", "ZUP_TO_YUP", "Joint", "Link", "MotionCheck", "Overlap", "Robot", "SweepReport",
    "SweepSummary", "UrdfError", "fk", "find_urdf", "limit_poses",
    "link_world_meshes", "load_urdf", "motion_direction_check", "pose_label", "pose_samples", "render_poses",
    "aggregate_findings", "buried_links", "robot_scene", "sweep_collisions", "sweep_findings", "sweep_gate", "urdf_to_glb",
]

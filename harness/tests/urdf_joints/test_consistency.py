"""FK ↔ authored bbox consistency check."""

from __future__ import annotations

from codeverse3d.contracts.artifacts import Severity
from codeverse3d.languages.urdf import check_fk_consistency
from codeverse3d.spatial.joints_model import load_urdf
from tests.urdf_joints.conftest import write_mesh_robot

CENSUS = {"body": {"bbox_min": [-0.3, -0.2, 0.0], "bbox_max": [0.3, 0.2, 0.8]},
          "door": {"bbox_min": [-0.29, -0.22, 0.01], "bbox_max": [0.29, -0.2, 0.79]}}


def test_consistent(tmp_path):
    urdf, meshes = write_mesh_robot(tmp_path)
    assert check_fk_consistency(load_urdf(urdf, meshes), CENSUS) == []


def test_sign_error_reports_exact_fix(tmp_path):
    urdf, meshes = write_mesh_robot(tmp_path, door_visual_xyz="-0.29 -0.2 0")
    f = check_fk_consistency(load_urdf(urdf, meshes), CENSUS)
    assert len(f) == 1 and f[0].severity == Severity.ERROR and f[0].target == "door"
    assert 'xyz="0.29 0.2 0"' in f[0].fix_hint
    assert f[0].data["corrected_visual_origin"]["xyz"] == [0.29, 0.2, 0.0]
    assert f[0].data["error_m"] > 0.5


def test_missing_census_row(tmp_path):
    urdf, meshes = write_mesh_robot(tmp_path)
    f = check_fk_consistency(load_urdf(urdf, meshes), {"body": CENSUS["body"]})
    assert [x.target for x in f] == ["door"] and "no authored mesh" in f[0].message

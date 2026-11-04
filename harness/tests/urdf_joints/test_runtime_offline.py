"""UrdfBlenderRuntime.build with the Blender step faked (offline)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import codeverse3d.languages.urdf as rt_mod
from codeverse3d.languages.urdf import UrdfBlenderRuntime
from codeverse3d.proc import ProcResult
from codeverse3d.workspace import Workspace
from tests.urdf_joints.conftest import box_glb

BODY = ((0, 0, 0.4), (0.6, 0.4, 0.8))
DOOR = ((0, -0.21, 0.4), (0.58, 0.02, 0.78))


def _census_row(center, size):
    lo = [c - s / 2 for c, s in zip(center, size, strict=True)]
    hi = [c + s / 2 for c, s in zip(center, size, strict=True)]
    return {"mesh": "", "tris": 12, "verts": 8, "bbox_min": lo, "bbox_max": hi, "objects": []}


@pytest.fixture
def fake_blender(monkeypatch):
    """Replace the Blender subprocess with a writer of meshes/census/build.json."""
    state = {"door": DOOR, "ok": True, "error": None}

    def run(blender, ws, art, timeout_s, rlimit_gb):
        (art / "meshes").mkdir(parents=True, exist_ok=True)
        if state["error"]:
            (art / "build.json").write_text(json.dumps({"ok": False, **state["error"]}))
            (art / "census.json").write_text(json.dumps({"objects": [], "links": {}, "hints": {"door": "did you mean ['Door']?"}}))
            return ProcResult(returncode=0, stdout="", stderr="", timed_out=False, duration_ms=0)
        box_glb(art / "meshes" / "body.glb", *BODY)
        box_glb(art / "meshes" / "door.glb", *state["door"])
        (art / "census.json").write_text(json.dumps({"objects": [], "links": {"body": _census_row(*BODY), "door": _census_row(*state["door"])},
                                                     "unmatched_objects": [], "missing_links": [], "hints": {}}))
        (art / "build.json").write_text(json.dumps({"ok": True}))
        return ProcResult(returncode=0, stdout="built\n", stderr="", timed_out=False, duration_ms=0)

    monkeypatch.setattr(rt_mod, "_run_blender", run)
    monkeypatch.setattr(rt_mod, "get_settings", lambda: SimpleNamespace(
        resolve_blender=lambda: "/fake/blender", limits=SimpleNamespace(build_timeout_s=60, bpy_rlimit_gb=4)))
    return state


def _ws(tmp_path, cabinet_plan):
    ws = Workspace(tmp_path / "ws").create()
    UrdfBlenderRuntime().skeleton(ws, _two_link(cabinet_plan))
    return ws


def _two_link(plan):
    return plan.model_copy(update={"parts": plan.parts[:2], "joints": plan.joints[:1]})


def test_build_rest_penetration_fails_and_publishes_nothing(tmp_path, cabinet_plan, fake_blender):
    fake_blender["door"] = ((0, -0.19, 0.4), (0.58, 0.02, 0.78))  # authored 10 mm inside the body
    ws = _ws(tmp_path, cabinet_plan)
    res = UrdfBlenderRuntime().build(ws)
    assert not res.ok and res.error_type == "RestPenetration"
    assert "at the rest pose (max 5 mm)" in res.error_message and "'body' and 'door' overlap" in res.error_message
    # a failed build publishes NOTHING but its status: the fresh GLB stays unshipped
    assert res.glb_path is None and res.extra_paths == {}
    for name in ("object.glb", "robot.urdf"):
        assert not (ws.artifacts / name).exists(), name
    assert not (ws.artifacts / "meshes").exists()
    assert json.loads((ws.artifacts / "build.json").read_text())["ok"] is False


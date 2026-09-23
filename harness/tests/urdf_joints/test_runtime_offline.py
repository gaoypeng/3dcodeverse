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


def test_build_script_error_maps_line_and_a_timeout_is_typed(tmp_path, cabinet_plan, fake_blender, monkeypatch):
    fake_blender["error"] = {"error_type": "NameError", "error_message": "name 'bpyy' is not defined", "error_file": "src/model.py",
                            "error_line": 7, "stderr_tail": "Traceback..."}
    ws = _ws(tmp_path, cabinet_plan)
    res = UrdfBlenderRuntime().build(ws)
    assert not res.ok and res.error_type == "NameError" and res.error_line == 7 and res.error_file == "src/model.py"
    assert "hint: did you mean" in res.error_message
    monkeypatch.setattr(rt_mod, "_run_blender",
                        lambda *a, **k: ProcResult(returncode=-9, stdout="", stderr="", timed_out=True, duration_ms=0))
    res = UrdfBlenderRuntime().build(ws, timeout_s=1)
    assert not res.ok and res.error_type == "BuildTimeout"


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


def test_post_wrapper_failure_never_leaves_ok_true_build_json(tmp_path, cabinet_plan, fake_blender):
    """The wrapper reports ok:true, then FkInconsistent fails the build — the
    published build.json must say what the returned BuildResult says (the old code
    left the wrapper's ok:true on disk while the run failed)."""
    ws = _ws(tmp_path, cabinet_plan)
    u = ws.src / "robot.urdf"
    u.write_text(u.read_text().replace('xyz="0.29 0.2 0"', 'xyz="-0.29 -0.2 0"'))
    res = UrdfBlenderRuntime().build(ws)
    assert not res.ok and res.error_type == "FkInconsistent"
    assert 'xyz="0.29 0.2 0"' in res.error_message and res.census["fk_check"][0]["target"] == "door"
    disk = json.loads((ws.artifacts / "build.json").read_text())
    assert disk["ok"] is False and disk["error_type"] == "FkInconsistent"
    assert not (ws.artifacts / "object.glb").exists() and not (ws.artifacts / "meshes").exists()


def test_lint_fail_invalidates_stale_artifacts(tmp_path, cabinet_plan, fake_blender):
    ws = _ws(tmp_path, cabinet_plan)
    rt = UrdfBlenderRuntime()
    assert rt.build(ws).ok
    (ws.src / "robot.urdf").write_text("<robot name='x'><link name='a'>")
    res = rt.build(ws)
    assert not res.ok and res.error_type == "LintError" and "well-formed" in res.error_message
    assert not (ws.artifacts / "object.glb").exists() and not (ws.artifacts / "meshes").exists()
    assert json.loads((ws.artifacts / "build.json").read_text())["error_type"] == "LintError"


def test_wrapper_rejects_unsafe_link_names_offline():
    """run_bpy_links refuses link names that are not plain identifiers — ``../evil``
    must be a build error, never a ``meshes/<link>.glb`` write outside meshes/.  The
    wrapper needs bpy, so its pure name check is exec'd out of the source."""
    import ast
    import re as _re

    src = rt_mod.WRAPPER.read_text()
    assert "UnsafeLinkName" in src and "_bad_links(links)" in src  # main() wired to the check
    tree = ast.parse(src)
    picked = [n for n in tree.body
              if (isinstance(n, ast.FunctionDef) and n.name == "_bad_links")
              or (isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "_SAFE_LINK")]
    assert len(picked) == 2
    ns: dict = {"re": _re}
    exec(compile(ast.Module(body=picked, type_ignores=[]), str(rt_mod.WRAPPER), "exec"), ns)
    bad = ns["_bad_links"](["body", "door_2", "DoorHandle", "../evil", "a/b", "Door.001", "", "9lives"])
    assert bad == ["../evil", "a/b", "Door.001", "", "9lives"]

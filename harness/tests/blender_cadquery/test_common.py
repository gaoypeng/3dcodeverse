"""Unit tests for codeverse3d.languages._common (offline)."""

from __future__ import annotations

import json
from pathlib import Path

from codeverse3d.contracts.artifacts import BuildResult
from codeverse3d.languages._common import (
    ProcResult,
    compose_build_result,
    strip_blender_noise,
)
from codeverse3d.proc import write_json_atomic


def test_strip_blender_noise() -> None:
    raw = "Blender 5.0.1 (hash x)\nRead prefs: ...\n12:00:00 | INFO: Starting glTF 2.0 export\nTraceback (most recent call last):\n  boom\nBlender quit"
    assert strip_blender_noise(raw).splitlines() == ["Traceback (most recent call last):", "  boom"]


def _proc(**kw) -> ProcResult:
    base = dict(returncode=0, stdout="", stderr="", timed_out=False, duration_ms=10)
    base.update(kw)
    return ProcResult(**base)


def test_compose_timeout(tmp_path: Path) -> None:
    r = compose_build_result(language="blender", proc=_proc(timed_out=True, returncode=-9), build_json=tmp_path / "b.json",
                             census_json=tmp_path / "c.json", glb_path=tmp_path / "o.glb", extra_paths={})
    assert not r.ok and r.error_type == "BuildTimeout"
    assert json.loads((tmp_path / "b.json").read_text())["error_type"] == "BuildTimeout"  # a timeout is published too


def test_compose_wrapper_crash(tmp_path: Path) -> None:
    r = compose_build_result(language="blender", proc=_proc(returncode=2, stderr="Traceback: bad"), build_json=tmp_path / "b.json",
                             census_json=tmp_path / "c.json", glb_path=tmp_path / "o.glb", extra_paths={})
    assert not r.ok and r.error_type == "WrapperCrash" and "bad" in r.stderr_tail


def test_compose_ok_requires_glb(tmp_path: Path) -> None:
    b = tmp_path / "build.json"
    report = {"ok": True, "error_type": "", "error_message": "", "duration_ms": 42, "warnings": ["w"]}
    write_json_atomic(b, report)
    write_json_atomic(tmp_path / "census.json", {"tri_count": 12})
    r = compose_build_result(language="blender", proc=_proc(), build_json=b, census_json=tmp_path / "census.json",
                             glb_path=tmp_path / "o.glb", extra_paths={"stl": tmp_path / "o.stl"})
    assert not r.ok and r.error_type == "ExportEmpty"
    assert BuildResult.model_validate_json(b.read_text()) == r  # the one status file: the final result, not the report
    (tmp_path / "o.glb").write_bytes(b"glTF" + b"\0" * 20)
    (tmp_path / "o.stl").write_bytes(b"solid")
    write_json_atomic(b, report)  # the next build's wrapper reports again
    r = compose_build_result(language="blender", proc=_proc(), build_json=b, census_json=tmp_path / "census.json",
                             glb_path=tmp_path / "o.glb", extra_paths={"stl": tmp_path / "o.stl", "blend": tmp_path / "nope.blend"})
    assert r.ok and r.glb_path == str(tmp_path / "o.glb") and r.duration_ms == 42
    assert r.extra_paths == {"stl": str(tmp_path / "o.stl")}
    assert r.census["tri_count"] == 12 and r.census["build_report"]["warnings"] == ["w"]
    assert BuildResult.model_validate_json(b.read_text()) == r


def test_compose_without_a_glb_goes_by_the_report(tmp_path: Path) -> None:
    """The URDF wrapper exports meshes/, not object.glb: its report alone decides."""
    b = tmp_path / "build.json"
    write_json_atomic(b, {"ok": True, "duration_ms": 7})
    r = compose_build_result(language="urdf_blender", proc=_proc(), build_json=b, census_json=tmp_path / "c.json",
                             glb_path=None, extra_paths={})
    assert r.ok and r.glb_path is None and r.error_type == ""


def test_compose_script_error_fields(tmp_path: Path) -> None:
    b = tmp_path / "build.json"
    b.write_text(json.dumps({"ok": False, "error_type": "IndexError", "error_message": "boom", "error_file": "model.py", "error_line": 15}))
    r = compose_build_result(language="blender", proc=_proc(), build_json=b, census_json=tmp_path / "none.json",
                             glb_path=tmp_path / "o.glb", extra_paths={})
    assert not r.ok and r.error_type == "IndexError" and r.error_line == 15 and r.error_file == "model.py"

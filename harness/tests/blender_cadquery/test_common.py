"""Unit tests for codeverse3d.languages._common (offline)."""

from __future__ import annotations

import json
from pathlib import Path

from codeverse3d.contracts.artifacts import BuildResult
from codeverse3d.languages._common import (
    ProcResult,
    compose_build_result,
)
from codeverse3d.proc import write_json_atomic


def _proc(**kw) -> ProcResult:
    base = dict(returncode=0, stdout="", stderr="", timed_out=False, duration_ms=10)
    base.update(kw)
    return ProcResult(**base)


def test_compose_build_result(tmp_path: Path) -> None:
    r = compose_build_result(language="blender", proc=_proc(timed_out=True, returncode=-9), build_json=tmp_path / "t.json",
                             census_json=tmp_path / "c.json", glb_path=tmp_path / "o.glb", extra_paths={})
    assert not r.ok and r.error_type == "BuildTimeout"
    assert json.loads((tmp_path / "t.json").read_text())["error_type"] == "BuildTimeout"  # a timeout is published too
    r = compose_build_result(language="blender", proc=_proc(returncode=2, stderr="Traceback: bad"), build_json=tmp_path / "w.json",
                             census_json=tmp_path / "c.json", glb_path=tmp_path / "o.glb", extra_paths={})
    assert not r.ok and r.error_type == "WrapperCrash" and "bad" in r.stderr_tail
    # ok requires the GLB
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

"""Unit tests for codeverse.languages._common (offline)."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from codeverse.languages._common import (
    ProcResult,
    compose_build_result,
    run_subprocess,
    strip_blender_noise,
    tail,
    write_json_atomic,
)


def test_run_subprocess_captures_output(tmp_path: Path) -> None:
    r = run_subprocess([sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"],
                       cwd=tmp_path, timeout_s=10)
    assert r.returncode == 3 and r.stdout.strip() == "out" and r.stderr.strip() == "err" and not r.timed_out


def test_run_subprocess_timeout_kills_group(tmp_path: Path) -> None:
    code = "import subprocess, sys, time; p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); print(p.pid, flush=True); time.sleep(30)"
    t0 = time.monotonic()
    r = run_subprocess([sys.executable, "-c", code], cwd=tmp_path, timeout_s=1.5)
    assert r.timed_out and time.monotonic() - t0 < 6
    child_pid = int(r.stdout.strip())
    time.sleep(0.3)
    assert not Path(f"/proc/{child_pid}").exists() or "zombie" in (Path(f"/proc/{child_pid}/status").read_text().lower())


def test_tail_limits() -> None:
    text = "\n".join(str(i) for i in range(100))
    t = tail(text, max_lines=5)
    assert t.splitlines() == ["95", "96", "97", "98", "99"]
    assert len(tail("x" * 10_000, max_chars=100)) == 100


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


def test_compose_wrapper_crash(tmp_path: Path) -> None:
    r = compose_build_result(language="blender", proc=_proc(returncode=2, stderr="Traceback: bad"), build_json=tmp_path / "b.json",
                             census_json=tmp_path / "c.json", glb_path=tmp_path / "o.glb", extra_paths={})
    assert not r.ok and r.error_type == "WrapperCrash" and "bad" in r.stderr_tail


def test_compose_ok_requires_glb(tmp_path: Path) -> None:
    b = tmp_path / "build.json"
    write_json_atomic(b, {"ok": True, "error_type": "", "error_message": "", "duration_ms": 42, "warnings": ["w"]})
    write_json_atomic(tmp_path / "census.json", {"tri_count": 12})
    r = compose_build_result(language="blender", proc=_proc(), build_json=b, census_json=tmp_path / "census.json",
                             glb_path=tmp_path / "o.glb", extra_paths={"stl": tmp_path / "o.stl"})
    assert not r.ok and r.error_type == "ExportEmpty"
    (tmp_path / "o.glb").write_bytes(b"glTF" + b"\0" * 20)
    (tmp_path / "o.stl").write_bytes(b"solid")
    r = compose_build_result(language="blender", proc=_proc(), build_json=b, census_json=tmp_path / "census.json",
                             glb_path=tmp_path / "o.glb", extra_paths={"stl": tmp_path / "o.stl", "blend": tmp_path / "nope.blend"})
    assert r.ok and r.glb_path == str(tmp_path / "o.glb") and r.duration_ms == 42
    assert r.extra_paths == {"stl": str(tmp_path / "o.stl")}
    assert r.census["tri_count"] == 12 and r.census["build_report"]["warnings"] == ["w"]


def test_compose_script_error_fields(tmp_path: Path) -> None:
    b = tmp_path / "build.json"
    b.write_text(json.dumps({"ok": False, "error_type": "IndexError", "error_message": "boom", "error_file": "model.py", "error_line": 15}))
    r = compose_build_result(language="blender", proc=_proc(), build_json=b, census_json=tmp_path / "none.json",
                             glb_path=tmp_path / "o.glb", extra_paths={})
    assert not r.ok and r.error_type == "IndexError" and r.error_line == 15 and r.error_file == "model.py"

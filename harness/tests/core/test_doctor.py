"""``3dcodeverse doctor``: the GPU probe hands the browser back, and a broken install is a row, never a crash."""

from __future__ import annotations

import json
from pathlib import Path


def _fake_probe(monkeypatch, tmp_path: Path, payload: dict) -> list:
    from codeverse3d import doctor
    from codeverse3d.proc import ProcResult

    def run(cmd, **kw):
        return ProcResult(0, "Blender 5.2.2 LTS\nC3D_DOCTOR " + json.dumps(payload) + "\nBlender quit\n", "", False, 5)

    monkeypatch.setenv("C3D_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(doctor, "run_subprocess", run)
    return doctor.check_blender_render()


def test_doctor_reports_the_blender_render_capability(tmp_path: Path, monkeypatch):
    rows = {r[0]: r for r in _fake_probe(monkeypatch, tmp_path, {"cuda": ["RTX"], "cycles_ms": 310})}
    assert rows["blender cycles"][1] == "OK" and "CUDA: RTX" in rows["blender cycles"][2]
    assert "310 ms" in rows["blender cycles"][2]
    assert rows["blender gpu slots"][1] == "OK"
    soft = {r[0]: r for r in _fake_probe(monkeypatch, tmp_path, {"cuda": []})}
    assert soft["blender cycles"][1] == "WARN" and "CPU" in soft["blender cycles"][2]

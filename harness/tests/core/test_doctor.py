"""``3dcode doctor``: the GPU probe hands the browser back, and a broken install is a row, never a crash."""

from __future__ import annotations

import json
from pathlib import Path


def _fake_probe(monkeypatch, tmp_path: Path, payload: dict) -> list:
    from codeverse3d import doctor
    from codeverse3d.proc import ProcResult

    seen: dict = {}

    def run(cmd, **kw):
        seen["cmd"], seen["env"] = cmd, kw.get("env") or {}
        return ProcResult(0, "Blender 5.2.2 LTS\nC3D_DOCTOR " + json.dumps(payload) + "\nBlender quit\n", "", False, 5)

    monkeypatch.setenv("C3D_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(doctor, "run_subprocess", run)
    rows = doctor.check_blender_render(eevee=True)
    assert seen["env"].get("GALLIUM_DRIVER") == "d3d12", "EEVEE is probed under the d3d12 env it renders with"
    return rows


def test_doctor_reports_the_blender_render_capability(tmp_path: Path, monkeypatch):
    rows = {r[0]: r for r in _fake_probe(monkeypatch, tmp_path, {"cuda": ["RTX"], "gl": "D3D12 (NVIDIA RTX)", "cycles_ms": 310})}
    assert rows["blender cycles"][1] == "OK" and "CUDA: RTX" in rows["blender cycles"][2]
    assert "310 ms" in rows["blender cycles"][2]
    assert rows["blender eevee"][1] == "OK" and rows["blender gpu slots"][1] == "OK"
    soft = {r[0]: r for r in _fake_probe(monkeypatch, tmp_path, {"cuda": [], "gl": "llvmpipe (LLVM 19.1.1, 256 bits)"})}
    assert soft["blender cycles"][1] == "WARN" and "CPU" in soft["blender cycles"][2]
    assert soft["blender eevee"][1] == "WARN" and "software GL" in soft["blender eevee"][2]

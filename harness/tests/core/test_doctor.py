"""``3dcode doctor``: the GPU probe hands the browser back, and a broken install is a row, never a crash."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from codeverse3d.doctor import check_gpu_probe, check_node


@pytest.mark.node
def test_the_gpu_probe_releases_the_browser_instead_of_closing_it(tmp_path: Path, monkeypatch):
    """close() would kill the daemon's SHARED browser under every concurrent run; release() disconnects."""
    mark = tmp_path / "mark"
    (tmp_path / "gpu_launch.cjs").write_text(
        f"const fs = require('fs'), mark = {json.dumps(str(mark))};\n"
        "exports.launchBrowser = async () => ({gpu: true, renderer: 'fake', shared: true,\n"
        "  browser: {close: async () => fs.writeFileSync(mark, 'close')},\n"
        "  release: async () => fs.writeFileSync(mark, 'release')});\n")
    monkeypatch.setenv("C3D_RUNTIME_JS", str(tmp_path))
    assert check_gpu_probe(timeout_s=60) == [("chrome webgl", "OK", "GPU: fake")]
    assert mark.read_text() == "release"


def test_a_torn_package_json_is_a_failed_row_not_a_crash(tmp_path: Path, monkeypatch):
    """B9: one torn node_modules/*/package.json crashed the whole report."""
    for pkg in ("three", "puppeteer"):
        (tmp_path / "node_modules" / pkg).mkdir(parents=True)
        (tmp_path / "node_modules" / pkg / "package.json").write_text('{"version": "0.18')
    monkeypatch.setenv("C3D_RUNTIME_JS", str(tmp_path))
    rows = {name: (status, detail) for name, status, detail in check_node()}
    assert rows["three"][0] == "FAIL" and "unreadable" in rows["three"][1]
    assert rows["puppeteer"][0] == "FAIL"


def _fake_probe(monkeypatch, tmp_path: Path, payload: dict) -> list:
    from codeverse3d import doctor
    from codeverse3d.proc import ProcResult

    seen: dict = {}

    def run(cmd, **kw):
        seen["cmd"], seen["env"] = cmd, kw.get("env") or {}
        return ProcResult(0, "Blender 5.0.1\nC3D_DOCTOR " + json.dumps(payload) + "\nBlender quit\n", "", False, 5)

    monkeypatch.setenv("C3D_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(doctor, "run_subprocess", run)
    rows = doctor.check_blender_render(eevee=True)
    assert seen["env"].get("GALLIUM_DRIVER") == "d3d12", "EEVEE is probed under the d3d12 env it renders with"
    return rows


def test_doctor_reports_the_blender_render_capability(tmp_path: Path, monkeypatch):
    rows = {r[0]: r for r in _fake_probe(monkeypatch, tmp_path, {"cuda": ["RTX"], "gl": "D3D12 (NVIDIA RTX)"})}
    assert rows["blender cycles"][1] == "OK" and "CUDA: RTX" in rows["blender cycles"][2]
    assert "cycles 32 spp" in rows["blender cycles"][2]
    assert rows["blender eevee"][1] == "OK" and rows["blender gpu slots"][1] == "OK"
    soft = {r[0]: r for r in _fake_probe(monkeypatch, tmp_path, {"cuda": [], "gl": "llvmpipe (LLVM 19.1.1, 256 bits)"})}
    assert soft["blender cycles"][1] == "WARN" and "CPU" in soft["blender cycles"][2]
    assert soft["blender eevee"][1] == "WARN" and "software GL" in soft["blender eevee"][2]


@pytest.mark.blender
def test_doctor_blender_render_probe_runs_for_real():
    from codeverse3d.config import get_settings
    from codeverse3d.doctor import check_blender_render

    if not get_settings().resolve_blender():
        pytest.skip("no Blender binary")
    rows = {r[0]: r for r in check_blender_render(eevee=False)}
    assert rows["blender cycles"][1] in ("OK", "WARN") and "blender eevee" not in rows

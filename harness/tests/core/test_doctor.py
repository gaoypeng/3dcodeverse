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
        return ProcResult(0, "Blender 5.2.2 LTS\nC3D_DOCTOR " + json.dumps(payload) + "\nBlender quit\n", "", False, 5)

    monkeypatch.setenv("C3D_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(doctor, "run_subprocess", run)
    rows = doctor.check_blender_render(eevee=True)
    assert seen["env"].get("GALLIUM_DRIVER") == "d3d12", "EEVEE is probed under the d3d12 env it renders with"
    return rows


def test_doctor_reports_the_blender_render_capability(tmp_path: Path, monkeypatch):
    rows = {r[0]: r for r in _fake_probe(monkeypatch, tmp_path, {"cuda": ["RTX"], "gl": "D3D12 (NVIDIA RTX)", "cycles_ms": 310})}
    assert rows["blender cycles"][1] == "OK" and "CUDA: RTX" in rows["blender cycles"][2]
    assert "cycles 32 spp" in rows["blender cycles"][2]
    assert "310 ms" in rows["blender cycles"][2] and "persistent data" in rows["blender cycles"][2]
    assert "frame cap 20s" in rows["blender cycles"][2]
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


def test_doctor_skills_walks_every_shipped_overlay_and_labels_an_invalid_bundle_by_its_variant(tmp_path: Path, monkeypatch):
    """An overlay bundle is routed the moment its variant is active (``all_skills``), so the doctor
    checks each shipped variant's overlay as it checks the base library — one ``skills overlay`` row
    per variant, the variant leading the detail (the row NAME is fixed: docs/INSTALL.md documents it)."""
    from codeverse3d.config import get_settings
    from codeverse3d.doctor import check_skills
    from codeverse3d.prompts import variants as V
    from codeverse3d.skills.registry import ROUTED_SKILLS
    from tests.skills.conftest import write_bundle

    lib = tmp_path / "lib"
    for name in ROUTED_SKILLS:
        write_bundle(lib, name)
    overlay = lib / "variants" / "tv"
    write_bundle(overlay, "c3d-bbox-contract", body="OVERLAY RULES.")
    (overlay / "c3d-new-sheet").mkdir(parents=True)
    (overlay / "c3d-new-sheet" / "SKILL.md").write_text("---\nname: c3d-new-sheet\n# the closing fence is missing\n")
    prompts_root = tmp_path / "prompt_variants"
    (prompts_root / "tv").mkdir(parents=True)
    (prompts_root / "tv" / "variant.toml").write_text('[variant]\nname = "tv"\nversion = 1\n')
    monkeypatch.setattr(V, "VARIANTS_DIR", prompts_root)
    monkeypatch.setenv("C3D_SKILLS_DIR", str(lib))
    monkeypatch.delenv("C3D_VARIANT", raising=False)
    get_settings.cache_clear()
    try:
        rows = {name: (status, detail) for name, status, detail in check_skills()}
        assert rows["skills library"] == ("OK", f"{len(ROUTED_SKILLS)} bundles, 0 invalid")
        status, detail = rows["skills overlay"]
        assert status == "FAIL" and detail.startswith("tv: 2 bundles, 1 invalid: tv/c3d-new-sheet"), detail
        write_bundle(overlay, "c3d-new-sheet")
        status, detail = {n: (st, d) for n, st, d in check_skills()}["skills overlay"]
        assert status == "OK" and detail.startswith("tv: 2 bundles, 0 invalid") and "routes.toml: no rows" in detail
        (overlay / "routes.toml").write_text('[[boost]]\nskill = "c3d-bbox-contract"\ndelta = "ten"\n')
        status, detail = {n: (st, d) for n, st, d in check_skills()}["skills overlay"]
        assert status == "FAIL" and detail.startswith("tv: ") and "routes.toml invalid" in detail
        assert "delta must be an integer" in detail
    finally:
        get_settings.cache_clear()


def test_doctor_skills_reports_every_shipped_overlay(monkeypatch):
    """One ``skills overlay`` row per shipped variant, in name order: ``argon`` (empty) and ``argon_raw``
    (D113: the three lib-free ``c3d-bpy-scene-*`` shadows, no routes) — a dict keyed by the row name
    would keep only the last, so the rows are read as a list."""
    from codeverse3d.config import get_settings
    from codeverse3d.doctor import check_skills

    monkeypatch.delenv("C3D_SKILLS_DIR", raising=False)
    monkeypatch.delenv("C3D_VARIANT", raising=False)
    get_settings.cache_clear()
    try:
        rows = check_skills()
    finally:
        get_settings.cache_clear()
    assert next(status for name, status, _ in rows if name == "skills library") == "OK"
    overlays = [(status, detail) for name, status, detail in rows if name == "skills overlay"]
    assert [status for status, _ in overlays] == ["OK", "OK"], overlays
    assert overlays[0][1].startswith("argon: 0 bundles, 0 invalid")
    assert overlays[1][1].startswith("argon_raw: 3 bundles, 0 invalid") and "routes.toml: no rows" in overlays[1][1]

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

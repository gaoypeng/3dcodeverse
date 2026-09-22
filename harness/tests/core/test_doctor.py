"""``3dcode doctor``'s GPU probe must hand the browser back, never close it."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from codeverse3d.doctor import check_gpu_probe


@pytest.mark.node
def test_the_gpu_probe_releases_the_browser_instead_of_closing_it(tmp_path: Path, monkeypatch):
    """launchBrowser hands back the daemon's SHARED browser by default: ``browser.close()``
    sends CDP Browser.close and takes it down under every concurrent run, ``release()``
    disconnects.  A stand-in gpu_launch.cjs records which one the probe called."""
    mark = tmp_path / "mark"
    (tmp_path / "gpu_launch.cjs").write_text(
        f"const fs = require('fs'), mark = {json.dumps(str(mark))};\n"
        "exports.launchBrowser = async () => ({gpu: true, renderer: 'fake', shared: true,\n"
        "  browser: {close: async () => fs.writeFileSync(mark, 'close')},\n"
        "  release: async () => fs.writeFileSync(mark, 'release')});\n")
    monkeypatch.setenv("C3D_RUNTIME_JS", str(tmp_path))
    assert check_gpu_probe(timeout_s=60) == [("chrome webgl", "OK", "GPU: fake")]
    assert mark.read_text() == "release"

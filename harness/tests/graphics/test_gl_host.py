"""Offline: GlHost subprocess driving with a fake runner (GPU → CPU fallback, timeouts, crashes, gif/sheet)."""

from __future__ import annotations

import json

import pytest

from codeverse3d.spatial import gl_render
from codeverse3d.spatial.gl_render import (
    GlHost,
)

FAKE_RUNNER = r'''
import json, os, sys
from pathlib import Path
job = json.loads(Path(sys.argv[sys.argv.index("--job") + 1]).read_text())
out = Path(job["out_dir"]); out.mkdir(parents=True, exist_ok=True)
mode = os.environ.get("FAKE_MODE", "ok")
if os.environ.get("GALLIUM_DRIVER") == "d3d12" and mode in ("gpu_fail", "ok_cpu_only"):
    (out / "gl_result.json").write_text(json.dumps({"ok": False, "stage": "context", "error_type": "Error", "error_message": "no d3d12"}))
    sys.exit(3)
frames = []
for i, t in enumerate(sorted(set(job["times"]) | set(job.get("extra_times", [])))):
    p = out / "frames" / f"f{i:02d}_t{t:06.2f}.png"; p.parent.mkdir(exist_ok=True)
    from PIL import Image
    Image.new("RGB", (32, 18), (int(40 * t) % 255, 80, 120)).save(p)
    frames.append({"index": i, "time": t, "path": str(p), "judge": t in job["times"], "nan": 0, "inf": 0, "mean": 0.3})
(out / "gl_result.json").write_text(json.dumps({"ok": True, "stage": "render", "renderer": "fake " + os.environ.get("GALLIUM_DRIVER", ""), "frames": frames}))
'''


@pytest.fixture
def fake_runner(tmp_path, monkeypatch):
    runner = tmp_path / "fake_run_gl.py"
    runner.write_text(FAKE_RUNNER)
    monkeypatch.setattr(gl_render, "RUNNER", runner)
    monkeypatch.setattr(gl_render, "_ENV_PASS", gl_render._ENV_PASS + ("FAKE_MODE",))
    gl_render._gpu_state["ok"] = None
    yield runner
    gl_render._gpu_state["ok"] = None


def test_gpu_then_cpu_fallback_is_cached(tmp_path, fake_runner, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "gpu_fail")
    host = GlHost(gpu="auto", timeout_s=20)
    res = host.render_fragment_shader("src", tmp_path / "o1", width=32, height=18, times=(0.0, 1.0), extra_times=(0.5,))
    assert res.ok and not res.gpu and res.renderer == "fake llvmpipe"
    assert [f.time for f in res.judge_frames] == [0.0, 1.0] and len(res.frames) == 3
    assert gl_render._gpu_state["ok"] is False and gl_render.gpu_preference("auto") == [False]
    monkeypatch.setenv("FAKE_MODE", "ok")
    res2 = host.run_program(fake_runner, tmp_path / "o2", width=32, height=18, times=(0.0,))
    assert res2.ok and not res2.gpu  # cached decision: no GPU retry
    assert (tmp_path / "o2" / gl_render.JOB_NAME).is_file() and json.loads((tmp_path / "o2" / gl_render.JOB_NAME).read_text())["mode"] == "program"

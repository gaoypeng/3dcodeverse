"""Offline: GlHost subprocess driving with a fake runner (GPU → CPU fallback, timeouts, crashes, gif/sheet)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from codeverse3d.spatial import gl_render
from codeverse3d.spatial.gl_render import (
    GlFrame,
    GlHost,
    GlHostError,
    gif_times,
    write_contact_sheet,
    write_gif,
)

FAKE_RUNNER = r'''
import json, os, sys, time
from pathlib import Path
job = json.loads(Path(sys.argv[sys.argv.index("--job") + 1]).read_text())
out = Path(job["out_dir"]); out.mkdir(parents=True, exist_ok=True)
mode = os.environ.get("FAKE_MODE", "ok")
if os.environ.get("GALLIUM_DRIVER") == "d3d12" and mode in ("gpu_fail", "ok_cpu_only"):
    (out / "gl_result.json").write_text(json.dumps({"ok": False, "stage": "context", "error_type": "Error", "error_message": "no d3d12"}))
    sys.exit(3)
if mode == "crash":
    sys.exit(139)
if mode == "hang":
    time.sleep(30)
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


def test_gpu_on_failure_raises_and_crash_timeout_are_typed(tmp_path, fake_runner, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "gpu_fail")
    with pytest.raises(GlHostError):
        GlHost(gpu="on", timeout_s=20).render_fragment_shader("src", tmp_path / "o", width=8, height=8, times=(0.0,))
    monkeypatch.setenv("FAKE_MODE", "crash")
    res = GlHost(gpu="off", timeout_s=20).render_fragment_shader("src", tmp_path / "c", width=8, height=8, times=(0.0,))
    assert not res.ok and res.error_type == "RunnerCrash" and res.stage == "crash"
    monkeypatch.setenv("FAKE_MODE", "hang")
    res = GlHost(gpu="off", timeout_s=1.5).render_fragment_shader("src", tmp_path / "h", width=8, height=8, times=(0.0,))
    assert not res.ok and res.timed_out and res.error_type == "RenderTimeout"


def test_sheet_gif_and_gif_times(tmp_path):
    frames = []
    for i, t in enumerate((0.0, 1.0, 2.5)):
        p = tmp_path / f"f{i}.png"
        Image.fromarray(np.full((18, 32, 3), 60 * i, dtype=np.uint8), "RGB").save(p)
        frames.append(GlFrame(index=i, time=t, path=str(p)))
    sheet = write_contact_sheet(frames, tmp_path / "sheet.png", cols=2, tile_w=64)
    im = Image.open(sheet)
    assert im.width > 128 and im.height > 2 * 36  # 2 columns × 2 rows of 64×36 tiles + labels
    gif = write_gif(frames, tmp_path / "p.gif", width=32)
    assert gif is not None and Image.open(gif).n_frames == 3
    assert write_gif(frames[:1], tmp_path / "one.gif") is None
    assert gif_times(8.0, 4) == [0.0, 2.0, 4.0, 6.0] and len(gif_times(0.1, 12)) == 12
    with pytest.raises(ValueError):
        write_contact_sheet([], tmp_path / "x.png")
    assert Path(sheet).is_file()

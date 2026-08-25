"""CQ-2: run_asset_stage fans assets out over a thread pool, so _checker_path runs
concurrently.  Every thread wrote the same '<cache>/scene_asset_check.mjs.tmp' and the
loser's replace() raised FileNotFoundError — swallowed by the broad `except Exception`
in check_threejs_asset into AssetCheck(ok=True, ran=False).  The losing asset was then
reported as PASSING while the min_y sink, the ASSET_MAX_TRIS budget and the
single-low-poly-box detector were never computed, and size_m stayed None.

Offline: _checker_path only writes the script, it never launches node.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from codeverse.tracks.scene_asset_gen import _CHECK_JS, _checker_path


def _ctx(cache: Path) -> SimpleNamespace:
    """_checker_path reads exactly one thing off the context."""
    return SimpleNamespace(settings=SimpleNamespace(cache_dir=str(cache)))


def test_concurrent_asset_checks_all_get_the_script(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path / "cache")
    n = 8
    bar = threading.Barrier(n)

    def one() -> Path:
        bar.wait()  # every thread hits the write at once, as the asset fan-out does
        return _checker_path(ctx)

    with ThreadPoolExecutor(n) as ex:
        paths = [f.result() for f in [ex.submit(one) for _ in range(n)]]

    assert len({str(p) for p in paths}) == 1
    assert paths[0].read_text() == _CHECK_JS
    assert [p.name for p in paths[0].parent.iterdir()] == ["scene_asset_check.mjs"]


def test_the_checker_is_rewritten_when_it_is_stale_and_reused_when_it_is_not(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path / "cache")
    p = _checker_path(ctx)
    p.write_text("// stale\n")
    assert _checker_path(ctx).read_text() == _CHECK_JS
    mtime = p.stat().st_mtime_ns
    assert _checker_path(ctx).stat().st_mtime_ns == mtime  # unchanged → not rewritten

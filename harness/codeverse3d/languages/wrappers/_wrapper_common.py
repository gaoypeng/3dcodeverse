"""Shared helpers for the python build wrappers (run_bpy, run_bpy_links, run_cq).

Standalone: stdlib only, no 3.12+ syntax, never imported by the harness.  The wrapper's
directory is on ``sys.path`` for the import and removed before the agent's code runs."""

from __future__ import annotations

import importlib
import json
import os
import random
import runpy
import sys
import time
import traceback
from collections.abc import Callable
from typing import Any

WRAPPER_DIR = os.path.dirname(os.path.realpath(__file__))
PARTS_PKG = "parts"


def wrapper_argv() -> list[str]:
    return sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]


def new_report(rlimit_gb: float, **extra: Any) -> dict[str, Any]:
    """The build.json skeleton; applies the memory cap first (RLIMIT_AS)."""
    _apply_rlimit(rlimit_gb)
    return {"ok": False, "error_type": "", "error_message": "", "error_file": "",
            "error_line": None, "error_source": "", "traceback": "", "warnings": [], "exported": {}, **extra}


def _apply_rlimit(gb: float) -> None:
    if gb <= 0:
        return
    try:
        import resource

        cap = int(gb * 1024**3)
        _soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        lim = cap if hard == resource.RLIM_INFINITY else min(cap, hard)
        resource.setrlimit(resource.RLIMIT_AS, (lim, hard))
    except (ImportError, ValueError, OSError):
        pass


def seed_everything(seed: int) -> None:
    random.seed(seed)
    try:
        import numpy as np

        np.random.seed(seed)
    except ImportError:
        pass
    try:
        from mathutils import noise

        noise.seed_set(seed)
    except (ImportError, AttributeError):
        pass


def src_relative(path: str, src_dir: str) -> str | None:
    """``/ws/src/parts/leg.py`` -> ``src/parts/leg.py`` when under ``src_dir``; else None."""
    real_src = os.path.realpath(src_dir)
    real = os.path.realpath(path)
    if real == real_src or not real.startswith(real_src + os.sep):
        return None
    return os.path.join(os.path.basename(real_src), os.path.relpath(real, real_src)).replace(os.sep, "/")


def map_exception(exc: BaseException, script_path: str, *, hint: str = "", memory: str = "") -> dict[str, Any]:
    """Traceback -> error fields; the innermost frame under ``src/`` wins."""
    src_dir = os.path.dirname(os.path.abspath(script_path))
    info: dict[str, Any] = {"error_type": type(exc).__name__, "error_message": (str(exc) or type(exc).__name__) + hint,
                            "error_file": "", "error_line": None, "error_source": ""}
    syntax_rel = src_relative(exc.filename, src_dir) if isinstance(exc, SyntaxError) and exc.filename else None
    if syntax_rel:
        info.update(error_file=syntax_rel, error_line=exc.lineno, error_source=(exc.text or "").strip())
    else:
        for frame in reversed(traceback.extract_tb(exc.__traceback__)):
            rel = src_relative(frame.filename, src_dir)
            if rel:
                info.update(error_file=rel, error_line=frame.lineno, error_source=(frame.line or "").strip())
                break
    if isinstance(exc, MemoryError):
        info["error_message"] = f"MemoryError: the script hit the memory cap — {memory or 'reduce it'}"
    info["traceback"] = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-6000:]
    return info


def run_script(script_path: str) -> tuple[BaseException | None, dict[str, Any] | None]:
    """Run the agent's entry script as ``__main__`` -> (exception, globals); ``sys.exit(0)`` gives ``(None, None)``."""
    src_dir = os.path.dirname(os.path.abspath(script_path))
    sys.path[:] = [p for p in sys.path if os.path.realpath(p) not in (os.path.realpath(src_dir), WRAPPER_DIR)]
    sys.path.insert(0, src_dir)
    sys.dont_write_bytecode = True
    for name in [m for m in sys.modules if m == PARTS_PKG or m.startswith(PARTS_PKG + ".")]:
        del sys.modules[name]
    importlib.invalidate_caches()
    saved_argv = sys.argv
    sys.argv = [script_path]
    try:
        return None, runpy.run_path(script_path, run_name="__main__")
    except SystemExit as e:
        return (None, None) if e.code in (None, 0) else (e, None)
    except BaseException as e:  # noqa: BLE001
        return e, None
    finally:
        sys.argv = saved_argv


def write_report(out_dir: str, report: dict[str, Any], census: dict[str, Any], t0: float, *,
                 names: tuple[str, str] = ("build.json", "census.json")) -> None:
    """Write census.json + build.json atomically; a failure's traceback also goes to stderr."""
    report["duration_ms"] = int((time.monotonic() - t0) * 1000)
    _write_json_atomic(os.path.join(out_dir, names[1]), census)
    _write_json_atomic(os.path.join(out_dir, names[0]), report)
    if report.get("traceback"):
        print(report["traceback"], file=sys.stderr, end="")


def _write_json_atomic(path: str, data: Any) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, default=str)
    os.replace(tmp, path)


def exit_after(main: Callable[[], int]) -> None:
    """0 once the report is written (even when the script failed); 2 on a wrapper bug."""
    try:
        code = main()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        code = 2
    sys.exit(code)

"""What every python build wrapper does around the agent's script — run_bpy.py and
run_bpy_links.py inside Blender (its bundled python 3.11), run_cq.py in the harness python.

Standalone: stdlib only and no 3.12+ syntax, never imported by the harness.  A wrapper puts
its own directory on ``sys.path`` and imports this module as a sibling; ``run_script`` takes
that directory off again before the agent's code runs.

The report a wrapper writes is ``build.json`` — the runtime reads it once and replaces it
with the final ``BuildResult`` (``languages/_common.compose_build_result``).
"""

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
PARTS_PKG = "parts"  # src/parts/<snake>.py → ``from parts.<snake> import build_<snake>``


def wrapper_argv() -> list[str]:
    """The wrapper's own arguments: after ``--`` under Blender, else ``sys.argv[1:]``."""
    return sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]


def new_report(rlimit_gb: float, **extra: Any) -> dict[str, Any]:
    """The build.json skeleton; applies the memory cap first (RLIMIT_AS, so a geometry bomb
    dies in-process)."""
    _apply_rlimit(rlimit_gb)
    return {"ok": False, "error_type": "", "error_message": "", "error_file": "",
            "error_line": None, "error_source": "", "traceback": "", "warnings": [], "exported": {}, **extra}


def _apply_rlimit(gb: float) -> None:
    """Best effort: where RLIMIT_AS cannot be set the build runs uncapped."""
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
    """``random``, numpy and ``mathutils.noise`` (whichever this interpreter has)."""
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
    """``/ws/src/parts/leg.py`` → ``src/parts/leg.py`` when ``path`` lives under ``src_dir``; else None.

    Every runtime reports ``error_file`` workspace-relative so the repair loop can open it."""
    real_src = os.path.realpath(src_dir)
    real = os.path.realpath(path)
    if real == real_src or not real.startswith(real_src + os.sep):
        return None
    return os.path.join(os.path.basename(real_src), os.path.relpath(real, real_src)).replace(os.sep, "/")


def map_exception(exc: BaseException, script_path: str, *, hint: str = "", memory: str = "") -> dict[str, Any]:
    """Traceback → {error_type, error_message, error_file, error_line, error_source, traceback}.

    The innermost frame under ``src/`` wins, so a failure in a part or helper file is
    reported against that file, not the ``model.py`` line that called it.  ``hint`` is
    appended to the message; ``memory`` replaces it on a MemoryError (what to reduce)."""
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
    """Execute the agent's entry script as ``__main__`` → (what it raised, its globals).

    ``<ws>/src`` goes first on ``sys.path`` (``from parts.leg import build_leg``, a helper
    module beside model.py); the wrapper's own directory goes off it, no bytecode is
    written into the git-tracked src/.  ``sys.exit(0)`` is not a failure: it returns
    ``(None, None)`` — no globals to read."""
    src_dir = os.path.dirname(os.path.abspath(script_path))
    sys.path[:] = [p for p in sys.path if os.path.realpath(p) not in (os.path.realpath(src_dir), WRAPPER_DIR)]
    sys.path.insert(0, src_dir)
    sys.dont_write_bytecode = True
    for name in [m for m in sys.modules if m == PARTS_PKG or m.startswith(PARTS_PKG + ".")]:
        del sys.modules[name]  # never reuse a stale module from somewhere else on the path
    importlib.invalidate_caches()
    saved_argv = sys.argv
    sys.argv = [script_path]
    try:
        return None, runpy.run_path(script_path, run_name="__main__")
    except SystemExit as e:
        return (None, None) if e.code in (None, 0) else (e, None)
    except BaseException as e:  # noqa: BLE001 — every failure must be reported, never propagate
        return e, None
    finally:
        sys.argv = saved_argv


def write_report(out_dir: str, report: dict[str, Any], census: dict[str, Any], t0: float, *,
                 names: tuple[str, str] = ("build.json", "census.json")) -> None:
    """census.json + build.json (``names``: the report's and the census's file names), atomically;
    a failure's traceback also goes to stderr, where the runtime's ``stderr_tail`` (and so the
    repair prompt) picks it up."""
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
    """``sys.exit(main())``: 0 once the report is written (even when the script failed),
    2 on a wrapper bug — traceback on stderr, no report."""
    try:
        code = main()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        code = 2
    sys.exit(code)

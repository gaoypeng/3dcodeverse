"""Executors: run one generated program of a dialect and report what it produced.

Every executor has the signature ``run(code: str, workdir: str, timeout: int | None = None) -> dict`` and
returns a JSON-serialisable report with a **uniform status vocabulary**:

    OK        ran, produced non-empty output (mesh / compiled shader / non-blank canvas)
    EMPTY     ran without error but produced nothing usable (no mesh, blank canvas)
    FAIL      the program raised / failed to compile / JS error
    CRASH     the host (Blender, node, browser) died without a report
    TIMEOUT   exceeded the per-dialect wall-clock limit

plus ``error`` (str | None), ``latency_s`` (float) and, where applicable, ``mesh`` (path to GLB/STL or None),
``render`` (for glsl: OK/STATIC/FAIL) and ``shot`` (screenshot path).

Per-dialect wall-clock limits (seconds) — identical for every suite of that dialect, unlike the old code
which used 300 for 3DCodeBench and 180 for the Blender held-out set.
"""
from __future__ import annotations

from . import blender, cadquery, glsl, openscad, threejs

TIMEOUTS = {"blender": 300, "cadquery": 90, "openscad": 120, "glsl": 60, "threejs": 60}
CODE_EXT = {"blender": "py", "cadquery": "py", "openscad": "scad", "glsl": "frag", "threejs": "html"}
RUNNERS = {
    "blender": blender.run,
    "cadquery": cadquery.run,
    "openscad": openscad.run,
    "glsl": glsl.run,
    "threejs": threejs.run,
}

STATUSES = ("OK", "EMPTY", "FAIL", "CRASH", "TIMEOUT", "MISSING")


def run(dialect: str, code: str, workdir: str, timeout: int | None = None) -> dict:
    return RUNNERS[dialect](code, workdir, timeout or TIMEOUTS[dialect])

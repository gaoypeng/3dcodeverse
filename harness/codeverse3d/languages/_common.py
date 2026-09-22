"""Shared helpers for python-language runtimes (blender, cadquery, urdf).

Runtimes execute a *wrapper script* in a subprocess (Blender's python or the
harness python).  The wrapper writes ``artifacts/build.json`` (+ ``census.json``)
and the runtime turns those into a :class:`BuildResult`.  This module owns:

* :func:`compose_build_result` — wrapper json + process outcome → BuildResult,
  failing loud when the wrapper did not report, published as ``build.json``;
* :func:`read_json_file` / :func:`strip_blender_noise`.

Wrappers themselves are standalone scripts in ``languages/wrappers/`` (they never
import ``codeverse3d``; Blender's bundled python cannot see this package).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from codeverse3d.contracts.artifacts import BuildResult
from codeverse3d.proc import ProcResult, tail, write_json_atomic

#: The one spelling of every runtime's typed build failures — recorded in build.json /
#: record.json / bench cells, so the strings stay as first recorded (2026-08-29: threejs,
#: urdf said "Timeout" and the GL runtimes "MissingEntry"; nothing keyed on either).
MISSING_ENTRY = "MissingEntryFile"
BUILD_TIMEOUT = "BuildTimeout"

# lines Blender prints on every headless run that carry no signal for the agent
_BLENDER_NOISE_PREFIXES = (
    "Blender ",
    "Read prefs",
    "Color management",
    "Warning: Falling back",
    "ALSA lib",
    "libEGL",
    "MESA",
    "glx:",
    "Xlib:",
    "Unable to open a display",
    "Blender quit",
    "Saved session recovery",
    "Info: ",
    "INFO Draco",
    "Timer '",
)
_BLENDER_NOISE_SUBSTR = ("| INFO: ", "| WARNING: Draco")


class WrapperError(RuntimeError):
    """The wrapper process did not produce a readable ``build.json`` (harness bug or crash)."""


def strip_blender_noise(text: str) -> str:
    """Drop Blender's boilerplate stderr/stdout lines so the agent sees signal only."""
    keep = [
        ln for ln in text.splitlines()
        if ln.strip() and not ln.startswith(_BLENDER_NOISE_PREFIXES) and not any(s in ln for s in _BLENDER_NOISE_SUBSTR)
    ]
    return "\n".join(keep)


def read_json_file(path: Path) -> dict[str, Any]:
    """Parse a JSON object file; raise :class:`WrapperError` if missing/invalid."""
    if not path.is_file():
        raise WrapperError(f"expected wrapper output missing: {path}")
    try:
        data = json.loads(path.read_text())
    except ValueError as e:
        raise WrapperError(f"invalid JSON in {path}: {e}") from e
    if not isinstance(data, dict):
        raise WrapperError(f"{path}: expected a JSON object")
    return data


def compose_build_result(
    *,
    language: str,
    proc: ProcResult,
    build_json: Path,
    census_json: Path,
    glb_path: Path | None,
    extra_paths: Mapping[str, Path],
    output_filter: Callable[[str], str] | None = None,
) -> BuildResult:
    """Merge the wrapper's ``build.json`` with the process outcome, and write the result
    over it: ``build.json`` is always the BuildResult, the one status file the tools read.

    Contract: success requires the wrapper to say ``ok`` AND — for a build that has one
    (``glb_path``) — a non-empty GLB on disk.  Timeouts / crashes before ``build.json``
    exists become a failed BuildResult with a typed error (``BUILD_TIMEOUT`` /
    ``WrapperCrash``) — never an exception — so the orchestrator can route them to repair.
    The wrapper's own diagnostics (traceback, warnings, exports …) land in
    ``census["build_report"]``.
    """
    stdout_tail = tail(output_filter(proc.stdout) if output_filter else proc.stdout)
    stderr_tail = tail(output_filter(proc.stderr) if output_filter else proc.stderr)
    base = dict(language=language, stdout_tail=stdout_tail, stderr_tail=stderr_tail, duration_ms=proc.duration_ms)
    if proc.timed_out:
        res = BuildResult(
            ok=False,
            error_type=BUILD_TIMEOUT,
            error_message=f"build exceeded the time limit ({proc.duration_ms // 1000}s); "
            "reduce geometry (subdivisions, array counts, boolean ops) so the script finishes quickly",
            **base,
        )
    elif not build_json.is_file():
        res = BuildResult(
            ok=False,
            error_type="WrapperCrash",
            error_message=(
                f"the build wrapper exited with code {proc.returncode} before reporting; "
                "see stderr_tail (memory cap hit / interpreter crash / missing dependency)"
            ),
            **base,
        )
    else:
        res = _merge_report(read_json_file(build_json), census_json, glb_path, extra_paths, base)
    write_json_atomic(build_json, res.model_dump(mode="json"))
    return res


def _merge_report(data: dict[str, Any], census_json: Path, glb_path: Path | None,
                  extra_paths: Mapping[str, Path], base: dict[str, Any]) -> BuildResult:
    census: dict[str, Any] = {}
    if census_json.is_file():
        try:
            census = read_json_file(census_json)
        except WrapperError:
            census = {}
    # keep the wrapper's extra diagnostics (traceback, warnings …) next to the census
    census["build_report"] = {k: data[k] for k in ("traceback", "error_source", "warnings", "exported", "exec_ms") if k in data}
    glb_ok = glb_path is None or (glb_path.is_file() and glb_path.stat().st_size > 0)
    ok = bool(data.get("ok")) and glb_ok
    error_type = str(data.get("error_type") or "")
    error_message = str(data.get("error_message") or "")
    if data.get("ok") and not glb_ok:
        error_type = error_type or "ExportEmpty"
        error_message = error_message or "the script ran but no mesh geometry was exported (object.glb empty)"
    extras = {k: str(p) for k, p in extra_paths.items() if p.is_file() and p.stat().st_size > 0}
    line = data.get("error_line")
    return BuildResult(
        ok=ok,
        glb_path=str(glb_path) if glb_path is not None and glb_ok else None,
        extra_paths=extras,
        error_type=error_type,
        error_message=error_message,
        error_file=str(data.get("error_file") or ""),
        error_line=int(line) if isinstance(line, int) else None,
        census=census,
        **{**base, "duration_ms": int(data.get("duration_ms") or base["duration_ms"])},
    )

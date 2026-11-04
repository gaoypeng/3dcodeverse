"""Shared helpers for the language runtimes: the python wrapper build (blender, cadquery, urdf),
finding locations and the typed failure names every lint uses.

Wrappers are standalone scripts in ``languages/wrappers/``; they never import ``codeverse3d``.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from codeverse3d.contracts.artifacts import BuildResult
from codeverse3d.contracts.common import ENTRY_FILE, Language
from codeverse3d.proc import ProcResult, read_json_or_none, run_subprocess, tail, write_json_atomic
from codeverse3d.workspace import Workspace

#: The one spelling of every runtime's typed build failures (recorded in build.json / record.json).
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


def line_of(text: str, pos: int) -> int:
    """1-based line of character offset ``pos``."""
    return text.count("\n", 0, pos) + 1


def target_file(target: str | None) -> str:
    """``src/zones/a.js:12`` → ``src/zones/a.js`` (a finding's target without its line)."""
    if not target:
        return ""
    head, sep, tail = target.rpartition(":")
    return head if sep and tail.isdigit() else target


def target_line(target: str | None) -> int | None:
    """``src/zones/a.js:12`` → ``12``; ``None`` when the target names no line."""
    if not target:
        return None
    _, sep, tail = target.rpartition(":")
    return int(tail) if sep and tail.isdigit() else None


def ws_rel(ws: Workspace, p: Path) -> str:
    """``p`` workspace-relative (posix), or as given when it lies outside the workspace."""
    try:
        return p.relative_to(ws.root).as_posix()
    except ValueError:
        return str(p)


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


def missing_entry(ws: Workspace, language: Language, *, write: bool = True) -> BuildResult | None:
    """Typed ``MISSING_ENTRY`` failure (published as ``build.json`` unless ``write`` is off) when the entry file is absent, else ``None``."""
    entry = ENTRY_FILE[language]
    if (ws.root / entry).is_file():
        return None
    result = BuildResult(ok=False, language=language.value, error_type=MISSING_ENTRY,
                         error_message=f"{entry} does not exist", error_file=entry)
    if write:
        ws.write_json(ws.artifacts / "build.json", result)
    return result


def run_wrapper_build(ws: Workspace, *, language: Language, extras: Mapping[str, str],
                      argv: Callable[[], list[str]], env: dict[str, str], timeout_s: float,
                      output_filter: Callable[[str], str] | None = None) -> BuildResult:
    """Run a python wrapper build; ``extras`` maps a ``BuildResult.extra_paths`` key to its artifact file name.

    Outputs are invalidated before the missing-entry return so a stale glb never looks current."""
    ws.artifacts.mkdir(parents=True, exist_ok=True)
    build_json = ws.artifacts / "build.json"
    ws.stage_artifacts("build.json", "census.json", "object.glb", *extras.values()).invalidate()
    if (missing := missing_entry(ws, language)) is not None:
        return missing
    proc = run_subprocess(argv(), cwd=ws.root, env=env, timeout_s=timeout_s)
    return compose_build_result(language=language.value, proc=proc, build_json=build_json, census_json=ws.artifacts / "census.json",
                                glb_path=ws.artifacts / "object.glb", extra_paths={k: ws.artifacts / v for k, v in extras.items()},
                                output_filter=output_filter)


def compose_build_result(
    *,
    language: str,
    proc: ProcResult,
    build_json: Path,
    census_json: Path,
    glb_path: Path | None,
    extra_paths: Mapping[str, Path],
    output_filter: Callable[[str], str] | None = None,
    publish: bool = True,
) -> BuildResult:
    """Merge the wrapper's ``build.json`` with the process outcome and write the result over it.

    Success requires wrapper ``ok`` and, when ``glb_path`` is set, a non-empty GLB.  Timeouts and
    crashes become a failed BuildResult.  ``publish=False`` leaves ``build_json`` as the wrapper wrote it."""
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
    if publish:
        write_json_atomic(build_json, res.model_dump(mode="json"))
    return res


def _merge_report(data: dict[str, Any], census_json: Path, glb_path: Path | None,
                  extra_paths: Mapping[str, Path], base: dict[str, Any]) -> BuildResult:
    census = read_json_or_none(census_json) or {}
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

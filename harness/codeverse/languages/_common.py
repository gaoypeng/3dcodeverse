"""Shared helpers for python-language runtimes (blender, cadquery, urdf).

Runtimes execute a *wrapper script* in a subprocess (Blender's python or the
harness python).  The wrapper writes ``artifacts/build.json`` (+ ``census.json``)
and the runtime turns those into a :class:`BuildResult`.  This module owns:

* :func:`run_subprocess` — timeout, process-group kill, captured output;
* :func:`tail` — bounded log tails for BuildResult;
* :func:`read_json_file` / :func:`write_json_atomic`;
* :func:`compose_build_result` — wrapper json + process outcome → BuildResult,
  failing loud when the wrapper did not report.

Wrappers themselves are standalone scripts (they never import ``codeverse``;
Blender's bundled python cannot see this package).
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from codeverse.contracts.artifacts import BuildResult

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


@dataclass(frozen=True)
class ProcResult:
    """Outcome of one subprocess run."""

    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    duration_ms: int


def run_subprocess(
    cmd: list[str],
    *,
    cwd: Path | str,
    timeout_s: float,
    env: Mapping[str, str] | None = None,
    stdin_text: str | None = None,
) -> ProcResult:
    """Run ``cmd`` in its own process group; kill the whole group on timeout.

    Never raises on non-zero exit — callers inspect :attr:`ProcResult.returncode`.
    """
    t0 = time.monotonic()
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env=dict(env) if env is not None else None,
        stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    timed_out = False
    try:
        out, err = proc.communicate(input=stdin_text, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_group(proc)
        out, err = proc.communicate()
    return ProcResult(
        returncode=proc.returncode if proc.returncode is not None else -1,
        stdout=out or "",
        stderr=err or "",
        timed_out=timed_out,
        duration_ms=int((time.monotonic() - t0) * 1000),
    )


def _kill_group(proc: subprocess.Popen[str]) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        proc.kill()


def tail(text: str, *, max_lines: int = 40, max_chars: int = 4000) -> str:
    """Last ``max_lines`` lines of ``text``, capped at ``max_chars`` characters."""
    lines = text.splitlines()[-max_lines:]
    s = "\n".join(lines)
    return s[-max_chars:] if len(s) > max_chars else s


def strip_blender_noise(text: str) -> str:
    """Drop Blender's boilerplate stderr/stdout lines so the agent sees signal only."""
    keep = [
        ln for ln in text.splitlines()
        if ln.strip() and not ln.startswith(_BLENDER_NOISE_PREFIXES) and not any(s in ln for s in _BLENDER_NOISE_SUBSTR)
    ]
    return "\n".join(keep)


def write_json_atomic(path: Path, data: Any) -> None:
    """tmp + rename so readers never see a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str))
    tmp.replace(path)


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


def remove_stale(*paths: Path) -> None:
    """Delete previous wrapper outputs so a crashed run cannot be mistaken for a fresh one."""
    for p in paths:
        p.unlink(missing_ok=True)


def compose_build_result(
    *,
    language: str,
    proc: ProcResult,
    build_json: Path,
    census_json: Path,
    glb_path: Path,
    extra_paths: Mapping[str, Path],
    output_filter: Callable[[str], str] | None = None,
) -> BuildResult:
    """Merge the wrapper's ``build.json`` with the process outcome.

    Contract: success requires the wrapper to say ``ok`` AND a non-empty GLB on
    disk.  Timeouts / crashes before ``build.json`` exists become a failed
    BuildResult with a typed error (``BuildTimeout`` / ``WrapperCrash``) — never
    an exception — so the orchestrator can route them to repair.
    """
    stdout_tail = tail(output_filter(proc.stdout) if output_filter else proc.stdout)
    stderr_tail = tail(output_filter(proc.stderr) if output_filter else proc.stderr)
    base = dict(language=language, stdout_tail=stdout_tail, stderr_tail=stderr_tail, duration_ms=proc.duration_ms)

    if proc.timed_out:
        return BuildResult(
            ok=False,
            error_type="BuildTimeout",
            error_message=f"build exceeded the time limit ({proc.duration_ms // 1000}s); "
            "reduce geometry (subdivisions, array counts, boolean ops) so the script finishes quickly",
            **base,
        )
    if not build_json.is_file():
        return BuildResult(
            ok=False,
            error_type="WrapperCrash",
            error_message=(
                f"the build wrapper exited with code {proc.returncode} before reporting; "
                "see stderr_tail (memory cap hit / interpreter crash / missing dependency)"
            ),
            **base,
        )
    data = read_json_file(build_json)
    census: dict[str, Any] = {}
    if census_json.is_file():
        try:
            census = read_json_file(census_json)
        except WrapperError:
            census = {}
    # keep the wrapper's extra diagnostics (traceback, warnings …) next to the census
    census["build_report"] = {k: data[k] for k in ("traceback", "error_source", "warnings", "exported", "exec_ms") if k in data}
    glb_ok = glb_path.is_file() and glb_path.stat().st_size > 0
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
        glb_path=str(glb_path) if glb_ok else None,
        extra_paths=extras,
        error_type=error_type,
        error_message=error_message,
        error_file=str(data.get("error_file") or ""),
        error_line=int(line) if isinstance(line, int) else None,
        census=census,
        **{**base, "duration_ms": int(data.get("duration_ms") or proc.duration_ms)},
    )

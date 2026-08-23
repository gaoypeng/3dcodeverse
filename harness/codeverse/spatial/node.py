"""Run harness-owned node scripts (``runtime_js/*.mjs``) as subprocesses.

All node work in the harness goes through :func:`run_node`: it sets
``NODE_PATH`` to ``runtime_js/node_modules`` (CommonJS resolution), optionally
adds the ESM ``--import`` hook that lets agent code outside ``runtime_js``
import ``three`` / ``three/addons/*``, enforces a timeout, kills the whole
process group on expiry and parses the *last JSON line* of stdout (the
convention every runtime_js script follows).
"""

from __future__ import annotations

import contextlib
import json
import os
import resource
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from codeverse.config import get_settings

TAIL_CHARS = 4000


class NodeError(RuntimeError):
    """A node script failed (non-zero exit, timeout or missing binary).

    ``result`` carries whatever was captured so callers can turn it into a
    ``BuildResult`` / ``GateReport`` without losing the tail.
    """

    def __init__(self, message: str, result: NodeResult | None = None):
        super().__init__(message)
        self.result = result


@dataclass
class NodeResult:
    rc: int
    stdout: str
    stderr: str
    last_json: dict[str, Any] | None
    duration_ms: int
    timed_out: bool = False
    cmd: list[str] = field(default_factory=list)

    @property
    def stdout_tail(self) -> str:
        return self.stdout[-TAIL_CHARS:]

    @property
    def stderr_tail(self) -> str:
        return self.stderr[-TAIL_CHARS:]


def runtime_js_dir() -> Path:
    return get_settings().runtime_js_dir()


def node_modules_dir() -> Path:
    return runtime_js_dir() / "node_modules"


def three_import_hook() -> Path:
    """The ``--import`` hook that redirects bare ``three`` specifiers to runtime_js."""
    return runtime_js_dir() / "lib" / "resolve_three.mjs"


def parse_last_json(stdout: str) -> dict[str, Any] | None:
    """Return the last line of ``stdout`` that parses as a JSON object, else None."""
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _limit_memory(limit_gb: float | None):
    if not limit_gb:
        return None
    limit = int(limit_gb * 1024**3)

    def _pre() -> None:
        with contextlib.suppress(ValueError, OSError):
            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))

    return _pre


def run_node(
    script: Path | str,
    args: list[str] | None = None,
    *,
    cwd: Path | str | None = None,
    timeout_s: float = 300.0,
    env_extra: dict[str, str] | None = None,
    node_args: list[str] | None = None,
    three_hook: bool = False,
    mem_limit_gb: float | None = None,
    check: bool = True,
) -> NodeResult:
    """Run ``node [node_args] script [args]`` and return a :class:`NodeResult`.

    * ``three_hook=True`` adds ``--import runtime_js/lib/resolve_three.mjs`` so the
      script (and anything it imports, e.g. agent code) can ``import 'three'``.
    * On timeout the process group is killed and ``NodeError`` is raised.
    * With ``check=True`` (default) a non-zero exit raises ``NodeError`` whose
      ``.result`` has the tails and ``last_json`` (a structured error record when
      the script emitted one).
    """
    settings = get_settings()
    node_bin = settings.binaries.node
    script = Path(script)
    if not script.is_file():
        raise NodeError(f"node script not found: {script}")
    cmd = [node_bin, *(node_args or [])]
    if three_hook:
        cmd += ["--import", str(three_import_hook())]
    cmd += [str(script), *(args or [])]

    env = dict(os.environ)
    env["NODE_PATH"] = str(node_modules_dir())
    env.setdefault("CV3D_CACHE_DIR", str(settings.cache_dir))
    env.setdefault("NODE_OPTIONS", "")
    env["NODE_NO_WARNINGS"] = "1"
    if env_extra:
        env.update(env_extra)

    t0 = time.time()
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd) if cwd else None,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
            preexec_fn=_limit_memory(mem_limit_gb),
        )
    except FileNotFoundError as e:
        raise NodeError(f"node binary not found ({node_bin}): {e}") from e

    timed_out = False
    try:
        out, err = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_group(proc)
        out, err = proc.communicate()
    duration_ms = int((time.time() - t0) * 1000)
    result = NodeResult(
        rc=proc.returncode if not timed_out else -9,
        stdout=out or "",
        stderr=err or "",
        last_json=parse_last_json(out or ""),
        duration_ms=duration_ms,
        timed_out=timed_out,
        cmd=cmd,
    )
    if timed_out:
        raise NodeError(f"node script {script.name} timed out after {timeout_s:.0f}s\n{result.stderr_tail}", result)
    if check and result.rc != 0:
        msg = f"node script {script.name} exited {result.rc}"
        if result.last_json and isinstance(result.last_json.get("error"), (dict, str)):
            e = result.last_json["error"]
            msg += ": " + (e.get("message", "") if isinstance(e, dict) else str(e))
        raise NodeError(msg + "\n" + result.stderr_tail, result)
    return result


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError:
        proc.kill()

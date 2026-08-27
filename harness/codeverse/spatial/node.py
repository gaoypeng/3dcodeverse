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
import re
import resource
import subprocess
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from codeverse.config import get_settings
from codeverse.proc import run_subprocess, scrub_secrets

TAIL_CHARS = 4000

#: Oldest node the harness runs on — the single source of truth for the floor.
#: 20.6.0 is what ``node --import`` needs (``run_node(three_hook=True)``, the only
#: way agent code outside runtime_js can ``import 'three'``); everything else the
#: harness and ``runtime_js`` use is older (``node:util.parseArgs`` 18.3), and the
#: npm dependencies bottom out at node 18.  ``runtime_js/package.json``'s
#: ``engines.node`` restates this for npm and is pinned to it by
#: ``tests/core/test_portability.py``.
NODE_MIN: tuple[int, int, int] = (20, 6, 0)
NODE_MIN_STR = ".".join(str(n) for n in NODE_MIN)

_VERSION_RE = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")


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


def parse_node_version(text: str) -> tuple[int, int, int] | None:
    """``"v20.6.1\\n"`` -> ``(20, 6, 1)``; None when the output is not a version."""
    m = _VERSION_RE.search(text.strip())
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


@lru_cache(maxsize=8)
def node_version(node_bin: str) -> tuple[int, int, int] | None:
    """``node --version`` for ``node_bin``, or None when it cannot be asked.

    Cached per binary: this runs at most once per interpreter per node path.
    """
    try:
        proc = subprocess.run([node_bin, "--version"], capture_output=True, text=True,
                              timeout=20, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_node_version(proc.stdout or proc.stderr) if proc.returncode == 0 else None


def node_version_error(version: tuple[int, int, int] | None) -> str:
    """The actionable message for a too-old ``version``, or ``""`` when it is fine.

    Unknown versions pass: a node we could not interrogate fails later with its
    own error, and guessing here would break more than it protects.
    """
    if version is None or version >= NODE_MIN:
        return ""
    got = ".".join(str(n) for n in version)
    return (f"node {got} is too old: 3dcodeverse needs node >= {NODE_MIN_STR} "
            f"(`--import` module hooks; see runtime_js/package.json \"engines\").  "
            f"Install a newer one (`nvm install --lts`, or https://nodejs.org) and put it on PATH, "
            f"or point the harness at it with CV3D_BINARIES__NODE=/path/to/node.")


def require_node_version(node_bin: str) -> None:
    """Raise :class:`NodeError` when ``node_bin`` is older than :data:`NODE_MIN`."""
    msg = node_version_error(node_version(node_bin))
    if msg:
        raise NodeError(msg)


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
    require_node_version(node_bin)
    script = Path(script)
    if not script.is_file():
        raise NodeError(f"node script not found: {script}")
    cmd = [node_bin, *(node_args or [])]
    if three_hook:
        cmd += ["--import", str(three_import_hook())]
    cmd += [str(script), *(args or [])]

    # node runs harness drivers AND model-generated code (scene.js, agent modules):
    # credential-shaped vars are stripped; env_extra (harness-owned) is applied after.
    env = scrub_secrets(dict(os.environ))
    env["NODE_PATH"] = str(node_modules_dir())
    env.setdefault("CV3D_CACHE_DIR", str(settings.cache_dir))
    env.setdefault("NODE_OPTIONS", "")
    env["NODE_NO_WARNINGS"] = "1"
    if env_extra:
        env.update(env_extra)

    try:
        proc = run_subprocess(cmd, cwd=cwd or Path.cwd(), timeout_s=timeout_s, env=env,
                              preexec_fn=_limit_memory(mem_limit_gb))
    except FileNotFoundError as e:
        raise NodeError(f"node binary not found ({node_bin}): {e}") from e

    result = NodeResult(
        rc=proc.returncode if not proc.timed_out else -9,
        stdout=proc.stdout,
        stderr=proc.stderr,
        last_json=parse_last_json(proc.stdout),
        duration_ms=proc.duration_ms,
        timed_out=proc.timed_out,
        cmd=cmd,
    )
    if result.timed_out:
        raise NodeError(f"node script {script.name} timed out after {timeout_s:.0f}s\n{result.stderr_tail}", result)
    if check and result.rc != 0:
        msg = f"node script {script.name} exited {result.rc}"
        if result.last_json and isinstance(result.last_json.get("error"), (dict, str)):
            e = result.last_json["error"]
            msg += ": " + (e.get("message", "") if isinstance(e, dict) else str(e))
        raise NodeError(msg + "\n" + result.stderr_tail, result)
    return result

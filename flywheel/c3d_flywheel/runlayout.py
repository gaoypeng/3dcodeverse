"""The v0.0.x run directory as the harness laid it out, read-only.

The plugin's ``codeverse3d.workspace.Workspace`` is a project workspace (``spec.json`` / ``src/`` /
``artifacts/`` / ``judge/``); a RECORDED run adds ``record.json``, ``events.jsonl``, ``run_state.json``,
``deliverable/``, ``telemetry/`` and a git repository of ``src/``.  This module carries those paths,
the sanitised git read helpers and the JSONL readers the flywheel needs; nothing here writes a run
except ``pick`` (``deliverable/`` + ``selection.json``).
"""

from __future__ import annotations

import contextlib
import json
import logging
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from codeverse3d.proc import write_json_atomic
from pydantic import BaseModel

DELIVERABLE_DIR = "deliverable"
TELEMETRY_DIR = "telemetry"

#: prepended to every git argv run against a run directory: an agent-planted hook, fsmonitor or
#: global attributes file never runs.  ``-c`` outranks ``.git/config``.
GIT_SAFE_FLAGS = ("-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
                  "-c", "core.attributesFile=/dev/null")
#: added to every git command that renders file content: a named diff / textconv driver in the
#: agent-writable .git/config is not reachable by ``-c`` and would execute without these.
GIT_SAFE_DIFF_FLAGS = ("--no-ext-diff", "--no-textconv")
#: a has_commit probe that has not answered by then answers no
HAS_COMMIT_TIMEOUT_S = 60


def _git_home() -> str:
    """HOME for git in a run directory: ours, never the run's."""
    d = Path.home() / ".cache" / "c3d_flywheel" / "githome"
    with contextlib.suppress(OSError):
        d.mkdir(parents=True, exist_ok=True)
    return str(d)


def git_safe_env(**extra: str) -> dict[str, str]:
    """Environment for git in an agent-written repository: no system / global config, fixed PATH,
    no credential prompt."""
    return {"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": _git_home(),
            "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0", **extra}


# --------------------------------------------------------------------------- jsonl
def iter_jsonl_lines(path: Path | str) -> Iterator[tuple[int, str]]:
    """``(line_no, line)`` for every non-blank line of a JSONL file, 1-based.

    Missing file → nothing; invalid bytes are replaced so one bad byte costs at most that line."""
    p = Path(path)
    if not p.is_file():
        return
    for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines()):
        if line.strip():
            yield i + 1, line


def read_jsonl_lenient(
    path: Path | str, *, log: logging.Logger | None = None, dicts_only: bool = False
) -> list[Any]:
    """Every parseable JSON line of ``path``; unparseable lines are skipped (debug-logged to ``log``
    when given), ``dicts_only`` also drops non-object rows."""
    out: list[Any] = []
    for i, line in iter_jsonl_lines(path):
        try:
            row = json.loads(line)
        except ValueError as e:
            if log is not None:
                log.debug("%s:%d unreadable: %s", path, i, e)
            continue
        if dicts_only and not isinstance(row, dict):
            continue
        out.append(row)
    return out


# --------------------------------------------------------------------------- the run dir
class RunWorkspace:
    """One recorded run directory."""

    def __init__(self, root: Path | str):
        self.root = Path(root).resolve()

    def rebase(self, stored: str | Path) -> Path:
        """A stored (possibly foreign absolute) path resolved against THIS run directory.

        Relative paths join the root; an absolute path outside it is re-rooted by its longest
        trailing segment that exists here, else returned as-is."""
        p = Path(stored)
        if not p.is_absolute():
            return self.root / p
        if not p.is_relative_to(self.root):
            parts = p.parts
            for i in range(1, len(parts)):
                candidate = self.root.joinpath(*parts[i:])
                if candidate.exists():
                    return candidate
        return p

    # ----------------------------------------------------------------- paths
    @property
    def src(self) -> Path:
        return self.root / "src"

    @property
    def public(self) -> Path:
        return self.root / "public"

    @property
    def artifacts(self) -> Path:
        return self.root / "artifacts"

    @property
    def deliverable(self) -> Path:
        return self.root / DELIVERABLE_DIR

    @property
    def telemetry(self) -> Path:
        return self.root / TELEMETRY_DIR

    @property
    def deliverable_manifest_path(self) -> Path:
        return self.deliverable / "manifest.json"

    def round_artifacts(self, round_index: int) -> Path:
        return self.artifacts / f"r{round_index:02d}"

    @property
    def spec_path(self) -> Path:
        return self.root / "spec.json"

    @property
    def plan_path(self) -> Path:
        return self.root / "plan.json"

    @property
    def state_path(self) -> Path:
        return self.root / "run_state.json"

    @property
    def record_path(self) -> Path:
        return self.root / "record.json"

    @property
    def events_path(self) -> Path:
        return self.root / "events.jsonl"

    # ----------------------------------------------------------------- lifecycle
    def exists(self) -> bool:
        return self.spec_path.is_file()

    def ensure_layout(self) -> None:
        """The directories ``pick`` writes into."""
        self.deliverable.mkdir(parents=True, exist_ok=True)

    def write_json(self, path: Path, model: BaseModel | dict[str, Any]) -> None:
        data = model.model_dump(mode="json") if isinstance(model, BaseModel) else model
        write_json_atomic(path, data)

    # ----------------------------------------------------------------- git (read-only)
    def has_commit(self, commit: str) -> bool:
        if not commit:
            return False
        try:
            proc = subprocess.run(
                ["git", *GIT_SAFE_FLAGS, "cat-file", "-e", f"{commit}^{{commit}}"], cwd=self.root,
                capture_output=True, check=False, env=git_safe_env(), timeout=HAS_COMMIT_TIMEOUT_S,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return proc.returncode == 0

"""Run workspace: fixed directory layout + git snapshots of ``src/``.

A workspace is a directory under ``runs/<slug>/`` (see docs/ARCHITECTURE.md §3).
``src/`` (and ``public/``) is a git repository so every round is a commit:
diffs, rollback and "files changed by the agent" come for free and the
flywheel can replay any round.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from codeverse.contracts.agent import FileChange

_DIRS = ("src", "public", "artifacts", "artifacts/renders", "artifacts/gates", "artifacts/judge", "trajectories")


class Workspace:
    """Paths + snapshot helpers for one run.  Cheap to construct; no I/O until used."""

    def __init__(self, root: Path | str):
        self.root = Path(root).resolve()

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
    def trajectories(self) -> Path:
        return self.root / "trajectories"

    def renders_dir(self, round_index: int) -> Path:
        return self.artifacts / "renders" / f"r{round_index:02d}"

    def gates_dir(self, round_index: int) -> Path:
        return self.artifacts / "gates" / f"r{round_index:02d}"

    def judge_path(self, round_index: int, suffix: str = "") -> Path:
        return self.artifacts / "judge" / f"r{round_index:02d}{suffix}.json"

    def trajectory_dir(self, stage: str, round_index: int) -> Path:
        d = self.trajectories / f"{stage}_r{round_index:02d}"
        d.mkdir(parents=True, exist_ok=True)
        return d

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
    def create(self) -> Workspace:
        for d in _DIRS:
            (self.root / d).mkdir(parents=True, exist_ok=True)
        self._git_init()
        return self

    def exists(self) -> bool:
        return self.spec_path.is_file()

    # ----------------------------------------------------------------- json io
    def write_json(self, path: Path, model: BaseModel | dict[str, Any]) -> None:
        """Atomic JSON write (tmp + rename)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        data = model.model_dump(mode="json") if isinstance(model, BaseModel) else model
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        tmp.replace(path)

    def read_json(self, path: Path) -> dict[str, Any]:
        return json.loads(path.read_text())

    # ----------------------------------------------------------------- git
    _LOCKS: dict[str, threading.RLock] = {}
    _LOCKS_GUARD = threading.Lock()

    @property
    def _lock(self) -> threading.RLock:
        """One in-process lock per workspace root: parallel agent/tool threads
        must not race on .git/index."""
        key = str(self.root)
        with Workspace._LOCKS_GUARD:
            return Workspace._LOCKS.setdefault(key, threading.RLock())

    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        with self._lock:
            for attempt in range(4):
                proc = subprocess.run(
                    ["git", *args], cwd=self.root, text=True, capture_output=True, check=False,
                    env={"GIT_AUTHOR_NAME": "c3v", "GIT_AUTHOR_EMAIL": "c3v@local",
                         "GIT_COMMITTER_NAME": "c3v", "GIT_COMMITTER_EMAIL": "c3v@local",
                         "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(self.root)},
                )
                if proc.returncode == 0 or "index.lock" not in (proc.stderr or ""):
                    break
                time.sleep(0.2 * (attempt + 1))  # another process holds .git/index.lock
            if check and proc.returncode != 0:
                raise subprocess.CalledProcessError(proc.returncode, proc.args, proc.stdout, proc.stderr)
            return proc

    def _git_init(self) -> None:
        if (self.root / ".git").exists():
            return
        self._git("init", "-q")
        (self.root / ".gitignore").write_text(
            "# harness-owned run state is never part of the code snapshot\n"
            "artifacts/\ntrajectories/\nstages/\nrounds/\n_assets/\n.c3v/\n.gemini/\n.claude/\n"
            "events.jsonl\nrun_state.json\nrecord.json\n*.log\nnode_modules/\n*.tmp\n__pycache__/\n"
        )
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "init", "--allow-empty")

    def commit(self, message: str) -> str:
        """Commit everything tracked (src/, public/, plan, ...) and return the sha."""
        self._git("add", "-A")
        self._git("commit", "-q", "-m", message, "--allow-empty")
        return self._git("rev-parse", "HEAD").stdout.strip()

    def head(self) -> str:
        return self._git("rev-parse", "HEAD").stdout.strip()

    def changed_files(self, since: str | None = None) -> list[FileChange]:
        """Files changed vs ``since`` (a commit) or vs HEAD (uncommitted work)."""
        self._git("add", "-A", "-N")  # register untracked so they show up in diff
        args = ["diff", "--numstat", "--diff-filter=ADM"] + ([since] if since else ["HEAD"])
        out = self._git(*args).stdout
        status = {}
        for line in self._git("diff", "--name-status", since or "HEAD").stdout.splitlines():
            if "\t" in line:
                st, path = line.split("\t", 1)
                status[path] = {"A": "added", "D": "deleted"}.get(st[0], "modified")
        changes = []
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            add, rem, path = parts
            changes.append(FileChange(path=path, status=status.get(path, "modified"),
                                      lines_added=int(add) if add.isdigit() else 0,
                                      lines_removed=int(rem) if rem.isdigit() else 0))
        return changes

    def restore(self, commit: str) -> None:
        """Make src/ + public/ exactly match ``commit`` (artifacts untouched)."""
        tracked = self._git("ls-tree", "--name-only", commit).stdout.split()
        roots = [r for r in ("src", "public") if r in tracked]
        # remove current tracked+untracked content of the roots, then check out the target
        self._git("rm", "-r", "-q", "--cached", "--ignore-unmatch", "--", "src", "public")
        for r in ("src", "public"):
            d = self.root / r
            if d.exists():
                shutil.rmtree(d)
            d.mkdir(parents=True, exist_ok=True)
        if roots:
            self._git("checkout", "-q", commit, "--", *roots)
        self._git("add", "-A")

    def diff(self, a: str, b: str = "HEAD") -> str:
        return self._git("diff", a, b, "--", "src", "public").stdout

    def snapshot_src(self, dest: Path) -> None:
        """Copy src/ (raw code only) to ``dest`` — used by the flywheel exporter."""
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(self.src, dest, ignore=shutil.ignore_patterns("node_modules", "__pycache__"))

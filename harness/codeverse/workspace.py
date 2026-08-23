"""Run workspace: fixed directory layout + git snapshots of ``src/``.

A workspace is a directory under ``runs/<slug>/`` (see docs/ARCHITECTURE.md §3).
``src/`` (and ``public/``) is a git repository so every round is a commit:
diffs, rollback and "files changed by the agent" come for free and the
flywheel can replay any round.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from codeverse.contracts.agent import FileChange
from codeverse.proc import write_json_atomic

#: the three top-level buckets of a run directory (docs/RUN_LAYOUT.md)
DELIVERABLE_DIR = "deliverable"   # (a) what the user asked for
EVIDENCE_DIR = "evidence"         # (b) why we believe it is good  (alias of artifacts/)
TELEMETRY_DIR = "telemetry"       # (c) what it cost and how it was configured

#: physical directories every run owns.  ``artifacts/`` is the physical home of the
#: evidence (six packages and ~50 recorded runs write paths into it); ``evidence/``
#: is an alias for it — see docs/RUN_LAYOUT.md for why the alias points that way.
LAYOUT_DIRS = ("src", "public", "artifacts", "artifacts/renders", "artifacts/gates", "artifacts/judge",
               "trajectories", DELIVERABLE_DIR, TELEMETRY_DIR)

#: navigation aliases: ``alias path`` → ``symlink target`` (relative to the alias's parent).
#: The alias is always the symlink, never the physical file: writers (events.jsonl append,
#: atomic run_state.json rewrite) keep using the paths they already use, and a tmp+rename
#: on the physical path can never replace a symlink out from under a reader.
LAYOUT_ALIASES: tuple[tuple[str, str], ...] = (
    (EVIDENCE_DIR, "artifacts"),
    (f"{TELEMETRY_DIR}/events.jsonl", "../events.jsonl"),
    (f"{TELEMETRY_DIR}/run_state.json", "../run_state.json"),
    (f"{TELEMETRY_DIR}/stages", "../stages"),
    (f"{TELEMETRY_DIR}/trajectories", "../trajectories"),
)

#: harness-owned paths that never belong in the code snapshot
_GITIGNORE_LINES = (
    "# harness-owned run state is never part of the code snapshot",
    "artifacts/", "trajectories/", "stages/", "rounds/", "_assets/", ".3dcv/", ".gemini/", ".claude/",
    "deliverable/", "telemetry/", "evidence", "captions.json",
    "events.jsonl", "run_state.json", "record.json", "*.log", "node_modules/", "*.tmp", "__pycache__/",
)


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

    @property
    def deliverable(self) -> Path:
        """(a) the hand-over folder: best-round code + canonical artifact + sheet + captions."""
        return self.root / DELIVERABLE_DIR

    @property
    def evidence(self) -> Path:
        """(b) renders / gates / judge / measurements — the alias name for ``artifacts/``."""
        return self.root / EVIDENCE_DIR

    @property
    def telemetry(self) -> Path:
        """(c) cost ledger, per-call usage rows, resolved settings, stages, trajectories."""
        return self.root / TELEMETRY_DIR

    @property
    def cost_path(self) -> Path:
        return self.telemetry / "cost.json"

    @property
    def usage_path(self) -> Path:
        return self.telemetry / "usage.jsonl"

    @property
    def settings_path(self) -> Path:
        return self.telemetry / "settings.json"

    @property
    def deliverable_manifest_path(self) -> Path:
        return self.deliverable / "manifest.json"

    @property
    def stages(self) -> Path:
        return self.root / "stages"

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
        self.ensure_layout()
        self._git_init()
        return self

    def ensure_layout(self, *, dry_run: bool = False) -> dict[str, str]:
        """Create the directory buckets + navigation aliases.  Idempotent, never
        destructive: an alias path that already holds real data is left alone.

        Returns ``{path: action}`` with action in ``created`` / ``relinked`` /
        ``kept`` / ``skipped:<reason>`` — the migration helper prints it.
        ``dry_run`` reports the same actions without touching the filesystem."""
        actions: dict[str, str] = {}
        for d in LAYOUT_DIRS:
            p = self.root / d
            if not p.exists():
                if not dry_run:
                    p.mkdir(parents=True, exist_ok=True)
                actions[d] = "created"
        for name, target in LAYOUT_ALIASES:
            actions.update(self._ensure_alias(name, target, dry_run=dry_run))
        return actions

    def _ensure_alias(self, name: str, target: str, *, dry_run: bool = False) -> dict[str, str]:
        link = self.root / name
        if link.is_symlink():
            if os.readlink(link) == target:
                return {}
            if not dry_run:
                link.unlink()
                os.symlink(target, link)
            return {name: "relinked"}
        if link.exists():  # a real file/dir sits there — never clobber run data
            return {name: "kept"}
        if dry_run:
            return {name: "created"}
        link.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.symlink(target, link)
        except OSError as e:  # filesystem without symlinks (some Windows shares)
            return {name: f"skipped:{type(e).__name__}"}
        return {name: "created"}

    def exists(self) -> bool:
        return self.spec_path.is_file()

    # ----------------------------------------------------------------- json io
    def write_json(self, path: Path, model: BaseModel | dict[str, Any]) -> None:
        """Atomic JSON write (tmp + rename, via :func:`codeverse.proc.write_json_atomic`)."""
        data = model.model_dump(mode="json") if isinstance(model, BaseModel) else model
        write_json_atomic(path, data)

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
                    env={"GIT_AUTHOR_NAME": "3dcv", "GIT_AUTHOR_EMAIL": "3dcv@local",
                         "GIT_COMMITTER_NAME": "3dcv", "GIT_COMMITTER_EMAIL": "3dcv@local",
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
        self.ensure_gitignore()
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "init", "--allow-empty")

    def ensure_gitignore(self, *, dry_run: bool = False) -> bool:
        """Make sure every harness-owned path is ignored by the run's git repo.
        Idempotent: missing lines are appended, existing ones are left in place.
        Returns True when the file changed."""
        path = self.root / ".gitignore"
        existing = path.read_text().splitlines() if path.is_file() else []
        have = {line.strip() for line in existing}
        missing = [line for line in _GITIGNORE_LINES if line not in have]
        if not missing or dry_run:
            return bool(missing)
        lines = existing + ([""] if existing and existing[-1].strip() else []) + missing
        path.write_text("\n".join(lines).rstrip("\n") + "\n")
        return True

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

"""Run workspace: fixed directory layout + git snapshots of ``src/``.

A workspace is a directory under ``runs/<slug>/``; ``src/`` (and ``public/``) is a git
repository so every round is a commit (diffs, rollback, "files changed by the agent").
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from codeverse3d.contracts.agent import FileChange
from codeverse3d.proc import write_json_atomic

#: top-level buckets of a run directory
DELIVERABLE_DIR = "deliverable"   # (a) what the user asked for
TELEMETRY_DIR = "telemetry"       # (c) what it cost and how it was configured
#: harness-owned read-only files inside the workspace
C3D_DIR = ".3dcode"

#: directories every run owns
LAYOUT_DIRS = ("src", "public", "artifacts", "artifacts/renders", "artifacts/gates", "artifacts/judge",
               "trajectories", DELIVERABLE_DIR, TELEMETRY_DIR)

#: harness-owned paths that never belong in the code snapshot
_GITIGNORE_LINES = (
    "# harness-owned run state is never part of the code snapshot",
    "artifacts/", "trajectories/", "stages/", "rounds/", "_assets/", "_cand/", ".3dcode/", ".gemini/", ".claude/", ".agents/",
    "deliverable/", "telemetry/", "captions.json",
    "events.jsonl", "run_state.json", "record.json", "*.log", "node_modules/", "*.tmp", "__pycache__/",
)


#: prepended to every git argv run against a run workspace: an agent-planted hook, fsmonitor or
#: global attributes file never runs.  ``-c`` outranks ``.git/config``.  ``diff.external`` is
#: deliberately not set here: an empty value makes git try to execute it.
GIT_SAFE_FLAGS = ("-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
                  "-c", "core.attributesFile=/dev/null")
#: added to every git command that renders file content: a named diff / textconv driver in the
#: agent-writable .git/config is not reachable by ``-c`` and would execute without these.
GIT_SAFE_DIFF_FLAGS = ("--no-ext-diff", "--no-textconv")
#: a has_commit probe that has not answered by then answers no
HAS_COMMIT_TIMEOUT_S = 60
#: local (.git/config) keys that make git EXECUTE a program; unset in place before every commit.
_GIT_EXEC_SECTIONS = ("filter", "diff", "alias", "gpg", "credential")
_GIT_EXEC_KEYS = ("core.hookspath", "core.fsmonitor", "core.sshcommand", "core.pager", "commit.gpgsign")


def _git_home() -> str:
    """HOME for workspace git: harness-owned, never the workspace.  The cache_dir default, spelled
    out because this module may not import config."""
    d = Path.home() / ".cache" / "codeverse3d" / "githome"
    with contextlib.suppress(OSError):  # a missing HOME is harmless; an unwritable one must not sink git
        d.mkdir(parents=True, exist_ok=True)
    return str(d)


def git_safe_env(**extra: str) -> dict[str, str]:
    """Environment for git in an agent-writable repository: no system / global config, fixed PATH,
    no credential prompt.  ``extra`` adds identity."""
    return {"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": _git_home(),
            "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0", **extra}


class WorkspaceGitError(subprocess.CalledProcessError):
    """A failed git command that keeps git's own stderr in its message."""

    def __init__(self, root: Path, args: tuple[str, ...], returncode: int, stderr: str):
        super().__init__(returncode, ["git", *args], None, stderr)
        self.root = root
        self._detail = (stderr or "").strip()[-800:]

    def __str__(self) -> str:
        msg = f"git {' '.join(self.cmd[1:])} failed in {self.root} (exit {self.returncode})"
        if self._detail:
            msg += f": {self._detail}"
        if "index.lock" in (self.stderr or ""):
            msg += (f"\n  a stale lock is left behind when a run is killed mid-commit; "
                    f"if no other git is running here, remove it:  rm {self.root / '.git' / 'index.lock'}")
        return msg


class Workspace:
    def __init__(self, root: Path | str):
        self.root = Path(root).resolve()
        self._cfg_mtime: int = -1  # .git/config mtime at the last _sanitise_git_config

    # ----------------------------------------------------------------- paths
    def rebase(self, stored: str | Path) -> Path:
        """A stored (possibly foreign absolute) path resolved against THIS workspace.

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
        return self.root / DELIVERABLE_DIR

    @property
    def telemetry(self) -> Path:
        return self.root / TELEMETRY_DIR

    @property
    def cost_path(self) -> Path:
        return self.telemetry / "cost.json"

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

    def round_artifacts(self, round_index: int) -> Path:
        return self.artifacts / f"r{round_index:02d}"

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

    def ensure_layout(self) -> None:
        for d in LAYOUT_DIRS:
            (self.root / d).mkdir(parents=True, exist_ok=True)

    def exists(self) -> bool:
        return self.spec_path.is_file()

    # ----------------------------------------------------------------- json io
    def write_json(self, path: Path, model: BaseModel | dict[str, Any]) -> None:
        data = model.model_dump(mode="json") if isinstance(model, BaseModel) else model
        write_json_atomic(path, data)

    # ----------------------------------------------------------------- artifact staging
    def stage_artifacts(self, *names: str) -> ArtifactStage:
        """An :class:`ArtifactStage` over canonical ``artifacts/`` names (context manager, or a bare ``.invalidate()``)."""
        return ArtifactStage(self, names)

    def read_json(self, path: Path) -> dict[str, Any]:
        return json.loads(path.read_text())

    # ----------------------------------------------------------------- git
    _LOCKS: dict[str, threading.RLock] = {}
    _LOCKS_GUARD = threading.Lock()

    @property
    def _lock(self) -> threading.RLock:
        key = str(self.root)
        with Workspace._LOCKS_GUARD:
            return Workspace._LOCKS.setdefault(key, threading.RLock())

    def _git(self, *args: str, check: bool = True, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
        with self._lock:
            for attempt in range(4):
                proc = subprocess.run(
                    ["git", *GIT_SAFE_FLAGS, *args], cwd=self.root, text=True, capture_output=True, check=False,
                    timeout=timeout,
                    env=git_safe_env(GIT_AUTHOR_NAME="3dcode", GIT_AUTHOR_EMAIL="3dcode@local",
                                     GIT_COMMITTER_NAME="3dcode", GIT_COMMITTER_EMAIL="3dcode@local"),
                )
                if proc.returncode == 0 or "index.lock" not in (proc.stderr or ""):
                    break
                time.sleep(0.2 * (attempt + 1))  # another process holds .git/index.lock
            if check and proc.returncode != 0:
                raise WorkspaceGitError(self.root, args, proc.returncode, proc.stderr or "")
            return proc

    def _sanitise_git_config(self) -> None:
        cfg = self.root / ".git" / "config"
        mtime = cfg.stat().st_mtime_ns if cfg.is_file() else 0
        if mtime == self._cfg_mtime:
            return
        names = self._git("config", "--local", "--list", "--name-only", check=False).stdout.split()
        for name in {n for n in names if n.split(".", 1)[0] in _GIT_EXEC_SECTIONS or n in _GIT_EXEC_KEYS}:
            self._git("config", "--local", "--unset-all", name, check=False)
        self._cfg_mtime = cfg.stat().st_mtime_ns if cfg.is_file() else 0

    def _git_init(self) -> None:
        with self._lock:  # init + config + first commit are one unit
            if (self.root / ".git").exists():
                self._sanitise_git_config()  # a resumed run: the agent has had the repo since
                return
            self._git("init", "-q")
            self.ensure_gitignore()
            self._git("add", "-A")
            self._git("commit", "-q", "-m", "init", "--allow-empty")

    def ensure_gitignore(self) -> bool:
        path = self.root / ".gitignore"
        existing = path.read_text().splitlines() if path.is_file() else []
        have = {line.strip() for line in existing}
        missing = [line for line in _GITIGNORE_LINES if line not in have]
        if not missing:
            return False
        lines = existing + ([""] if existing and existing[-1].strip() else []) + missing
        path.write_text("\n".join(lines).rstrip("\n") + "\n")
        return True

    def commit(self, message: str) -> str:
        with self._lock:
            self._sanitise_git_config()
            self._git("add", "-A")
            self._git("commit", "-q", "-m", message, "--allow-empty")
            return self._git("rev-parse", "HEAD").stdout.strip()

    def head(self) -> str:
        return self._git("rev-parse", "HEAD").stdout.strip()

    def has_commit(self, commit: str) -> bool:
        if not commit:
            return False
        try:
            return self._git("cat-file", "-e", f"{commit}^{{commit}}", check=False,
                             timeout=HAS_COMMIT_TIMEOUT_S).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    def changed_files(self, since: str | None = None) -> list[FileChange]:
        with self._lock:  # add -N + the diff are one unit: a sibling commit between them mislabels a status
            self._sanitise_git_config()  # `add` applies clean filters; -c cannot unset a local one
            self._git("add", "-A", "-N")  # register untracked so they show up in diff
            out = self._git("diff", *GIT_SAFE_DIFF_FLAGS, "--name-status", "--no-renames", "--diff-filter=ADM",
                            since or "HEAD").stdout
            changes = []
            for line in out.splitlines():
                st, sep, path = line.partition("\t")
                if sep:
                    changes.append(FileChange(path=path, status={"A": "added", "D": "deleted"}.get(st[0], "modified")))
            return changes

    def restore(self, commit: str) -> None:
        with self._lock:  # rm + rmtree + checkout + add: a sibling must never see the half-removed tree
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

    def restore_paths(self, commit: str, paths: Sequence[str]) -> None:
        if not paths:
            return
        self._git("checkout", "-q", commit, "--", *paths)


class ArtifactStage:
    """Stage-then-promote lifecycle for a fixed set of canonical ``artifacts/`` names.

    A canonical artifact exists on disk ONLY when the build that owns it finished with it.
    ``with ws.stage_artifacts(...)`` invalidates the names on enter and claims a private
    ``artifacts/.staging/<pid>-<nonce>/``; ``path(name)`` is where to write, ``promote()``
    publishes (``os.replace`` per file), exit without promote discards.  ``invalidate()`` also
    works standalone.  Directory promotion is not atomic (rename old away, new in, drop old);
    concurrent stages use private staging dirs and the last rename wins.
    """

    STAGING_ROOT = ".staging"

    def __init__(self, ws: Workspace, names: tuple[str, ...] | Sequence[str]):
        if not names:
            raise ValueError("ArtifactStage needs at least one artifact name")
        self._ws = ws
        self.names: tuple[str, ...] = tuple(names)
        self._dir: Path | None = None

    # ------------------------------------------------------------------ lifecycle
    def __enter__(self) -> ArtifactStage:
        self.invalidate()
        _ = self.staging_dir
        return self

    def __exit__(self, *exc: object) -> None:
        self.discard()

    @property
    def staging_dir(self) -> Path:
        if self._dir is None:
            d = self._ws.artifacts / self.STAGING_ROOT / f"{os.getpid()}-{os.urandom(4).hex()}"
            d.mkdir(parents=True, exist_ok=True)
            self._dir = d
        return self._dir

    def path(self, name: str) -> Path:
        self._check(name)
        p = self.staging_dir / name
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    # ------------------------------------------------------------------ operations
    def invalidate(self, *names: str) -> None:
        for name in names or self.names:
            self._check(name)
            target = self._ws.artifacts / name
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target, ignore_errors=True)
            else:
                target.unlink(missing_ok=True)

    def promote(self, *names: str) -> list[Path]:
        """Publish staged entries into ``artifacts/``; explicit ``names`` must all exist
        (``FileNotFoundError``), otherwise every staged name that was written is published."""
        chosen = names or self.names
        published: list[Path] = []
        for name in chosen:
            self._check(name)
            src = self.staging_dir / name
            if not src.exists():
                if names:
                    raise FileNotFoundError(f"promote({name!r}): nothing staged at {src}")
                continue
            dest = self._ws.artifacts / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir() and not src.is_symlink():
                self._replace_dir(src, dest, name)
            else:
                os.replace(src, dest)
            published.append(dest)
        return published

    def discard(self) -> None:
        if self._dir is not None:
            shutil.rmtree(self._dir, ignore_errors=True)
            with contextlib.suppress(OSError):  # tidy the shared .staging parent when empty
                self._dir.parent.rmdir()
            self._dir = None

    # ------------------------------------------------------------------ internals
    def _check(self, name: str) -> None:
        if name not in self.names:
            raise ValueError(f"{name!r} is not staged here (staged: {list(self.names)})")

    def _replace_dir(self, src: Path, dest: Path, name: str) -> None:
        parked: Path | None = None
        if dest.exists():
            parked = self.staging_dir / f"{name.replace('/', '_')}.old"
            os.rename(dest, parked)
        try:
            os.rename(src, dest)
        except OSError:
            if parked is not None:  # best effort: put the old dir back
                os.rename(parked, dest)
            raise
        if parked is not None:
            shutil.rmtree(parked, ignore_errors=True)

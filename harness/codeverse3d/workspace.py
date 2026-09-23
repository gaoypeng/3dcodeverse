"""Run workspace: fixed directory layout + git snapshots of ``src/``.

A workspace is a directory under ``runs/<slug>/`` (see docs/ARCHITECTURE.md §3).
``src/`` (and ``public/``) is a git repository so every round is a commit:
diffs, rollback and "files changed by the agent" come for free and the
flywheel can replay any round.
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
    "artifacts/", "trajectories/", "stages/", "rounds/", "_assets/", "_cand/", ".3dcode/", ".3dcv/", ".gemini/", ".claude/", ".agents/",
    "deliverable/", "telemetry/", "evidence", "captions.json",
    "events.jsonl", "run_state.json", "record.json", "*.log", "node_modules/", "*.tmp", "__pycache__/",
)


#: prepended to every git argv run against a run workspace: an agent-planted hook or
#: fsmonitor never runs, and the GLOBAL attributes file cannot name a driver either.
#: ``-c`` outranks ``.git/config``, so these hold even in a repository the agent has
#: written to.  ``diff.external`` is deliberately NOT here: ``-c diff.external=`` sets
#: it to the empty string, which git tries to EXECUTE (``fatal: external diff died``) on
#: any diff that forgets ``GIT_SAFE_DIFF_FLAGS`` — and ``--no-ext-diff`` there is what
#: neutralises a planted driver in the first place.
GIT_SAFE_FLAGS = ("-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
                  "-c", "core.attributesFile=/dev/null")
#: added to every git command that RENDERS file content (diff / show / log -p).  A
#: NAMED driver — ``.gitattributes`` "*.bin diff=x" plus ``[diff "x"] textconv``/``command``
#: in .git/config, both agent-writable — is not reachable by ``-c``, and ``--no-ext-diff``
#: alone does not disable textconv: without ``--no-textconv`` that driver EXECUTES.
GIT_SAFE_DIFF_FLAGS = ("--no-ext-diff", "--no-textconv")
#: a has_commit probe that has not answered by then answers no (record/_git.py reads use 60 s too)
HAS_COMMIT_TIMEOUT_S = 60
#: local (.git/config) config that makes git EXECUTE a program.  ``-c`` cannot override a
#: local ``filter.*`` / ``diff.*`` driver, so these are unset in place before every commit.
_GIT_EXEC_SECTIONS = ("filter", "diff", "alias", "gpg", "credential")
_GIT_EXEC_KEYS = ("core.hookspath", "core.fsmonitor", "core.sshcommand", "core.pager", "commit.gpgsign")


def _git_home() -> str:
    """HOME for workspace git: harness-owned, never the workspace (whose agent-written
    ``.gitconfig`` git reads as its GLOBAL config).  The cache_dir DEFAULT, spelled out:
    workspace.py may not import config (tests/install/test_import_direction)."""
    d = Path.home() / ".cache" / "codeverse3d" / "githome"
    with contextlib.suppress(OSError):  # a missing HOME is harmless; an unwritable one must not sink git
        d.mkdir(parents=True, exist_ok=True)
    return str(d)


def git_safe_env(**extra: str) -> dict[str, str]:
    """Environment for git in an agent-writable repository: no system or global config
    (``.gitconfig`` written into the workspace is git's GLOBAL config when HOME is the
    workspace), a fixed PATH, and no credential prompt.  ``extra`` adds identity."""
    return {"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": _git_home(),
            "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0", **extra}


class WorkspaceGitError(subprocess.CalledProcessError):
    """A git command in a run workspace failed, carrying git's own explanation.

    Subclasses CalledProcessError so existing ``except subprocess.CalledProcessError``
    handlers keep working.  The reason it exists: CalledProcessError.__str__ drops
    stderr, and codeverse3d/cli/main.py reports failures as
    "run failed: {type(e).__name__}: {e}" — so a stale ``.git/index.lock`` surfaced as
    the bare line "Command '['git', 'add', '-A']' returned non-zero exit status 128"
    while git's own "fatal: Unable to create ... index.lock: File exists" sat unread in
    e.stderr.  The 4-try/1.2 s retry loop above is built for a LIVE holder and can never
    clear a stale lock, so every subsequent ``3dcode resume <slug>`` repeated the same
    opaque failure with no clue and no named remedy.
    """

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
    """Paths + snapshot helpers for one run.  Cheap to construct; no I/O until used."""

    def __init__(self, root: Path | str):
        self.root = Path(root).resolve()
        self._cfg_mtime: int = -1  # .git/config mtime at the last _sanitise_git_config

    # ----------------------------------------------------------------- paths
    def rebase(self, stored: str | Path) -> Path:
        """A path out of ``record.json`` / ``run_state.json``, resolved against THIS
        workspace wherever it was written.

        The records store ABSOLUTE host paths (29 per run: rounds[].renders[].path,
        contact_sheet, build.glb_path, build.extra_paths, census exports, and
        run_state.stages[].result_path), so archiving, moving or rsyncing a run silently
        broke every consumer that trusted them — `3dcode show` printed a contact sheet at
        the old location that did not exist, while the real one sat under the new root.
        object.glb kept working because it is recomputed from the workspace, which made
        the breakage partial and therefore silent.

        Relative paths join the root (what the old ``_judge.resolve_paths`` did, and the
        only case it handled).  An absolute path under another root is re-rooted FIRST,
        by its longest trailing segment that exists under this root — for a moved run the
        tail is intact even though the prefix is not, and for a COPIED/rsynced run this
        workspace's own file must win even while the original still exists (readers used
        to read, and a retexture to write past, the original's files until that directory
        was deleted).  A path already under this root, or with no local tail match, is
        returned as-is — an existing original still resolves, and a missing one is
        reported as a real missing file rather than a silently wrong one.
        """
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
        """(a) the hand-over folder: one round's code + artifact + sheet + captions (``3dcode pick``)."""
        return self.root / DELIVERABLE_DIR

    @property
    def telemetry(self) -> Path:
        """(c) cost ledger, per-call usage rows, resolved settings, stages, trajectories."""
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

    def rendered_rounds(self) -> list[int]:
        """The rounds that have a :meth:`renders_dir`, ascending."""
        return sorted(int(p.name[1:]) for p in (self.artifacts / "renders").glob("r[0-9][0-9]") if p.is_dir())

    def gates_dir(self, round_index: int) -> Path:
        return self.artifacts / "gates" / f"r{round_index:02d}"

    def judge_path(self, round_index: int, suffix: str = "") -> Path:
        return self.artifacts / "judge" / f"r{round_index:02d}{suffix}.json"

    def round_artifacts(self, round_index: int) -> Path:
        """``artifacts/rNN/`` — the round's own copy of the build outputs a hand-over needs
        (``codeverse3d.record.deliverable.keep_round_artifacts``; docs/RUN_LAYOUT.md)."""
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
        """Atomic JSON write (tmp + rename, via :func:`codeverse3d.proc.write_json_atomic`)."""
        data = model.model_dump(mode="json") if isinstance(model, BaseModel) else model
        write_json_atomic(path, data)

    # ----------------------------------------------------------------- artifact staging
    def stage_artifacts(self, *names: str) -> ArtifactStage:
        """An :class:`ArtifactStage` over canonical ``artifacts/`` names.

        Use as a context manager for the full stage-then-promote lifecycle, or
        call ``.invalidate()`` on the returned stage for a bare wipe of the
        canonical names (a bare wipe, no staging)."""
        return ArtifactStage(self, names)

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
        """Unset the exec-capable keys an agent may have written into ``.git/config``.
        Cached on the file's mtime, so the common path costs one stat."""
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
        """Commit everything tracked (src/, public/, plan, ...) and return the sha.

        ONE transaction: ``_git`` locks per command, so a sibling thread that committed
        between the commit and the ``rev-parse`` made this return ITS sha — the round
        journal then named a commit this task never made (repro'd 2026-08-27)."""
        with self._lock:
            self._sanitise_git_config()
            self._git("add", "-A")
            self._git("commit", "-q", "-m", message, "--allow-empty")
            return self._git("rev-parse", "HEAD").stdout.strip()

    def head(self) -> str:
        return self._git("rev-parse", "HEAD").stdout.strip()

    def has_commit(self, commit: str) -> bool:
        """Does this repo actually have ``commit``?  A round journal can name a commit
        the crash never wrote (``tracks/lifecycle.reconcile_resume`` drops such rounds).
        A read that cannot answer — no git binary, no repo dir, a hung git — is a no."""
        if not commit:
            return False
        try:
            return self._git("cat-file", "-e", f"{commit}^{{commit}}", check=False,
                             timeout=HAS_COMMIT_TIMEOUT_S).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    def changed_files(self, since: str | None = None) -> list[FileChange]:
        """Files changed vs ``since`` (a commit) or vs HEAD (uncommitted work)."""
        with self._lock:  # add -N + two diffs are one unit: a sibling commit between them mislabels a status
            self._sanitise_git_config()  # `add` applies clean filters; -c cannot unset a local one
            self._git("add", "-A", "-N")  # register untracked so they show up in diff
            args = ["diff", *GIT_SAFE_DIFF_FLAGS, "--numstat", "--diff-filter=ADM"] + ([since] if since else ["HEAD"])
            out = self._git(*args).stdout
            status = {}
            for line in self._git("diff", *GIT_SAFE_DIFF_FLAGS, "--name-status", since or "HEAD").stdout.splitlines():
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
        """Make just ``paths`` match ``commit`` (worktree + index; everything else untouched).

        Used by the CLI post-hoc write-scope check (``agents/cli_common.finish_session``):
        an ``edit_only`` session's out-of-scope writes are rolled back to the session's own
        ``pre:`` commit without disturbing its in-scope work.  Every path must exist in
        ``commit`` — the caller passes only modified/deleted paths, which by definition did."""
        if not paths:
            return
        self._git("checkout", "-q", commit, "--", *paths)


class ArtifactStage:
    """Stage-then-promote lifecycle for a fixed set of canonical ``artifacts/`` names.

    The invariant this enforces: a canonical artifact exists on disk ONLY when the
    build/pass that owns it finished with that artifact as its result.  Before this,
    every runtime's early return (missing entry file, lint error, wrapper crash)
    left the *previous* round's ``object.glb`` / ``build.json`` looking current, and
    every downstream consumer (measure/render tools, deliverable packaging, the
    gallery) trusted bare existence.

    Lifecycle (``with ws.stage_artifacts("build.json", "object.glb") as stage:``):

    * ``__enter__`` **invalidates** the canonical names immediately — before any
      early return can leak — and claims a private staging directory under
      ``artifacts/.staging/<pid>-<nonce>/``;
    * ``path(name)`` is the staging location to write ``name`` to (wrappers taking
      an ``--out`` directory can be pointed at ``staging_dir`` directly);
    * ``promote(*names)`` publishes staged entries into ``artifacts/`` —
      ``os.replace`` per file (atomic: staging lives on the same filesystem);
    * exit without promote **discards** the staging directory — canonical stays absent.

    ``invalidate()`` also works standalone (no ``with``) as the one-call
    replacement for wiping the canonical names by hand with ``shutil.rmtree``.

    Directory promotion (``meshes/``) is NOT atomic: the old dir is renamed away
    (parked inside the staging dir), the new one renamed in, then the parked copy
    is removed.  A reader can observe the name missing between the two renames, and
    a SIGKILL between them leaves the old copy parked under ``artifacts/.staging/``
    — inert, because every consumer reads canonical paths only, and the next stage
    of the same names starts by invalidating them.  Concurrent stages over the same
    names (the scene track fans agents over one workspace) each use a private
    staging dir; promotion follows the ``write_json_atomic`` precedent: last writer
    to rename wins.
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
        _ = self.staging_dir  # claim it now so wrappers can be pointed at it
        return self

    def __exit__(self, *exc: object) -> None:
        self.discard()

    @property
    def staging_dir(self) -> Path:
        """The private staging directory (created on first use)."""
        if self._dir is None:
            d = self._ws.artifacts / self.STAGING_ROOT / f"{os.getpid()}-{os.urandom(4).hex()}"
            d.mkdir(parents=True, exist_ok=True)
            self._dir = d
        return self._dir

    def path(self, name: str) -> Path:
        """Staging location for ``name`` (parent directories created)."""
        self._check(name)
        p = self.staging_dir / name
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    # ------------------------------------------------------------------ operations
    def invalidate(self, *names: str) -> None:
        """Remove the canonical copies of ``names`` (default: every staged name)."""
        for name in names or self.names:
            self._check(name)
            target = self._ws.artifacts / name
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target, ignore_errors=True)
            else:
                target.unlink(missing_ok=True)

    def promote(self, *names: str) -> list[Path]:
        """Publish staged entries into ``artifacts/`` and return the canonical paths.

        With explicit ``names``, every named entry must exist in staging
        (``FileNotFoundError`` otherwise — a build that claims success without its
        outputs is a bug, not a skip).  With no arguments, every staged name that
        was actually written is published and the rest are left invalidated."""
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
        """Drop the staging directory (anything not promoted is gone)."""
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
        """Replace-dir strategy: rename old away (parked in staging), rename new in,
        remove old.  See the class docstring for the atomicity limits."""
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

"""Project workspace: the fixed directory layout every instrument reads and writes.

A workspace is a directory holding ``spec.json``, ``plan.json``, ``src/`` (+ ``public/``: the agent's raw
code), ``artifacts/`` (build outputs, renders, gates) and ``judge/rNN/`` (brief, montages, judgment, score).
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from codeverse3d.proc import write_json_atomic

#: directories every workspace owns
LAYOUT_DIRS = ("src", "public", "artifacts", "artifacts/renders", "artifacts/gates", "judge")
#: the agent's code: what ``src_hash`` fingerprints (``public/`` carries compiled assets)
CODE_ROOTS = ("src", "public")


class Workspace:
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
    def spec_path(self) -> Path:
        return self.root / "spec.json"

    @property
    def plan_path(self) -> Path:
        return self.root / "plan.json"

    @property
    def gates_path(self) -> Path:
        """``artifacts/gates.json``: what the last ``check`` found (build, measurement, every gate)."""
        return self.artifacts / "gates.json"

    def renders_dir(self, round_index: int) -> Path:
        return self.artifacts / "renders" / f"r{round_index:02d}"

    def gates_dir(self, round_index: int) -> Path:
        return self.artifacts / "gates" / f"r{round_index:02d}"

    def judge_dir(self, round_index: int) -> Path:
        return self.root / "judge" / f"r{round_index:02d}"

    # ----------------------------------------------------------------- lifecycle
    def ensure_layout(self) -> None:
        for d in LAYOUT_DIRS:
            (self.root / d).mkdir(parents=True, exist_ok=True)

    def exists(self) -> bool:
        return self.spec_path.is_file()

    def src_hash(self) -> str:
        """Content hash of the agent's code (``src/`` + ``public/``): the state a ``check`` verdict belongs to."""
        h = hashlib.sha256()
        for root in CODE_ROOTS:
            base = self.root / root
            if not base.is_dir():
                continue
            for p in sorted(x for x in base.rglob("*") if x.is_file() and "node_modules" not in x.parts
                            and "__pycache__" not in x.parts):
                h.update(p.relative_to(self.root).as_posix().encode())
                h.update(p.read_bytes())
        return h.hexdigest()[:16]

    # ----------------------------------------------------------------- json io
    def write_json(self, path: Path, model: BaseModel | dict[str, Any]) -> None:
        data = model.model_dump(mode="json") if isinstance(model, BaseModel) else model
        write_json_atomic(path, data)

    # ----------------------------------------------------------------- artifact staging
    def stage_artifacts(self, *names: str) -> ArtifactStage:
        """An :class:`ArtifactStage` over canonical ``artifacts/`` names (context manager, or a bare ``.invalidate()``)."""
        return ArtifactStage(self, names)


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

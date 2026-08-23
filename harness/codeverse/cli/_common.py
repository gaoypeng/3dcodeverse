"""Shared CLI plumbing: lazy imports (sub-packages may be incomplete), workspace
resolution, slug building, typed exits.  Keeps ``main.py`` thin."""

from __future__ import annotations

import hashlib
import importlib
import shutil
import sys
from pathlib import Path
from typing import Any

import typer

from codeverse.cli._fmt import console, err
from codeverse.config import get_settings
from codeverse.contracts.spec import Spec
from codeverse.conventions import slugify
from codeverse.workspace import Workspace

REPO_ROOT = Path(__file__).resolve().parents[2]


class CliError(typer.Exit):
    """Typed, already-reported failure → exit code."""

    def __init__(self, message: str, code: int = 1):
        err(message)
        super().__init__(code=code)


def lazy(module: str, attr: str | None = None) -> Any:
    """Import ``module`` (and ``attr``) at call time; a clear message when the
    sub-package is not implemented yet (packages are developed in parallel)."""
    try:
        mod = importlib.import_module(module)
    except ImportError as e:
        raise CliError(
            f"{module} is not available ({e}). This command needs that sub-package; "
            f"run `3dcv doctor` to see what is installed.", code=2
        ) from e
    if attr is None:
        return mod
    try:
        return getattr(mod, attr)
    except AttributeError as e:
        raise CliError(f"{module}.{attr} is missing ({e}); the sub-package may be incomplete.", code=2) from e


def import_bench() -> Any:
    """``bench`` lives at the repo root (not inside the package); make it importable."""
    try:
        return importlib.import_module("bench")
    except ImportError:
        if str(REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(REPO_ROOT))
        return lazy("bench")


def runs_root(runs_dir: Path | None) -> Path:
    return Path(runs_dir) if runs_dir is not None else Path(get_settings().runs_dir)


def open_workspace(slug: str, runs_dir: Path | None = None) -> Workspace:
    """``slug`` may be a run name under runs_dir or a path to a run directory."""
    p = Path(slug)
    if p.is_dir() and (p / "spec.json").is_file():
        return Workspace(p)
    ws = Workspace(runs_root(runs_dir) / slug)
    if not ws.exists():
        raise CliError(f"run not found: {ws.root} (no spec.json)")
    return ws


def load_spec(ws: Workspace) -> Spec:
    try:
        return Spec.model_validate_json(ws.spec_path.read_text())
    except Exception as e:
        raise CliError(f"invalid spec.json in {ws.root}: {e}") from e


def make_slug(prompt: str, track: str, language: str, explicit: str | None = None) -> str:
    if explicit:
        return slugify(explicit, 64)
    h = hashlib.sha256(f"{prompt.strip()}|{track}|{language}".encode()).hexdigest()[:8]
    return f"{slugify(prompt, 40)}_{h}"


def create_workspace(root: Path, *, force: bool) -> Workspace:
    ws = Workspace(root)
    if ws.root.exists() and any(ws.root.iterdir()):
        if not force:
            raise CliError(f"{ws.root} already exists; use --force to overwrite or --slug for a new name")
        shutil.rmtree(ws.root)
    ws.create()
    return ws


def parse_kv_floats(items: list[str], flag: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for it in items:
        if "=" not in it:
            raise CliError(f"{flag} expects key=value, got {it!r}")
        k, v = it.split("=", 1)
        try:
            out[k.strip()] = float(v)
        except ValueError as e:
            raise CliError(f"{flag} {k}: not a number: {v!r}") from e
    return out


def echo_json(obj: Any) -> None:
    import json

    console.print_json(json.dumps(obj, default=str, ensure_ascii=False))

"""Chores shared by the object, scene and GL render flavours: output dir, view payload, contact sheet."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from codeverse3d.config import get_settings
from codeverse3d.conventions import ViewPreset
from codeverse3d.spatial.sheet import contact_sheet


def out_directory(out_dir: Path | str, *, clean: Sequence[str] = ()) -> Path:
    """Resolve + create ``out_dir``; delete the ``clean`` side-car files left by a previous run."""
    d = Path(out_dir).resolve()
    d.mkdir(parents=True, exist_ok=True)
    for name in clean:
        (d / name).unlink(missing_ok=True)
    return d


def view_specs(views: Sequence[ViewPreset]) -> list[dict[str, Any]]:
    """``ViewPreset``s → the ``[{name, azimuth, elevation}]`` payload the render drivers take."""
    return [{"name": v.name, "azimuth": float(v.azimuth_deg), "elevation": float(v.elevation_deg)} for v in views]


def build_sheet(images: Sequence[tuple[str, str | Path]], out: Path | str) -> str | None:
    """Labelled contact sheet at the configured grid; ``None`` when there are no images."""
    if not images:
        return None
    settings = get_settings()
    return str(contact_sheet(list(images), Path(out), cols=settings.render.sheet_cols, tile=settings.render.sheet_tile))

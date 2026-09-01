"""Shared core of the three render flavours (object GLB, scene, GL frames).

``render.py`` (objects, node + headless Chrome), ``render_scene.py`` (scene
workspaces, same browser through the scene host) and the graphics build
(``languages/_gl_common`` via ``gl_render``) all repeat the same four chores:
resolve the output directory, turn ``ViewPreset``s into the driver's camera
JSON, and build the labelled contact sheet with the settings' grid.  They live here once so the flavours differ only in
what they actually drive.

Nothing in here talks to node — see ``spatial.node.run_node`` (objects) and
``spatial.render_scene.run_scene_script`` (scenes) for that.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from codeverse.config import get_settings
from codeverse.conventions import ViewPreset
from codeverse.spatial.sheet import contact_sheet


def out_directory(out_dir: Path | str, *, clean: Sequence[str] = ()) -> Path:
    """Resolve + create ``out_dir``; ``clean`` names side-cars of a previous run
    that must never be read as if they belonged to this one."""
    d = Path(out_dir).resolve()
    d.mkdir(parents=True, exist_ok=True)
    for name in clean:
        (d / name).unlink(missing_ok=True)
    return d


def view_specs(views: Sequence[ViewPreset]) -> list[dict[str, Any]]:
    """``ViewPreset``s → the ``[{name, azimuth, elevation}]`` payload every
    renderer driver takes (render_glb ``--views``, render_scene ``--orbit-views``)."""
    return [{"name": v.name, "azimuth": float(v.azimuth_deg), "elevation": float(v.elevation_deg)} for v in views]


def build_sheet(images: Sequence[tuple[str, str | Path]], out: Path | str) -> str | None:
    """Labelled contact sheet at the configured grid (``settings.render.sheet_cols``
    / ``sheet_tile``); ``None`` when there is nothing to show."""
    if not images:
        return None
    settings = get_settings()
    return str(contact_sheet(list(images), Path(out), cols=settings.render.sheet_cols, tile=settings.render.sheet_tile))

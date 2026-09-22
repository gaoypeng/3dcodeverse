"""Tracks: static_object · articulated_object · scene · graphics.

``get_track(Track.X)`` builds the pipeline; ``TrackPipeline`` is the protocol the
orchestrator drives.  (Formerly ``tracks/base.py`` — folded into the package root
2026-08-28: its only importer was this ``__init__``.)
"""

from __future__ import annotations

from typing import Any, Protocol

from codeverse3d.contracts.common import Track
from codeverse3d.contracts.run import RunRecord
from codeverse3d.contracts.spec import Spec
from codeverse3d.workspace import Workspace


class TrackPipeline(Protocol):
    track: Track
    rubric: str  # judges/rubrics/<rubric>.yaml

    def run(self, spec: Spec, ws: Workspace, *, resume: bool = False, force: bool = False) -> RunRecord:
        """Full pipeline: plan → baseline → rounds → finalise.  Must be resumable;
        ``force`` re-plans a resumed run whose spec changed (archives the old rounds)."""
        ...


def get_track(track: Track | str, **options: Any) -> TrackPipeline:
    """Track instance for ``track``.  ``options`` are forwarded to the track
    constructor (e.g. ``n_candidates=2`` for a best-of-N baseline, ``policy=``)."""
    t = Track(track)
    if t is Track.STATIC_OBJECT:
        from codeverse3d.tracks.static_object import StaticObjectTrack

        return StaticObjectTrack(**options)
    if t is Track.ARTICULATED_OBJECT:
        from codeverse3d.tracks.articulated_object import ArticulatedObjectTrack

        return ArticulatedObjectTrack(**options)
    if t is Track.GRAPHICS:
        from codeverse3d.tracks.graphics import GraphicsTrack

        return GraphicsTrack(**options)
    from codeverse3d.tracks.scene import SceneTrack

    return SceneTrack(**options)


__all__ = ["TrackPipeline", "get_track"]

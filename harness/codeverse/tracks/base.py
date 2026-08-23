"""Track protocol: what every pipeline must provide to the orchestrator."""

from __future__ import annotations

from typing import Any, Protocol

from codeverse.contracts.common import Track
from codeverse.contracts.plan import Plan
from codeverse.contracts.run import RunRecord
from codeverse.contracts.spec import Spec
from codeverse.workspace import Workspace


class TrackPipeline(Protocol):
    track: Track
    rubric: str  # judges/rubrics/<rubric>.yaml

    def plan(self, spec: Spec, ws: Workspace) -> Plan:
        """Planner model call(s) → validated Plan (re-asked on validation errors)."""
        ...

    def run(self, spec: Spec, ws: Workspace, *, resume: bool = False) -> RunRecord:
        """Full pipeline: plan → baseline → rounds → finalise.  Must be resumable."""
        ...


def get_track(track: Track | str, **options: Any) -> TrackPipeline:
    """Track instance for ``track``.  ``options`` are forwarded to the track
    constructor (e.g. ``n_candidates=2`` for a best-of-N baseline, ``policy=``)."""
    t = Track(track)
    if t is Track.STATIC_OBJECT:
        from codeverse.tracks.static_object import StaticObjectTrack

        return StaticObjectTrack(**options)
    if t is Track.ARTICULATED_OBJECT:
        from codeverse.tracks.articulated_object import ArticulatedObjectTrack

        return ArticulatedObjectTrack(**options)
    if t is Track.GRAPHICS:
        from codeverse.tracks.graphics import GraphicsTrack

        return GraphicsTrack(**options)
    from codeverse.tracks.scene import SceneTrack

    return SceneTrack(**options)

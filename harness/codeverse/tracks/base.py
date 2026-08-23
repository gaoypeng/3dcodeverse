"""Track protocol: what every pipeline must provide to the orchestrator."""

from __future__ import annotations

from typing import Protocol

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


def get_track(track: Track | str) -> TrackPipeline:
    t = Track(track)
    if t is Track.STATIC_OBJECT:
        from codeverse.tracks.static_object import StaticObjectTrack

        return StaticObjectTrack()
    if t is Track.ARTICULATED_OBJECT:
        from codeverse.tracks.articulated_object import ArticulatedObjectTrack

        return ArticulatedObjectTrack()
    from codeverse.tracks.scene import SceneTrack

    return SceneTrack()

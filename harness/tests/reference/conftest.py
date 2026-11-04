"""Fixtures for the reference package (chat: the shared FakeChatModel)."""

from __future__ import annotations

from typing import Any

from codeverse3d.contracts.common import Language, Track
from codeverse3d.contracts.spec import Constraints, Spec


def make_spec(**kw: Any) -> Spec:
    base: dict[str, Any] = dict(
        id="t1", track=Track.STATIC_OBJECT, language=Language.BLENDER,
        prompt="a hand-crank coffee grinder with a square wooden box body and a cast-iron cone hopper",
        constraints=Constraints(dimensions_m={"width": 0.14, "depth": 0.14, "height": 0.30},
                                ),
    )
    base.update(kw)
    return Spec(**base)



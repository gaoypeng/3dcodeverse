"""Fixtures for the reference package (chat: the shared FakeChatModel)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from codeverse3d.contracts.common import Language, Track
from codeverse3d.contracts.spec import Constraints, Spec


def make_spec(**kw: Any) -> Spec:
    base: dict[str, Any] = dict(
        id="t1", track=Track.STATIC_OBJECT, language=Language.BLENDER,
        prompt="a hand-crank coffee grinder with a square wooden box body and a cast-iron cone hopper",
        constraints=Constraints(dimensions_m={"width": 0.14, "depth": 0.14, "height": 0.30},
                                must_have=["a crank handle", "a front drawer"]),
    )
    base.update(kw)
    return Spec(**base)


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    d = tmp_path / "cache"
    d.mkdir()
    return d

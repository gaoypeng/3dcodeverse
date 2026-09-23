"""Settings.backends() — one source of backend-role defaults."""

from __future__ import annotations

import pytest

from codeverse3d.config import Settings
from codeverse3d.contracts.common import Backends


def test_backends_keyword_overrides_win_only_when_truthy_and_name_a_role():
    b = Settings().backends(judge="gemini:custom", planner=None, generator="")
    assert b.judge == "gemini:custom"
    assert (b.planner, b.generator) == (Backends().planner, Backends().generator)
    with pytest.raises(TypeError, match="unknown backend role"):
        Settings().backends(judger="x")

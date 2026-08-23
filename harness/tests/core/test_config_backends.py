"""Settings.backends() — one source of backend-role defaults (Batch-1)."""

from __future__ import annotations

import pytest

from codeverse.config import Settings
from codeverse.contracts.common import Backends


def test_settings_defaults_mirror_backends():
    s = Settings()
    assert s.backends() == Backends()
    assert s.default_planner == Backends().planner
    assert s.default_generator == Backends().generator
    assert s.default_judge == Backends().judge
    assert s.default_captioner == Backends().captioner


def test_settings_overrides_flow_through():
    s = Settings(default_generator="single-shot:gemini:gemini-3.7-flash", default_captioner="gemini:x")
    b = s.backends()
    assert b.generator == "single-shot:gemini:gemini-3.7-flash"
    assert b.captioner == "gemini:x"
    assert b.planner == Backends().planner


def test_backends_keyword_overrides_win_when_truthy():
    s = Settings()
    b = s.backends(judge="gemini:custom", planner=None, generator="")
    assert b.judge == "gemini:custom"
    assert b.planner == Backends().planner  # None falls through
    assert b.generator == Backends().generator  # "" falls through


def test_backends_rejects_unknown_role():
    with pytest.raises(TypeError, match="unknown backend role"):
        Settings().backends(judger="x")


def test_env_override_still_wins(monkeypatch):
    monkeypatch.setenv("CV3D_DEFAULT_CAPTIONER", "gemini:from-env")
    assert Settings().backends().captioner == "gemini:from-env"

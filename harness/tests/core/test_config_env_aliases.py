"""One switch grammar, the flat spellings, and layered configuration."""

from __future__ import annotations

import logging

import pytest

from codeverse3d.config import Settings


def test_every_switch_speaks_one_grammar(monkeypatch, caplog):
    """on/off, 1/0, true/false, yes/no in any case and padding; empty is unset; else warn (D44 c)."""
    env, default = "C3D_PLAN_BRIEF", True   # a top-level Flag (the flat spellings left are an int and a str, D107)
    def read(raw: str | None) -> bool:
        if raw is None:
            monkeypatch.delenv(env, raising=False)
        else:
            monkeypatch.setenv(env, raw)
        s = Settings()
        return s.plan_brief

    assert read(None) is default and read("") is default
    for word in ("off", "0", "false", "no", "OFF", " Off "):
        assert read(word) is False, word
    for word in ("on", "1", "true", "yes", "ON"):
        assert read(word) is True, word
    with caplog.at_level(logging.WARNING, logger="codeverse3d.config"):
        assert read("maybe") is default
    assert env in caplog.text


# --------------------------------------------------------------------------- yaml layering


def test_runtime_js_dir_override_and_loud_failure(tmp_path, monkeypatch):
    """C3D_RUNTIME_JS supports relocation and rejects a missing directory."""

    good = tmp_path / "runtime_js"
    good.mkdir()
    monkeypatch.setenv("C3D_RUNTIME_JS", str(good))
    assert Settings().runtime_js_dir() == good
    monkeypatch.setenv("C3D_RUNTIME_JS", str(tmp_path / "nowhere"))
    with pytest.raises(RuntimeError, match="C3D_RUNTIME_JS"):
        Settings().runtime_js_dir()
    monkeypatch.delenv("C3D_RUNTIME_JS")
    assert Settings().runtime_js_dir().name == "runtime_js"   # editable checkout resolves

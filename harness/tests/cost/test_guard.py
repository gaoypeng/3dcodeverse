"""The prompt-size estimate."""

from __future__ import annotations

from codeverse3d.cost.guard import text_tokens


def test_text_tokens():
    assert text_tokens("x" * 4000) == 1000
    assert text_tokens(["ab" * 2, "cd" * 2]) == 2

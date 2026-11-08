"""Prompt-size estimate (~4 chars/token, +-15%) for the skills' token accounting."""

from __future__ import annotations

from collections.abc import Iterable

CHARS_PER_TOKEN = 4.0


def text_tokens(text: str | Iterable[str]) -> int:
    """Estimated prompt tokens for one string or a bag of blocks."""
    if isinstance(text, str):
        return int(len(text) / CHARS_PER_TOKEN + 0.5)
    return sum(text_tokens(t) for t in text)

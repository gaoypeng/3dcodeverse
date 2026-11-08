"""Lenient JSON extraction from model text (the judge subagent's reply)."""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE_RE = re.compile(r"```(?:json|JSON|javascript|js)?\s*\n?(.*?)```", re.DOTALL)


class JsonParseError(ValueError):
    """The model's text does not contain a parseable JSON value."""


def _first_balanced(text: str, opener: str = "{", closer: str = "}") -> str | None:
    """The first depth-balanced ``{...}`` span (naive: braces inside strings can fool it)."""
    start = text.find(opener)
    if start < 0:
        return None
    depth = 0
    for i, ch in enumerate(text[start:], start):
        if ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


#: C0 control characters except TAB, LF and CR
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
#: replaced by a space, not "": deleting could fuse adjacent numbers
_CONTROL_SUB = " "


def strip_control_chars(value: Any) -> Any:
    """Replace C0 control characters in every string of a parsed JSON value (a NUL later
    crashes ``subprocess.Popen`` far from its source)."""
    if isinstance(value, str):
        return _CONTROL_RE.sub(_CONTROL_SUB, value)
    if isinstance(value, dict):
        return {k: strip_control_chars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [strip_control_chars(v) for v in value]
    return value


def parse_json_lenient(text: str) -> Any:
    """Parse JSON from model text tolerating fences, leading prose and trailing chatter."""
    if text is None:
        raise JsonParseError("empty response")
    s = text.strip()
    if not s:
        raise JsonParseError("empty response")
    candidates: list[str] = [s]
    for m in _FENCE_RE.finditer(s):
        candidates.append(m.group(1).strip())
    balanced = _first_balanced(s)
    if balanced is not None:
        candidates.append(balanced)
    for opener, closer in (("{", "}"), ("[", "]")):
        i, j = s.find(opener), s.rfind(closer)
        if i != -1 and j > i:
            candidates.append(s[i : j + 1])
    last_err: Exception | None = None
    for cand in candidates:
        try:
            return strip_control_chars(json.loads(cand))
        except json.JSONDecodeError as exc:
            last_err = exc
            fixed = re.sub(r",\s*([}\]])", r"\1", cand)
            if fixed != cand:
                try:
                    return strip_control_chars(json.loads(fixed))
                except json.JSONDecodeError:
                    pass
    raise JsonParseError(f"no JSON value found: {last_err} — head={s[:120]!r}")

"""Split prompt markdown into heading-delimited sections.

Prompt material, so it lives with the prompts.  It was in ``spatial/cookbook_tool.py``
next to a ``read_cookbook`` MCP tool that served one chapter at a time — a tool the repo
measured at 0 calls in 16 zone sessions (cli/skills_cmd.py:4) and 0 across every recorded
run, and which had nothing left to do once the whole cookbook started reaching the model
in the prompt (2026-08-28).  The tool is gone; the parser stays, because naming a chapter
is how a STAGE says which recipes it needs — scene's env/zone recipes, graphics' recipe
seeding, repair's error-matched section.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_HEADING = re.compile(r"^(#{1,3})\s+(.+?)\s*$", re.MULTILINE)
_FENCE = re.compile(r"^(`{3,}|~{3,}).*?^\1[ \t]*$", re.MULTILINE | re.DOTALL)


@dataclass
class Section:
    level: int
    title: str
    body: str  # heading line included


def _heading_matches(md: str) -> list[re.Match[str]]:
    """Headings outside fenced code blocks (``# comment`` lines in snippets are not headings)."""
    out: list[re.Match[str]] = []
    fence_spans: list[tuple[int, int]] = []
    for m in _FENCE.finditer(md):
        fence_spans.append((m.start(), m.end()))
    for m in _HEADING.finditer(md):
        if not any(a <= m.start() < b for a, b in fence_spans):
            out.append(m)
    return out


def split_sections(md: str) -> list[Section]:
    """Split markdown into heading-delimited sections (text before the first heading is 'preamble')."""
    matches = _heading_matches(md)
    if not matches:
        return [Section(0, "preamble", md)]
    out: list[Section] = []
    if matches[0].start() > 0 and md[: matches[0].start()].strip():
        out.append(Section(0, "preamble", md[: matches[0].start()]))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(md)
        out.append(Section(len(m.group(1)), m.group(2), md[m.start(): end]))
    return out


def find_section(sections: list[Section], query: str) -> Section | None:
    """Case-insensitive match: exact title first, then substring, then word overlap."""
    q = query.strip().lower()
    if not q:
        return None
    for s in sections:
        if s.title.lower() == q:
            return s
    for s in sections:
        if q in s.title.lower():
            return s
    words = set(re.findall(r"[a-z0-9]+", q))
    best, best_n = None, 0
    for s in sections:
        n = len(words & set(re.findall(r"[a-z0-9]+", s.title.lower())))
        if n > best_n:
            best, best_n = s, n
    return best

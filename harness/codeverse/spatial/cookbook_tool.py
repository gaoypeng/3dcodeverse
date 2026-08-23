"""``read_cookbook`` tool: serve the language cookbook (or a section of it).

Source order: ``prompts/<language>/cookbook.md`` (via ``codeverse.prompts.load_text``)
→ the runtime's ``contract_doc()`` → a clear "no cookbook" message.  Sections
are markdown headings (``#``…``###``); matching is case-insensitive substring.
Output is capped at ``MAX_CHARS`` and always lists the available sections.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import BaseModel, Field

from codeverse.spatial.observe import truncate
from codeverse.spatial.registry import Observation, ToolContext, tool
from codeverse.spatial.tool_common import ToolUnavailable, language_of, lazy

MAX_CHARS = 6000
_HEADING = re.compile(r"^(#{1,3})\s+(.+?)\s*$", re.MULTILINE)
_FENCE = re.compile(r"^(`{3,}|~{3,}).*?^\1[ \t]*$", re.MULTILINE | re.DOTALL)

#: prompts/<dir> per language id (urdf_blender's docs live under prompts/urdf)
_PROMPT_DIR = {"urdf_blender": "urdf"}


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


def load_cookbook(language: str) -> tuple[str, str]:
    """(markdown, source) for ``language``; raises ToolUnavailable when nothing exists."""
    rel = f"{_PROMPT_DIR.get(language, language)}/cookbook.md"
    try:
        load_text = lazy("codeverse.prompts", "load_text")
        return load_text(rel), f"prompts/{rel}"
    except (ToolUnavailable, FileNotFoundError):
        pass
    try:
        get_runtime = lazy("codeverse.languages", "get_runtime")
        doc = get_runtime(language).contract_doc()
        if doc and doc.strip():
            return doc, f"{language} runtime contract_doc()"
    except Exception as e:  # runtime missing / contract missing
        raise ToolUnavailable(f"no cookbook for {language}: {type(e).__name__}: {e}") from e
    raise ToolUnavailable(f"no cookbook or contract doc for {language}")


class ReadCookbookArgs(BaseModel):
    section: str = Field(default="", description="heading to return (case-insensitive substring); empty = whole cookbook (truncated)")


@tool("read_cookbook", ReadCookbookArgs, "Read the language cookbook (copyable snippets, pitfalls, export contract) — whole or one section.")
def read_cookbook(ctx: ToolContext, args: ReadCookbookArgs) -> Observation:
    language = language_of(ctx)
    try:
        md, source = load_cookbook(language)
    except ToolUnavailable as e:
        return Observation.error(f"read_cookbook: {e}")
    sections = split_sections(md)
    titles = [s.title for s in sections if s.level > 0]
    listing = "sections: " + (" | ".join(titles) if titles else "(no headings)")
    if args.section:
        sec = find_section(sections, args.section)
        if sec is None:
            return Observation.error(f"no section matching {args.section!r} in {source}\n{listing}",
                                     available=titles)
        body = sec.body
        head = f"[{source} › {sec.title}]"
    else:
        body = md
        head = f"[{source}]"
    text = f"{head}\n{truncate(body, MAX_CHARS, tail_ratio=0.1)}\n\n{listing}"
    return Observation(ok=True, text=text, numbers={"source": source, "chars": len(body), "sections": titles[:60],
                                                   "truncated": len(body) > MAX_CHARS})

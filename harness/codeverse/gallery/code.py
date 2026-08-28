"""Reading a run's code in the browser: directory listings + a file viewer.

No external JS and no highlighter dependency — the "syntax-highlighted-ish"
look is one tokeniser over comments / strings / numbers / keywords, applied only
to the languages the harness actually generates.  Everything else is a plain
``<pre>`` with line numbers, which is what makes this safe to point at any file
in a run directory.
"""

from __future__ import annotations

import re
from pathlib import Path

from codeverse.gallery.model import RunEntry
from codeverse.gallery.theme import esc, footer, page_shell, top_bar
from codeverse.gallery.urls import UrlMaker, safe_join

MAX_VIEW_BYTES = 512 * 1024
HIGHLIGHT_SUFFIXES = {".py", ".js", ".mjs", ".cjs", ".frag", ".vert", ".glsl", ".json"}
TEXT_SUFFIXES = HIGHLIGHT_SUFFIXES | {".md", ".txt", ".urdf", ".xml", ".yaml", ".yml", ".jsonl", ".log",
                                      ".cfg", ".ini", ".toml", ".csv", ".html", ".css", ".sh"}

_KEYWORDS = (
    "def|class|import|from|as|return|yield|pass|raise|try|except|finally|with|lambda|global|nonlocal|assert|del|"
    "if|elif|else|for|while|break|continue|in|is|not|and|or|None|True|False|self|await|async|"
    "const|let|var|function|export|default|new|this|typeof|instanceof|extends|super|"
    "uniform|varying|attribute|precision|highp|mediump|lowp|out|inout|discard|"
    "void|bool|int|float|double|vec2|vec3|vec4|ivec2|ivec3|mat2|mat3|mat4|sampler2D|samplerCube|struct|null|true|false"
)
_TOKENS = re.compile(
    r"(?P<comment>//[^\n]*|#[^\n]*|/\*.*?\*/)"
    r"|(?P<str>\"\"\".*?\"\"\"|'''.*?'''|\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*')"
    r"|(?P<num>\b\d+\.?\d*(?:[eE][-+]?\d+)?\b)"
    rf"|(?P<kw>\b(?:{_KEYWORDS})\b)",
    re.S,
)

CODE_CSS = """
.code-css .cm{color:var(--fg-3);font-style:italic}
.code-css .st{color:var(--ok)}
.code-css .nu{color:var(--warn)}
.code-css .kw{color:var(--accent);font-weight:600}
.filelist{list-style:none;margin:0;padding:0}
.filelist li{display:flex;gap:var(--s-3);padding:5px 0;border-bottom:1px solid var(--line);font-size:var(--fs-sm)}
.filelist li a{font-family:var(--mono);overflow-wrap:anywhere}
.filelist .sz{margin-left:auto;color:var(--fg-3);font-variant-numeric:tabular-nums;white-space:nowrap}
"""


def highlight(text: str, *, suffix: str = "") -> str:
    """Escaped HTML for ``text``; light token colouring for known code suffixes."""
    if suffix.lower() not in HIGHLIGHT_SUFFIXES:
        return esc(text)
    out: list[str] = []
    pos = 0
    for m in _TOKENS.finditer(text):
        out.append(esc(text[pos:m.start()]))
        cls = {"comment": "cm", "str": "st", "num": "nu", "kw": "kw"}[m.lastgroup or "kw"]
        out.append(f"<span class='{cls}'>{esc(m.group())}</span>")
        pos = m.end()
    out.append(esc(text[pos:]))
    return "".join(out)


def numbered(text: str, *, suffix: str = "") -> str:
    """``<pre>`` with a line-number gutter (selectable code, unselectable numbers)."""
    lines = highlight(text, suffix=suffix).split("\n")
    body = "\n".join(f"<span class='ln'>{i}</span>{line}" for i, line in enumerate(lines, 1))
    return f"<pre class='code code-css'>{body}</pre>"


def is_text(path: Path) -> bool:
    return path.suffix.lower() in TEXT_SUFFIXES


def read_text(path: Path) -> tuple[str, str]:
    """``(text, note)`` — truncates huge files and never raises for binary content."""
    try:
        data = path.read_bytes()
    except OSError as e:
        return "", f"unreadable: {e}"
    note = ""
    if len(data) > MAX_VIEW_BYTES:
        data, note = data[:MAX_VIEW_BYTES], f"truncated to {MAX_VIEW_BYTES // 1024} KB of {len(data) // 1024} KB"
    try:
        return data.decode("utf-8"), note
    except UnicodeDecodeError:
        return data.decode("utf-8", "replace"), (note + "; " if note else "") + "not valid UTF-8 (replaced)"


def _crumbs(entry: RunEntry, urls: UrlMaker, rel: str) -> str:
    parts = ["<a href='/'>gallery</a>", f"<a href='{esc(urls.detail(entry))}'>{esc(entry.slug)}</a>"]
    walked = ""
    for piece in [p for p in rel.split("/") if p]:
        walked = f"{walked}/{piece}" if walked else piece
        parts.append(f"<a href='{esc(urls.code(entry, walked))}'>{esc(piece)}</a>")
    return " <span class='faint'>/</span> ".join(parts)


def render_dir(entry: RunEntry, urls: UrlMaker, rel: str) -> str:
    """A directory listing inside a run (``src/``, ``artifacts/frames/`` …)."""
    target = safe_join(entry.path, rel)
    if not target.is_dir():
        raise FileNotFoundError(f"not a directory: {rel}")
    rows = []
    for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        child_rel = f"{rel}/{child.name}".strip("/") if rel else child.name
        if child.is_dir():
            rows.append(f"<li><a href='{esc(urls.dir(entry, child_rel))}'>{esc(child.name)}/</a>"
                        f"<span class='sz'>dir</span></li>")
        else:
            href = urls.code(entry, child_rel) if is_text(child) else urls.file(entry, child_rel)
            try:
                size = child.stat().st_size
            except OSError:
                size = 0
            rows.append(f"<li><a href='{esc(href)}'>{esc(child.name)}</a>"
                        f"<a class='sz' href='{esc(urls.file(entry, child_rel))}'>{size:,} B · raw</a></li>")
    listing = "".join(rows) or "<li class='faint'>empty</li>"
    body = (top_bar("3dcv gallery", entry.slug, crumbs=_crumbs(entry, urls, rel))
            + f"<main class='wrap'><div class='panel'><h2>{esc(rel or '.')}</h2>"
              f"<ul class='filelist'>{listing}</ul></div></main>"
            + footer(entry.path))
    return page_shell(f"{rel or '/'} — {entry.slug}", body, extra_css=CODE_CSS)


def render_file(entry: RunEntry, urls: UrlMaker, rel: str) -> str:
    """One file as a readable page (a directory redirects to the listing)."""
    target = safe_join(entry.path, rel)
    if target.is_dir():
        return render_dir(entry, urls, rel)
    if not target.is_file():
        raise FileNotFoundError(f"not a file: {rel}")
    if not is_text(target):
        body = (top_bar("3dcv gallery", entry.slug, crumbs=_crumbs(entry, urls, rel))
                + f"<main class='wrap'><div class='panel'><h2>{esc(rel)}</h2>"
                  f"<p class='muted small'>binary file — "
                  f"<a href='{esc(urls.file(entry, rel))}'>open the raw bytes</a></p></div></main>"
                + footer(str(target)))
        return page_shell(f"{rel} — {entry.slug}", body, extra_css=CODE_CSS)
    text, note = read_text(target)
    head = (f"<h2>{esc(rel)}</h2><p class='small muted'>{len(text.splitlines())} lines · "
            f"<a href='{esc(urls.file(entry, rel))}'>raw</a>"
            + (f" · <span class='pill-warn'>{esc(note)}</span>" if note else "") + "</p>")
    body = (top_bar("3dcv gallery", entry.slug, crumbs=_crumbs(entry, urls, rel))
            + f"<main class='wrap'><div class='panel'>{head}{numbered(text, suffix=target.suffix)}</div></main>"
            + footer(str(target)))
    return page_shell(f"{rel} — {entry.slug}", body, extra_css=CODE_CSS)


def src_files(run_dir: Path | str, limit: int = 200) -> list[tuple[str, int]]:
    """``(run-relative path, size)`` for every file under ``src/`` (sorted, capped)."""
    root = Path(run_dir) / "src"
    if not root.is_dir():
        return []
    out: list[tuple[str, int]] = []
    for p in sorted(root.rglob("*")):
        if p.is_file() and ".git" not in p.parts and "node_modules" not in p.parts:
            try:
                out.append((p.relative_to(run_dir).as_posix(), p.stat().st_size))
            except OSError:
                continue
        if len(out) >= limit:
            break
    return out

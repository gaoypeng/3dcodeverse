"""``3dcv gallery build`` — the shareable single-file form of the same page.

Identical markup to the served gallery; only the URL maker differs.  Without
``--embed`` the ``<img>`` tags point at the run directories with ``file://``
(fast, tiny file, works on this machine); with ``--embed`` every contact sheet is
inlined as a downscaled JPEG so the page can be copied or published — at the
price of a much bigger file.  The write is atomic: a bench reading the previous
gallery never sees a half-written one.
"""

from __future__ import annotations

from pathlib import Path

from codeverse.gallery.index import build_index
from codeverse.gallery.model import GalleryIndex
from codeverse.gallery.page import render_index
from codeverse.gallery.urls import THUMB_PX, StaticUrls
from codeverse.proc import write_text_atomic


def render_static(index: GalleryIndex, *, title: str = "3dcv gallery", embed: bool = False,
                  thumb_px: int = THUMB_PX, sort: str = "score", view: str = "cards",
                  extra_html: str = "") -> str:
    """The complete HTML document for ``index`` (no server involved)."""
    note = ("images are inlined; the links open the original run directories on this machine"
            if embed else "images and links point at the run directories with file:// — "
                          "use `3dcv gallery serve` for a page that works anywhere")
    return render_index(index, StaticUrls(embed=embed, thumb_px=thumb_px),
                        title=title, sort=sort, view=view, note=note, extra_html=extra_html)


def write_atomic(path: Path, text: str) -> Path:
    """tmp + rename, so a reader never sees a partial gallery."""
    return write_text_atomic(path, text)


def build_static(roots: list[Path] | list[str], out_html: Path | str, *, title: str | None = None,
                 embed: bool = False, thumb_px: int = THUMB_PX) -> tuple[Path, int, GalleryIndex]:
    """Scan ``roots`` and write one self-contained page; ``(path, n_runs, index)``."""
    index = build_index(roots)
    label = title or ("3dcv gallery — " + ", ".join(s.label for s in index.sections[:4])
                      + ("…" if len(index.sections) > 4 else ""))
    html = render_static(index, title=label, embed=embed, thumb_px=thumb_px)
    return write_atomic(Path(out_html), html), len(index.entries()), index

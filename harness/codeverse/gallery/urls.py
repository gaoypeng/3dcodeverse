"""How a run-relative path becomes an href.

The page renderers never build URLs themselves — they ask a :class:`UrlMaker`.
That is the whole difference between the served gallery (real routes, images
fetched lazily over HTTP) and the static single-file build (``file://`` links,
images optionally inlined as ``data:`` URIs), so the HTML is written once.
"""

from __future__ import annotations

import base64
import io
import mimetypes
from pathlib import Path
from urllib.parse import quote

from codeverse.gallery.model import RunEntry, RunLink

THUMB_PX = 720
JPEG_QUALITY = 78


class UrlMaker:
    """Server-side URLs (the default): every route is handled by ``gallery.server``."""

    #: does this target have per-run detail pages?
    has_detail = True

    def detail(self, entry: RunEntry) -> str:
        return f"/run/{quote(entry.battery)}/{quote(entry.slug)}"

    def file(self, entry: RunEntry, rel: str) -> str:
        return f"/file/{quote(entry.battery)}/{quote(entry.slug)}/{quote(rel)}"

    def dir(self, entry: RunEntry, rel: str = "") -> str:
        return f"/dir/{quote(entry.battery)}/{quote(entry.slug)}/{quote(rel)}"

    def code(self, entry: RunEntry, rel: str) -> str:
        return f"/code/{quote(entry.battery)}/{quote(entry.slug)}/{quote(rel)}"

    def viewer(self, entry: RunEntry, rel: str) -> str:
        return f"/viewer/{quote(entry.battery)}/{quote(entry.slug)}/{quote(rel)}"

    def img(self, entry: RunEntry, rel: str) -> str:
        """``src`` for an ``<img>``: a URL here, a ``data:`` URI in embed mode."""
        return self.file(entry, rel)

    def link(self, entry: RunEntry, link: RunLink) -> str:
        return {"dir": self.dir, "code": self.code, "viewer": self.viewer}.get(link.kind, self.file)(entry, link.rel)


class StaticUrls(UrlMaker):
    """``file://`` targets for the shareable single-file build.

    ``embed=True`` inlines every contact sheet as a downscaled JPEG ``data:`` URI
    so the page survives being copied off this machine; the links still point at
    the original run directories (they work on the box that produced them)."""

    has_detail = False

    def __init__(self, *, embed: bool = False, thumb_px: int = THUMB_PX) -> None:
        self.embed = embed
        self.thumb_px = thumb_px

    def _uri(self, entry: RunEntry, rel: str) -> str:
        p = Path(entry.path) / rel if rel else Path(entry.path)
        return p.as_uri() if p.is_absolute() else quote(str(p))

    def detail(self, entry: RunEntry) -> str:
        return self._uri(entry, "")

    def file(self, entry: RunEntry, rel: str) -> str:
        return self._uri(entry, rel)

    def dir(self, entry: RunEntry, rel: str = "") -> str:
        return self._uri(entry, rel)

    def code(self, entry: RunEntry, rel: str) -> str:
        return self._uri(entry, rel)

    def viewer(self, entry: RunEntry, rel: str) -> str:
        return self._uri(entry, rel)

    def img(self, entry: RunEntry, rel: str) -> str:
        if not self.embed:
            return self._uri(entry, rel)
        return thumbnail_data_uri(Path(entry.path) / rel, max_px=self.thumb_px) or self._uri(entry, rel)


def thumbnail_data_uri(path: Path | str, *, max_px: int = THUMB_PX) -> str:
    """Downscaled JPEG as a ``data:`` URI; ``""`` when the image cannot be read."""
    try:
        from PIL import Image

        with Image.open(path) as im:
            im = im.convert("RGB")
            im.thumbnail((max_px, max_px))
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    except Exception:  # noqa: BLE001 - missing PIL, unreadable/half-written file
        return ""
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


#: extensions the harness writes that the stdlib does not type correctly (or at all)
CONTENT_TYPES: dict[str, str] = {
    ".glb": "model/gltf-binary",
    ".gltf": "model/gltf+json",
    ".stl": "model/stl",
    ".step": "model/step",
    ".stp": "model/step",
    ".obj": "text/plain; charset=utf-8",
    ".urdf": "text/plain; charset=utf-8",
    ".py": "text/plain; charset=utf-8",
    ".js": "text/plain; charset=utf-8",
    ".mjs": "text/plain; charset=utf-8",
    ".cjs": "text/plain; charset=utf-8",
    ".frag": "text/plain; charset=utf-8",
    ".vert": "text/plain; charset=utf-8",
    ".glsl": "text/plain; charset=utf-8",
    ".md": "text/plain; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".log": "text/plain; charset=utf-8",
    ".yaml": "text/plain; charset=utf-8",
    ".yml": "text/plain; charset=utf-8",
    ".csv": "text/plain; charset=utf-8",
    ".jsonl": "text/plain; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".blend": "application/octet-stream",
}


def content_type(path: Path | str) -> str:
    """Content type for a run artifact.  Unknown text-ish files fall back to
    ``text/plain`` (a browser must never be asked to *download* source code),
    everything else to ``application/octet-stream``."""
    suffix = Path(path).suffix.lower()
    if suffix in CONTENT_TYPES:
        return CONTENT_TYPES[suffix]
    guessed, _ = mimetypes.guess_type(str(path))
    if guessed is None:
        return "application/octet-stream"
    if guessed.startswith("text/") and "charset" not in guessed:
        return f"{guessed}; charset=utf-8"
    return guessed

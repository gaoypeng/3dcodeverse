"""How a run-relative path becomes an href."""

from __future__ import annotations

import base64
import io
import mimetypes
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from codeverse3d.addons.gallery.model import RunEntry, RunLink

THUMB_PX = 720
JPEG_QUALITY = 78


class UrlMaker:

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
        return self.file(entry, rel)

    def link(self, entry: RunEntry, link: RunLink) -> str:
        return {"dir": self.dir, "code": self.code, "viewer": self.viewer}.get(link.kind, self.file)(entry, link.rel)


class StaticUrls(UrlMaker):

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
    except Exception:  # noqa: BLE001
        return ""
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


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
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
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
    suffix = Path(path).suffix.lower()
    if suffix in CONTENT_TYPES:
        return CONTENT_TYPES[suffix]
    guessed, _ = mimetypes.guess_type(str(path))
    if guessed is None:
        return "application/octet-stream"
    if guessed.startswith("text/") and "charset" not in guessed:
        return f"{guessed}; charset=utf-8"
    return guessed


# ===================================================================== paths
class PathError(ValueError):
    """A requested path is outside the run directory (or is not usable)."""


def safe_join(root: Path | str, rel: str) -> Path:
    base = Path(root).resolve()
    rel = (rel or "").strip()
    if "\x00" in rel:
        raise PathError("null byte in path")
    if rel.startswith(("/", "\\")) or (len(rel) > 1 and rel[1] == ":"):
        raise PathError(f"absolute path refused: {rel!r}")
    parts = [p for p in PurePosixPath(rel.replace("\\", "/")).parts if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise PathError(f"parent traversal refused: {rel!r}")
    target = base.joinpath(*parts)
    try:
        resolved = target.resolve()
    except OSError as e:
        raise PathError(f"unresolvable path: {rel!r} ({e})") from e
    if resolved != base and base not in resolved.parents:
        raise PathError(f"path escapes the run directory: {rel!r}")
    return resolved

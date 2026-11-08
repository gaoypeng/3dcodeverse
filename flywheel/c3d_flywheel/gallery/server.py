"""The local gallery server: ``c3d-flywheel gallery serve``."""

from __future__ import annotations

import contextlib
import os
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from c3d_flywheel.gallery import code as code_page
from c3d_flywheel.gallery import viewer as viewer_page
from c3d_flywheel.gallery.compare import MAX_COMPARE, export_csv, parse_keys, render_compare
from c3d_flywheel.gallery.detail import render_broken_detail, render_detail
from c3d_flywheel.gallery.index import build_index, entry_from_record
from c3d_flywheel.gallery.model import FILTER_KEYS, RunEntry, match, sort_entries
from c3d_flywheel.gallery.page import render_index
from c3d_flywheel.gallery.theme import esc, footer, page_shell, top_bar
from c3d_flywheel.gallery.urls import PathError, UrlMaker, content_type, safe_join
from c3d_flywheel.runlayout import RunWorkspace

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
STREAM_CHUNK = 256 * 1024
#: /file/ types a browser would execute: served with CSP sandbox so LLM-authored HTML/SVG never runs same-origin.
SANDBOXED_TYPES = frozenset({"text/html", "image/svg+xml"})


class GalleryError(RuntimeError):
    """The server cannot start (bad host, port in use, no roots)."""


@dataclass
class Response:

    status: int = 200
    content_type: str = "text/html; charset=utf-8"
    body: bytes = b""
    path: Path | None = None
    headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def html(cls, markup: str, status: int = 200) -> Response:
        return cls(status=status, body=markup.encode("utf-8"))

    @classmethod
    def csv(cls, text: str, filename: str = "runs.csv") -> Response:
        return cls(content_type="text/csv; charset=utf-8", body=text.encode("utf-8"),
                   headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def _error_page(status: int, title: str, detail: str) -> Response:
    body = (top_bar("3dcodeverse gallery", "", crumbs="<a href='/'>gallery</a>")
            + f"<main class='wrap'><div class='panel'><h2>{status} — {esc(title)}</h2>"
              f"<p class='muted'>{esc(detail)}</p></div></main>" + footer("3dcodeverse gallery"))
    return Response.html(page_shell(f"{status} {title}", body), status=status)


class GalleryApp:

    def __init__(self, roots: list[Path] | list[str], *, reload: bool = False,
                 title: str = "3dcodeverse gallery", runtime_js: Path | str | None = None) -> None:
        self.roots = [Path(r).resolve() for r in roots]
        self.reload = reload
        self.title = title
        self.runtime_js = runtime_js
        self.urls = UrlMaker()
        self._lock = threading.Lock()
        self.index = build_index(self.roots)

    def refresh(self) -> None:
        if not self.reload:
            return
        with self._lock:
            self.index = build_index(self.roots)

    # ----------------------------------------------------------------- helpers
    def _entry(self, battery: str, slug: str, *, rescan: bool = True) -> RunEntry | None:
        index = self.index
        entry = index.find(battery, slug)
        if entry is None and rescan:
            with self._lock:
                if self.index is index:
                    self.index = build_index(self.roots)
            entry = self.index.find(battery, slug)
        return entry

    def _filters(self, query: dict[str, str]) -> dict[str, str]:
        return {k: query.get(k, "") for k in FILTER_KEYS}

    def selected(self, query: dict[str, str]) -> list[RunEntry]:
        flt = self._filters(query)
        return sort_entries([e for e in self.index.entries() if match(e, flt)], query.get("sort", "score"))

    def picked(self, keys: list[str]) -> tuple[list[RunEntry], list[str]]:
        found, missing, scanned = [], [], False
        for key in keys:
            battery, _, slug = key.partition("/")
            entry = self._entry(battery, slug, rescan=not scanned) if slug else None
            if entry is None:
                missing.append(key)
                scanned = scanned or bool(slug)
            else:
                found.append(entry)
        return found, missing

    def _picked(self, query: dict[str, str]) -> tuple[list[RunEntry], UrlMaker, str]:
        self.refresh()
        keys = parse_keys(query.get("runs", ""))
        notes = []
        if len(keys) > MAX_COMPARE:
            notes.append(f"showing the first {MAX_COMPARE} of {len(keys)} selected runs")
            keys = keys[:MAX_COMPARE]
        entries, missing = self.picked(keys)
        if missing:
            notes.append("unknown: " + ", ".join(missing[:6]))
        return entries, self.urls, " · ".join(notes)

    # ----------------------------------------------------------------- routing
    def route(self, path: str, query: dict[str, str] | None = None) -> Response:
        query = query or {}
        parts = [unquote(p) for p in path.split("/") if p != ""]
        try:
            return self._route(parts, query)
        except PathError as e:
            return _error_page(403, "forbidden", str(e))
        except FileNotFoundError as e:
            return _error_page(404, "not found", str(e))
        except Exception as e:  # noqa: BLE001
            return _error_page(500, "gallery error", f"{type(e).__name__}: {e}")

    def _route(self, parts: list[str], query: dict[str, str]) -> Response:
        if not parts or parts == ["index.html"]:
            self.refresh()
            return Response.html(render_index(
                self.index, self.urls, title=self.title, flt=self._filters(query),
                sort=query.get("sort", "score"), view=query.get("view", "cards")))
        head, rest = parts[0], parts[1:]
        if head == "compare":
            entries, urls, note = self._picked(query)
            return Response.html(render_compare(entries, urls, note=note))
        if head in ("export.csv", "export"):
            entries, _, _note = self._picked(query)
            return Response.csv(export_csv(entries or self.selected(query)))
        if head == "vendor":
            target = viewer_page.vendor_path("/".join(rest), self.runtime_js)
            if target is None:
                raise FileNotFoundError("/".join(rest))
            return Response(content_type=_js_type(target), path=target)
        if head in ("run", "file", "dir", "code", "viewer"):
            if len(rest) < 2:
                raise FileNotFoundError("expected /<battery>/<slug>/…")
            entry = self._entry(rest[0], rest[1])
            if entry is None:
                raise FileNotFoundError(f"unknown run {rest[0]}/{rest[1]}")
            rel = "/".join(rest[2:])
            return self._run_route(head, entry, rel)
        raise FileNotFoundError("/".join(parts))

    def _run_route(self, head: str, entry: RunEntry, rel: str) -> Response:
        if head == "run":
            return self._detail(entry)
        if head == "dir":
            return Response.html(code_page.render_dir(entry, self.urls, rel))
        if head == "code":
            return Response.html(code_page.render_file(entry, self.urls, rel))
        if head == "viewer":
            if not viewer_page.viewer_available(self.runtime_js):
                return _error_page(501, "no viewer",
                                   "runtime_js/node_modules/three is not installed; open the raw GLB instead")
            safe_join(entry.path, rel)
            return Response.html(viewer_page.render_viewer(entry, self.urls, rel))
        target = safe_join(entry.path, rel)
        if target.is_dir():
            return Response.html(code_page.render_dir(entry, self.urls, rel))
        if not target.is_file():
            raise FileNotFoundError(f"{entry.slug}/{rel}")
        ctype = content_type(target)
        headers: dict[str, str] = {}
        if ctype.partition(";")[0].strip() in SANDBOXED_TYPES:
            headers["Content-Security-Policy"] = "sandbox"
        return Response(content_type=ctype, path=target, headers=headers)

    def _detail(self, entry: RunEntry) -> Response:
        from c3d_flywheel.record.record import RecordError, load_record

        ws = RunWorkspace(entry.path)
        try:
            rec = load_record(ws)
        except (RecordError, OSError, ValueError) as e:
            state = entry.state if entry.state != "ok" else "broken"
            broken = entry.model_copy(update={"state": state, "error": str(e)[:400]})
            return Response.html(render_broken_detail(broken, self.urls))

        fresh = entry_from_record(entry.battery, ws, rec, slug=entry.slug)
        prev, nxt = self.neighbours(entry)
        return Response.html(render_detail(fresh, self.urls, ws, rec, prev=prev, nxt=nxt))

    def neighbours(self, entry: RunEntry) -> tuple[RunEntry | None, RunEntry | None]:
        section = next((s for s in self.index.sections if s.label == entry.battery), None)
        if section is None:
            return None, None
        ordered = sort_entries(section.entries, "score")
        keys = [e.key for e in ordered]
        if entry.key not in keys:
            return None, None
        i = keys.index(entry.key)
        return (ordered[i - 1] if i > 0 else None,
                ordered[i + 1] if i + 1 < len(ordered) else None)


def _js_type(path: Path) -> str:
    return "text/javascript; charset=utf-8" if path.suffix in (".js", ".mjs") else content_type(path)


# --------------------------------------------------------------------------- http plumbing
class _Handler(BaseHTTPRequestHandler):
    server_version = "3dcode-gallery"
    protocol_version = "HTTP/1.1"
    app: GalleryApp
    quiet: bool = True

    def do_GET(self) -> None:  # noqa: N802
        self._respond(body=True)

    def do_HEAD(self) -> None:  # noqa: N802
        self._respond(body=False)

    def _respond(self, *, body: bool) -> None:
        split = urlsplit(self.path)
        query = {k: v[0] for k, v in parse_qs(split.query).items()}
        resp = self.app.route(split.path, query)
        handle = None
        length = len(resp.body)
        if resp.path is not None:
            # open first, then fstat THAT descriptor: a file a bench is still writing
            # cannot change length between the header and the body
            try:
                handle = resp.path.open("rb")
                length = os.fstat(handle.fileno()).st_size
            except OSError:
                resp, handle = _error_page(404, "not found", str(resp.path)), None
                length = len(resp.body)
        try:
            self.send_response(resp.status)
            self.send_header("Content-Type", resp.content_type)
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in resp.headers.items():
                self.send_header(k, v)
            self.end_headers()
            if not body:
                return
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                if handle is None:
                    self.wfile.write(resp.body)
                else:
                    self._stream(handle, length)
        finally:
            if handle is not None:
                handle.close()

    def _stream(self, handle: Any, length: int) -> None:
        remaining = length
        while remaining > 0:
            chunk = handle.read(min(STREAM_CHUNK, remaining))
            if not chunk:
                self.wfile.write(b"\0" * remaining)
                return
            self.wfile.write(chunk)
            remaining -= len(chunk)

    def log_message(self, fmt: str, *args: Any) -> None:
        if not self.quiet:
            super().log_message(fmt, *args)


def make_server(app: GalleryApp, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                *, quiet: bool = True) -> ThreadingHTTPServer:
    handler = type("_BoundHandler", (_Handler,), {"app": app, "quiet": quiet})
    try:
        return ThreadingHTTPServer((host, port), handler)  # type: ignore[arg-type]
    except OSError as e:
        raise GalleryError(f"cannot bind {host}:{port}: {e}") from e


def serve(roots: list[Path] | list[str], *, host: str | None = None,
          port: int = DEFAULT_PORT, reload: bool = False, open_browser: bool = False,
          title: str = "3dcodeverse gallery", runtime_js: Path | str | None = None,
          on_start: Any = None) -> None:
    bind = host or DEFAULT_HOST
    app = GalleryApp(roots, reload=reload, title=title, runtime_js=runtime_js)
    httpd = make_server(app, bind, port)
    url = f"http://{'127.0.0.1' if bind == '0.0.0.0' else bind}:{httpd.server_address[1]}/"  # noqa: S104
    if on_start is not None:
        on_start(app, url)
    if open_browser:
        threading.Thread(target=_open, args=(url,), daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()
        httpd.server_close()


def _open(url: str) -> None:
    import webbrowser

    with contextlib.suppress(Exception):
        webbrowser.open(url)

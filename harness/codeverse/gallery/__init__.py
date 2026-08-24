"""The local run gallery: ``3dcv gallery serve`` (localhost) and ``3dcv gallery build`` (one file).

* ``index``       ``build_index(roots)`` · ``default_roots()`` — record.json → typed entries
* ``model``       ``RunEntry`` / ``RootSection`` / ``GalleryIndex`` / ``summarize`` / ``match``
* ``page``        the index page (summary strip, filters, cards + table)
* ``detail``      ``/run/<battery>/<slug>`` — rounds, judge, measurement, renders, cost, code
* ``code``        directory listings + the file viewer
* ``viewer``      optional GLB viewer on the vendored three.js (no CDN)
* ``server``      ``GalleryApp.route`` + ``serve(...)`` — loopback-only unless --host is typed
* ``static_site`` ``build_static(roots, out.html, embed=…)``
"""

from codeverse.gallery.index import build_index, default_roots
from codeverse.gallery.model import GalleryIndex, RootSection, RunEntry, Summary, summarize
from codeverse.gallery.server import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    GalleryApp,
    GalleryError,
    resolve_host,
    serve,
)
from codeverse.gallery.static_site import build_static, render_static

__all__ = [
    "DEFAULT_HOST", "DEFAULT_PORT", "GalleryApp", "GalleryError", "GalleryIndex", "RootSection",
    "RunEntry", "Summary", "build_index", "build_static", "default_roots", "render_static",
    "resolve_host", "serve", "summarize",
]

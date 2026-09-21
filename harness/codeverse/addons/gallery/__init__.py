"""The local run gallery: ``3dcode gallery serve`` (localhost) and ``3dcode gallery build`` (one file).

* ``index``       ``build_index(roots)`` · ``default_roots()`` — record.json → typed entries
* ``model``       ``RunEntry`` / ``RootSection`` / ``GalleryIndex`` / ``summarize`` / ``match``,
                  view-name humanisation + the four disjoint verdict buckets
* ``page``        the index page (status rail, filters, cards + table) and
                  ``build_static(roots, out.html, embed=…)``
* ``cards``       one run as a card / a table row / a compare column
* ``compare``     ``/compare`` side-by-side + ``/export.csv`` (read-only projections)
* ``detail``      ``/run/<battery>/<slug>`` — rounds, judge, measurement, renders, cost, code
* ``code``        directory listings + the file viewer
* ``viewer``      optional GLB viewer on the vendored three.js (no CDN)
* ``server``      ``GalleryApp.route`` + ``serve(...)`` — loopback-only unless --host is typed
"""

from codeverse.addons.gallery.compare import export_csv, render_compare
from codeverse.addons.gallery.index import build_index, default_roots
from codeverse.addons.gallery.model import (
    VERDICTS,
    GalleryIndex,
    RootSection,
    RunEntry,
    Summary,
    humanize_view,
    summarize,
    verdict_breakdown,
)
from codeverse.addons.gallery.page import build_static, render_static
from codeverse.addons.gallery.server import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    GalleryApp,
    GalleryError,
    resolve_host,
    serve,
)

__all__ = [
    "DEFAULT_HOST", "DEFAULT_PORT", "VERDICTS", "GalleryApp", "GalleryError", "GalleryIndex",
    "RootSection", "RunEntry", "Summary", "build_index", "build_static", "default_roots",
    "export_csv", "humanize_view", "render_compare", "render_static", "resolve_host", "serve",
    "summarize", "verdict_breakdown",
]

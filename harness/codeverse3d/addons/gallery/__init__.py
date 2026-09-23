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

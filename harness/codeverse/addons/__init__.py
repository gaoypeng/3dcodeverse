"""Optional tools that READ finished runs — nothing a run needs in order to happen.

* ``gallery``  the run browser (``3dcode gallery serve|build``)
* ``dataset``  run records → samples, packs, preference pairs, captions, an index
               (``3dcode flywheel export|pairs|caption|index``)

The boundary is one-way and pinned by ``tests/core/test_addons_boundary.py``: an addon may
import anything in ``codeverse``; outside ``codeverse.cli`` nothing in ``codeverse`` imports an
addon.  What a run itself writes (``record.json``, ``deliverable/``, telemetry) is NOT an
addon — that is ``codeverse.flywheel``.
"""

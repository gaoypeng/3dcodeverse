"""Optional tools that READ finished runs — nothing a run needs in order to happen.

* ``gallery``        the run browser (``3dcode gallery serve|build``)
* ``dataset``        run records → samples, packs, preference pairs, captions, an index
                     (``3dcode flywheel export|pairs|caption|index``)
* ``costreport``     the cost audit of finished runs (``3dcode cost``)
* ``calibration``    judge repeatability / calibration over recorded runs
                     (``python -m codeverse.addons.calibration``)
* ``skill_targets``  each skill bundle's falsifiable claim, measured (``3dcode skills …``,
                     ``bench/skill_targets.py``)

The boundary is one-way and pinned by ``tests/core/test_addons_boundary.py``: an addon may
import anything in ``codeverse``; outside ``codeverse.cli`` nothing in ``codeverse`` imports an
addon.  What a run itself writes (``record.json``, ``deliverable/``, telemetry) is NOT an
addon — that is ``codeverse.record``; booking its cost while it runs is ``codeverse.cost``.
"""

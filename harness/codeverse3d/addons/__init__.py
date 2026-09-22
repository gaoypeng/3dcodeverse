"""Optional tools that READ finished runs — nothing a run needs in order to happen.

* ``gallery``        the run browser (``3dcode gallery serve|build``)
* ``dataset``        run records → samples, packs, preference pairs, captions, an index
                     (``3dcode flywheel export|pairs|caption|index``)
* ``costreport``     the cost audit of finished runs (``3dcode cost``)
* ``calibration``    judge repeatability / calibration over recorded runs
                     (``python -m codeverse3d.addons.calibration``)
* ``skill_targets``  each skill bundle's falsifiable claim, measured (``3dcode skills …``,
                     ``eval/bench/skill_targets.py``)
* ``select``         which round of a finished run to hand over, and ``deliverable/`` for it
                     (``3dcode pick``; ``3dcode make`` calls it after the run) — the reader
                     every other reader asks for a run's baseline / picked score

The boundary is one-way and pinned by ``tests/core/test_addons_boundary.py``: an addon may
import anything in ``codeverse3d``; outside ``codeverse3d.cli`` nothing in ``codeverse3d`` imports an
addon.  What a run itself writes (``record.json``, the per-round ``artifacts/rNN/``,
telemetry) is NOT an addon — that is ``codeverse3d.record``; booking its cost while it runs is
``codeverse3d.cost``.
"""

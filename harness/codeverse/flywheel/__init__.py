"""Flywheel: what every run WRITES so it can be learned from later.

Public API (see docs/INTERFACES.md):

* ``record``       ``finalize_record(ws, record)`` · ``load_record(ws)`` · ``iter_runs(runs_dir)``
* ``deliverable``  the run's ``deliverable/`` directory
* ``telemetry``    the settings / environment a record carries
* ``sample``       one run → one dataset sample (code files of the best round)
* ``quality``      ``quality_tier`` (A/B/C/D) · ``prompt_hash`` · code / mesh fingerprints, duplicate groups
* ``code_quality`` the per-record code metrics

Turning a tree of finished runs into a dataset (export, pack, pairs, captions, index) is
``codeverse.addons.dataset``.
"""

"""What every run WRITES so it can be read later: ``record.json`` and what travels with it.

* ``record``       ``finalize_record(ws, record)`` · ``load_record(ws)`` · ``iter_runs(runs_dir)`` ·
                   ``effective_judgment`` · ``unique_files``
* ``deliverable``  every round's kept build (``artifacts/rNN/``) and ``deliverable/`` for one round
* ``telemetry``    the settings / environment a record carries
* ``_git``         reading the workspace's own git history (files at a round's commit)

Turning a tree of finished runs into a dataset, a gallery or a cost report is ``codeverse3d.addons``.
"""

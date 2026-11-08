"""What every v0.0.x run wrote so it can be read later: ``record.json`` and what travels with it.

* ``record``       ``load_record(ws)`` · ``iter_runs(runs_dir)`` ·
                   ``effective_judgment`` · ``unique_files``
* ``deliverable``  every round's kept build (``artifacts/rNN/``) and ``deliverable/`` for one round
* ``telemetry``    the cost summary of a run, from its ledger
* ``git_history``  reading the workspace's own git history (files at a round's commit)

Turning a tree of finished runs into a dataset, a gallery or a cost report is ``c3d_flywheel``.
"""

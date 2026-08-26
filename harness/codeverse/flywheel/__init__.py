"""Flywheel: run records → dataset samples, preference pairs, captions, dedupe, index.

Public API (see docs/INTERFACES.md):

* ``record``    ``finalize_record(ws, record)`` · ``load_record(ws)`` · ``iter_runs(runs_dir)``
* ``export``    ``export_samples(runs_dir, out_dir, min_score=..)`` → ``ExportReport``
* ``pack``      ``pack_samples(out_dir)`` (plain tars + byte-range locators, optional)
* ``pairs``     ``build_pairs(runs_dir, out_jsonl, min_delta=..)`` → n
* ``captions``  ``caption_sample(ws, record, model_id)`` → ``Captions``
* ``dedupe``    ``code_fingerprint`` · ``mesh_fingerprint`` · ``near_duplicates``
* ``quality``   ``quality_tier`` (A/B/C/D) · ``prompt_hash`` · exact (code, prompt) duplicate groups
* ``trajectories`` in-session repair pairs mined from api-agent transcripts
* ``index``     ``build_index(runs_dir, out_sqlite)`` + query helpers
"""

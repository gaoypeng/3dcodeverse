"""Run records → a dataset: what ``3dcv flywheel …`` does AFTER the runs exist.

* ``export``    ``export_samples(runs_dir, out_dir, min_score=..)`` → ``ExportReport``
* ``pack``      ``pack_samples(out_dir)`` (plain tars + byte-range locators, optional)
* ``pairs``     ``build_pairs(runs_dir, out_jsonl, min_delta=..)`` → n
* ``refine``    the refine-transition rows (``RefineTransition``)
* ``captions``  ``caption_sample(ws, record, model_id)`` → ``Captions``
* ``index``     ``build_index(runs_dir, out_sqlite)`` + query helpers
"""

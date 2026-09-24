"""A resume cache survives a crash in its own rewrite (eval/llm stages, eval/bench caches)."""

from __future__ import annotations

import json

import pytest


def test_a_failed_rewrite_keeps_the_resume_cache_whole(tmp_path, monkeypatch):
    """``execute_dir`` rewrites exec_results.jsonl whole.  The rewrite used to truncate the
    file first, so a crash part-way lost every cached row (re-paid on the next --resume) —
    the torn-cache bug; now the old file stays until the new one is complete."""
    from llm import execute

    gen = tmp_path / "gen"
    for task in ("a_new", "b_cached", "c_cached"):
        (gen / task).mkdir(parents=True)
    cached = [{"id": "b_cached", "status": "OK"}, {"id": "c_cached", "status": "OK"}]
    out = gen / "exec_results.jsonl"
    out.write_text("".join(json.dumps(r) + "\n" for r in cached))
    # the new row sorts first and cannot be serialised: the rewrite dies on its first line
    monkeypatch.setattr(execute, "_one", lambda dialect, p, timeout: {"id": p.name, "status": "OK", "x": object()})
    with pytest.raises(TypeError):
        execute.execute_dir(gen, "cadquery", workers=1)
    assert [json.loads(line) for line in out.read_text().splitlines()] == cached
    assert not list(gen.glob("*.tmp"))

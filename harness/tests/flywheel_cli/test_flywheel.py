"""Unit tests for flywheel record / export / pack / pairs / dedupe / index (offline)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from codeverse3d.addons import select
from codeverse3d.addons.dataset.export import export_samples
from codeverse3d.addons.dataset.pairs import build_pairs
from codeverse3d.record import git_history
from codeverse3d.record.record import (
    RecordError,
    finalize_record,
    iter_runs,
    load_record,
)

# --------------------------------------------------------------------------- record


# --------------------------------------------------------------------------- git helpers


# --------------------------------------------------------------------------- export + pack


def test_export_pairs_and_a_core_only_install_over_a_runs_dir(runs_dir: Path, tmp_path: Path, monkeypatch):
    out = tmp_path / "dataset"
    rep = export_samples(runs_dir, out)
    assert rep.n_runs == 3 and rep.n_exported == 3 and not rep.skipped
    sdir = out / "static_object" / "blender" / "wooden_chair_ab12cd34"
    assert (sdir / "code.py").read_text().startswith("# round 1")  # best round = 1
    assert (sdir / "src" / "parts" / "leg.py").is_file()
    meta = json.loads((sdir / "meta.json").read_text())
    assert meta["id"] == "3dcodeverse/static_object/blender/wooden_chair_ab12cd34"
    assert meta["entry"] == "code.py" and meta["multi_file"] is True and meta["score"] == 0.80
    assert meta["license"] == "CC-BY-4.0" and meta["source"] == "3dcodeverse" and meta["code_source"] == "commit"
    assert "renders/sheet.png" in meta["renders"] and "renders/object.glb" in meta["renders"]
    assert "renders/view_front.png" in meta["renders"]
    assert json.loads((sdir / "captions.json").read_text()) == {}
    js = out / "static_object" / "threejs" / "lamp_three"
    assert (js / "code.js").is_file() and (js / "src" / "object.js").is_file()
    # index
    import pyarrow.parquet as pq

    table = pq.read_table(out / "metadata.parquet")
    assert table.num_rows == 3
    cols = set(table.column_names)
    assert {"id", "key", "name", "captions", "meta_json", "code", "tar", "byte_start", "byte_len", "n_files"} <= cols
    rows = table.to_pylist()
    row = next(r for r in rows if r["key"] == "wooden_chair_ab12cd34")
    assert row["code"].startswith("# round 1") and row["captions"]["detailed"] == ""
    assert len((out / "metadata.jsonl").read_text().splitlines()) == 3
    # filters
    rep2 = export_samples(runs_dir, tmp_path / "d2", min_score=0.75)
    assert rep2.n_exported == 1 and len(rep2.skipped) == 2
    rep3 = export_samples(runs_dir, tmp_path / "d3", only_passed=True)
    assert rep3.n_exported == 1

    # pairs
    pairs_out = tmp_path / "pairs.jsonl"
    n = build_pairs(runs_dir, pairs_out, min_delta=0.05)
    pairs = [json.loads(ln) for ln in pairs_out.read_text().splitlines()]
    assert n == len(pairs)
    assert {p["kind"] for p in pairs} == {"preference", "repair", "cross_backend"}
    pref = [p for p in pairs if p["kind"] == "preference"]
    assert len(pref) == 2  # chair (0.25) + codex chair (0.10); lamp delta 0.02 < min
    p = next(x for x in pref if x["run"] == "wooden_chair_ab12cd34")
    assert p["delta"] == 0.25 and "thicken the legs" in p["reason"]
    assert p["chosen"]["files"]["src/model.py"].startswith("# round 1")
    assert p["rejected"]["files"]["src/model.py"].startswith("# round 0")
    xb = next(x for x in pairs if x["kind"] == "cross_backend")
    assert xb["chosen"]["generator"].startswith("agy") and xb["rejected"]["generator"].startswith("codex")
    assert xb["delta"] == pytest.approx(0.2)

    # a core-only install (no pyarrow) always exports JSONL, makes parquet optional, and --pack refuses
    from typer.testing import CliRunner

    from codeverse3d.cli.main import app

    _block_pyarrow(monkeypatch)
    core = tmp_path / "ds_core"
    rep = export_samples(runs_dir, core)  # must NOT raise
    assert rep.n_exported == 3 and rep.n_indexed == 3
    assert len((core / "metadata.jsonl").read_text().splitlines()) == 3
    # the parquet is skipped, loudly and by name — never silently
    assert not (core / "metadata.parquet").exists() and rep.parquet == ""
    assert rep.notes and "pyarrow" in rep.notes[0] and "harness[flywheel]" in rep.notes[0]
    packed = tmp_path / "ds_pack"
    res = CliRunner().invoke(app, ["flywheel", "export", str(runs_dir), str(packed), "--pack"])
    assert res.exit_code == 1
    assert "harness[flywheel]" in res.output
    assert not packed.exists(), "no partial dataset may be left behind"

    # a broken record: iter_runs raises, or reports it through on_error
    (runs_dir / "broken").mkdir()
    (runs_dir / "broken" / "record.json").write_text("{not json")
    with pytest.raises(RecordError):
        list(iter_runs(runs_dir))
    bad = []
    runs = list(iter_runs(runs_dir, on_error=lambda d, e: bad.append(d.name)))
    assert len(runs) == 3 and bad == ["broken"]


def _block_pyarrow(monkeypatch) -> None:
    """Make ``import pyarrow`` fail the way a core-only install does."""
    import builtins

    real_import = builtins.__import__

    def no_pyarrow(name, *a, **kw):
        if name == "pyarrow" or name.startswith("pyarrow."):
            raise ModuleNotFoundError("No module named 'pyarrow'")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", no_pyarrow)


def _degrade(judgment):
    return judgment.model_copy(update={"overall": 0.0, "passed": False, "summary": "judge_error: 429 on all samples",
                                       "scores": {k: 0.0 for k in judgment.scores}})


def test_finalize_and_load_record(fake_run):
    ws, rec = fake_run
    rec.environment = {}
    path = finalize_record(ws, rec)
    assert path == ws.record_path and path.is_file()
    loaded = load_record(ws)
    stored = json.loads(path.read_text())
    assert not {"best_round", "baseline_score", "final_score"} & stored.keys()  # the run names no best (2026-09-22)
    assert loaded.environment["python"] and loaded.environment["codeverse3d"]
    assert "three" in loaded.environment and "node" in loaded.environment
    assert "rounds_summary" not in loaded.extra, "written, never read: gone 2026-09-22"
    assert loaded.total_usage.cost_usd == pytest.approx(0.06), "the total is the ledger's"


def test_a_planted_smudge_filter_or_diff_driver_never_runs(fake_run, tmp_path):
    """Programs git RUNS (a smudge filter, a diff driver / textconv) never run on the export read."""
    ws, _rec = fake_run
    fired = tmp_path / "fired"
    payload = f"sh -c 'echo pwned >> {fired}; cat'"
    (ws.src / "model.py").write_text("# real content\n")
    commit = ws.commit("content")
    (ws.root / ".git" / "info").mkdir(parents=True, exist_ok=True)
    (ws.root / ".git" / "info" / "attributes").write_text("* filter=evil\n")
    (ws.root / ".gitattributes").write_text("* filter=evil\n")
    ws._git("config", "--local", "filter.evil.smudge", payload)
    ws._git("config", "--local", "filter.evil.required", "false")
    files = git_history.read_tree_at(ws, commit)
    assert not fired.exists(), f"a planted smudge filter ran: {fired.read_text()!r}"
    assert files["src/model.py"] == b"# real content\n"  # the raw blob, unfiltered

    (ws.root / ".git" / "info" / "attributes").unlink()
    (ws.root / ".gitattributes").unlink()
    (ws.src / "blob.bin").write_bytes(b"\x00\x01before\n")
    before = ws.commit("binary before")
    (ws.src / "blob.bin").write_bytes(b"\x00\x01after\n")
    after = ws.commit("binary after")
    # planted AFTER the last commit: ws.commit() sanitises .git/config on the way past
    (ws.root / ".gitattributes").write_text("*.bin diff=evil\n*.py diff=evil\n")
    for key in ("textconv", "command"):
        ws._git("config", "--local", f"diff.evil.{key}", payload)
    ws._git("config", "--local", "diff.external", payload)
    text, _total, _truncated = git_history.diff_between(ws, before, after)
    files = git_history.changed_files_between(ws, before, after)
    assert not fired.exists(), f"a planted diff driver ran: {fired.read_text()!r}"
    assert files == ["src/blob.bin"] and "src/blob.bin" in text


def test_degraded_round_is_not_a_zero_score(tmp_path: Path):
    from codeverse3d.addons.dataset.pairs import preference_pairs
    from codeverse3d.record.record import effective_judgment, effective_score, round_summary
    from tests.flywheel_cli.conftest import make_fake_run

    ws, rec = make_fake_run(tmp_path / "runs", scores=(0.62, 0.64))
    # splice a degraded round between the two judged ones (own commit)
    (ws.src / "noise.py").write_text("x = 1\n")
    degraded_commit = ws.commit("degraded round")
    mid = rec.rounds[1].model_copy(update={"index": 1, "commit": degraded_commit,
                                           "judgment": _degrade(rec.rounds[1].judgment)})
    last = rec.rounds[1].model_copy(update={"index": 2})
    rec.rounds = [rec.rounds[0], mid, last]
    assert effective_judgment(mid) is None and effective_score(mid) is None
    assert round_summary(mid)["score"] is None and round_summary(mid)["judge_degraded"] is True
    ws.write_json(ws.record_path, rec)
    assert select.pick(ws.root) == 2  # 0.64 beats 0.62; the degraded 0.0 never competes
    pairs = preference_pairs(ws, rec, min_delta=0.05)
    assert pairs == []
    pairs = preference_pairs(ws, rec, min_delta=0.01)
    assert len(pairs) == 1 and pairs[0]["rejected"]["round"] == 0 and pairs[0]["chosen"]["round"] == 2


def test_a_run_with_only_degraded_verdicts_exports_unscored(tmp_path: Path):
    from tests.flywheel_cli.conftest import make_fake_run

    ws, rec = make_fake_run(tmp_path / "runs", scores=(0.5, 0.9))
    for r in rec.rounds:
        if r.judgment is not None:
            r.judgment = _degrade(r.judgment)
    ws.write_json(ws.record_path, rec)
    out = tmp_path / "ds"
    rep = export_samples(ws.root.parent, out)
    assert rep.n_exported == 1
    meta = json.loads(next(out.rglob("meta.json")).read_text())
    assert meta["score"] is None and meta["passed"] is None and meta["quality_tier"] == "D"
    assert meta["acceptance_results"] == {}

"""Unit tests for flywheel record / export / pack / pairs / dedupe / index (offline)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from codeverse3d.addons import select
from codeverse3d.addons.dataset.export import export_samples
from codeverse3d.addons.dataset.pairs import build_pairs
from codeverse3d.record import _git
from codeverse3d.record.record import (
    RecordError,
    finalize_record,
    iter_runs,
    load_record,
)

# --------------------------------------------------------------------------- record


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


def test_a_missing_runtime_js_does_not_lose_a_finished_runs_record(fake_run, monkeypatch):
    """The version probe raised through finalize_record, so a shader run (no node needed) with a
    bad C3D_RUNTIME_JS paid for every round and ended with no record.json (sweep 2026-09-23)."""
    from codeverse3d import config
    from codeverse3d.record import record as R

    ws, rec = fake_run
    rec.environment = {}
    monkeypatch.setenv("C3D_RUNTIME_JS", str(ws.root / "nowhere"))
    config.get_settings.cache_clear()
    R.environment_versions.cache_clear()
    try:
        finalize_record(ws, rec)
        assert load_record(ws).environment["three"] == ""
    finally:
        config.get_settings.cache_clear()
        R.environment_versions.cache_clear()


def test_iter_runs_and_errors(runs_dir: Path):
    (runs_dir / "broken").mkdir()
    (runs_dir / "broken" / "record.json").write_text("{not json")
    with pytest.raises(RecordError):
        list(iter_runs(runs_dir))
    bad = []
    runs = list(iter_runs(runs_dir, on_error=lambda d, e: bad.append(d.name)))
    assert len(runs) == 3 and bad == ["broken"]


# --------------------------------------------------------------------------- git helpers


def test_git_tree_at_commit(fake_run):
    ws, rec = fake_run
    c0, c1 = rec.rounds[0].commit, rec.rounds[1].commit
    t0 = _git.read_tree_at(ws, c0)
    t1 = _git.read_tree_at(ws, c1)
    assert set(t0) == {"src/model.py"}
    assert set(t1) == {"src/model.py", "src/parts/leg.py"}
    assert b"round 0" in t0["src/model.py"] and b"round 1" in t1["src/model.py"]
    with pytest.raises(_git.GitReadError):
        _git.read_tree_at(ws, "deadbeef")


def test_a_planted_diff_driver_never_runs(fake_run, tmp_path):
    """An agent-planted diff driver or textconv (a program git RUNS) never runs on the export read."""
    ws, _rec = fake_run
    fired = tmp_path / "fired"
    payload = f"sh -c 'echo pwned >> {fired}; cat'"
    (ws.src / "blob.bin").write_bytes(b"\x00\x01before\n")
    before = ws.commit("binary before")
    (ws.src / "blob.bin").write_bytes(b"\x00\x01after\n")
    after = ws.commit("binary after")
    # planted AFTER the last commit: ws.commit() sanitises .git/config on the way past
    (ws.root / ".gitattributes").write_text("*.bin diff=evil\n*.py diff=evil\n")
    for key in ("textconv", "command"):
        ws._git("config", "--local", f"diff.evil.{key}", payload)
    ws._git("config", "--local", "diff.external", payload)

    text, _total, _truncated = _git.diff_between(ws, before, after)
    files = _git.changed_files_between(ws, before, after)

    assert not fired.exists(), f"a planted diff driver ran: {fired.read_text()!r}"
    assert files == ["src/blob.bin"] and "src/blob.bin" in text


def test_a_symlink_is_not_exported_as_a_file_of_its_target(fake_run) -> None:
    """ls-tree lists a symlink as a blob holding its target — never a file of the sample."""
    from codeverse3d.record._git import read_tree_at

    ws, _rec = fake_run
    (ws.src / "model.py").write_text("import bpy\n")
    (ws.src / "link.py").symlink_to("model.py")
    commit = ws.commit("with a symlink")

    tree = read_tree_at(ws, commit)
    assert "src/model.py" in tree
    assert "src/link.py" not in tree, "a symlink is not a file the agent wrote"


def test_a_planted_smudge_filter_never_runs(fake_run, tmp_path):
    """read_tree_at reads the object database directly, so a planted smudge filter never runs."""
    ws, _rec = fake_run
    fired = tmp_path / "smudged"
    (ws.src / "model.py").write_text("# real content\n")
    commit = ws.commit("content")
    (ws.root / ".git" / "info").mkdir(parents=True, exist_ok=True)
    (ws.root / ".git" / "info" / "attributes").write_text("* filter=evil\n")
    (ws.root / ".gitattributes").write_text("* filter=evil\n")
    ws._git("config", "--local", "filter.evil.smudge", f"sh -c 'echo pwned >> {fired}; cat'")
    ws._git("config", "--local", "filter.evil.required", "false")

    files = _git.read_tree_at(ws, commit)

    assert not fired.exists(), f"a planted smudge filter ran: {fired.read_text()!r}"
    assert files["src/model.py"] == b"# real content\n"  # the raw blob, unfiltered


def test_diff_between_refuses_a_sha_the_repo_does_not_have(fake_run):
    """A sha the repository no longer holds raises rather than diffing against an empty tree."""
    ws, rec = fake_run
    good = rec.rounds[1].commit
    with pytest.raises(_git.GitReadError):
        _git.diff_between(ws, "0" * 40, good)
    with pytest.raises(_git.GitReadError):
        _git.diff_between(ws, good, "0" * 40)
    with pytest.raises(_git.GitReadError):
        _git.changed_files_between(ws, good, "0" * 40)


# --------------------------------------------------------------------------- export + pack


def test_export_samples(runs_dir: Path, tmp_path: Path):
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


def test_export_with_captions_and_reexport(fake_run, tmp_path: Path):
    ws, rec = fake_run
    rec.extra["captions"] = {"detailed": "A chair.", "instruction": "Write a Blender Python script for a chair.",
                             "factory": "Build seat then legs.", "provenance": {"captioner": "gemini:x"}}
    ws.write_json(ws.record_path, rec)
    out = tmp_path / "ds"
    export_samples(ws.root.parent, out)
    rep = export_samples(ws.root.parent, out)  # re-export overwrites, index stays at 1 row
    assert rep.n_indexed == 1
    row = json.loads((out / "metadata.jsonl").read_text().splitlines()[0])
    assert row["captions"]["factory"] == "Build seat then legs."
    assert json.loads(row["meta_json"])["has_captions"] is True


# --------------------------------------------------------------------------- pairs


def test_build_pairs(runs_dir: Path, tmp_path: Path):
    out = tmp_path / "pairs.jsonl"
    n = build_pairs(runs_dir, out, min_delta=0.05)
    pairs = [json.loads(ln) for ln in out.read_text().splitlines()]
    assert n == len(pairs)
    kinds = {p["kind"] for p in pairs}
    assert kinds == {"preference", "repair", "cross_backend"}
    pref = [p for p in pairs if p["kind"] == "preference"]
    assert len(pref) == 2  # chair (0.25) + codex chair (0.10); lamp delta 0.02 < min
    p = next(x for x in pref if x["run"] == "wooden_chair_ab12cd34")
    assert p["delta"] == 0.25 and "thicken the legs" in p["reason"]
    assert p["chosen"]["files"]["src/model.py"].startswith("# round 1")
    assert p["rejected"]["files"]["src/model.py"].startswith("# round 0")
    xb = next(x for x in pairs if x["kind"] == "cross_backend")
    assert xb["chosen"]["generator"].startswith("gemini-cli") and xb["rejected"]["generator"].startswith("codex")
    assert xb["delta"] == pytest.approx(0.2)


# --------------------------------------------------------------------------- finding: repair pairs (pairs.py:115)
def test_repair_pairs_match_structurally_not_by_kind(tmp_path: Path):
    """Lifecycle labels the fixing round 'refine' — repair pairs must not need kind='repair'."""
    from codeverse3d.addons.dataset.pairs import repair_pairs
    from tests.flywheel_cli.conftest import make_fake_run

    ws, rec = make_fake_run(tmp_path / "runs", with_repair=True)
    fixing = rec.rounds[-1]
    assert fixing.kind == "repair"  # fixture legacy label
    fixing.kind = "refine"  # what the tracks actually emit
    pairs = repair_pairs(ws, rec)
    assert len(pairs) == 1
    p = pairs[0]
    assert p["kind"] == "repair" and p["source"] == "round"
    assert p["rejected"]["build_ok"] is False and p["chosen"]["build_ok"] is True
    assert p["error"]["type"] == "SyntaxError"
    assert "size=3.0" in p["chosen"]["files"]["src/model.py"]


def test_in_round_repair_pairs_from_generated_commit(tmp_path: Path):
    """build_with_repair fixes inside one round: generated commit → final commit."""
    import json as _json

    from codeverse3d.addons.dataset.pairs import in_round_repair_pairs
    from tests.flywheel_cli.conftest import make_fake_run

    ws, rec = make_fake_run(tmp_path / "runs")
    entry = ws.src / "model.py"
    entry.write_text("import bpy\nbpy.ops.mesh.primitive_cube_add(size=\n")  # as generated (broken)
    gen = ws.commit("r01 refine: generated")
    entry.write_text("import bpy\nbpy.ops.mesh.primitive_cube_add(size=2.0)\n")  # after in-round repair
    fin = ws.commit("r01 refine")
    rnd = rec.rounds[1]
    rnd.commit = fin
    rnd.notes = "repair attempts: 1 (fixed)"
    with ws.events_path.open("a") as fh:
        fh.write(_json.dumps({"t": 1.0, "event": "build.done", "round": 1, "ok": False,
                              "error": "SyntaxError: unexpected EOF"}) + "\n")
        fh.write(_json.dumps({"t": 2.0, "event": "build.done", "round": 1, "ok": True, "error": ""}) + "\n")
    pairs = in_round_repair_pairs(ws, rec)
    assert len(pairs) == 1
    p = pairs[0]
    assert p["source"] == "in_round" and p["rejected"]["commit"] == gen
    assert "size=\n" in p["rejected"]["files"]["src/model.py"]
    assert "size=2.0" in p["chosen"]["files"]["src/model.py"]
    assert p["error"]["message"] == "SyntaxError: unexpected EOF"
    # a round whose repair loop never fixed anything yields no pair
    rnd.notes = "repair attempts: 2 (still failing)"
    assert in_round_repair_pairs(ws, rec) == []


# --------------------------------------------------------------------------- finding: degraded judgments (pairs.py:75)
def _degrade(judgment):
    return judgment.model_copy(update={"overall": 0.0, "passed": False, "summary": "judge_error: 429 on all samples",
                                       "scores": {k: 0.0 for k in judgment.scores}})


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


def test_export_includes_textured_assets_when_shipped(fake_run, tmp_path: Path):
    from codeverse3d.texturing.run import report_path
    from tests.flywheel_cli.conftest import tiny_png

    ws, rec = fake_run
    tex_dir = ws.artifacts / "textures"
    tiny_png(tex_dir / "wood.png")
    (ws.artifacts / "object_textured.glb").write_bytes(b"glTF\x02\x00\x00\x00" + b"\0" * 8)
    rec.extra["texturing"] = {"shipped": True, "glb_textured": "artifacts/object_textured.glb",
                              "textures_dir": "artifacts/textures"}
    ws.write_json(ws.record_path, rec)
    # the pass's report names the GLB it started from: the exported round's (r01, the pick)
    ws.write_json(report_path(ws), {**rec.extra["texturing"], "glb_in": "artifacts/r01/object.glb"})
    out = tmp_path / "ds"
    export_samples(ws.root.parent, out)
    sdir = next(out.rglob("meta.json")).parent
    assert (sdir / "textures" / "wood.png").is_file()
    assert (sdir / "renders" / "object_textured.glb").is_file()
    meta = json.loads((sdir / "meta.json").read_text())
    assert "textures/wood.png" in meta["files"] and "renders/object_textured.glb" in meta["files"]
    # not shipped → nothing copied
    rec.extra["texturing"]["shipped"] = False
    ws.write_json(ws.record_path, rec)
    ws.write_json(report_path(ws), {**rec.extra["texturing"], "glb_in": "artifacts/r01/object.glb"})
    export_samples(ws.root.parent, tmp_path / "ds2")
    sdir2 = next((tmp_path / "ds2").rglob("meta.json")).parent
    assert not (sdir2 / "textures").exists()


def test_an_ab_plan_cell_is_one_run_and_its_eval_sibling_is_none(tmp_path):
    """The ab_plan layout resolves to its run; the compare layout is pinned in gallery/test_index."""
    from codeverse3d.addons.gallery.index import scan_root
    from codeverse3d.record.record import find_run_dirs

    ab = tmp_path / "ab_aa_noise"
    cell = ab / "arms" / "control" / "cells" / "ctrl_med_chair" / "harness_api-agent"
    (cell / "run").mkdir(parents=True)
    (cell / "run" / "record.json").write_text("{}")
    (cell / "eval").mkdir()
    (cell / "eval" / "spec.json").write_text("{}")
    assert find_run_dirs(ab) == [cell / "run"]
    section = scan_root(ab)
    assert [e.slug for e in section.entries] == ["control__ctrl_med_chair__harness_api-agent"]


def test_a_run_reached_twice_is_found_once_at_its_physical_path(tmp_path):
    """Batteries symlink each other's cells: export, index, pairs and the gallery counted them twice."""
    from codeverse3d.record.record import find_run_dirs

    real = tmp_path / "zz_home" / "runs" / "chair"
    real.mkdir(parents=True)
    (real / "record.json").write_text("{}")
    (tmp_path / "aa_borrower" / "runs").mkdir(parents=True)
    (tmp_path / "aa_borrower" / "runs" / "chair").symlink_to(real)   # sorts first
    assert find_run_dirs(tmp_path) == [real], "descending: one hit, the physical one"
    (real.parent / "alias").symlink_to(real)
    assert find_run_dirs(real.parent) == [real], "direct children: the alias collapses too"


def test_empty_run_roots_distinguish_a_failed_battery_from_legitimate_empty_input(tmp_path):
    from codeverse3d.record.record import iter_runs

    battery = tmp_path / "compare_empty"
    (battery / "cells" / "cmp_med_chair").mkdir(parents=True)
    with pytest.raises(FileNotFoundError, match=r"looks like a battery directory"):
        list(iter_runs(battery))

    # Plain empty inputs are valid and stay quiet.
    empty = tmp_path / "runs"
    empty.mkdir()
    assert list(iter_runs(empty)) == []

    noise = tmp_path / "noise"
    (noise / "a" / "b" / "c").mkdir(parents=True)
    assert list(iter_runs(noise)) == []


def test_parquet_keeps_the_complexity_columns_the_exporter_writes(tmp_path):
    """The fixed Arrow schema preserves exported complexity columns."""
    import pyarrow.parquet as pq

    from codeverse3d.addons.dataset.export import parquet_schema, write_parquet

    row = dict.fromkeys(parquet_schema().names)
    row.update(id="x", key="k", complexity=7.5, complexity_band="high")
    table = pq.read_table(write_parquet([row], tmp_path / "metadata.parquet"))
    assert {"complexity", "complexity_band"} <= set(table.column_names)
    got = table.to_pylist()[0]
    assert got["complexity"] == 7.5 and got["complexity_band"] == "high"
    # and the schema can never fall behind row_for_sample again
    with pytest.raises(ValueError, match="missing column"):
        write_parquet([{**row, "a_new_metric": 1.0}], tmp_path / "later.parquet")


def _block_pyarrow(monkeypatch) -> None:
    """Make ``import pyarrow`` fail the way a core-only install does."""
    import builtins

    real_import = builtins.__import__

    def no_pyarrow(name, *a, **kw):
        if name == "pyarrow" or name.startswith("pyarrow."):
            raise ModuleNotFoundError("No module named 'pyarrow'")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", no_pyarrow)


def test_a_core_only_install_still_gets_a_complete_dataset(runs_dir: Path, tmp_path: Path, monkeypatch):
    """Core installs always export JSONL and make parquet optional."""
    _block_pyarrow(monkeypatch)
    out = tmp_path / "ds_core"
    rep = export_samples(runs_dir, out)  # must NOT raise
    assert rep.n_exported == 3 and rep.n_indexed == 3
    # the index the operator can actually use is there, and complete
    assert (out / "metadata.jsonl").is_file()
    assert len((out / "metadata.jsonl").read_text().splitlines()) == 3
    # the parquet is skipped, loudly and by name — never silently
    assert not (out / "metadata.parquet").exists() and rep.parquet == ""
    assert rep.notes and "pyarrow" in rep.notes[0] and "harness[flywheel]" in rep.notes[0]


def test_pack_refuses_before_writing_anything_when_pyarrow_is_missing(runs_dir: Path, tmp_path: Path, monkeypatch):
    """Packing refuses before writing when its optional dependency is absent."""
    from typer.testing import CliRunner

    from codeverse3d.cli.main import app

    _block_pyarrow(monkeypatch)
    out = tmp_path / "ds_pack"
    res = CliRunner().invoke(app, ["flywheel", "export", str(runs_dir), str(out), "--pack"])
    assert res.exit_code == 1
    assert "harness[flywheel]" in res.output
    assert not out.exists(), "no partial dataset may be left behind"

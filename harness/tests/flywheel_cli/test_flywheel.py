"""Unit tests for flywheel record / export / pack / pairs / dedupe / index (offline)."""

from __future__ import annotations

import io
import json
import sqlite3
import tarfile
from pathlib import Path

import pytest

from codeverse.addons.dataset.export import export_samples
from codeverse.addons.dataset.index import build_index, summary
from codeverse.addons.dataset.pack import pack_samples, verify_locators
from codeverse.addons.dataset.pairs import build_pairs
from codeverse.addons.dataset.quality import (
    code_fingerprint,
    normalise_code,
)
from codeverse.contracts.run import RunRecord
from codeverse.record import _git
from codeverse.record.record import (
    RecordError,
    best_round_index,
    finalize_record,
    iter_runs,
    load_record,
)
from codeverse.workspace import Workspace

# --------------------------------------------------------------------------- record


def test_finalize_and_load_record(fake_run):
    ws, rec = fake_run
    rec.best_round = None
    rec.final_score = None
    rec.environment = {}
    path = finalize_record(ws, rec)
    assert path == ws.record_path and path.is_file()
    loaded = load_record(ws)
    assert loaded.best_round == 1 and loaded.final_score == 0.80 and loaded.baseline_score == 0.55
    assert loaded.environment["python"] and loaded.environment["codeverse"]
    assert "three" in loaded.environment and "node" in loaded.environment
    assert len(loaded.extra["rounds_summary"]) == 2
    assert loaded.extra["rounds_summary"][1]["score"] == 0.80


def test_best_round_fallbacks(fake_run):
    ws, rec = fake_run
    rec.best_round = None
    assert best_round_index(rec) == 1
    for r in rec.rounds:
        r.judgment = None
    assert best_round_index(rec) == 1  # last built round
    rec.rounds = []
    assert best_round_index(rec) is None


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
    """``.git/config`` and ``.gitattributes`` in a run workspace were writable by the
    agent, and a git diff driver is a program git RUNS.

    ``--no-ext-diff`` does not cover textconv: with only that flag, a ``.gitattributes``
    entry ``*.bin diff=evil`` plus ``[diff "evil"] textconv = sh -c …`` executes the
    command while the exporter merely reads the repository (reproduced on git 2.34).
    ``-c`` cannot unset a LOCAL named driver, so the flag is the fix, not the config.
    """
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
    """``git archive`` skipped symlinks (a tar member, not a file); ``ls-tree`` lists one
    as a blob whose content IS the link target, so ``src/link.py -> model.py`` came back
    as a one-line file saying ``model.py`` — and with ``--with-code`` that goes into a
    training sample."""
    from codeverse.record._git import read_tree_at

    ws, _rec = fake_run
    (ws.src / "model.py").write_text("import bpy\n")
    (ws.src / "link.py").symlink_to("model.py")
    commit = ws.commit("with a symlink")

    tree = read_tree_at(ws, commit)
    assert "src/model.py" in tree
    assert "src/link.py" not in tree, "a symlink is not a file the agent wrote"


def test_a_planted_smudge_filter_never_runs(fake_run, tmp_path):
    """The same window, the other content-rendering command: ``git archive`` renders
    blobs through ``convert_to_working_tree``, so a planted ``filter.<name>.smudge``
    RUNS — and archive has no ``--no-filters``, while ``-c core.attributesFile`` only
    silences the GLOBAL attributes file.  ``read_tree_at`` therefore reads the object
    database directly (``ls-tree`` + ``cat-file --batch``), which applies no filter.

    Planted with ``.git/info/attributes`` — no commit needed, nothing the harness
    sanitises on the read path."""
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
    """The recorded round shas are the only usable handles, and one the repository no
    longer holds must raise rather than diff against an empty tree."""
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


def test_pack_samples_roundtrip(runs_dir: Path, tmp_path: Path):
    out = tmp_path / "ds"
    export_samples(runs_dir, out)
    rep = pack_samples(out, tar_prefix="3dcodeverse/test/", max_tar_bytes=200_000)
    assert rep.n_samples == 3 and rep.tars
    assert verify_locators(out) == 3
    rows = [json.loads(ln) for ln in (out / "metadata.jsonl").read_text().splitlines()]
    r = rows[0]
    assert r["tar"].startswith("3dcodeverse/test/samples-")
    with (out / Path(r["tar"]).name).open("rb") as fh:
        fh.seek(r["byte_start"])
        blob = fh.read(r["byte_len"])
    with tarfile.open(fileobj=io.BytesIO(blob)) as tf:
        names = [m.name for m in tf.getmembers()]
    assert all(n.startswith(r["key"] + "/") for n in names) and f"{r['key']}/meta.json" in names


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
    rep = next(x for x in pairs if x["kind"] == "repair")
    assert rep["error"]["type"] == "SyntaxError" and rep["chosen"]["build_ok"] is True
    assert "size=3.0" in rep["chosen"]["files"]["src/model.py"]
    xb = next(x for x in pairs if x["kind"] == "cross_backend")
    assert xb["chosen"]["generator"].startswith("gemini-cli") and xb["rejected"]["generator"].startswith("codex")
    assert xb["delta"] == pytest.approx(0.2)


# --------------------------------------------------------------------------- dedupe


def test_code_fingerprint_normalises():
    a = {"src/model.py": "import bpy  # hi\n\nx = 1\n# c\n"}
    b = {"src/model.py": "import bpy\nx=1\n"}
    c = {"src/model.py": "import bpy\nx=2\n"}
    assert code_fingerprint(a) == code_fingerprint(b) != code_fingerprint(c)
    assert normalise_code("// a\nlet x = 1; // t\n") == "letx=1;"


# --------------------------------------------------------------------------- index


def test_build_index_and_queries(runs_dir: Path, tmp_path: Path):
    db = tmp_path / "idx.sqlite"
    assert build_index(runs_dir, db) == 3
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 3
    assert con.execute("SELECT COUNT(*) FROM rounds").fetchone()[0] == 2 + 4 + 2
    assert con.execute("SELECT COUNT(*) FROM usage WHERE role='judge'").fetchone()[0] == 6
    con.close()
    s = summary(db)
    assert {r["language"] for r in s} == {"blender", "threejs"}
    assert isinstance(load_record(Workspace(runs_dir / "lamp_three")), RunRecord)


# --------------------------------------------------------------------------- finding: repair pairs (pairs.py:115)
def test_repair_pairs_match_structurally_not_by_kind(tmp_path: Path):
    """Lifecycle labels the fixing round 'refine' — repair pairs must not need kind='repair'."""
    from codeverse.addons.dataset.pairs import repair_pairs
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

    from codeverse.addons.dataset.pairs import in_round_repair_pairs
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
    from codeverse.addons.dataset.pairs import preference_pairs
    from codeverse.record.record import effective_judgment, effective_score, round_summary
    from tests.flywheel_cli.conftest import make_fake_run

    ws, rec = make_fake_run(tmp_path / "runs", scores=(0.62, 0.64))
    # splice a degraded round between the two judged ones (own commit)
    (ws.src / "noise.py").write_text("x = 1\n")
    degraded_commit = ws.commit("degraded round")
    mid = rec.rounds[1].model_copy(update={"index": 1, "commit": degraded_commit,
                                           "judgment": _degrade(rec.rounds[1].judgment)})
    last = rec.rounds[1].model_copy(update={"index": 2})
    rec.rounds = [rec.rounds[0], mid, last]
    rec.best_round = None
    assert effective_judgment(mid) is None and effective_score(mid) is None
    assert round_summary(mid)["score"] is None and round_summary(mid)["judge_degraded"] is True
    assert best_round_index(rec) == 2  # 0.64 beats 0.62; the degraded 0.0 never competes
    pairs = preference_pairs(ws, rec, min_delta=0.05)
    # old behaviour: chosen=r2 rejected=r1(degraded) with delta 0.64 — a pure noise pair
    assert pairs == []
    pairs = preference_pairs(ws, rec, min_delta=0.01)
    assert len(pairs) == 1 and pairs[0]["rejected"]["round"] == 0 and pairs[0]["chosen"]["round"] == 2


def test_degraded_best_round_exports_unscored(tmp_path: Path):
    from tests.flywheel_cli.conftest import make_fake_run

    ws, rec = make_fake_run(tmp_path / "runs", scores=(0.5, 0.9))
    for r in rec.rounds:
        if r.judgment is not None:
            r.judgment = _degrade(r.judgment)
    rec.best_round = 1
    rec.baseline_score = rec.final_score = None
    ws.write_json(ws.record_path, rec)
    out = tmp_path / "ds"
    rep = export_samples(ws.root.parent, out)
    assert rep.n_exported == 1
    meta = json.loads(next(out.rglob("meta.json")).read_text())
    assert meta["score"] is None and meta["passed"] is None and meta["quality_tier"] == "D"
    assert meta["acceptance_results"] == {}


# --------------------------------------------------------------------------- graphics + textured exports
def test_export_graphics_sample(tmp_path: Path):
    from codeverse.contracts.common import Language
    from tests.flywheel_cli.conftest import make_fake_run, tiny_png

    ws, rec = make_fake_run(tmp_path / "runs", "rain_glsl", prompt="neon rain", language=Language.GLSL_SHADER)
    tiny_png(ws.artifacts / "preview.gif")  # gif magic irrelevant here — copied by name
    out = tmp_path / "ds"
    rep = export_samples(ws.root.parent, out)
    assert rep.n_exported == 1, rep.skipped
    sdir = out / "graphics" / "glsl_shader" / "rain_glsl"
    assert (sdir / "code.frag").is_file() and (sdir / "src" / "shader.frag").is_file()
    meta = json.loads((sdir / "meta.json").read_text())
    assert meta["entry"] == "code.frag" and meta["type"] == "Procedural Graphics"
    assert "renders/preview.gif" in meta["renders"]


def test_export_includes_textured_assets_when_shipped(fake_run, tmp_path: Path):
    from tests.flywheel_cli.conftest import tiny_png

    ws, rec = fake_run
    tex_dir = ws.artifacts / "textures"
    tiny_png(tex_dir / "wood.png")
    (ws.artifacts / "object_textured.glb").write_bytes(b"glTF\x02\x00\x00\x00" + b"\0" * 8)
    rec.extra["texturing"] = {"shipped": True, "glb_textured": "artifacts/object_textured.glb",
                              "textures_dir": "artifacts/textures"}
    ws.write_json(ws.record_path, rec)
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
    export_samples(ws.root.parent, tmp_path / "ds2")
    sdir2 = next((tmp_path / "ds2").rglob("meta.json")).parent
    assert not (sdir2 / "textures").exists()


def test_battery_layouts_are_discovered_by_flywheel_and_gallery(tmp_path):
    """All three battery layouts resolve runs; eval siblings never become gallery runs."""
    from codeverse.addons.gallery.index import scan_root
    from codeverse.record.record import find_run_dirs, iter_runs

    battery = tmp_path / "static_v2_flash"          # run_bench: runs/<id>
    (battery / "runs" / "some_run").mkdir(parents=True)
    (battery / "runs" / "some_run" / "record.json").write_text("{}")
    assert [d.name for d in find_run_dirs(battery)] == ["some_run"]

    compare = tmp_path / "compare_v3"               # compare_backends: cells/<id>/<arm>/run
    for pid in ("p0", "p1"):
        for arm in ("harness_codex", "oneshot_gemini"):
            run = compare / "cells" / pid / arm / "run"
            run.mkdir(parents=True)
            (run / "record.json").write_text("{}")
    assert len(find_run_dirs(compare)) == 4
    invalid: list[str] = []
    list(iter_runs(compare, on_error=lambda d, e: invalid.append(d.name)))
    assert len(invalid) == 4, "every cell is reached; these stub records are invalid, not absent"

    ab = tmp_path / "ab_aa_noise"
    cell = ab / "arms" / "control" / "cells" / "ctrl_med_chair" / "harness_api-agent"
    (cell / "run").mkdir(parents=True)
    (cell / "run" / "record.json").write_text("{}")
    (cell / "eval").mkdir()
    (cell / "eval" / "spec.json").write_text("{}")
    assert find_run_dirs(ab) == [cell / "run"]
    section = scan_root(ab)
    assert [e.slug for e in section.entries] == ["control__ctrl_med_chair__harness_api-agent"]


def test_empty_run_roots_distinguish_a_failed_battery_from_legitimate_empty_input(tmp_path):
    from codeverse.record.record import iter_runs

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

    from codeverse.addons.dataset.export import parquet_schema, write_parquet

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

    from codeverse.cli.main import app

    _block_pyarrow(monkeypatch)
    out = tmp_path / "ds_pack"
    res = CliRunner().invoke(app, ["flywheel", "export", str(runs_dir), str(out), "--pack"])
    assert res.exit_code == 1
    assert "harness[flywheel]" in res.output
    assert not out.exists(), "no partial dataset may be left behind"

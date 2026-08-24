"""Unit tests for flywheel record / export / pack / pairs / dedupe / index (offline)."""

from __future__ import annotations

import io
import json
import sqlite3
import tarfile
from pathlib import Path

import pytest

from codeverse.contracts.run import RunRecord
from codeverse.flywheel import _git
from codeverse.flywheel.dedupe import (
    DedupeItem,
    MeshFingerprint,
    code_fingerprint,
    near_duplicates,
    normalise_code,
)
from codeverse.flywheel.export import export_samples
from codeverse.flywheel.index import build_index, round_curve, summary, top_runs
from codeverse.flywheel.pack import pack_samples, verify_locators
from codeverse.flywheel.pairs import build_pairs
from codeverse.flywheel.record import (
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
    assert xb["chosen"]["generator"].startswith("api-agent") and xb["rejected"]["generator"].startswith("codex")
    assert xb["delta"] == pytest.approx(0.2)


# --------------------------------------------------------------------------- dedupe


def test_code_fingerprint_normalises():
    a = {"src/model.py": "import bpy  # hi\n\nx = 1\n# c\n"}
    b = {"src/model.py": "import bpy\nx=1\n"}
    c = {"src/model.py": "import bpy\nx=2\n"}
    assert code_fingerprint(a) == code_fingerprint(b) != code_fingerprint(c)
    assert normalise_code("// a\nlet x = 1; // t\n") == "letx=1;"


def test_near_duplicates_groups():
    fa = MeshFingerprint(extents_cm=(100, 50, 40), tri_count=1000, tri_bucket=9, voxels=list(range(100)), digest="a")
    fb = MeshFingerprint(extents_cm=(102, 50, 40), tri_count=1200, tri_bucket=10, voxels=list(range(95)), digest="b")
    fc = MeshFingerprint(extents_cm=(100, 50, 40), tri_count=1000, tri_bucket=9, voxels=list(range(50, 150)), digest="c")
    items = [DedupeItem(id="a", code_fp="x", mesh_fp=fa), DedupeItem(id="b", code_fp="y", mesh_fp=fb),
             DedupeItem(id="c", code_fp="z", mesh_fp=fc), DedupeItem(id="d", code_fp="x")]
    assert near_duplicates(items) == [["a", "b", "d"]]


def test_mesh_fingerprint_real_glb(tmp_path: Path):
    trimesh = pytest.importorskip("trimesh")
    from codeverse.flywheel.dedupe import mesh_fingerprint

    box = trimesh.creation.box(extents=(1.0, 0.5, 0.25))
    p = tmp_path / "box.glb"
    box.export(p)
    fp = mesh_fingerprint(p)
    assert fp.tri_count == 12 and fp.voxels
    assert sorted(fp.extents_cm) == [25, 50, 100]
    assert fp.jaccard(mesh_fingerprint(p)) == 1.0
    assert fp.jaccard(mesh_fingerprint(p, seed=7)) > 0.9


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
    assert top_runs(db, 1)[0]["slug"] == "wooden_chair_ab12cd34"
    assert [r["score"] for r in round_curve(db, "wooden_chair_ab12cd34")] == [0.55, 0.80]
    assert isinstance(load_record(Workspace(runs_dir / "lamp_three")), RunRecord)


# --------------------------------------------------------------------------- finding: repair pairs (pairs.py:115)
def test_repair_pairs_match_structurally_not_by_kind(tmp_path: Path):
    """Lifecycle labels the fixing round 'refine' — repair pairs must not need kind='repair'."""
    from codeverse.flywheel.pairs import repair_pairs
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

    from codeverse.flywheel.pairs import in_round_repair_pairs
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
    from codeverse.flywheel.pairs import preference_pairs
    from codeverse.flywheel.record import effective_judgment, effective_score, round_summary
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


def test_pointing_at_a_battery_dir_says_so_instead_of_exporting_nothing(tmp_path):
    """`bench/out/<battery>` holds its runs in `runs/`, and pointing one level too high
    used to report "runs 0, exported 0" as a SUCCESS — an empty dataset that looks like
    a finished one.  Silence must not read as an empty result."""
    import pytest

    from codeverse.flywheel.record import iter_runs

    battery = tmp_path / "static_v2_flash"
    (battery / "runs" / "some_run").mkdir(parents=True)
    (battery / "runs" / "some_run" / "record.json").write_text("{}")

    with pytest.raises(FileNotFoundError, match=r"did you mean .*runs"):
        list(iter_runs(battery))


def test_a_genuinely_empty_runs_root_is_still_just_empty(tmp_path):
    """The hint only fires when a `runs/` sibling exists — an empty runs root is a
    legitimate empty result, not an error."""
    from codeverse.flywheel.record import iter_runs

    empty = tmp_path / "runs"
    empty.mkdir()
    assert list(iter_runs(empty)) == []

"""The dataset generation manifest + the manifest-driven pack.

Export used to filter duplicates only in memory while ``pack_samples`` re-scanned
the directory tree — so ``--drop-duplicates --pack`` shipped every dropped
duplicate, a re-pack truncated the previous valid archive at open, and a planted
``src/meta.json`` (sample folders carry the run's LLM-written src/** tree) could
inject a phantom row.  The manifest is now the single source of truth: pack tars
ONLY manifest entries, verifies every byte against the recorded sha256, and
publishes the archives AND the index in one replace loop after the whole pack
succeeded (a failure in either window leaves the previous dataset untouched).
"""

from __future__ import annotations

import hashlib
import json
import re
import tarfile
from pathlib import Path

import pytest

from codeverse.flywheel.export import collect_rows, export_samples
from codeverse.flywheel.manifest import (
    MANIFEST_NAME,
    ManifestError,
    load_manifest,
    write_manifest,
)
from codeverse.flywheel.pack import PackError, pack_samples, verify_locators

CHAIR_ID = "3dcodeverse/static_object/blender/wooden_chair_ab12cd34"


def _tar_member_names(out: Path) -> list[str]:
    names: list[str] = []
    for tp in sorted(out.glob("samples-*.tar")):
        with tarfile.open(tp) as tf:
            names += [m.name for m in tf.getmembers()]
    return names


# --------------------------------------------------------------------------- manifest
def test_export_writes_a_hashed_manifest(runs_dir: Path, tmp_path: Path):
    out = tmp_path / "ds"
    rep = export_samples(runs_dir, out)
    assert rep.manifest == str(out / MANIFEST_NAME)
    m = load_manifest(out)
    assert m.generated_at is not None
    assert m.filters.drop_duplicates is False and m.filters.min_score is None
    assert len(m.entries) == 3 and m.dropped == []
    e = next(x for x in m.entries if x.sample_id == CHAIR_ID)
    assert e.run.battery == "runs" and e.run.rel == "wooden_chair_ab12cd34"
    assert e.sample_rel_dir == "static_object/blender/wooden_chair_ab12cd34"
    assert {"meta.json", "captions.json", "code.py", "src/model.py"} <= set(e.files)
    for rel, digest in e.files.items():  # every byte the export wrote is hashed
        assert hashlib.sha256((out / e.sample_rel_dir / rel).read_bytes()).hexdigest() == digest
    assert len(e.code_sha256) == 64 and len(e.code_fingerprint) == 64
    assert e.code_sha256 != e.code_fingerprint  # raw-exact vs normalised


def test_filtered_runs_are_recorded_as_dropped(runs_dir: Path, tmp_path: Path):
    export_samples(runs_dir, tmp_path / "ds", min_score=0.75)
    m = load_manifest(tmp_path / "ds")
    assert len(m.entries) == 1 and len(m.dropped) == 2
    assert m.filters.min_score == 0.75
    assert all("min_score" in d.reason for d in m.dropped)
    assert {d.run.rel for d in m.dropped} == {"wooden_chair_codex", "lamp_three"}


# --------------------------------------------------------------------------- drop-duplicates → pack
def test_drop_duplicates_then_pack_ships_no_duplicate(runs_dir: Path, tmp_path: Path):
    out = tmp_path / "ds"
    # the codex run shares prompt + best-round code bytes with the chair → exact duplicate
    rep = export_samples(runs_dir, out, drop_duplicates=True)
    assert rep.n_exported == 3 and rep.n_indexed == 2 and rep.n_duplicates == 1
    m = load_manifest(out)
    assert {e.run.rel for e in m.entries} == {"wooden_chair_ab12cd34", "lamp_three"}
    dup = [d for d in m.dropped if d.reason.startswith("duplicate_of:")]
    assert len(dup) == 1 and dup[0].run.rel == "wooden_chair_codex"
    assert dup[0].reason == f"duplicate_of:{CHAIR_ID}"
    # the duplicate FOLDER stays on disk by design — it must still never ship
    assert (out / "static_object" / "blender" / "wooden_chair_codex").is_dir()
    prep = pack_samples(out)
    assert prep.n_samples == 2 and verify_locators(out) == 2
    names = _tar_member_names(out)
    assert names and not any(n.startswith("wooden_chair_codex/") for n in names)
    rows = [json.loads(ln) for ln in (out / "metadata.jsonl").read_text().splitlines()]
    assert {r["key"] for r in rows} == {"wooden_chair_ab12cd34", "lamp_three"}
    assert all(r["tar"] for r in rows)


# --------------------------------------------------------------------------- pack is manifest-only
def test_pack_without_a_manifest_is_a_hard_error(tmp_path: Path):
    out = tmp_path / "ds"
    (out / "static_object" / "blender" / "x").mkdir(parents=True)  # samples alone don't count
    with pytest.raises(PackError, match="no longer rescans"):
        pack_samples(out)
    with pytest.raises(ManifestError, match="dataset_manifest.json"):
        load_manifest(out)


def _packed_state(out: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes()
            for p in [*out.glob("samples-*.tar"), out / "metadata.jsonl", out / "metadata.parquet"]}


def test_a_failure_inside_the_pack_loop_leaves_the_old_tar_and_index_intact(runs_dir: Path, tmp_path: Path):
    out = tmp_path / "ds"
    export_samples(runs_dir, out)
    pack_samples(out)
    before = _packed_state(out)
    # corrupt the recorded hash of a file in the LAST entry: the mismatch fires
    # mid-pack, after earlier samples were already added to the new tmp archive
    m = load_manifest(out)
    victim = m.entries[-1]
    rel = sorted(victim.files)[0]
    victim.files[rel] = "0" * 64
    write_manifest(m, out)
    with pytest.raises(PackError, match=re.escape(rel)):
        pack_samples(out)
    assert _packed_state(out) == before  # old archives AND index untouched
    assert not list(out.glob("*.tmp"))  # the half-written tmp tar is cleaned up
    assert verify_locators(out) == 3  # the previous pack still round-trips


def test_a_failure_after_the_pack_loop_publishes_nothing(runs_dir: Path, tmp_path: Path, monkeypatch):
    """The window the tar loop does NOT cover: every tar is written, then the index
    write fails.  Publishing the tars alone left the OLD index resolving to the new
    tars' byte offsets — six rows pointing at the wrong bytes, and verify_locators
    happily passing a corrupt dataset."""
    import codeverse.flywheel.pack as P

    out = tmp_path / "ds"
    export_samples(runs_dir, out)
    pack_samples(out)
    before = _packed_state(out)
    monkeypatch.setattr(P, "write_parquet", lambda *a, **k: (_ for _ in ()).throw(OSError("No space left on device")))
    with pytest.raises(OSError, match="No space left"):
        pack_samples(out)
    assert _packed_state(out) == before
    assert not list(out.glob("*.tmp"))
    assert verify_locators(out) == 3


def test_a_repack_with_fewer_shards_leaves_no_orphan_tar(runs_dir: Path, tmp_path: Path):
    out = tmp_path / "ds"
    export_samples(runs_dir, out)
    assert pack_samples(out, max_tar_bytes=1).tars == ["samples-000.tar", "samples-001.tar", "samples-002.tar"]
    prep = pack_samples(out)  # one shard now: 001/002 are dead bytes the index never names
    assert prep.tars == ["samples-000.tar"]
    assert sorted(p.name for p in out.glob("samples-*.tar")) == ["samples-000.tar"]
    assert verify_locators(out) == 3


def test_a_tampered_sample_file_fails_fast_and_names_it(runs_dir: Path, tmp_path: Path):
    out = tmp_path / "ds"
    export_samples(runs_dir, out)
    (out / "static_object" / "blender" / "wooden_chair_ab12cd34" / "code.py").write_text("tampered")
    with pytest.raises(PackError, match="code.py"):
        pack_samples(out)
    assert not list(out.glob("samples-*.tar")) and not list(out.glob("*.tmp"))


def test_repack_over_an_existing_archive_succeeds_atomically(runs_dir: Path, tmp_path: Path):
    out = tmp_path / "ds"
    export_samples(runs_dir, out)
    assert pack_samples(out).n_samples == 3
    prep2 = pack_samples(out)  # re-pack over the existing archive (used to truncate it at open)
    assert prep2.n_samples == 3 and verify_locators(out) == 3
    assert not list(out.glob("*.tmp"))


# --------------------------------------------------------------------------- phantom rows
def test_planted_src_meta_json_is_not_a_row_and_not_packed(runs_dir: Path, tmp_path: Path):
    out = tmp_path / "ds"
    export_samples(runs_dir, out)
    sdir = out / "static_object" / "blender" / "wooden_chair_ab12cd34"
    (sdir / "src" / "meta.json").write_text(json.dumps({"id": "phantom", "key": "phantom"}))
    rows = collect_rows(out)  # the recovery/debug rescan is exactly 3 levels deep
    assert len(rows) == 3 and all(r["id"] != "phantom" for r in rows)
    pack_samples(out)  # and pack never looks at the directory tree at all
    assert verify_locators(out) == 3
    assert "wooden_chair_ab12cd34/src/meta.json" not in _tar_member_names(out)
    keys = {json.loads(ln)["key"] for ln in (out / "metadata.jsonl").read_text().splitlines()}
    assert keys == {"wooden_chair_ab12cd34", "wooden_chair_codex", "lamp_three"}

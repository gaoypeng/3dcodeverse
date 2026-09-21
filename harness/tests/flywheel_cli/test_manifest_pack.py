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

from codeverse.addons.dataset.export import (
    MANIFEST_NAME,
    ManifestError,
    export_samples,
    load_manifest,
    write_manifest,
)
from codeverse.addons.dataset.pack import PackError, pack_samples, verify_locators

CHAIR_ID = "3dcodeverse/static_object/blender/wooden_chair_ab12cd34"


@pytest.fixture
def exported_ds(runs_dir: Path, tmp_path: Path) -> Path:
    """A freshly exported dataset: 3 samples + a hashed manifest, not packed yet."""
    out = tmp_path / "ds"
    export_samples(runs_dir, out)
    return out


@pytest.fixture
def packed_ds(exported_ds: Path) -> Path:
    """...and packed once — the previous GOOD dataset every failure window must leave intact."""
    pack_samples(exported_ds)
    return exported_ds


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


def test_a_failure_inside_the_pack_loop_leaves_the_old_tar_and_index_intact(packed_ds: Path):
    before = _packed_state(packed_ds)
    # corrupt the recorded hash of a file in the LAST entry: the mismatch fires
    # mid-pack, after earlier samples were already added to the new tmp archive
    m = load_manifest(packed_ds)
    victim = m.entries[-1]
    rel = sorted(victim.files)[0]
    victim.files[rel] = "0" * 64
    write_manifest(m, packed_ds)
    with pytest.raises(PackError, match=re.escape(rel)):
        pack_samples(packed_ds)
    assert _packed_state(packed_ds) == before  # old archives AND index untouched
    assert not list(packed_ds.glob("*.tmp"))  # the half-written tmp tar is cleaned up
    assert verify_locators(packed_ds) == 3  # the previous pack still round-trips


def test_a_failure_after_the_pack_loop_publishes_nothing(packed_ds: Path, monkeypatch):
    """The window the tar loop does NOT cover: every tar is written, then the index
    write fails.  Publishing the tars alone left the OLD index resolving to the new
    tars' byte offsets — six rows pointing at the wrong bytes, and verify_locators
    happily passing a corrupt dataset."""
    import codeverse.addons.dataset.pack as P

    before = _packed_state(packed_ds)
    monkeypatch.setattr(P, "write_parquet", lambda *a, **k: (_ for _ in ()).throw(OSError("No space left on device")))
    with pytest.raises(OSError, match="No space left"):
        pack_samples(packed_ds)
    assert _packed_state(packed_ds) == before
    assert not list(packed_ds.glob("*.tmp"))
    assert verify_locators(packed_ds) == 3


def test_repacking_over_an_existing_archive_succeeds_and_leaves_no_orphan_tar(packed_ds: Path):
    """Re-packing used to truncate the previous archive at open; and a repack into FEWER
    shards left 001/002 on disk as dead bytes the new index never names."""
    assert pack_samples(packed_ds, max_tar_bytes=1).tars == ["samples-000.tar", "samples-001.tar", "samples-002.tar"]
    prep = pack_samples(packed_ds)
    assert prep.tars == ["samples-000.tar"] and prep.n_samples == 3
    assert sorted(p.name for p in packed_ds.glob("samples-*.tar")) == ["samples-000.tar"]
    assert verify_locators(packed_ds) == 3
    assert not list(packed_ds.glob("*.tmp"))


def test_a_tampered_sample_file_fails_fast_and_names_it(exported_ds: Path):
    (exported_ds / "static_object" / "blender" / "wooden_chair_ab12cd34" / "code.py").write_text("tampered")
    with pytest.raises(PackError, match="code.py"):
        pack_samples(exported_ds)
    assert not list(exported_ds.glob("samples-*.tar")) and not list(exported_ds.glob("*.tmp"))


# --------------------------------------------------------------------------- phantom rows
def test_planted_src_meta_json_is_not_a_row_and_not_packed(exported_ds: Path):
    sdir = exported_ds / "static_object" / "blender" / "wooden_chair_ab12cd34"
    (sdir / "src" / "meta.json").write_text(json.dumps({"id": "phantom", "key": "phantom"}))
    pack_samples(exported_ds)  # and pack never looks at the directory tree at all
    assert verify_locators(exported_ds) == 3
    assert "wooden_chair_ab12cd34/src/meta.json" not in _tar_member_names(exported_ds)
    keys = {json.loads(ln)["key"] for ln in (exported_ds / "metadata.jsonl").read_text().splitlines()}
    assert keys == {"wooden_chair_ab12cd34", "wooden_chair_codex", "lamp_three"}

"""The dataset manifest is the single source of truth: pack tars only its entries, verifies every
byte against it, and publishes archives and index together only after the whole pack succeeded."""

from __future__ import annotations

import json
import tarfile
from pathlib import Path

import pytest

from codeverse3d.addons.dataset.export import (
    ManifestError,
    export_samples,
    load_manifest,
)
from codeverse3d.addons.dataset.pack import PackError, pack_samples, verify_locators

CHAIR_ID = "3dcodeverse/static_object/blender/wooden_chair_ab12cd34"


@pytest.fixture
def exported_ds(runs_dir: Path, tmp_path: Path) -> Path:
    """A freshly exported dataset: 3 samples + a hashed manifest, not packed yet."""
    out = tmp_path / "ds"
    export_samples(runs_dir, out)
    return out


def _tar_member_names(out: Path) -> list[str]:
    names: list[str] = []
    for tp in sorted(out.glob("samples-*.tar")):
        with tarfile.open(tp) as tf:
            names += [m.name for m in tf.getmembers()]
    return names


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


def test_a_tampered_sample_file_fails_fast_and_names_it(exported_ds: Path):
    (exported_ds / "static_object" / "blender" / "wooden_chair_ab12cd34" / "code.py").write_text("tampered")
    with pytest.raises(PackError, match="code.py"):
        pack_samples(exported_ds)
    assert not list(exported_ds.glob("samples-*.tar")) and not list(exported_ds.glob("*.tmp"))


# --------------------------------------------------------------------------- phantom rows, failed and repeated packs
def test_a_planted_row_a_failed_publish_and_a_repack(exported_ds: Path, monkeypatch):
    """A planted src/meta.json is not a row and not packed; every tar written then the index write
    fails: the old dataset stays whole; a repack into fewer shards leaves no orphan tar."""
    import codeverse3d.addons.dataset.pack as P

    sdir = exported_ds / "static_object" / "blender" / "wooden_chair_ab12cd34"
    (sdir / "src" / "meta.json").write_text(json.dumps({"id": "phantom", "key": "phantom"}))
    pack_samples(exported_ds)  # and pack never looks at the directory tree at all
    assert verify_locators(exported_ds) == 3
    assert "wooden_chair_ab12cd34/src/meta.json" not in _tar_member_names(exported_ds)
    keys = {json.loads(ln)["key"] for ln in (exported_ds / "metadata.jsonl").read_text().splitlines()}
    assert keys == {"wooden_chair_ab12cd34", "wooden_chair_codex", "lamp_three"}

    before = _packed_state(exported_ds)
    with monkeypatch.context() as mp:
        mp.setattr(P, "write_parquet", lambda *a, **k: (_ for _ in ()).throw(OSError("No space left on device")))
        with pytest.raises(OSError, match="No space left"):
            pack_samples(exported_ds)
    assert _packed_state(exported_ds) == before
    assert not list(exported_ds.glob("*.tmp"))
    assert verify_locators(exported_ds) == 3

    assert pack_samples(exported_ds, max_tar_bytes=1).tars == ["samples-000.tar", "samples-001.tar", "samples-002.tar"]
    prep = pack_samples(exported_ds)
    assert prep.tars == ["samples-000.tar"] and prep.n_samples == 3
    assert sorted(p.name for p in exported_ds.glob("samples-*.tar")) == ["samples-000.tar"]
    assert verify_locators(exported_ds) == 3
    assert not list(exported_ds.glob("*.tmp"))

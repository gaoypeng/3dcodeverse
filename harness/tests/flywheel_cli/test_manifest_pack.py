"""The dataset manifest is the single source of truth: pack tars only its entries, verifies every
byte against it, and publishes archives and index together only after the whole pack succeeded."""

from __future__ import annotations

import json
import tarfile
from pathlib import Path

import pytest

from codeverse3d.addons.dataset.export import (
    export_samples,
)
from codeverse3d.addons.dataset.pack import pack_samples, verify_locators

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


def _packed_state(out: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes()
            for p in [*out.glob("samples-*.tar"), out / "metadata.jsonl", out / "metadata.parquet"]}


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

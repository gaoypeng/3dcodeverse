"""A gallery restage must never relabel old media with different source code."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

BUILDER = Path(__file__).resolve().parents[2] / "examples/graphics_lab/build.py"
spec = importlib.util.spec_from_file_location("graphics_lab_builder", BUILDER)
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


@pytest.fixture
def studio(tmp_path: Path, monkeypatch):
    source, library, out = tmp_path / "studio", tmp_path / "lib", tmp_path / "out"
    (source / "scenes").mkdir(parents=True)
    library.mkdir()
    (library / "shader.js").write_text("export const version = 1;\n")
    for key in ("one", "two"):
        (source / "scenes" / f"{key}.js").write_text(f"export const scene = '{key}';\n")
    for name in ("index.html", "viewer.js", "viewer.css"):
        (source / name).write_text("<!--COMPARISON_LINK-->" if name == "index.html" else "")
    monkeypatch.setattr(build, "HERE", source)
    monkeypatch.setattr(build, "LIB", library)
    monkeypatch.setattr(build, "CASES", tuple((key, str(i), key, "Study", "life")
                                             for i, key in enumerate(("one", "two"), 1)))
    return source, library, out


def file_snapshot(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.mark.parametrize("capture_hashes", [False, True])
def test_unchanged_selected_rebuild_preserves_other_media(studio, capture_hashes):
    _, _, out = studio
    cases = build.stage(out, [])
    (out / "two.mp4").write_bytes(b"previous film")
    record = {"ok": True}
    if capture_hashes:
        manifest = json.loads((out / "manifest.json").read_text())
        record["source_sha256"] = build.workspace_hashes(cases[1], manifest["library_sha256"])
    (out / "two.capture.json").write_text(json.dumps(record))
    before = file_snapshot(out)
    selected = build.stage(out, ["one"])
    assert [case["id"] for case in selected] == ["one"]
    assert file_snapshot(out) == before


@pytest.mark.parametrize("change", ["library", "unselected_scene"])
def test_changed_sources_refuse_before_mutating_old_media(studio, change):
    source, library, out = studio
    build.stage(out, [])
    (out / "two.mp4").write_bytes(b"previous film")
    target = library / "shader.js" if change == "library" else source / "scenes/two.js"
    target.write_text("export const changed = true;\n")
    before = file_snapshot(out)
    with pytest.raises(ValueError, match="new --out"):
        build.stage(out, ["one"])
    assert file_snapshot(out) == before


def test_capture_hashes_catch_an_already_mislabelled_video(studio):
    _, library, out = studio
    cases = build.stage(out, [])
    old_manifest = json.loads((out / "manifest.json").read_text())
    captured = build.workspace_hashes(cases[1], old_manifest["library_sha256"])
    # Simulate the former unsafe builder: newer source/manifest with an old film.
    (library / "shader.js").write_text("export const version = 2;\n")
    build.stage(out, [])
    (out / "two.mp4").write_bytes(b"older film")
    (out / "two.capture.json").write_text(json.dumps({"ok": True, "source_sha256": captured}))
    before = file_snapshot(out)
    with pytest.raises(ValueError, match="capture-time sources"):
        build.stage(out, ["one"])
    assert file_snapshot(out) == before


def test_missing_case_fails_without_partial_output(studio):
    source, _, out = studio
    (source / "scenes/two.js").unlink()
    with pytest.raises(FileNotFoundError, match="Showcase scene missing"):
        build.stage(out, ["one"])
    assert not out.exists()


def test_unversioned_media_cannot_acquire_new_source_labels(studio):
    _, _, out = studio
    out.mkdir()
    (out / "one.png").write_bytes(b"unknown old still")
    before = file_snapshot(out)
    with pytest.raises(ValueError, match="no source manifest"):
        build.stage(out, [])
    assert file_snapshot(out) == before


def test_source_only_refresh_removes_obsolete_modules(studio):
    _, library, out = studio
    (library / "obsolete.js").write_text("export const old = true;\n")
    build.stage(out, [])
    (library / "obsolete.js").unlink()
    (library / "new.js").write_text("export const current = true;\n")
    build.stage(out, [])
    assert not list(out.rglob("obsolete.js"))
    assert len(list(out.rglob("new.js"))) == 3


@pytest.mark.parametrize("suffix", [".glsl", ".vert", ".frag"])
def test_external_shader_bytes_are_staged_and_guarded(studio, suffix):
    source, _, out = studio
    shader = source / "shaders/nested" / f"surface{suffix}"
    shader.parent.mkdir(parents=True)
    shader.write_text("// Independent shader source\nvoid main() {}\n")
    cases = build.stage(out, [])
    manifest = json.loads((out / "manifest.json").read_text())
    shader_key = f"nested/surface{suffix}"
    assert manifest["shader_sha256"] == {shader_key: build.digest(shader.read_bytes())}
    for root in [out / "src", out / "cases/one/src", out / "cases/two/src"]:
        assert (root / "shaders" / shader_key).read_bytes() == shader.read_bytes()
    captured = build.workspace_hashes(cases[1], manifest["library_sha256"], manifest["shader_sha256"])
    (out / "two.mp4").write_bytes(b"previous film")
    (out / "two.capture.json").write_text(json.dumps({"ok": True, "source_sha256": captured}))
    before = file_snapshot(out)
    build.stage(out, ["one"])
    assert file_snapshot(out) == before
    shader.write_text("// Changed optical response\nvoid main() {}\n")
    with pytest.raises(ValueError, match="external shader contents changed"):
        build.stage(out, ["one"])
    assert file_snapshot(out) == before


@pytest.mark.parametrize("where", ["src", "cases/two/src"])
def test_corrupted_published_or_staged_shader_refuses_restage(studio, where):
    source, _, out = studio
    (source / "shaders").mkdir()
    (source / "shaders/surface.frag").write_text("void main() {}\n")
    build.stage(out, [])
    (out / "two.mp4").write_bytes(b"previous film")
    (out / where / "shaders/surface.frag").write_text("// Unrecorded shader change\n")
    before = file_snapshot(out)
    with pytest.raises(ValueError, match="disagree"):
        build.stage(out, ["one"])
    assert file_snapshot(out) == before

"""Check that exported media retains its source, camera and animation evidence."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

ffmpeg = pytest.importorskip("imageio_ffmpeg")
HERE = Path(__file__).resolve().parents[2] / "examples/graphics_lab"


def load_module(name):
    spec = importlib.util.spec_from_file_location(f"graphics_lab_{name}", HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build, verify, package = [load_module(name) for name in ["build", "verify_media", "package"]]


def write_json(path, value):
    path.write_text(json.dumps(value))


def encode(path, frames):
    writer = ffmpeg.write_frames(str(path), (96, 64), fps=3, codec="libx264",
                                output_params=["-crf", "18"], macro_block_size=1)
    writer.send(None)
    try:
        for frame in frames:
            writer.send(frame)
    finally:
        writer.close()


@pytest.fixture
def gallery(tmp_path, monkeypatch):
    source, library, out = tmp_path / "source", tmp_path / "lib", tmp_path / "out"
    (source / "scenes").mkdir(parents=True)
    (source / "shaders").mkdir()
    library.mkdir()
    (library / "shader.js").write_text("export const scale = 1;\n")
    (source / "scenes/smoke.js").write_text("export const kind = 'smoke';\n")
    for suffix in ["glsl", "vert", "frag"]:
        (source / "shaders" / f"surface.{suffix}").write_text("void main() {}\n")
    for name in ["index.html", "viewer.js", "viewer.css"]:
        (source / name).write_text("")
    monkeypatch.setattr(build, "HERE", source)
    monkeypatch.setattr(build, "LIB", library)
    monkeypatch.setattr(build, "CASES", (("smoke", "01", "Smoke", "Study", "air"),))
    case = build.stage(out, [])[0]
    manifest = json.loads((out / "manifest.json").read_text())
    frames, references = [], []
    (out / "capture_frames/smoke").mkdir(parents=True)
    for index in range(9):
        pixels = np.full((64, 96, 3), [25, 30, 35], dtype=np.uint8)
        x = 8 + index * 6
        pixels[21:38, x:x+17] = [190, 110, 55]
        frames.append(pixels)
        if index in [0, 3, 6]:
            file = out / "capture_frames/smoke" / f"frame_{index}.png"
            Image.fromarray(pixels).save(file)
            references.append({"index": index, "time_s": index / 3,
                               "path": file.relative_to(out).as_posix(),
                               "sha256": verify.sha256(file)})
    encode(out / "smoke.mp4", frames)
    renders = out / "renders/smoke"
    renders.mkdir(parents=True)
    Image.fromarray(frames[0]).save(renders / "main_t0.png")
    shutil.copy2(renders / "main_t0.png", out / "smoke.png")
    write_json(renders / "views.json", [{"name": "main", "time_s": 0, "path": "main_t0.png"}])
    record = {"ok": True, "console_errors": [], "shader_errors": [], "camera": "main",
              "width": 96, "height": 64, "fps": 3, "frames": 9, "seconds": 3,
              "source_sha256": build.workspace_hashes(case, manifest["library_sha256"],
                                                       manifest["shader_sha256"]),
              "video_sha256": verify.sha256(out / "smoke.mp4"), "reference_frames": references}
    write_json(out / "smoke.capture.json", record)
    write_json(out / "capture-plan.json", {"films": [
        {"file": "smoke.mp4", "scene": "smoke", "camera": "main", "fps": 3, "frames": 9}]})
    return out, frames


def test_decoded_animation_matches_retained_inputs_and_external_sources(gallery):
    out, _ = gallery
    report = verify.verify(out)
    assert report["external_shader_files"] == 3
    film = report["films"][0]
    assert film["frames_decoded"] == 9
    assert set(film["motion_correspondence_at_160x90"]) == {3, 6}
    assert film["capture_binding"] == "encoded_digest_and_input_frames"


@pytest.mark.parametrize("change, message", [
    ("frozen", "differs from its input|does not follow captured motion"),
    ("encoded_bytes", "differs from its capture digest"),
    ("missing_camera", "complete capture plan"),
    ("fragment", "Published shaders"),
    ("legacy", "lacks encoded-file/anchor evidence"),
])
def test_mislabelled_or_incomplete_media_is_rejected(gallery, change, message):
    out, frames = gallery
    record = json.loads((out / "smoke.capture.json").read_text())
    if change == "frozen":
        encode(out / "smoke.mp4", [frames[0]] * 9)
        record["video_sha256"] = verify.sha256(out / "smoke.mp4")
        write_json(out / "smoke.capture.json", record)
    elif change == "encoded_bytes":
        with (out / "smoke.mp4").open("ab") as stream:
            stream.write(b"changed")
    elif change == "fragment":
        (out / "src/shaders/surface.frag").write_text("// Changed after capture\n")
    elif change == "legacy":
        record.pop("reference_frames")
        record.pop("video_sha256")
        write_json(out / "smoke.capture.json", record)
    else:
        plan = json.loads((out / "capture-plan.json").read_text())
        plan["films"].append({**plan["films"][0], "file": "smoke_close.mp4"})
        write_json(out / "capture-plan.json", plan)
    with pytest.raises(ValueError, match=message):
        verify.verify(out)


def six_studies(gallery):
    out, _ = gallery
    manifest = json.loads((out / "manifest.json").read_text())
    template = manifest["cases"][0]
    record = json.loads((out / "smoke.capture.json").read_text())
    manifest["cases"] = []
    for key in ["fire", "ocean", "stream", "sand", "rocks", "meadow"]:
        scene = f"export const kind = '{key}';\n".encode()
        case = {**template, "id": key, "module": f"./src/scenes/{key}.js",
                "scene_sha256": hashlib.sha256(scene).hexdigest()}
        manifest["cases"].append(case)
        (out / f"src/scenes/{key}.js").write_bytes(scene)
        shutil.copy2(out / "smoke.mp4", out / f"{key}.mp4")
        shutil.copy2(out / "smoke.png", out / f"{key}.png")
        write_json(out / f"{key}.capture.json", {**record, "source_sha256":
            build.workspace_hashes(case, manifest["library_sha256"], manifest["shader_sha256"])})
    write_json(out / "manifest.json", manifest)
    return out, record


def test_transfer_package_keeps_shader_and_capture_inputs(gallery):
    out, record = six_studies(gallery)
    result = package.build_package(out, "transfer")
    folder = Path(result["folder"])
    for suffix in ["glsl", "vert", "frag"]:
        assert (folder / f"src/shaders/surface.{suffix}").read_bytes() == b"void main() {}\n"
    for reference in record["reference_frames"]:
        assert verify.sha256(folder / reference["path"]) == reference["sha256"]
    assert result["zip_crc_and_extracted_hashes"] == "pass"
    with pytest.raises(FileExistsError):
        package.build_package(out, "transfer")


@pytest.mark.parametrize("changed", ["fire.mp4", "fire.capture.json"])
def test_concurrent_recapture_cannot_change_verified_package_bytes(gallery, monkeypatch, changed):
    out, _ = six_studies(gallery)
    original_copy = shutil.copy2
    changed_source = out / changed

    def mutate_before_copy(source, target):
        if source == changed_source:
            with source.open("ab") as stream:
                stream.write(b"changed during packaging")
        return original_copy(source, target)

    monkeypatch.setattr(package.shutil, "copy2", mutate_before_copy)
    with pytest.raises(ValueError, match="hash mismatch"):
        package.build_package(out, "inconsistent")
    assert not (out / "packages/inconsistent.zip").exists()
    assert not (out / "packages/inconsistent").exists()

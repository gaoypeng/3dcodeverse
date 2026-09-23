"""Decode every gallery film and check its retained scene/source correspondence.

From harness/: python examples/graphics_lab/verify_media.py --out /path/to/gallery
Motion statistics describe decoded pixels; they are not an aesthetic score.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import imageio_ffmpeg
import numpy as np
from PIL import Image, ImageDraw


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(out: Path, *, allow_legacy: bool = False) -> dict:
    manifest = json.loads((out / "manifest.json").read_text())
    libraries = manifest["library_sha256"]
    actual = {p.name: sha256(p) for p in (out / "src/lib").glob("*.js")}
    if actual != libraries:
        raise ValueError("Published library does not match the source manifest")
    shaders = manifest.get("shader_sha256", {})
    shader_root = out / "src/shaders"
    actual = {p.relative_to(shader_root).as_posix(): sha256(p)
              for p in shader_root.rglob("*")
              if p.is_file() and p.suffix in {".glsl", ".vert", ".frag"}}
    if actual != shaders:
        raise ValueError("Published shaders do not match the source manifest")
    expected_sources = {}
    for case in manifest["cases"]:
        key = case["id"]
        scene = out / "src/scenes" / f"{key}.js"
        if sha256(scene) != case["scene_sha256"]:
            raise ValueError(f"Published scene does not match its manifest: {key}")
        workspace = out / "cases" / key / "src"
        wanted = {f"lib/{name}": value for name, value in libraries.items()}
        wanted.update({f"shaders/{name}": value for name, value in shaders.items()})
        wanted[f"scenes/{key}.js"] = case["scene_sha256"]
        wanted["scene.js"] = hashlib.sha256(
            f"export {{createScene}} from './scenes/{key}.js';\n".encode()).hexdigest()
        actual = {p.relative_to(workspace).as_posix(): sha256(p)
                  for p in workspace.rglob("*")
                  if p.is_file() and p.suffix in {".js", ".mjs", ".glsl", ".vert", ".frag"}}
        if actual != wanted:
            raise ValueError(f"Staged workspace does not match published source: {key}")
        expected_sources[key] = wanted
        if not (out / f"{key}.mp4").is_file():
            raise ValueError(f"Missing gallery film: {key}")

    plan_file = out / "capture-plan.json"
    planned = {}
    if plan_file.is_file():
        entries = json.loads(plan_file.read_text())["films"]
        planned = {item["file"]: item for item in entries}
        if len(planned) != len(entries) or set(planned) != {p.name for p in out.glob("*.mp4")}:
            raise ValueError("Movie files do not match the complete capture plan")
    evidence = out / "media-verification"
    evidence.mkdir(exist_ok=True)
    reports = []
    for film in sorted(out.glob("*.mp4")):
        capture = json.loads(film.with_suffix(".capture.json").read_text())
        owners = [key for key, source in expected_sources.items()
                  if capture.get("source_sha256") == source]
        if len(owners) != 1:
            raise ValueError(f"Film has no unique matching source snapshot: {film.name}")
        if not capture.get("ok") or capture.get("console_errors") or capture.get("shader_errors"):
            raise ValueError(f"Capture reported errors: {film.name}")
        key = owners[0]
        if film.stem in expected_sources and key != film.stem:
            raise ValueError(f"Primary gallery film belongs to another scene: {film.name}: {key}")
        if film.name in planned:
            wanted = planned[film.name]
            if key != wanted["scene"] or any(capture[field] != wanted[field]
                                             for field in ["camera", "fps", "frames"]):
                raise ValueError(f"Capture differs from its declared plan: {film.name}")
        video_hash = sha256(film)
        references = capture.get("reference_frames", [])
        legacy = not capture.get("video_sha256") or not references
        if legacy and not allow_legacy:
            raise ValueError(f"Capture lacks encoded-file/anchor evidence: {film.name}; "
                             "use --allow-legacy only for historical movies")
        if capture.get("video_sha256") and capture["video_sha256"] != video_hash:
            raise ValueError(f"Encoded movie differs from its capture digest: {film.name}")
        anchors = {}
        for reference in references:
            file = (out / reference["path"]).resolve()
            if not file.is_relative_to(out) or sha256(file) != reference["sha256"]:
                raise ValueError(f"Invalid retained input frame: {film.name}")
            index = reference["index"]
            if (not isinstance(index, int) or index < 0 or index >= capture["frames"]
                    or index in anchors or abs(reference["time_s"] - index / capture["fps"]) > 1e-8):
                raise ValueError(f"Invalid retained frame index/time: {film.name}")
            anchors[index] = Image.open(file).convert("RGB")
        if not legacy and set(anchors) != {0, capture["frames"] // 3, 2 * capture["frames"] // 3}:
            raise ValueError(f"Capture reference frames are incomplete: {film.name}")
        first_input = (np.asarray(anchors[0].resize((160, 90)), dtype=np.float32)
                       if 0 in anchors else None)
        views = json.loads((out / "renders" / key / "views.json").read_text())
        stills = [view for view in views
                  if view["name"] == capture["camera"] and view["time_s"] == 0]
        if len(stills) != 1:
            raise ValueError(f"Film has no matching time-zero camera still: {film.name}")
        still = Image.open(out / "renders" / key / stills[0]["path"]).convert("RGB")
        frames = imageio_ffmpeg.read_frames(str(film), pix_fmt="rgb24")
        metadata = next(frames)
        width, height = metadata["size"]
        if (width, height) != (capture["width"], capture["height"]):
            raise ValueError(f"Encoded dimensions differ from capture: {film.name}")
        if abs(metadata["fps"] - capture["fps"]) > .01:
            raise ValueError(f"Encoded frame rate differs from capture: {film.name}")
        selected = {0, capture["frames"] // 3, 2 * capture["frames"] // 3}
        tiles, previous, motion, first_error, count = [], None, [], None, 0
        anchor_errors = {}
        motion_matches = {}
        try:
            for count, raw in enumerate(frames, 1):
                pixels = np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 3)
                if count == 1:
                    if still.size != (width, height):
                        raise ValueError(f"Matching still has different dimensions: {film.name}")
                    first_error = float(np.abs(pixels.astype(np.float32)
                                              - np.asarray(still, dtype=np.float32)).mean())
                    if first_error > 6:
                        raise ValueError(f"First decoded frame differs from its scene still: "
                                         f"{film.name}: {first_error:.3f}/255")
                image = Image.fromarray(pixels)
                small = np.asarray(image.resize((160, 90)), dtype=np.float32)
                if count - 1 in anchors:
                    anchor = anchors[count - 1]
                    if anchor.size != (width, height):
                        raise ValueError(f"Retained frame dimensions differ: {film.name}")
                    intended = np.asarray(anchor.resize((160, 90)), dtype=np.float32)
                    error = float(np.abs(small - intended).mean())
                    # Downsampling removes fine chroma-subsampling differences,
                    # while retaining the scene's changing forms and lighting.
                    if error > 1.5:
                        raise ValueError(f"Encoded frame {count - 1} differs from its input: "
                                         f"{film.name}: {error:.3f}/255")
                    anchor_errors[count - 1] = error
                    moving = np.max(np.abs(intended - first_input), axis=2) > 4
                    if count > 1 and int(moving.sum()) >= 16:
                        own_error = float(np.abs(small[moving] - intended[moving]).mean())
                        frozen_error = float(np.abs(small[moving] - first_input[moving]).mean())
                        if own_error + .1 >= frozen_error:
                            raise ValueError(f"Encoded frame does not follow captured motion: "
                                             f"{film.name}: frame {count - 1}")
                        motion_matches[count - 1] = {"changed_pixels": int(moving.sum()),
                                                    "intended_frame_error": own_error,
                                                    "first_frame_error": frozen_error}
                if previous is not None:
                    motion.append(float(np.abs(small - previous).mean()))
                previous = small
                if count - 1 in selected:
                    tiles.append((image.resize((640, 360)), (count - 1) / capture["fps"]))
        finally:
            frames.close()
        if count != capture["frames"]:
            raise ValueError(f"Decoded frame count differs: {film.name}: "
                             f"{count} versus {capture['frames']}")
        sheet = Image.new("RGB", (640 * len(tiles), 396), (18, 23, 27))
        draw = ImageDraw.Draw(sheet)
        for i, (tile, time) in enumerate(tiles):
            sheet.paste(tile, (640 * i, 36))
            draw.text((640 * i + 12, 12), f"{film.stem} / {capture['camera']} / {time:.2f}s",
                      fill="white")
        sheet.save(evidence / f"{film.stem}.jpg", quality=94)
        reports.append({"film": film.name, "scene": key, "camera": capture["camera"],
                        "sha256": video_hash, "frames_decoded": count,
                        "capture_binding": "legacy_source_only" if legacy else "encoded_digest_and_input_frames",
                        "input_frame_rgb_mae_255_at_160x90": anchor_errors,
                        "motion_correspondence_at_160x90": motion_matches,
                        "fps": metadata["fps"], "size": [width, height],
                        "first_frame_rgb_mae_255": first_error,
                        "adjacent_frame_mean_rgb_mae_255_at_160x90": float(np.mean(motion))
                        if motion else 0, "source_files_checked": len(expected_sources[key])})
        print(f"Verified {film.name}: {count} frames, source {key}, "
              f"first-frame error {first_error:.3f}/255", flush=True)
    report = {"ok": True, "manifest_sha256": sha256(out / "manifest.json"),
              "library_modules": len(libraries), "external_shader_files": len(shaders),
              "gallery_cases": len(expected_sources),
              "films": reports,
              "limitations": "Pixel motion includes compression and rendering changes. Static "
              "studies may have zero motion; these statistics do not certify visual realism."}
    (evidence / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--allow-legacy", action="store_true",
                        help="Allow historical sidecars without encoded-file digests or retained input frames")
    args = parser.parse_args()
    verify(args.out.resolve(), allow_legacy=args.allow_legacy)

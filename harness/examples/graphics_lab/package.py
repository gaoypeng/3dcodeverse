"""Package six published studies without rebuilding or rendering anything.

python examples/graphics_lab/package.py --out examples/graphics_lab/output --name studies_v2
Existing packages are never overwritten. Uses only the Python standard library.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import re
import shutil
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def local_file(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError(f"Missing or out-of-root published file: {relative}")
    return path


def check_hash(path: Path, expected: str) -> None:
    if digest(path) != expected:
        raise ValueError(f"Published source hash mismatch: {path}")


def capture_record(path: Path, contents: bytes | None = None) -> dict:
    record = json.loads(contents if contents is not None else path.read_bytes())
    if not record.get("ok") or record.get("console_errors") or record.get("shader_errors"):
        raise ValueError(f"Capture did not complete cleanly: {path}")
    for field in ("width", "height", "fps", "frames", "seconds"):
        value = record.get(field)
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"Invalid capture {field}: {path}")
    if not math.isclose(record["frames"] / record["fps"], record["seconds"], abs_tol=0.01):
        raise ValueError(f"Capture frame count and duration disagree: {path}")
    return record


def verify_capture_sources(out: Path, scene: str, record: dict, libraries: dict,
                           shaders: dict | None = None) -> dict:
    """Prefer the capture-time hashes; older sidecars only identify a camera."""
    if "source_sha256" not in record:
        return {"mode": "legacy_without_capture_source_hashes", "files_checked": 0}
    hashes = record["source_sha256"]
    required = {f"lib/{name}" for name in libraries} | {f"scenes/{scene}.js", "scene.js"}
    required.update(f"shaders/{name}" for name in (shaders or {}))
    if not isinstance(hashes, dict) or not required.issubset(hashes):
        raise ValueError(f"Capture source hashes are incomplete: {scene}")
    staged = out / "cases" / scene / "src"
    for relative, expected in hashes.items():
        if staged.is_dir():
            check_hash(local_file(staged.resolve(), relative), expected)
        elif relative == "scene.js":
            wrapper = f"export {{createScene}} from './scenes/{scene}.js';\n"
            if hashlib.sha256(wrapper.encode()).hexdigest() != expected:
                raise ValueError(f"Cannot verify nonstandard capture entry point without staged workspace: {scene}")
        elif relative in required:
            check_hash(local_file((out / "src").resolve(), relative), expected)
        else:
            raise ValueError(f"Cannot verify captured source without staged workspace: {scene}/{relative}")
    return {"mode": "capture_source_hashes", "files_checked": len(hashes),
            "reference": "staged_workspace" if staged.is_dir() else "published_sources_and_standard_entry"}


def offline_page(cases: list[dict], videos: list[dict]) -> str:
    titles = {case["id"]: case["title"] for case in cases}
    cards = []
    for video in videos:
        scene, key, capture = video["origin_scene_id"], video["origin_video_id"], video["capture"]
        title = titles[scene] if key == scene else "Extra: Fire at the hearth"
        details = f"{capture['width']} × {capture['height']} · {capture['fps']} fps · {capture['seconds']} seconds"
        cards.append(
            f'<article><h2>{html.escape(title)}</h2>'
            f'<video controls playsinline preload="metadata" poster="previews/{scene}.png">'
            f'<source src="videos/{key}.mp4" type="video/mp4"></video>'
            f'<p>{details}</p><nav><a href="videos/{key}.mp4" download>Download video</a>'
            f'<a href="src/scenes/{scene}.js">Scene source</a>'
            f'<a href="previews/{scene}.png">Preview</a></nav></article>'
        )
    return """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Graphics Lab: Six Published Studies</title><style>
*{box-sizing:border-box}body{margin:0;background:#101716;color:#edf0e9;font:16px/1.6 system-ui,sans-serif}
header,main,footer{max-width:1320px;margin:auto;padding:32px}h1{font-size:clamp(32px,5vw,60px);line-height:1.1}
main{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,430px),1fr));gap:26px}
article{background:#18211f;border:1px solid #34443b;border-radius:14px;overflow:hidden}
h2{font-size:20px;font-weight:550;margin:18px 20px}video{width:100%;display:block;aspect-ratio:16/9;background:#060908}
article p,nav{padding:0 20px}p,footer{color:#b6c5bb}nav{display:flex;flex-wrap:wrap;gap:20px;padding-bottom:22px}
a{color:#d2e2ae;text-underline-offset:4px}</style></head><body><header>
<h1>Six studies in light and nature.</h1><p>The first six published manual library studies, with existing
films and source code. Open this page directly to watch offline.</p><a href="README.md">Package notes</a>
 · <a href="manifest.json">Source and capture manifest</a></header><main>""" + "".join(cards) + """</main>
<footer>The video page needs no server, JavaScript or network access. Executing the Three.js scenes
requires a compatible runtime; see <a href="README.md">README.md</a>.</footer></body></html>
"""


def readme(cases: list[dict], library_count: int) -> str:
    table = "\n".join(
        f"| {c['id']} | `src/scenes/{c['id']}.js` | `videos/{c['id']}.mp4` | `previews/{c['id']}.png` |"
        for c in cases
    )
    return f"""# Graphics Lab: first six published studies

This package is a snapshot of the supplied gallery's first six manual library studies.
Scene code and all {library_count} shared library modules are byte-for-byte copies of that gallery,
validated against its published manifest. Films and previews are existing gallery assets.
Later gallery changes are excluded. No source rebuild, rendering, dependency download or model
call occurs during packaging.

## Watch offline

Open `index.html` directly in a browser using a `file://` URL. The video page needs no installation,
server, JavaScript or network access. MP4 files in `videos/` can be uploaded individually.
If included, `hearth.mp4` is a second camera from the fire scene.
Original resolution, duration, frame rate and renderer records are in `provenance/captures/`.

| ID | Scene code | Film | Preview |
| --- | --- | --- | --- |
{table}

## Run the scene source

Scenes export `createScene({{ THREE, renderer, loaders }})`, returning the scene, authored cameras
and update function. `src/scene.js` selects fire by default; change its export path to another ID.
Keep `src/scenes/` beside `src/lib/` and `src/shaders/` (when present) so relative imports resolve.

Executing the code requires Three.js r182 (`three` 0.182.x), WebGL 2, ES module resolution for
`three` and `three/addons/`, and a compatible scene host. The original harness uses Node.js 20.6+
and Chromium through Puppeteer. Its host configures sRGB output, ACES tone mapping, filtered
shadows and a post-processing chain including ambient occlusion, bloom and grading. Another
host can produce a different appearance. Runtime code and npm dependencies are not bundled.

## Provenance and integrity

`manifest.json` records SHA-256 and size for every payload file except itself, plus original
scene/video IDs and capture records. `provenance/published_selection.json` preserves expected
scene/library hashes, the original published manifest digest, and staged-workspace comparisons
where those workspaces exist. The packager verifies the ZIP CRC and every extracted file hash.

When a capture sidecar includes `source_sha256`, every recorded source hash is verified against
the retained capture workspace. If that workspace is absent, the published scene/library files
and the standard generated entry point are checked instead. Mismatches stop packaging.
Per-video `source_verification` records the evidence used. Legacy sidecars without source hashes
are explicitly marked: for those films, published/staged source agreement is not a direct
source-to-video hash binding. Capture records are not a new playback or aesthetic evaluation.
When present, the encoded MP4 digest and retained input-frame hashes are also checked.
Input frames keep their original paths under `capture_frames/`; paths recorded in original
capture metadata resolve from this package's root, not from `provenance/captures/`.

The package excludes model-run records, credentials, environment files and `node_modules`.
The offline video page is independent of the Three.js runtime.
"""


def build_package(out: Path, name: str) -> dict:
    out = out.resolve()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name):
        raise ValueError("Package name must be one folder name using letters, digits, '.', '_' or '-'")
    packages = out / "packages"
    destination, archive = packages / name, packages / f"{name}.zip"
    if destination.exists() or archive.exists():
        raise FileExistsError(f"Package already exists; choose a new --name: {destination}")
    published_file = local_file(out, "manifest.json")
    published_hash = digest(published_file)
    published = json.loads(published_file.read_text(encoding="utf-8"))
    cases = published["cases"][:6]
    if len(cases) != 6 or any(c.get("source_kind") != "library_study" for c in cases):
        raise ValueError("The first six published cases must be manual library studies")
    ids = [case["id"] for case in cases]
    if len(set(ids)) != 6 or "fire" not in ids or any(not re.fullmatch(r"[a-z0-9_]+", i) for i in ids):
        raise ValueError("Scene IDs must be unique safe filenames and include fire")
    libraries = published["library_sha256"]
    shaders = published.get("shader_sha256", {})
    actual = {p.name for p in (out / "src/lib").iterdir() if p.is_file()}
    if not libraries or actual != set(libraries):
        raise ValueError("Published library files and manifest entries disagree")
    packages.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".package-", dir=packages) as temp:
        work = Path(temp) / name
        work.mkdir()
        origins, checks, videos = {}, [], []

        def copy(source: Path, relative: str, expected: str | None = None) -> None:
            expected = expected or digest(source)
            target = work / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            check_hash(target, expected)
            origins[relative] = source.relative_to(out).as_posix()

        for filename, expected in sorted(libraries.items()):
            if Path(filename).name != filename or not filename.endswith(".js"):
                raise ValueError(f"Invalid shared library filename: {filename}")
            copy(local_file(out, f"src/lib/{filename}"), f"src/lib/{filename}", expected)
        for filename, expected in sorted(shaders.items()):
            if (Path(filename).is_absolute() or ".." in Path(filename).parts
                    or Path(filename).suffix not in {".glsl", ".vert", ".frag"}):
                raise ValueError(f"Invalid external shader filename: {filename}")
            copy(local_file(out, f"src/shaders/{filename}"), f"src/shaders/{filename}", expected)
        for case in cases:
            key = case["id"]
            copy(local_file(out, case["module"]), f"src/scenes/{key}.js", case["scene_sha256"])
            preview = local_file(out, f"{key}.png")
            with preview.open("rb") as stream:
                if stream.read(8) != b"\x89PNG\r\n\x1a\n":
                    raise ValueError(f"Preview is not a PNG: {preview}")
            copy(preview, f"previews/{key}.png")
            staged = out / "cases" / key / "src"
            if staged.exists():
                check_hash(staged / "scenes" / f"{key}.js", case["scene_sha256"])
                for filename, expected in libraries.items():
                    check_hash(staged / "lib" / filename, expected)
                for filename, expected in shaders.items():
                    check_hash(staged / "shaders" / filename, expected)
            checks.append({"scene_id": key, "published_hash_matches": True,
                           "staged_workspace_present": staged.exists(),
                           "staged_library_files_checked": len(libraries) if staged.exists() else 0,
                           "staged_shader_files_checked": len(shaders) if staged.exists() else 0})
        video_ids = [(key, key) for key in ids]
        if (out / "hearth.mp4").exists():
            video_ids.append(("fire", "hearth"))
        for scene, key in video_ids:
            source = local_file(out, f"{key}.mp4")
            with source.open("rb") as stream:
                if stream.read(12)[4:8] != b"ftyp":
                    raise ValueError(f"Video is not an MP4 container: {source}")
            capture = local_file(out, f"{key}.capture.json")
            capture_bytes = capture.read_bytes()
            capture_digest = hashlib.sha256(capture_bytes).hexdigest()
            record = capture_record(capture, capture_bytes)
            verification = verify_capture_sources(out, scene, record, libraries, shaders)
            video_digest = record.get("video_sha256") or digest(source)
            if record.get("video_sha256"):
                check_hash(source, video_digest)
                verification["encoded_digest_checked"] = True
            references = record.get("reference_frames", [])
            for reference in references:
                relative = reference["path"]
                if (Path(relative).is_absolute() or ".." in Path(relative).parts
                        or not relative.startswith("capture_frames/") or not relative.endswith(".png")):
                    raise ValueError(f"Invalid retained input frame path: {relative}")
                copy(local_file(out, relative), relative, reference["sha256"])
            verification["retained_input_frames_checked"] = len(references)
            copy(source, f"videos/{key}.mp4", video_digest)
            copy(capture, f"provenance/captures/{key}.capture.json", capture_digest)
            videos.append({"video": f"videos/{key}.mp4", "origin_scene_id": scene,
                           "origin_video_id": key, "capture": record,
                           "source_verification": verification})
        (work / "src/scene.js").write_text("export { createScene } from './scenes/fire.js';\n", encoding="utf-8")
        (work / "README.md").write_text(readme(cases, len(libraries)), encoding="utf-8")
        (work / "index.html").write_text(offline_page(cases, videos), encoding="utf-8")
        provenance = {"published_manifest_sha256": published_hash, "selected_cases": cases,
                      "library_sha256": libraries, "shader_sha256": shaders, "source_checks": checks}
        write_json(work / "provenance/published_selection.json", provenance)
        files = {}
        for file in sorted(work.rglob("*")):
            if not file.is_file():
                continue
            relative = file.relative_to(work).as_posix()
            if file.suffix in {".js", ".html", ".md", ".glsl", ".vert", ".frag"} and re.search(r"[\u3400-\u9fff]", file.read_text(encoding="utf-8")):
                raise ValueError(f"Example text must be English: {relative}")
            files[relative] = {"sha256": digest(file), "bytes": file.stat().st_size,
                               "origin": origins.get(relative, "Generated package metadata or offline viewer")}
        manifest = {"package_name": name, "created_utc": datetime.now(UTC).isoformat(),
                    "default_scene_id": "fire", "shared_library_count": len(libraries),
                    "published_manifest_sha256": published_hash, "scenes": cases,
                    "videos": videos, "source_checks": checks,
                    "integrity_scope": "All payload files except manifest.json itself", "files": files}
        write_json(work / "manifest.json", manifest)
        check_hash(published_file, published_hash)
        expected_files = {p.relative_to(work).as_posix(): digest(p) for p in work.rglob("*") if p.is_file()}
        temporary_zip = Path(temp) / f"{name}.zip"
        with zipfile.ZipFile(temporary_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zipped:
            for relative in sorted(expected_files):
                zipped.write(work / relative, f"{name}/{relative}")
        with zipfile.ZipFile(temporary_zip) as zipped:
            expected_names = {f"{name}/{relative}" for relative in expected_files}
            if zipped.testzip() is not None or set(zipped.namelist()) != expected_names:
                raise ValueError("ZIP CRC or file list verification failed")
            extracted = Path(temp) / "verify"
            zipped.extractall(extracted)
            for relative, expected in expected_files.items():
                check_hash(extracted / name / relative, expected)
        if destination.exists() or archive.exists():
            raise FileExistsError(f"Package appeared during packaging: {destination}")
        work.rename(destination)
        temporary_zip.rename(archive)
    return {"folder": str(destination), "zip": str(archive), "files": len(expected_files),
            "zip_bytes": archive.stat().st_size, "zip_sha256": digest(archive),
            "source_manifest": "pass", "zip_crc_and_extracted_hashes": "pass"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=HERE / "output", help="Existing published gallery root")
    parser.add_argument("--name", required=True, help="New folder/ZIP name under <out>/packages")
    args = parser.parse_args()
    try:
        print(json.dumps(build_package(args.out, args.name), indent=2))
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f"Package failed: {error}\n")


if __name__ == "__main__":
    main()

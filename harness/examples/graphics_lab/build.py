"""Stage reproducible effect cases and optionally render stills and animation.

From harness/: python examples/graphics_lab/build.py --render --video
The default output is ignored by git. No provider calls or external downloads.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
HARNESS = HERE.parents[1]
LIB = HARNESS / "codeverse3d/languages/scene_threejs/starter/src/lib"
SHADER_SUFFIXES = {".glsl", ".vert", ".frag"}
SOURCE_SUFFIXES = {".js", ".mjs", *SHADER_SUFFIXES}
CASES = (
    ("fire", "01", "Fire & candlelight", "Volumetric flames · Wax and embers", "flame"),
    ("ocean", "02", "Open water", "Displaced waves · Reflections and whitecaps", "water"),
    ("stream", "03", "A moving stream", "Shallow water · Flow over a stone bed", "water"),
    ("sand", "04", "Wind over sand", "Sculpted dunes · Ripples and fine grains", "earth"),
    ("rocks", "05", "Stone studies", "Fractured surfaces · Layers and weathering", "earth"),
    ("meadow", "06", "The living meadow", "Folded blades · Wind and transmitted light", "life"),
    ("tree", "07", "A woodland edge", "Connected branches · Living leaves and bark", "life"),
    ("cloud_volume", "08", "Clouds in depth", "Volumetric light · Evolving cloud banks", "air"),
    ("smoke", "09", "Smoke & steam", "Rising plumes · Light through suspended density", "air"),
    ("ice", "10", "Frozen water", "Transmitted light · Frost and trapped detail", "water"),
    ("waterfall", "11", "The falling sheet", "Accelerating flow · Spray and impact foam", "water"),
    ("rain_shelter", "12", "Shelter from rain", "Rainfall · Wet surfaces and reflected light", "weather"),
    ("workshop", "13", "The material workshop", "Worked surfaces · Light and weathering", "materials"),
    ("campfire", "14", "Fire in the clearing", "Campfire and bonfire · Char, embers and rising smoke", "flame"),
    ("structural_fire", "15", "Fire through a structure", "Window and roof flames · Opaque-depth intersections", "flame"),
    ("external_shader", "16", "Light on alloy", "External GLSL · An animated material study", "shader study"),
    ("luna_candle_courtyard", "A1", "Candle courtyard", "GPT-6 Luna · Independent agent case", "agent study"),
    ("luna_coastal_creek_refined", "A2", "Coastal creek", "GPT-6 Luna · Reviewed agent refinement", "agent study"),
    ("luna_garden_workbench", "A3", "Garden workbench", "GPT-6 Luna · Independent agent case", "agent study"),
)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def source_hashes(root: Path, suffixes: set[str] = SOURCE_SUFFIXES) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): digest(p.read_bytes())
            for p in sorted(root.rglob("*")) if p.is_file() and p.suffix in suffixes}


def workspace_hashes(case: dict, library: dict[str, str],
                     shaders: dict[str, str] | None = None) -> dict[str, str]:
    key = case["id"]
    wrapper = f"export {{createScene}} from './scenes/{key}.js';\n"
    return {**{f"lib/{name}": value for name, value in library.items()},
            **{f"shaders/{name}": value for name, value in (shaders or {}).items()},
            f"scenes/{key}.js": case["scene_sha256"], "scene.js": digest(wrapper.encode())}


def has_media(out: Path) -> bool:
    """Comparisons/packages carry their own snapshots and are not restaged here."""
    if not out.is_dir():
        return False
    suffixes = {".png", ".jpg", ".jpeg", ".webp", ".mp4", ".webm"}
    return any(p.is_file() and (p.suffix.lower() in suffixes or p.name.endswith(".capture.json"))
               for p in out.iterdir()) or any(p.is_file() for p in (out / "renders").rglob("*"))


def guard_media(out: Path, manifest: dict) -> None:
    """Reject changed or already inconsistent sources before touching old media."""
    if not has_media(out):
        return

    def stale(reason: str) -> None:
        raise ValueError(f"Refusing stale source/media mixture in {out}: {reason}. "
                         "Choose a new --out directory for changed sources and render/capture there; "
                         "unchanged incremental --case runs remain supported.")

    published = out / "manifest.json"
    if not published.is_file():
        stale("existing media has no source manifest")
    try:
        previous = json.loads(published.read_text(encoding="utf-8"))
        old_cases = previous["cases"]
        old_library = previous["library_sha256"]
        if (not isinstance(old_cases, list) or not all(isinstance(case, dict) for case in old_cases)
                or not isinstance(old_library, dict)):
            raise ValueError("invalid manifest shape")
    except (ValueError, KeyError, TypeError) as exc:
        stale(f"the previous manifest cannot be verified ({exc})")
    if old_library != manifest["library_sha256"]:
        stale("shared library contents changed")
    shaders = manifest.get("shader_sha256", {})
    if previous.get("shader_sha256", {}) != shaders:
        stale("external shader contents changed")
    current = {case["id"]: case for case in manifest["cases"]}
    for old in old_cases:
        key = old.get("id")
        if key not in current or old.get("scene_sha256") != current[key]["scene_sha256"]:
            stale(f"scene {key!r} changed or was removed")
    # Also catch an output previously corrupted by an unsafe restage. Do not
    # silently repair its code while leaving its old video in place.
    expected_library = manifest["library_sha256"]
    actual_library = {p.name: digest(p.read_bytes()) for p in (out / "src/lib").glob("*.js")}
    if actual_library != expected_library:
        stale("published library files disagree with their manifest")
    if source_hashes(out / "src/shaders", SHADER_SUFFIXES) != shaders:
        stale("published shader files disagree with their manifest")
    for old in old_cases:
        key = old["id"]
        scene = out / "src/scenes" / f"{key}.js"
        if not scene.is_file() or digest(scene.read_bytes()) != current[key]["scene_sha256"]:
            stale(f"published scene {key!r} disagrees with its manifest")
        workspace = out / "cases" / key / "src"
        if workspace.is_dir():
            actual = source_hashes(workspace)
            if actual != workspace_hashes(current[key], expected_library, shaders):
                stale(f"staged workspace {key!r} disagrees with published sources")
    expected_workspaces = [workspace_hashes(case, expected_library, shaders) for case in current.values()]
    for sidecar in out.glob("*.capture.json"):
        try:
            capture = json.loads(sidecar.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            stale(f"capture record {sidecar.name} cannot be read ({exc})")
        if not isinstance(capture, dict):
            stale(f"capture record {sidecar.name} is not an object")
        if "source_sha256" in capture and capture["source_sha256"] not in expected_workspaces:
            stale(f"capture-time sources in {sidecar.name} disagree with current sources")


def write_source_tree(root: Path, files: dict[str, bytes]) -> None:
    """Replace generated source only, removing obsolete staged modules as well."""
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    for relative, data in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def stage(out: Path, selected: list[str]) -> list[dict]:
    unknown = set(selected) - {case[0] for case in CASES}
    if unknown:
        raise ValueError(f"Unknown showcase cases: {', '.join(sorted(unknown))}")
    # Read a complete snapshot before any output mutation. Concurrent source
    # edits cannot make copied workspaces disagree with the recorded hashes.
    library_files = {p.name: p.read_bytes() for p in sorted(LIB.glob("*.js"))}
    if not library_files:
        raise FileNotFoundError(f"Showcase library missing or empty: {LIB}")
    library_hashes = {name: digest(data) for name, data in library_files.items()}
    shader_root = HERE / "shaders"
    shader_files = {p.relative_to(shader_root).as_posix(): p.read_bytes()
                    for p in sorted(shader_root.rglob("*"))
                    if p.is_file() and p.suffix in SHADER_SUFFIXES}
    shader_hashes = {name: digest(data) for name, data in shader_files.items()}
    cases, sources = [], {}
    for key, number, title, subtitle, kind in CASES:
        source = HERE / ("agent_cases" if number.startswith("A") else "scenes") / f"{key}.js"
        if not source.is_file():
            raise FileNotFoundError(f"Showcase scene missing: {source}")
        sources[key] = source.read_bytes()
        cases.append({"id": key, "number": number, "title": title, "subtitle": subtitle,
                      "kind": kind, "module": f"./src/scenes/{key}.js",
                      "scene_sha256": digest(sources[key]),
                      "source_kind": "agent" if number.startswith("A") else "library_study"})
    assets = {name: (HERE / name).read_bytes() for name in ("index.html", "viewer.js", "viewer.css")}
    manifest = {"cases": cases, "library_sha256": library_hashes, "shader_sha256": shader_hashes}
    guard_media(out, manifest)
    shared = {**{f"lib/{name}": data for name, data in library_files.items()},
              **{f"shaders/{name}": data for name, data in shader_files.items()}}
    write_source_tree(out / "src", {**shared, **{f"scenes/{key}.js": data for key, data in sources.items()}})
    for case in cases:
        key = case["id"]
        write_source_tree(out / "cases" / key / "src", {
            **shared, f"scenes/{key}.js": sources[key],
            "scene.js": f"export {{createScene}} from './scenes/{key}.js';\n".encode(),
        })
    for name, data in assets.items():
        (out / name).write_bytes(data)
    if (out / "comparison.html").is_file():
        index = out / "index.html"
        index.write_text(index.read_text().replace('<!--COMPARISON_LINK-->',
            '<p><a href="./comparison.html">Compare library refinements ↗</a></p>'))
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    return [case for case in cases if not selected or case["id"] in selected]


def overview(out: Path) -> None:
    """A contact sheet of available manual studies, using actual rendered frames."""
    from PIL import Image, ImageDraw, ImageFont

    studies = [case for case in CASES if case[4] != "agent study"
               and (out / f"{case[0]}.png").is_file()]
    if not studies:
        return
    rows = (len(studies) + 2) // 3
    sheet = Image.new("RGB", (1920, rows * 404), (17, 22, 25))
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 24)
    except OSError:
        font = ImageFont.load_default()
    for i, (key, number, title, _, _) in enumerate(studies):
        x, y = (i % 3) * 640, (i // 3) * 404
        with Image.open(out / f"{key}.png") as frame:
            sheet.paste(frame.convert("RGB").resize((640, 360), Image.Resampling.LANCZOS), (x, y))
        draw.text((x + 14, y + 370), f"{number}  {title}", font=font, fill=(230, 235, 228))
    sheet.save(out / "overview.jpg", quality=94)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=HERE / "output")
    parser.add_argument("--case", action="append", choices=[c[0] for c in CASES], default=[])
    parser.add_argument("--render", action="store_true")
    parser.add_argument("--video", action="store_true")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--seconds", type=float, default=6)
    parser.add_argument("--fps", type=int, default=24)
    args = parser.parse_args()
    out = args.out.resolve()
    try:
        cases = stage(out, args.case)
    except (ValueError, FileNotFoundError) as exc:
        parser.error(str(exc))
    ffmpeg = None
    if args.video:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            try:
                import imageio_ffmpeg
                ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
            except ImportError as exc:
                raise SystemExit("Video export needs ffmpeg on PATH or imageio-ffmpeg installed") from exc
    for case in cases:
        key = case["id"]
        ws = out / "cases" / key
        if args.render:
            print(f"Rendering {key}", flush=True)
            subprocess.run([
                "node", str(HARNESS / "runtime_js/render_scene.mjs"),
                "--ws", str(ws), "--out", str(out / "renders" / key),
                "--times", "0,1.5,3", "--width", str(args.width), "--height", str(args.height),
                "--fps-seconds", "0", "--no-settle", "--timeout-ms", "180000",
            ], check=True, cwd=HARNESS, timeout=200)
            views = json.loads((out / "renders" / key / "views.json").read_text())
            if views:
                shutil.copy2(out / "renders" / key / views[0]["path"], out / f"{key}.png")
        if args.video:
            print(f"Capturing {key}", flush=True)
            subprocess.run([
                "node", str(HERE / "capture.mjs"), "--ws", str(ws),
                "--out", str(out / f"{key}.mp4"), "--ffmpeg", ffmpeg,
                "--width", str(args.width), "--height", str(args.height),
                "--seconds", str(args.seconds), "--fps", str(args.fps),
            ], check=True, cwd=HARNESS, timeout=max(240, args.seconds * args.fps * 3))
    overview(out)
    print(f"Gallery: {out / 'index.html'}")
    print(f"Serve: node {HERE / 'serve.mjs'} --out {out}")


if __name__ == "__main__":
    main()

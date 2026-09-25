"""Stage reproducible effect cases and optionally render stills and animation.

From harness/: python examples/graphics_lab/build.py --render --video
The default output is ignored by git. No provider calls or external downloads.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
HARNESS = HERE.parents[1]
LIB = HARNESS / "codeverse3d/languages/scene_threejs/starter/src/lib"
SHADER_SUFFIXES = {".glsl", ".vert", ".frag"}
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
    ("night", "17", "The moonlit coast", "Connected cloud volumes · Moonlight over open water", "night"),
)


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
    library_files = {p.name: p.read_bytes() for p in sorted(LIB.glob("*.js"))}
    if not library_files:
        raise FileNotFoundError(f"Showcase library missing or empty: {LIB}")
    shader_root = HERE / "shaders"
    shader_files = {p.relative_to(shader_root).as_posix(): p.read_bytes()
                    for p in sorted(shader_root.rglob("*"))
                    if p.is_file() and p.suffix in SHADER_SUFFIXES}
    cases, sources = [], {}
    for key, number, title, subtitle, kind in CASES:
        source = HERE / "scenes" / f"{key}.js"
        if not source.is_file():
            raise FileNotFoundError(f"Showcase scene missing: {source}")
        sources[key] = source.read_bytes()
        cases.append({"id": key, "number": number, "title": title, "subtitle": subtitle,
                      "kind": kind, "module": f"./src/scenes/{key}.js"})
    shared = {**{f"lib/{name}": data for name, data in library_files.items()},
              **{f"shaders/{name}": data for name, data in shader_files.items()}}
    write_source_tree(out / "src", {**shared, **{f"scenes/{key}.js": data for key, data in sources.items()}})
    for case in cases:
        key = case["id"]
        write_source_tree(out / "cases" / key / "src", {
            **shared, f"scenes/{key}.js": sources[key],
            "scene.js": f"export {{createScene}} from './scenes/{key}.js';\n".encode(),
        })
    for name in ("index.html", "viewer.js", "viewer.css"):
        (out / name).write_bytes((HERE / name).read_bytes())
    (out / "manifest.json").write_text(json.dumps({"cases": cases}, indent=2, ensure_ascii=False))
    return [case for case in cases if not selected or case["id"] in selected]


def overview(out: Path) -> None:
    """A contact sheet of the available studies, using actual rendered frames."""
    from PIL import Image, ImageDraw, ImageFont

    studies = [case for case in CASES if (out / f"{case[0]}.png").is_file()]
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

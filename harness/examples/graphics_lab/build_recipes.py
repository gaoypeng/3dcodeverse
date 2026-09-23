"""Render the standalone GLSL recipe studies through the production GL host.

python examples/graphics_lab/build_recipes.py [--video] [--case rain_window]
The study shader and the recipes it uses are copied beside the renders.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import subprocess
from pathlib import Path

from codeverse3d.languages.glsl_shader import compose
from codeverse3d.prompts import load_text
from codeverse3d.spatial.gl_render import GlHost
from codeverse3d.tracks.graphics import cookbook_functions, defined_names, with_helpers

HERE = Path(__file__).resolve().parent
TITLES = {"rain_window": "Rain on glass", "material_light": "Material and moving light",
          "volume_plume": "Rising smoke volume"}


def build(out: Path, selected: list[str], video: bool) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    known = cookbook_functions(load_text("glsl_shader/cookbook.md"))
    manifest = {"cases": []}
    host = GlHost(timeout_s=240)
    cards = []
    for key, title in TITLES.items():
        if selected and key not in selected:
            continue
        source = (HERE / "glsl_cases" / f"{key}.frag").read_text()
        own = defined_names(source)
        used = [recipe for name, recipe in known.items()
                if name not in own and re.search(r"\b" + re.escape(name) + r"\s*\(", source)]
        recipes = "\n\n".join(recipe.text for recipe in with_helpers(used, known)) + "\n"
        case = out / key
        (case / "src").mkdir(parents=True, exist_ok=True)
        (case / "src/shader.frag").write_text(source)
        (case / "src/recipes.glsl").write_text(recipes)
        times = tuple(i / 24 for i in range(144)) if video else (0.0, 1.5, 3.0)
        result = host.render_fragment_shader(compose(source, recipes_src=recipes).source,
                                             case / "render", width=960, height=600, times=times)
        if not result.ok or any(frame.nan or frame.inf for frame in result.frames):
            raise RuntimeError(f"{key}: {result.error_message or 'nonfinite pixels'}")
        shutil.copy2(result.frames[0].path, case / "preview.png")
        if video:
            ffmpeg = shutil.which("ffmpeg")
            if not ffmpeg:
                import imageio_ffmpeg
                ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
            # The GL host's filenames use a minimum of two index digits:
            # lexicographic glob order puts frame 100 before frame 10. Feed
            # the ordered result directly so a six-second film stays in time.
            with subprocess.Popen([ffmpeg, "-y", "-loglevel", "error", "-f", "image2pipe",
                                   "-vcodec", "png", "-r", "24", "-i", "-", "-an",
                                   "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
                                   "-movflags", "+faststart", str(case / "film.mp4")],
                                  stdin=subprocess.PIPE) as encoder:
                assert encoder.stdin is not None
                for frame in result.frames:
                    encoder.stdin.write(Path(frame.path).read_bytes())
                encoder.stdin.close()
                if encoder.wait():
                    raise RuntimeError(f"{key}: video encoder failed")
        record = {"id": key, "title": title, "renderer": result.renderer, "gpu": result.gpu,
                  "frames": len(result.frames), "render_ms": result.duration_ms}
        manifest["cases"].append(record)
        media = (f'<video controls loop playsinline poster="{key}/preview.png" src="{key}/film.mp4"></video>'
                 if video else f'<img src="{key}/preview.png" alt="{html.escape(title)}">')
        cards.append(f'<article><h2>{html.escape(title)}</h2>{media}<p><a href="{key}/src/shader.frag">'
                     f'Study source</a> · <a href="{key}/src/recipes.glsl">Library recipes</a></p></article>')
        print(json.dumps(record), flush=True)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (out / "index.html").write_text('''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Graphics Lab · GLSL Studies</title>
<style>body{margin:40px auto;max-width:1280px;padding:0 24px;background:#101817;color:#e8eee9;font:16px/1.6 system-ui}
main{display:grid;grid-template-columns:repeat(auto-fit,minmax(360px,1fr));gap:24px}article{background:#1b2724;padding:20px}
img,video{width:100%;display:block}h1{font-size:42px}h2{font-size:22px}a{color:#c4d9af}</style>
<h1>Light, surfaces, atmosphere.</h1><p>Three authored studies using the agent-facing GLSL recipe library.
Rendered locally with the production OpenGL host. Source and dependencies are included.</p><main>'''
        + "".join(cards) + "</main></html>\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=HERE / "output/recipes")
    parser.add_argument("--case", action="append", choices=list(TITLES), default=[])
    parser.add_argument("--video", action="store_true")
    args = parser.parse_args()
    build(args.out, args.case, args.video)

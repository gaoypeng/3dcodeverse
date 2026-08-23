"""``3dcv texture`` — text-to-image texturing commands.

    3dcv texture pass <slug> [--no-judge] [--model gemini:gemini-3.7-flash] [--image-model gemini-3.1-flash-image]
    3dcv texture scene-pack <slug> [--n 8] [--model ...]
    3dcv texture show <slug>
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from codeverse.cli import _common as C
from codeverse.cli._fmt import console, kv_table, warn
from codeverse.contracts.common import Track

texture_app = typer.Typer(name="texture", help="Text-to-image texturing: object pass + scene texture pack.",
                          no_args_is_help=True)

RunsDirOpt = Annotated[Path | None, typer.Option("--runs-dir", help="runs root (default: settings.runs_dir)")]


def _image_model(name: str | None):
    GeminiImageModel = C.lazy("codeverse.models.gemini_image", "GeminiImageModel")
    return GeminiImageModel(name) if name else GeminiImageModel()


@texture_app.command("pass")
def pass_(
    slug: str,
    judge: Annotated[bool, typer.Option("--judge/--no-judge", help="before/after VLM ship gate")] = True,
    model: Annotated[str | None, typer.Option("--model", help="material-plan chat model id (default: spec planner)")] = None,
    judge_model: Annotated[str | None, typer.Option("--judge-model", help="judge chat model id (default: spec judge)")] = None,
    image_model: Annotated[str | None, typer.Option("--image-model", help="gemini image model (default gemini-3.1-flash-image)")] = None,
    size: Annotated[int, typer.Option("--size", min=256, max=2048)] = 1024,
    runs_dir: RunsDirOpt = None,
) -> None:
    """Texture the run's artifacts/object.glb → artifacts/object_textured.glb (+ textures/)."""
    ws = C.open_workspace(slug, runs_dir)
    spec = C.load_spec(ws)
    if spec.track not in (Track.STATIC_OBJECT, Track.ARTICULATED_OBJECT):
        raise C.CliError(f"texture pass is for object tracks; {spec.track.value} runs use `3dcv texture scene-pack`")
    load_plan = C.lazy("codeverse.spatial.tool_common", "load_plan")
    plan = load_plan(ws.plan_path)
    texture_pass = C.lazy("codeverse.texturing.run", "texture_pass")
    rep = texture_pass(ws, spec, plan, model_id=model or spec.backends.planner, image_model=_image_model(image_model),
                       judge=judge, judge_model_id=judge_model, size=size)
    _print_report(rep)


@texture_app.command("show")
def show(slug: str, runs_dir: RunsDirOpt = None) -> None:
    """Print the last texturing report of a run."""
    ws = C.open_workspace(slug, runs_dir)
    load_report = C.lazy("codeverse.texturing.run", "load_report")
    try:
        rep = load_report(ws)
    except FileNotFoundError as e:
        raise C.CliError(str(e)) from e
    _print_report(rep)


def _print_report(rep) -> None:
    plan_table = C.lazy("codeverse.texturing.plan", "plan_table")
    console.print(plan_table(rep.plan))
    rows = {tid: f"{Path(a.path).name}  seam={a.seam_score:.3f} (raw {a.seam_score_raw:.3f}) cached={a.cached}"
            + (f"  ERROR {a.error}" if a.error else "") for tid, a in rep.textures.textures.items()}
    console.print(kv_table("textures", rows or {"-": "none"}))
    s = rep.summary()
    if rep.gate is not None and rep.gate.overall_before is not None:
        s.update({"before": f"{rep.gate.overall_before:.3f}", "after": f"{rep.gate.overall_after:.3f}",
                  "materials": f"{rep.gate.materials_before:.3f} → {rep.gate.materials_after:.3f}"})
    console.print(kv_table("texture pass", s))
    for n in rep.notes:
        warn(n)


@texture_app.command("scene-pack")
def scene_pack(
    slug: str,
    n: Annotated[int, typer.Option("--n", min=1, max=16, help="max textures")] = 10,
    model: Annotated[str | None, typer.Option("--model", help="pack-plan chat model id (default: spec planner; '' = heuristic)")] = None,
    image_model: Annotated[str | None, typer.Option("--image-model")] = None,
    size: Annotated[int, typer.Option("--size", min=256, max=2048)] = 1024,
    out: Annotated[Path | None, typer.Option("--out", help="output dir (default <ws>/public/textures)")] = None,
    runs_dir: RunsDirOpt = None,
) -> None:
    """Generate the scene's tileable texture pack into public/textures/ (+ manifest.json)."""
    ws = C.open_workspace(slug, runs_dir)
    spec = C.load_spec(ws)
    load_plan = C.lazy("codeverse.spatial.tool_common", "load_plan")
    plan = load_plan(ws.plan_path)
    ScenePlan = C.lazy("codeverse.contracts.plan", "ScenePlan")
    if not isinstance(plan, ScenePlan):
        raise C.CliError("scene-pack needs a scene run (plan.json with zones)")
    scene_texture_pack = C.lazy("codeverse.texturing.scene_pack", "scene_texture_pack")
    texture_pack_prompt = C.lazy("codeverse.texturing.scene_pack", "texture_pack_prompt")
    model_id = spec.backends.planner if model is None else model
    pack = scene_texture_pack(plan, out or ws.public / "textures", _image_model(image_model), model_id, size=size, n_max=n)
    rows = {name: f"{e.file or 'FAILED'}  tile={e.tile_size_m:.2f}m {e.material_family}/{e.role} seam={e.seam_score:.3f}"
            + (f"  {e.error}" if e.error else "") for name, e in pack.entries.items()}
    console.print(kv_table("scene texture pack", rows))
    console.print(kv_table("summary", {"dir": pack.out_dir, "manifest": pack.manifest_path, "source": pack.source,
                                       "cost": f"${pack.usage.cost_usd:.4f}", "n": len(pack.manifest())}))
    console.print(texture_pack_prompt(pack.manifest()))

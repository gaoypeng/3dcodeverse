"""``3dcode texture`` — text-to-image texturing commands.

    3dcode texture pass <slug> [--no-judge] [--model gemini:gemini-3.7-flash] [--image-model MODEL]
    3dcode texture scene-pack <slug> [--n 8] [--model ...]
    3dcode texture show <slug>
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from codeverse3d.cli import _common as C
from codeverse3d.cli._common import console, kv_table, warn
from codeverse3d.contracts.common import DEFAULT_IMAGE_MODEL
from codeverse3d.contracts.plan import ScenePlan

texture_app = typer.Typer(name="texture", help="Text-to-image texturing: object pass + scene texture pack.",
                          no_args_is_help=True)

RunsDirOpt = C.RunsDirOpt


@texture_app.command("pass")
def pass_(
    slug: str,
    judge: Annotated[bool, typer.Option("--judge/--no-judge", help="before/after VLM ship gate")] = True,
    model: Annotated[str | None, typer.Option("--model", help="material-plan chat model id (default: spec planner)")] = None,
    judge_model: Annotated[str | None, typer.Option("--judge-model", help="judge chat model id (default: spec judge)")] = None,
    image_model: Annotated[str | None, typer.Option("--image-model", help=f"gemini image model (default {DEFAULT_IMAGE_MODEL})")] = None,
    size: Annotated[int, typer.Option("--size", min=256, max=2048)] = 1024,
    force: Annotated[bool, typer.Option("--force", help="buy a new pass even if one already started from this GLB")] = False,
    runs_dir: RunsDirOpt = None,
) -> None:
    """Texture the picked round's object.glb (``addons.select.texture_round``) → artifacts/object_textured.glb
    (+ textures/); a packaged hand-over of that round is refreshed."""
    from codeverse3d import reference
    from codeverse3d.addons import select
    from codeverse3d.record.record import RecordError, join_post_run, load_record
    from codeverse3d.texturing.run import load_report

    ws = C.open_workspace(slug, runs_dir)
    try:
        rec = load_record(ws)
    except RecordError as e:
        raise C.CliError(str(e), code=2) from e
    picked = select.summarise(ws.root, record=rec).picked_round
    if picked is None:
        raise C.CliError(f"no picked round to texture in {ws.root.name}: `3dcode pick {ws.root.name} --round N` names one")
    # a post-hoc pass joins the run's ledger (and its record's total) when it has one; it
    # rewrites the run's artifacts, so it holds the run mutex (one writer per run dir)
    with C.mutating(ws, what=f"3dcode texture pass {ws.root.name}", action="texture"), join_post_run(ws):
        try:
            rep = select.texture_round(ws, rec, picked, image_model=reference.get_image_model(image_model or ""),
                                       model_id=model, judge=judge, judge_model_id=judge_model, size=size, force=force)
        except ValueError as e:
            raise C.CliError(str(e)) from e
        sel = select.load_selection(ws)
        if sel is not None and sel.round == picked:  # the hand-over carries the pack only if it is refreshed
            select.package(ws.root, picked, method=sel.method)
    if rep is None:
        warn("a pass already started from this round's GLB: its report is below (--force buys a new one)")
        rep = load_report(ws)
    _print_report(rep, ws)


@texture_app.command("show")
def show(slug: str, runs_dir: RunsDirOpt = None) -> None:
    """Print the last texturing report of a run."""
    from codeverse3d.texturing.run import load_report

    ws = C.open_workspace(slug, runs_dir)
    try:
        rep = load_report(ws)
    except FileNotFoundError as e:
        raise C.CliError(str(e)) from e
    _print_report(rep, ws)


def _print_report(rep, ws) -> None:
    from codeverse3d.texturing.plan import plan_table

    console.print(plan_table(rep.plan))
    rows = {tid: f"{Path(a.path).name}  seam={a.seam_score:.3f} (raw {a.seam_score_raw:.3f}) cached={a.cached}"
            + (f"  ERROR {a.error}" if a.error else "") for tid, a in rep.textures.textures.items()}
    console.print(kv_table("textures", rows or {"-": "none"}))
    s = rep.summary()
    if s.get("glb_out"):
        s["glb_out"] = str(ws.rebase(str(s["glb_out"])))  # stored absolute; see print_evidence
    if rep.gate is not None and rep.gate.overall_before is not None:
        def _f(v: float | None) -> str:  # a judge outage leaves per-criterion fields None
            return "—" if v is None else f"{v:.3f}"
        s.update({"before": _f(rep.gate.overall_before), "after": _f(rep.gate.overall_after),
                  "materials": f"{_f(rep.gate.materials_before)} → {_f(rep.gate.materials_after)}"})
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
    from codeverse3d import reference
    from codeverse3d.record.record import join_post_run
    from codeverse3d.spatial.tool_common import load_plan
    from codeverse3d.texturing.plan import scene_texture_pack, texture_pack_prompt

    ws = C.open_workspace(slug, runs_dir)
    spec = C.load_spec(ws)
    plan = load_plan(ws.plan_path)
    if not isinstance(plan, ScenePlan):
        raise C.CliError("scene-pack needs a scene run (plan.json with zones)")
    model_id = spec.backends.planner if model is None else model

    # like `pass`: the pack's plan + image spend joins the run's ledger when it has one
    with C.mutating(ws, what=f"3dcode texture scene-pack {ws.root.name}", action="texture"), join_post_run(ws):
        pack = scene_texture_pack(plan, out or ws.public / "textures", reference.get_image_model(image_model or ""), model_id, size=size, n_max=n)
    rows = {name: f"{e.file or 'FAILED'}  tile={e.tile_size_m:.2f}m {e.material_family}/{e.role} seam={e.seam_score:.3f}"
            + (f"  {e.error}" if e.error else "") for name, e in pack.entries.items()}
    console.print(kv_table("scene texture pack", rows))
    console.print(kv_table("summary", {"dir": pack.out_dir, "manifest": pack.manifest_path, "source": pack.source,
                                       "cost": f"${pack.usage.cost_usd:.4f}", "n": len(pack.manifest())}))
    console.print(texture_pack_prompt(pack.manifest()))

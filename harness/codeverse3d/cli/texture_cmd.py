"""``3dcode texture`` — text-to-image texturing commands.

    3dcode texture pass <slug> [--no-judge] [--model gemini:gemini-3.7-flash] [--image-model gemini-3.1-flash-image]
    3dcode texture scene-pack <slug> [--n 8] [--model ...]
    3dcode texture show <slug>
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from codeverse3d.cli import _common as C
from codeverse3d.cli._common import console, kv_table, warn
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
    image_model: Annotated[str | None, typer.Option("--image-model", help="gemini image model (default gemini-3.1-flash-image)")] = None,
    size: Annotated[int, typer.Option("--size", min=256, max=2048)] = 1024,
    runs_dir: RunsDirOpt = None,
) -> None:
    """Texture the picked round's object.glb (``addons.select``) → artifacts/object_textured.glb (+ textures/)."""
    from codeverse3d import reference
    from codeverse3d.addons import select
    from codeverse3d.cost.instrument import run_ledger
    from codeverse3d.record.record import RecordError, load_record
    from codeverse3d.spatial.tool_common import load_plan
    from codeverse3d.texturing.run import texture_pass, texture_supported

    ws = C.open_workspace(slug, runs_dir)
    spec = C.load_spec(ws)
    if not texture_supported(spec.track):
        raise C.CliError(f"texture pass is for object tracks; {spec.track.value} runs use `3dcode texture scene-pack`")
    try:
        rec = load_record(ws)
    except RecordError as e:
        raise C.CliError(str(e), code=2) from e
    picked = select.summarise(ws.root, record=rec).picked_round
    rnd = next((r for r in rec.rounds if r.index == picked), None)
    if rnd is None or (glb := select.round_file(ws, rnd)) is None:
        raise C.CliError(f"no picked round with its own object.glb to texture in {ws.root.name} (picked: {picked})")
    sheet = ws.rebase(rnd.renders.contact_sheet) if rnd.renders is not None and rnd.renders.contact_sheet else None
    plan = load_plan(ws.plan_path)

    # a post-hoc pass joins the run's ledger when it has one, else the per-process log;
    # it rewrites the run's artifacts, so it holds the run mutex (one writer per run dir)
    with (C.mutating(ws, what=f"3dcode texture pass {ws.root.name}", action="texture"),
          run_ledger(ws.root, run=ws.root.name, create=False)):
        rep = texture_pass(ws, spec, plan, model_id=model or spec.backends.planner,
                           image_model=reference.get_image_model(image_model or ""), judge=judge,
                           judge_model_id=judge_model, size=size, glb_in=glb, sheet=sheet)
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
    from codeverse3d.cost.instrument import run_ledger
    from codeverse3d.spatial.tool_common import load_plan
    from codeverse3d.texturing.plan import scene_texture_pack, texture_pack_prompt

    ws = C.open_workspace(slug, runs_dir)
    spec = C.load_spec(ws)
    plan = load_plan(ws.plan_path)
    if not isinstance(plan, ScenePlan):
        raise C.CliError("scene-pack needs a scene run (plan.json with zones)")
    model_id = spec.backends.planner if model is None else model

    # like `pass`: the pack's plan + image spend joins the run's ledger when it has one
    with (C.mutating(ws, what=f"3dcode texture scene-pack {ws.root.name}", action="texture"),
          run_ledger(ws.root, run=ws.root.name, create=False)):
        pack = scene_texture_pack(plan, out or ws.public / "textures", reference.get_image_model(image_model or ""), model_id, size=size, n_max=n)
    rows = {name: f"{e.file or 'FAILED'}  tile={e.tile_size_m:.2f}m {e.material_family}/{e.role} seam={e.seam_score:.3f}"
            + (f"  {e.error}" if e.error else "") for name, e in pack.entries.items()}
    console.print(kv_table("scene texture pack", rows))
    console.print(kv_table("summary", {"dir": pack.out_dir, "manifest": pack.manifest_path, "source": pack.source,
                                       "cost": f"${pack.usage.cost_usd:.4f}", "n": len(pack.manifest())}))
    console.print(texture_pack_prompt(pack.manifest()))

"""Per-dialect executability runners. Each 3D-code type plugs in how to RUN a project's
code in its runtime(s) and report pass/fail. Runs on the CONTRIBUTOR's machine, in the
contributor's own runtimes — never sends data anywhere.
"""

from __future__ import annotations

from pathlib import Path

from ..config import Config
from . import blender_python, build123d_dialect, cadquery_dialect, freecad_dialect, openscad_dialect


def run_dialect(dialect: str, project_dir: Path, meta: dict, cfg: Config) -> dict:
    """Execute the project in its runtime(s); return {runtime: {status, ...}}."""
    code_file = project_dir / meta["code"]["file"]
    if dialect == "blender_python":
        return blender_python.run(code_file, {"5.0": cfg.blender_5_0, "5.1": cfg.blender_5_1})
    if dialect == "cadquery":
        return {"cadquery": cadquery_dialect.run(code_file)}
    if dialect == "build123d":
        return {"build123d": build123d_dialect.run(code_file)}
    if dialect == "openscad":
        return {"openscad": openscad_dialect.run(code_file, cfg.openscad)}
    if dialect == "freecad":
        return {"freecad": freecad_dialect.run(code_file, cfg.freecadcmd)}
    return {"runtime": {"status": "unsupported", "error": f"no exec runner for dialect '{dialect}'"}}


def render_dialect(dialect: str, project_dir: Path, meta: dict, cfg: Config, mode: str) -> dict:
    """Render the project's code into project_dir/renders/. mode = white|textured."""
    code_file = project_dir / meta["code"]["file"]
    blender = cfg.blender_5_0 or cfg.blender_5_1
    rdir = project_dir / "renders"
    if dialect == "blender_python":
        return blender_python.render(code_file, rdir, blender, mode)
    if dialect == "cadquery":
        return cadquery_dialect.render(code_file, rdir, blender, mode)
    if dialect == "build123d":
        return build123d_dialect.render(code_file, rdir, blender, mode)
    if dialect == "openscad":
        return openscad_dialect.render(code_file, rdir, blender, mode, cfg.openscad)
    if dialect == "freecad":
        return freecad_dialect.render(code_file, rdir, blender, mode, cfg.freecadcmd)
    return {"status": "unsupported", "error": f"no render runner for dialect '{dialect}'"}

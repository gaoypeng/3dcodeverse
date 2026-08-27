"""``urdf_blender`` LanguageRuntime: bpy link meshes + hand-written URDF.

build(ws):
  lint URDF/model.py → Blender wrapper (exec model.py, export meshes/<link>.glb,
  census) → load_urdf → FK consistency (authored bbox == FK bbox at q=0) →
  pose sweep (overlaps / floating) → artifacts/object.glb (hierarchical, Y-up)
  + artifacts/robot.urdf + artifacts/meshes/ + artifacts/articulation.json.

``ok`` iff the script ran, the URDF is valid, FK is consistent and nothing
interpenetrates by more than ``REST_PENETRATION_MAX_M`` at rest.  Overlaps in
moved poses and floating links are reported in ``census["articulation"]`` for
the gate layer (``sweep_findings``).
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

from codeverse.config import Settings, get_settings
from codeverse.contracts.artifacts import BuildResult, GateReport
from codeverse.contracts.common import ENTRY_FILE, Language
from codeverse.contracts.plan import ArticulatedPlan, Plan
from codeverse.languages._common import ProcResult, read_json_file, run_subprocess
from codeverse.languages.urdf.consistency import check_fk_consistency
from codeverse.languages.urdf.lint import lint_workspace
from codeverse.languages.urdf.skeleton import write_skeleton
from codeverse.prompts import PROMPTS_DIR, load_text
from codeverse.spatial.joints import (
    UrdfError,
    load_urdf,
    pose_samples,
    sweep_collisions,
    sweep_findings,
    urdf_to_glb,
)
from codeverse.workspace import ArtifactStage, Workspace

WRAPPER = Path(__file__).resolve().parent / "wrappers" / "run_bpy_links.py"
CONTRACT_MD = Path(__file__).resolve().parent / "CONTRACT.md"
REST_PENETRATION_MAX_M = 0.005
FK_TOL_M = 0.001

#: every canonical artifact this build owns — staged together so a failed build
#: can never leave a previous round's output looking current
STAGED_OUTPUTS = ("build.json", "census.json", "meshes", "robot.urdf", "articulation.json", "object.glb")


def _tail(s: str, n: int = 3000) -> str:
    return s[-n:] if s else ""


class UrdfBlenderRuntime:
    language = Language.URDF_BLENDER
    entry_globs = (ENTRY_FILE[Language.URDF_BLENDER], "src/robot.urdf")

    # ------------------------------------------------------------ protocol
    def skeleton(self, ws: Workspace, plan: Plan) -> list[Path]:
        if not isinstance(plan, ArticulatedPlan):
            raise TypeError(f"urdf_blender skeleton needs an ArticulatedPlan, got {type(plan).__name__}")
        return write_skeleton(ws, plan)

    def lint(self, ws: Workspace) -> GateReport:
        return lint_workspace(ws)

    def contract_doc(self) -> str:
        try:
            return load_text("urdf/contract.md")
        except FileNotFoundError:
            return CONTRACT_MD.read_text()

    def cookbook_path(self) -> Path:
        return PROMPTS_DIR / "urdf" / "cookbook.md"

    # ------------------------------------------------------------ build
    def build(self, ws: Workspace, *, timeout_s: int | None = None) -> BuildResult:
        """All six canonical outputs go through one :class:`ArtifactStage`: entering
        it invalidates them BEFORE any early return can leak a previous round's
        files, everything is written to staging, and only an ``ok=True`` build
        promotes the full set.  Every exit path publishes the FINAL BuildResult as
        ``artifacts/build.json`` — the wrapper's own build.json used to stay on disk
        saying ``ok: true`` while a post-wrapper check (UrdfError / FkInconsistent /
        RestPenetration) failed the build."""
        t0 = time.time()
        settings = get_settings()
        timeout_s = timeout_s or settings.limits.build_timeout_s
        ws.artifacts.mkdir(parents=True, exist_ok=True)
        with ws.stage_artifacts(*STAGED_OUTPUTS) as stage:
            return self._build_staged(ws, stage, t0=t0, settings=settings, timeout_s=timeout_s)

    def _build_staged(self, ws: Workspace, stage: ArtifactStage, *, t0: float, settings: Settings,
                      timeout_s: int) -> BuildResult:
        art = ws.artifacts

        def finish(res: BuildResult) -> BuildResult:
            # disk never contradicts the returned result: build.json is ALWAYS the
            # final BuildResult; the rest of the set is published only on ok
            ws.write_json(stage.path("build.json"), res)
            if res.ok:
                stage.promote()
            elif stage.path("census.json").is_file():
                stage.promote("build.json", "census.json")
            else:
                stage.promote("build.json")
            return res

        def fail(error_type: str, message: str, *, file: str = "src/robot.urdf", line: int | None = None,
                 census: dict[str, Any] | None = None, **kw: Any) -> BuildResult:
            return finish(BuildResult(ok=False, language=self.language.value, error_type=error_type,
                                      error_message=message, error_file=file, error_line=line,
                                      duration_ms=int((time.time() - t0) * 1000), census=census or {}, **kw))

        # 1. lint (cheap, no Blender)
        lint = self.lint(ws)
        census: dict[str, Any] = {"lint": [f.model_dump(mode="json") for f in lint.findings]}
        if not lint.passed:
            errs = lint.errors
            msg = "\n".join(f"- {f.message}" + (f"  → {f.fix_hint}" if f.fix_hint else "") for f in errs[:12])
            return fail("LintError", f"{len(errs)} lint error(s):\n{msg}", file=str(errs[0].target or "src/robot.urdf"),
                        line=errs[0].data.get("line"), census=census)

        # 2. Blender wrapper (writes build.json / census.json / meshes/ into the staging dir)
        blender = settings.resolve_blender()
        if not blender:
            return fail("BlenderNotFound", "no Blender binary (set CV3D_BINARIES__BLENDER)", file="", census=census)
        build_json, census_json = stage.path("build.json"), stage.path("census.json")
        proc = _run_blender(blender, ws, stage.staging_dir, timeout_s, settings.limits.bpy_rlimit_gb)
        if proc.timed_out:
            return fail("Timeout", f"Blender build exceeded {timeout_s}s (killed)", file="src/model.py", census=census,
                        stdout_tail=_tail(proc.stdout), stderr_tail=_tail(proc.stderr))
        if not build_json.is_file():
            return fail("WrapperCrash", f"wrapper produced no build.json (exit {proc.returncode})", file="src/model.py",
                        census=census, stdout_tail=_tail(proc.stdout), stderr_tail=_tail(proc.stderr))
        wb = read_json_file(build_json)
        wcensus = read_json_file(census_json) if census_json.is_file() else {}
        census.update({k: wcensus.get(k) for k in ("objects", "links", "unmatched_objects", "missing_links", "hints") if k in wcensus})
        if not wb.get("ok"):
            hints = "\n".join(f"  hint: {h}" for h in (wcensus.get("hints") or {}).values())
            return fail(wb.get("error_type") or "ScriptError", (wb.get("error_message") or "") + ("\n" + hints if hints else ""),
                        file=wb.get("error_file") or "src/model.py", line=wb.get("error_line"), census=census,
                        stdout_tail=_tail(wb.get("stdout_tail", "") or proc.stdout),
                        stderr_tail=_tail(wb.get("stderr_tail", "") or proc.stderr))

        # 3. URDF copy + load (staged files; extra_paths name the canonical homes)
        urdf_staged = stage.path("robot.urdf")
        shutil.copyfile(ws.root / "src" / "robot.urdf", urdf_staged)
        extra = {"urdf": str(art / "robot.urdf"), "meshes_dir": str(art / "meshes")}
        try:
            robot = load_urdf(urdf_staged, stage.path("meshes"))
        except UrdfError as e:
            return fail("UrdfError", str(e), census=census)

        # 4. FK consistency
        fk_findings = check_fk_consistency(robot, census.get("links") or {}, tol_m=FK_TOL_M)
        census["fk_check"] = [f.model_dump(mode="json") for f in fk_findings]
        if fk_findings:
            msg = "\n".join(f"- {f.as_line()}" for f in fk_findings)
            return fail("FkInconsistent", f"URDF frames do not reproduce the authored geometry:\n{msg}", census=census)

        # 5. pose sweep
        report = sweep_collisions(robot, pose_samples(robot, n_random=8, seed=0))
        findings = sweep_findings(report, rest_max_m=REST_PENETRATION_MAX_M)
        stage.path("articulation.json").write_text(json.dumps({"report": report.model_dump(mode="json"),
                                                               "findings": [f.model_dump(mode="json") for f in findings]}, indent=1))
        extra["articulation"] = str(art / "articulation.json")
        for name, n in report.summary.link_islands.items():
            census.setdefault("links", {}).setdefault(name, {})["islands"] = n
        census["articulation"] = {"summary": report.summary.model_dump(mode="json"),
                                  "findings": [f.model_dump(mode="json") for f in findings],
                                  "n_joints": len(robot.joints), "movable_joints": [j.name for j in robot.movable_joints()]}

        # 6. canonical GLB at rest
        urdf_to_glb(robot, stage.path("object.glb"), None)
        extra["object_glb"] = str(art / "object.glb")
        res = BuildResult(ok=True, language=self.language.value, glb_path=str(art / "object.glb"), extra_paths=extra,
                          stdout_tail=_tail(wb.get("stdout_tail", "")), duration_ms=int((time.time() - t0) * 1000), census=census)
        if report.summary.rest_max_penetration_m > REST_PENETRATION_MAX_M:
            worst = [f for f in findings if f.data.get("pose") == {} and f.severity == "error"]
            res.ok = False
            res.error_type = "RestPenetration"
            res.error_file = "src/model.py"
            res.error_message = (f"links interpenetrate by {report.summary.rest_max_penetration_m*1000:.1f} mm at the rest pose "
                                 f"(max {REST_PENETRATION_MAX_M*1000:.0f} mm):\n" +
                                 "\n".join(f"- {f.as_line()}" for f in worst[:8]))
            res.glb_path = None      # the fresh GLB is NOT published on a failed build
            res.extra_paths = {}
        return finish(res)


def _run_blender(blender: str, ws: Workspace, art: Path, timeout_s: int, rlimit_gb: int) -> ProcResult:
    cmd = [blender, "-b", "--factory-startup", "--python", str(WRAPPER), "--",
           "--script", str(ws.root / "src" / "model.py"), "--urdf", str(ws.root / "src" / "robot.urdf"),
           "--out", str(art), "--rlimit-gb", str(rlimit_gb)]
    # whitelist env (no inherited PYTHONPATH, user config pinned into artifacts) — deliberately
    # stricter than blender.runtime.blender_env(); keep it that way
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", str(ws.root)),
           "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1", "BLENDER_USER_CONFIG": str(art / ".blender_config")}
    return run_subprocess(cmd, cwd=ws.root, env=env, timeout_s=timeout_s)

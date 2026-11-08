"""scene_threejs: multi-file three.js scenes (+GLSL, +GLB assets) — lint, probe-build, skeleton, assembler."""

from __future__ import annotations

import re
import time
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from codeverse3d.contracts.artifacts import (
    BuildResult,
    GateFinding,
    GateReport,
    LintKind,
    RenderSet,
    Severity,
)
from codeverse3d.contracts.common import ENTRY_FILE, Language
from codeverse3d.contracts.plan import AssetPlan, CameraPlan, Plan
from codeverse3d.conventions import (
    SCENE_TIMES,
    to_snake,
)
from codeverse3d.languages._common import line_of, missing_entry, target_file, target_line, ws_rel
from codeverse3d.languages._js_lint import (
    ExportsCache,
    ImportKind,
    ImportVerdict,
    SyntaxProblem,
    check_imports,
    check_named_imports,
    describe_exports,
    js_sources,
    node_check_syntax,
    strip_js,
)
from codeverse3d.workspace import Workspace

if TYPE_CHECKING:
    from codeverse3d.spatial.probes import SceneProbeResult

# ===================================================================== lint
GATE = "lint:scene_threejs"

_CREATE_SCENE_RE = re.compile(r"export\s+(?:async\s+)?function\s+createScene\b|export\s+(?:const|let)\s+createScene\b|export\s*\{[^}]*\bcreateScene\b[^}]*\}")
_BUILD_RE = re.compile(r"export\s+(?:async\s+)?function\s+build\b|export\s+(?:const|let)\s+build\b|export\s*\{[^}]*\bbuild\b[^}]*\}")
_BUILD_ANY_RE = re.compile(r"export\s+(?:async\s+)?function\s+build[A-Za-z0-9_]*\b|export\s+(?:const|let)\s+build[A-Za-z0-9_]*\b")

_PATTERNS: list[tuple[re.Pattern[str], Severity, str, str, LintKind]] = [
    (re.compile(r"\brequestAnimationFrame\s*\("), Severity.ERROR, "requestAnimationFrame: the host drives update(t, dt)", "delete your render loop; animate inside update(t, dt)", LintKind.HARNESS_OWNED),
    (re.compile(r"\bsetAnimationLoop\s*\("), Severity.ERROR, "renderer.setAnimationLoop: the host drives rendering", "remove it; animate inside update(t, dt)", LintKind.HARNESS_OWNED),
    (re.compile(r"new\s+THREE\.WebGLRenderer\b|new\s+WebGLRenderer\b|new\s+THREE\.WebGPURenderer\b"), Severity.ERROR, "creating a renderer: the host owns the WebGLRenderer", "use the `renderer` passed to createScene only for capability checks", LintKind.HARNESS_OWNED),
    (re.compile(r"\brenderer\.render\s*\("), Severity.ERROR, "renderer.render(): the host renders", "remove; return {scene, cameras, update} and let the host render", LintKind.HARNESS_OWNED),
    (re.compile(r"\bfetch\s*\(\s*['\"]https?://|XMLHttpRequest|new\s+WebSocket\b|['\"]https?://[^'\"]+\.(?:js|mjs|glb|gltf|png|jpg|hdr)['\"]"), Severity.ERROR, "network / CDN access", "no network: import 'three' / 'three/addons/*' and load assets from '/assets/<name>.glb'", LintKind.SANDBOX),
    (re.compile(r"\bdocument\.(?:body|getElementById|querySelector|querySelectorAll|head)\b|\bwindow\.addEventListener\b|\.innerHTML\b|\blocalStorage\b"), Severity.ERROR, "DOM/page access outside the host contract", "the host owns the page; only document.createElement('canvas') for procedural textures is allowed", LintKind.SANDBOX),
    (re.compile(r"\bTHREE\.Geometry\b|\bFace3\b|\bexamples/js/"), Severity.ERROR, "removed three.js API (Geometry/Face3/examples/js)", "use BufferGeometry and 'three/addons/...' imports", LintKind.API_TRAP),
    (re.compile(r"\b(?:Box|Plane|Sphere|Cylinder|Cone|Torus|Circle|Ring|Shape|Extrude|Lathe|Tube|Icosahedron|Dodecahedron|Octahedron|Tetrahedron|Polyhedron|Edges|Wireframe)BufferGeometry\b"), Severity.ERROR, "*BufferGeometry aliases were removed (r144)", "drop the 'Buffer' infix: THREE.BoxGeometry", LintKind.API_TRAP),
    (re.compile(r"\.outputEncoding\b|\bsRGBEncoding\b|\bLinearEncoding\b|\.encoding\s*="), Severity.ERROR, "texture/renderer .encoding was removed (r152)", "use texture.colorSpace = THREE.SRGBColorSpace (colour maps only)", LintKind.API_TRAP),
    (re.compile(r"\bMath\.random\s*\("), Severity.WARN, "Math.random makes renders non-reproducible", "use a seeded PRNG (e.g. mulberry32) so rounds can be compared", LintKind.NONDETERMINISM),
    (re.compile(r"\brequire\s*\(|\bprocess\.env\b|\bmodule\.exports\b"), Severity.ERROR, "CommonJS / node globals in browser ESM", "use ESM import/export only", LintKind.SANDBOX),
    (re.compile(r"\bTHREE\.ImageUtils\b|\bTHREE\.SceneUtils\b|\bMeshFaceMaterial\b|\bMultiMaterial\b"), Severity.ERROR, "removed three.js helper", "use TextureLoader / material arrays", LintKind.API_TRAP),
]


def _f(kind: LintKind, sev: Severity, msg: str, *, target: str, hint: str = "", **data: object) -> GateFinding:
    return GateFinding(gate=GATE, severity=sev, target=target, message=msg, fix_hint=hint, data={"kind": kind.value, **data})


def _is_lib(path: Path, ws: Workspace) -> bool:
    """Is this one of the harness's own effect modules (``src/lib/*.js``)?  They are verified by the
    harness suite, not linted as agent code; syntax is still checked."""
    return path.parent == ws.src / "lib"


def _syntax_finding(p: SyntaxProblem, rel: str) -> GateFinding:
    return _f(LintKind.SYNTAX, Severity.ERROR, f"syntax: {p.message}", target=f"{rel}:{p.line}" if p.line else rel,
              hint="fix the syntax error at the quoted line (node --check)", line=p.line, detail=p.stderr_tail)


def _check_imports(rel: str, text: str, ws: Workspace, path: Path, export_cache: ExportsCache) -> list[GateFinding]:
    """The import policy on one agent file: specifier kinds, then every name imported from a relative
    target against what it exports (an unknown name is a SyntaxError at module link)."""
    def make_finding(v: ImportVerdict, spec: str, line: int) -> GateFinding:
        tgt = f"{rel}:{line}"
        if v.kind is ImportKind.ESCAPES:
            return _f(LintKind.BAD_IMPORT, Severity.ERROR, f"import '{spec}' escapes src/", target=tgt, hint="keep all code under src/")
        if v.kind is ImportKind.MISSING:
            return _f(LintKind.BAD_IMPORT, Severity.ERROR, f"import of missing file '{spec}'", target=tgt,
                      hint="create the file or fix the path (relative imports need the .js extension)")
        if v.kind is ImportKind.ABSOLUTE:
            return _f(LintKind.BAD_IMPORT, Severity.ERROR, f"absolute import '{spec}'", target=tgt, hint="use relative imports ('./x.js')")
        return _f(LintKind.BAD_IMPORT, Severity.ERROR, f"import '{spec}' is not allowed (only 'three', 'three/addons/*', relative files)", target=tgt,
                  hint="no CDN / npm packages; write the helper yourself in src/")

    def missing_name(spec: str, name: str, line: int, names: frozenset[str]) -> GateFinding:
        return _f(LintKind.BAD_IMPORT, Severity.ERROR,
                  f"import {{ {name} }} from '{spec}': that module exports no '{name}' ({describe_exports(names)})",
                  target=f"{rel}:{line}",
                  hint="import only names the module actually exports (for src/lib modules: the names the effects catalog lists)")

    findings = check_imports(text, path, ws.src, make_finding=make_finding)[0]
    return findings + check_named_imports(text, path, ws.src, make_finding=missing_name, exports=export_cache)


def lint(ws: Workspace) -> GateReport:
    t0 = time.time()
    findings: list[GateFinding] = []
    files = js_sources(ws.src, dotfiles=True, mjs=True)
    scene = ws.src / "scene.js"
    if not scene.is_file():
        findings.append(_f(LintKind.MISSING_FILE, Severity.ERROR, "src/scene.js is missing", target="src/scene.js",
                           hint="create src/scene.js exporting createScene({THREE, renderer, loaders})"))
    syntax = node_check_syntax(files)
    export_cache: ExportsCache = {}
    for path in files:
        rel = ws_rel(ws, path)
        text = path.read_text(errors="replace")
        if path in syntax:
            findings.append(_syntax_finding(syntax[path], rel))
            continue
        if _is_lib(path, ws):
            continue
        findings.extend(_check_imports(rel, text, ws, path, export_cache))
        code = strip_js(text, strings=False)
        for pat, sev, msg, hint, kind in _PATTERNS:
            m = pat.search(code)
            if m:
                line = line_of(code, m.start())
                findings.append(_f(kind, sev, msg, target=f"{rel}:{line}", hint=hint))
        if path == scene and not _CREATE_SCENE_RE.search(text):
            findings.append(_f(LintKind.LAYOUT, Severity.ERROR, "src/scene.js does not export createScene", target=rel,
                               hint="export function createScene({ THREE, renderer, loaders }) { ... return { scene, cameras, update } }"))
        if path.parent == ws.src / "zones" and not _BUILD_RE.search(text):
            findings.append(_f(LintKind.LAYOUT, Severity.ERROR, "zone module does not export build(ctx)", target=rel,
                               hint="export function build(ctx) { const g = new THREE.Group(); g.name = 'ZoneName'; ...; return g; }"))
        if path.parent == ws.src / "assets" and not _BUILD_ANY_RE.search(text):
            findings.append(_f(LintKind.LAYOUT, Severity.WARN, "asset module exports no build<Pascal>(THREE) factory", target=rel,
                               hint="export function buildLamp(THREE) { ... return group; }"))
    return GateReport.of(GATE, findings, duration_ms=int((time.time() - t0) * 1000))


# ===================================================================== skeleton
STARTER_DIR = Path(__file__).resolve().parent / "starter" / "src"
#: the harness-owned effect library (``src/lib/*.js``), shipped into every workspace; the example
#: scene's own files (``zones/``, ``assets/``, ``shaders/``) ship only without a plan
LIB_DIR = STARTER_DIR / "lib"


def lib_files() -> list[Path]:
    return sorted(LIB_DIR.glob("*.js"))


def _write(path: Path, text: str, written: list[Path]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    written.append(path)


def write_lib(ws: Workspace) -> list[Path]:
    """Ship the effect library into ``ws.src/lib`` (``3dcode init``); returns the written paths."""
    written: list[Path] = []
    for src in lib_files():
        _write(ws.src / "lib" / src.name, src.read_text(), written)
    return written


# ===================================================================== layout
#: what a whole-scene session edits: the entry and the environment
SCENE_FILES: tuple[str, ...] = ("src/scene.js", "src/env.js")


def zone_file(name: str) -> str:
    return f"src/zones/{to_snake(name)}.js"


def asset_file(asset: AssetPlan) -> str:
    snake = to_snake(asset.name)
    return f"src/assets/{snake}.js" if asset.kind == "threejs" else f"public/assets/{snake}.glb"


# ===================================================================== assemble


# --------------------------------------------------------------------------- the env module alone


def lib_interface(ws: Workspace) -> str:
    """The workspace's ``src/lib/`` surface, one line per module: exported names and parameter lists."""
    from codeverse3d.languages._js_lint import export_signatures

    lib = ws.src / "lib"
    rows = [f"lib/{p.name}: " + ", ".join(sigs) for p in sorted(lib.glob("*.js"))
            if (sigs := export_signatures(p.read_text(errors="replace")))] if lib.is_dir() else []
    return "\n".join(rows)


# --------------------------------------------------------------------------- scene.js


# ===================================================================== runtime


class SceneThreeJsRuntime:
    language = Language.SCENE_THREEJS
    entry_globs: tuple[str, ...] = (ENTRY_FILE[Language.SCENE_THREEJS], "src/zones/*.js", "src/assets/*.js", "src/env.js", "src/shaders/*.js")
    env_file: str = SCENE_FILES[1]

    zone_file = staticmethod(zone_file)
    asset_file = staticmethod(asset_file)

    def expected_files(self, plan: Plan | None) -> list[str]:
        return list(SCENE_FILES)


    def probe(self, ws: Workspace, *, timeout_s: float | None = None) -> SceneProbeResult:
        from codeverse3d.spatial.probes import probe_scene

        return probe_scene(ws, timeout_s=timeout_s)

    def render_scene(self, ws: Workspace, out_dir: Path, *, cameras: list[CameraPlan] | None = None, orbit: bool = True,
                     times: Sequence[float] = SCENE_TIMES, width: int = 1024, height: int = 576,
                     sheet: bool = True) -> RenderSet:
        from codeverse3d.spatial.render_scene import render_scene

        return render_scene(ws, out_dir, cameras=cameras, orbit=orbit, times=tuple(times), width=width, height=height, sheet=sheet)


    def lint(self, ws: Workspace) -> GateReport:
        return lint(ws)

    def lib_interface(self, ws: Workspace) -> str:
        return lib_interface(ws)


    def build(self, ws: Workspace, *, timeout_s: int | None = None) -> BuildResult:
        """Probe + shader preflight (one browser boot); ok iff the module loads and no shader errors."""
        t0 = time.time()
        ws.artifacts.mkdir(parents=True, exist_ok=True)
        # wipe stale driver outputs so a crashed probe never looks current (census.json is only rewritten `if census:`)
        ws.stage_artifacts("census.json", "scene_probe.json", "shader_preflight.json", "build.json").invalidate()
        if (missing := missing_entry(ws, self.language)) is not None:
            return missing
        from codeverse3d.spatial.probes import run_probe

        probe, shaders, census = run_probe(ws, compile=True, timeout_s=timeout_s)
        if census:
            ws.write_json(ws.artifacts / "census.json", census)
        gates_dir = ws.artifacts / "gates"
        ws.write_json(gates_dir / "scene_probe.json", probe)
        ws.write_json(gates_dir / "shader_preflight.json", shaders)
        errors = sorted(probe.errors + shaders.errors, key=lambda f: 0 if target_line(f.target) else 1)
        first = errors[0] if errors else None
        res = BuildResult(
            ok=probe.passed and shaders.passed,
            language=self.language.value,
            glb_path=None,
            extra_paths={
                "census": str(ws.artifacts / "census.json"),
                "scene_probe": str(gates_dir / "scene_probe.json"),
                "shader_preflight": str(gates_dir / "shader_preflight.json"),
            },
            stdout_tail=_summary_text(probe, shaders),
            error_type=(first.data.get("kind") or first.gate) if first else "",
            error_message=first.message if first else "",
            error_file=target_file(first.target) if first else "",
            error_line=target_line(first.target) if first else None,
            duration_ms=int((time.time() - t0) * 1000),
            census=census,
            gates=[probe, shaders],
            # a driver that could not run is not a scene defect; the round must not spend repair budget on it
            harness_failure=bool(first is not None and first.data.get("harness_failure")),
        )
        ws.write_json(ws.artifacts / "build.json", res)
        return res


def _summary_text(probe: GateReport, shaders: GateReport) -> str:
    lines = [f"scene_probe: {'ok' if probe.passed else 'FAILED'} ({len(probe.errors)} errors)"]
    lines += [f"  - {f.target or ''}: {f.message}"[:400] for f in probe.findings if f.severity != Severity.INFO][:12]
    lines.append(f"shader_preflight: {'ok' if shaders.passed else 'FAILED'} ({len(shaders.errors)} errors)")
    lines += [f"  - {f.target or ''}: {f.message}"[:400] for f in shaders.findings if f.severity != Severity.INFO][:12]
    return "\n".join(lines)


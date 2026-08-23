"""Static lint for ``scene_threejs`` workspaces (no browser).

Checks: ``node --check`` syntax per file, import allowlist ('three',
'three/addons/*', relative), forbidden network/DOM/render-loop usage, required
exports (``createScene`` in src/scene.js, ``build`` in zones), removed three.js
APIs, oversized files.  Returns a GateReport ``lint:scene_threejs``.
"""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path

from codeverse.config import get_settings
from codeverse.contracts.artifacts import GateFinding, GateReport, Severity
from codeverse.workspace import Workspace

GATE = "lint:scene_threejs"
MAX_LINES = 800

_IMPORT_RE = re.compile(r"""(?:^|\n)\s*(?:import|export)\s[^;'"\n]*?from\s*['"]([^'"]+)['"]|import\s*\(\s*['"]([^'"]+)['"]\s*\)""")
_CREATE_SCENE_RE = re.compile(r"export\s+(?:async\s+)?function\s+createScene\b|export\s+(?:const|let)\s+createScene\b|export\s*\{[^}]*\bcreateScene\b[^}]*\}")
_BUILD_RE = re.compile(r"export\s+(?:async\s+)?function\s+build\b|export\s+(?:const|let)\s+build\b|export\s*\{[^}]*\bbuild\b[^}]*\}")
_BUILD_ANY_RE = re.compile(r"export\s+(?:async\s+)?function\s+build[A-Za-z0-9_]*\b|export\s+(?:const|let)\s+build[A-Za-z0-9_]*\b")

#: (regex, severity, message, hint)
_PATTERNS: list[tuple[re.Pattern[str], Severity, str, str]] = [
    (re.compile(r"\brequestAnimationFrame\s*\("), Severity.ERROR, "requestAnimationFrame: the host drives update(t, dt)", "delete your render loop; animate inside update(t, dt)"),
    (re.compile(r"\bsetAnimationLoop\s*\("), Severity.ERROR, "renderer.setAnimationLoop: the host drives rendering", "remove it; animate inside update(t, dt)"),
    (re.compile(r"new\s+THREE\.WebGLRenderer\b|new\s+WebGLRenderer\b|new\s+THREE\.WebGPURenderer\b"), Severity.ERROR, "creating a renderer: the host owns the WebGLRenderer", "use the `renderer` passed to createScene only for capability checks"),
    (re.compile(r"\brenderer\.render\s*\("), Severity.ERROR, "renderer.render(): the host renders", "remove; return {scene, cameras, update} and let the host render"),
    (re.compile(r"\bfetch\s*\(\s*['\"]https?://|XMLHttpRequest|new\s+WebSocket\b|['\"]https?://[^'\"]+\.(?:js|mjs|glb|gltf|png|jpg|hdr)['\"]"), Severity.ERROR, "network / CDN access", "no network: import 'three' / 'three/addons/*' and load assets from '/assets/<name>.glb'"),
    (re.compile(r"\bdocument\.(?:body|getElementById|querySelector|querySelectorAll|head)\b|\bwindow\.addEventListener\b|\.innerHTML\b|\blocalStorage\b"), Severity.ERROR, "DOM/page access outside the host contract", "the host owns the page; only document.createElement('canvas') for procedural textures is allowed"),
    (re.compile(r"\bTHREE\.Geometry\b|\bFace3\b|\bexamples/js/"), Severity.ERROR, "removed three.js API (Geometry/Face3/examples/js)", "use BufferGeometry and 'three/addons/...' imports"),
    (re.compile(r"\b(?:Box|Plane|Sphere|Cylinder|Cone|Torus|Circle|Ring|Shape|Extrude|Lathe|Tube|Icosahedron|Dodecahedron|Octahedron|Tetrahedron|Polyhedron|Edges|Wireframe)BufferGeometry\b"), Severity.ERROR, "*BufferGeometry aliases were removed (r144)", "drop the 'Buffer' infix: THREE.BoxGeometry"),
    (re.compile(r"\.outputEncoding\b|\bsRGBEncoding\b|\bLinearEncoding\b|\.encoding\s*="), Severity.ERROR, "texture/renderer .encoding was removed (r152)", "use texture.colorSpace = THREE.SRGBColorSpace (colour maps only)"),
    (re.compile(r"\bMath\.random\s*\("), Severity.WARN, "Math.random makes renders non-reproducible", "use a seeded PRNG (e.g. mulberry32) so rounds can be compared"),
    (re.compile(r"\brequire\s*\(|\bprocess\.env\b|\bmodule\.exports\b"), Severity.ERROR, "CommonJS / node globals in browser ESM", "use ESM import/export only"),
    (re.compile(r"\bTHREE\.ImageUtils\b|\bTHREE\.SceneUtils\b|\bMeshFaceMaterial\b|\bMultiMaterial\b"), Severity.ERROR, "removed three.js helper", "use TextureLoader / material arrays"),
]


def _f(sev: Severity, msg: str, *, target: str, hint: str = "", **data: object) -> GateFinding:
    return GateFinding(gate=GATE, severity=sev, target=target, message=msg, fix_hint=hint, data=dict(data))


def _js_files(ws: Workspace) -> list[Path]:
    if not ws.src.is_dir():
        return []
    return sorted(p for p in ws.src.rglob("*.js") if "node_modules" not in p.parts) + sorted(ws.src.rglob("*.mjs"))


def _node_check(path: Path, rel: str) -> GateFinding | None:
    node = get_settings().binaries.node or "node"
    try:
        # ESM syntax check via stdin: `node --check file.js` treats a bare .js as CommonJS-or-detect
        proc = subprocess.run([node, "--input-type=module", "--check"], input=path.read_text(errors="replace"),
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        return _f(Severity.ERROR, f"node --check could not run: {e}", target=rel, hint="harness problem (node missing?)")
    if proc.returncode == 0:
        return None
    err = proc.stderr.strip()
    line = None
    m = re.search(r"\[stdin\]:(\d+)", err) or re.search(r":(\d+)\n", err)
    if m:
        line = int(m.group(1))
    msg = next((ln for ln in err.splitlines() if "Error" in ln), err.splitlines()[0] if err else "syntax error")
    return _f(Severity.ERROR, f"syntax: {msg}", target=f"{rel}:{line}" if line else rel,
              hint="fix the syntax error at the quoted line (node --check)", line=line, detail=err[-600:])


def _check_imports(rel: str, text: str, ws: Workspace, path: Path) -> list[GateFinding]:
    out: list[GateFinding] = []
    for m in _IMPORT_RE.finditer(text):
        spec = m.group(1) or m.group(2)
        line = text.count("\n", 0, m.start()) + 1
        tgt = f"{rel}:{line}"
        if spec == "three" or spec.startswith("three/addons/") or spec.startswith("three/examples/jsm/"):
            continue
        if spec.startswith("./") or spec.startswith("../"):
            dest = (path.parent / spec).resolve()
            if not dest.is_file():
                out.append(_f(Severity.ERROR, f"import of missing file '{spec}'", target=tgt,
                              hint="create the file or fix the path (relative imports need the .js extension)"))
            elif not str(dest).startswith(str(ws.src.resolve())):
                out.append(_f(Severity.ERROR, f"import '{spec}' escapes src/", target=tgt, hint="keep all code under src/"))
            continue
        if spec.startswith("/"):
            out.append(_f(Severity.ERROR, f"absolute import '{spec}'", target=tgt, hint="use relative imports ('./x.js')"))
            continue
        out.append(_f(Severity.ERROR, f"import '{spec}' is not allowed (only 'three', 'three/addons/*', relative files)", target=tgt,
                      hint="no CDN / npm packages; write the helper yourself in src/"))
    return out


def lint(ws: Workspace) -> GateReport:
    """Run all static checks; passed iff no ERROR findings."""
    t0 = time.time()
    findings: list[GateFinding] = []
    files = _js_files(ws)
    scene = ws.src / "scene.js"
    if not scene.is_file():
        findings.append(_f(Severity.ERROR, "src/scene.js is missing", target="src/scene.js",
                           hint="create src/scene.js exporting createScene({THREE, renderer, loaders})"))
    big: list[str] = []
    for path in files:
        rel = path.relative_to(ws.root).as_posix()
        text = path.read_text(errors="replace")
        syntax = _node_check(path, rel)
        if syntax:
            findings.append(syntax)
            continue
        findings.extend(_check_imports(rel, text, ws, path))
        for pat, sev, msg, hint in _PATTERNS:
            m = pat.search(text)
            if m:
                line = text.count("\n", 0, m.start()) + 1
                findings.append(_f(sev, msg, target=f"{rel}:{line}", hint=hint))
        n_lines = text.count("\n") + 1
        if n_lines > MAX_LINES:
            big.append(f"{rel} ({n_lines} lines)")
        if path == scene and not _CREATE_SCENE_RE.search(text):
            findings.append(_f(Severity.ERROR, "src/scene.js does not export createScene", target=rel,
                               hint="export function createScene({ THREE, renderer, loaders }) { ... return { scene, cameras, update } }"))
        if path.parent == ws.src / "zones" and not _BUILD_RE.search(text):
            findings.append(_f(Severity.ERROR, "zone module does not export build(ctx)", target=rel,
                               hint="export function build(ctx) { const g = new THREE.Group(); g.name = 'ZoneName'; ...; return g; }"))
        if path.parent == ws.src / "assets" and not _BUILD_ANY_RE.search(text):
            findings.append(_f(Severity.WARN, "asset module exports no build<Pascal>(THREE) factory", target=rel,
                               hint="export function buildLamp(THREE) { ... return group; }"))
    for b in big:
        findings.append(_f(Severity.WARN, f"large file {b} > {MAX_LINES} lines", target=b.split(" ")[0],
                           hint="split into zones/assets/shaders modules"))
    passed = not any(f.severity == Severity.ERROR for f in findings)
    return GateReport(gate=GATE, passed=passed, findings=findings, duration_ms=int((time.time() - t0) * 1000))

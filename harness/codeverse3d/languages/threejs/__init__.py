"""three.js (static object) language runtime: raw ESM authored by the agent,
GLB export + census by ``runtime_js/export_glb.mjs``."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from codeverse3d.config import get_settings
from codeverse3d.contracts.artifacts import BuildResult, GateFinding, GateReport, Severity
from codeverse3d.contracts.common import ENTRY_FILE, Language
from codeverse3d.contracts.plan import Plan, StaticPlan
from codeverse3d.conventions import to_pascal, to_snake
from codeverse3d.languages._common import BUILD_TIMEOUT
from codeverse3d.languages._docs import RuntimeDocs
from codeverse3d.languages._js_lint import (
    ImportKind,
    ImportVerdict,
    check_imports,
)
from codeverse3d.languages._js_lint import (
    node_check_syntax as check_syntax,  # module-level name: tests monkeypatch it
)
from codeverse3d.proc import read_json_or_none
from codeverse3d.spatial.node import NodeError, NodeResult, run_node, runtime_js_dir
from codeverse3d.workspace import Workspace

# ===================================================================== templates
PACKAGE_JSON = '{ "type": "module", "private": true }\n'

OBJECT_HEADER = """\
// {object_name} — three.js static object (raw ESM).  Harness contract:
//   export function build(THREE) -> THREE.Group   (root.name = object name)
//   Y up, +Z front, meters; object stands on y=0, footprint centred on the Y axis.
//   Parts live in ./parts/<snake>.js, each `export function build<Pascal>(THREE)`
//   returning a Group already at its WORLD pose.  object.js only assembles.
//   Optional idle animation: root.userData.tick = (t, dt) => {{ ... }}
//   Allowed imports: 'three', 'three/addons/...', relative './'.  No network, no DOM.
import * as THREE from 'three';
{imports}

export function build(THREE_) {{
  const root = new THREE.Group();
  root.name = '{object_name}';
{adds}
  return root;
}}
"""

PART_TEMPLATE = """\
// Part: {pascal}  ({role})
// {description}
// Plan bbox (world, meters): centre {center}  extents {extents}{material_line}
// Contract: export function build{pascal}(THREE) -> THREE.Group named '{pascal}', at WORLD pose.
// Y up, +Z front.  Use real sizes in meters.  Name every mesh.  No lights/cameras/renderers.
import * as THREE from 'three';
import {{ RoundedBoxGeometry }} from 'three/addons/geometries/RoundedBoxGeometry.js';

export function build{pascal}(THREE_) {{
  const group = new THREE.Group();
  group.name = '{pascal}';

  // ---- PLACEHOLDER (replace with real construction; keep the group name) ----
  // A rounded box filling the planned bbox.  Copyable recipes:
  //   box:        new THREE.BoxGeometry(w, h, d)
  //   bevelled:   new RoundedBoxGeometry(w, h, d, 4, Math.min(w, h, d) * 0.08)
  //   cylinder:   new THREE.CylinderGeometry(rTop, rBottom, h, 32)
  //   lathe:      new THREE.LatheGeometry(points /* Vector2[] (x=radius, y=height) */, 48)
  //   extrude w/ bevel:
  //     const shape = new THREE.Shape(); shape.moveTo(-w/2, -d/2); shape.lineTo(w/2, -d/2);
  //     shape.lineTo(w/2, d/2); shape.lineTo(-w/2, d/2); shape.closePath();
  //     const geo = new THREE.ExtrudeGeometry(shape, {{ depth: h, bevelEnabled: true,
  //       bevelThickness: 0.004, bevelSize: 0.004, bevelSegments: 3 }});
  //     geo.rotateX(-Math.PI / 2);            // extrude along +Y instead of +Z
  //   merge many: import {{ mergeGeometries }} from 'three/addons/utils/BufferGeometryUtils.js';
  const size = [{ex}, {ey}, {ez}];
  const geometry = new RoundedBoxGeometry(size[0], size[1], size[2], 3, Math.min(...size) * 0.08);
  const material = new THREE.MeshStandardMaterial({{ color: {color}, roughness: 0.6, metalness: 0.05 }});
  const mesh = new THREE.Mesh(geometry, material);
  mesh.name = '{pascal}Body';
  mesh.position.set({cx}, {cy}, {cz});   // world pose (bbox centre)
  group.add(mesh);
  // ---- END PLACEHOLDER ----

  return group;
}}
"""

PLACEHOLDER_COLORS = ("0x9aa5b1", "0xb08968", "0x7f8c8d", "0xc0a080", "0x6c7a89", "0xa0522d", "0x8fa3ad", "0xbfa27a")


# ===================================================================== lint
GATE = "lint:threejs"

_EXPORT_BUILD_RE = re.compile(r"\bexport\s+(?:async\s+)?function\s+(build[A-Za-z0-9_]*)\s*\(")
_EXPORT_CONST_BUILD_RE = re.compile(r"\bexport\s+(?:const|let|var)\s+(build[A-Za-z0-9_]*)\s*=")
_EXPORT_LIST_RE = re.compile(r"\bexport\s*\{([^}]*)\}")
# strings and comments in ONE alternation so a '//' inside a string is not a comment
_TOKEN_RE = re.compile(
    r"""'(?:\\.|[^'\\\n])*'|"(?:\\.|[^"\\\n])*"|`(?:\\.|[^`\\])*`|//[^\n]*|/\*.*?\*/""", re.S
)

# token -> (severity, message, fix hint)
_FORBIDDEN: dict[str, tuple[Severity, str, str]] = {
    r"\bfetch\s*\(": (Severity.ERROR, "network access (fetch) is not allowed in object code", "build geometry procedurally; no downloads"),
    r"\bXMLHttpRequest\b": (Severity.ERROR, "network access (XMLHttpRequest) is not allowed", "remove it"),
    r"\bWebSocket\b": (Severity.ERROR, "network access (WebSocket) is not allowed", "remove it"),
    r"\bdocument\b": (Severity.ERROR, "DOM access (document) is not available: code runs in node", "remove DOM/canvas usage; no textures"),
    r"\bwindow\b": (Severity.ERROR, "DOM access (window) is not available: code runs in node", "remove it"),
    r"\bnavigator\b": (Severity.ERROR, "navigator is not available in node", "remove it"),
    r"\blocalStorage\b": (Severity.ERROR, "localStorage is not available", "remove it"),
    r"\brequire\s*\(": (Severity.ERROR, "CommonJS require() is not allowed; use ESM imports of 'three' only", "import * as THREE from 'three'"),
    r"\bprocess\.": (Severity.ERROR, "node process access is not allowed in object code", "remove it"),
    r"\beval\s*\(": (Severity.ERROR, "eval is not allowed", "remove it"),
    r"\bWebGLRenderer\b": (Severity.ERROR, "do not create renderers: the harness renders", "return the Group from build(THREE); delete renderer code"),
    r"\brequestAnimationFrame\b": (Severity.ERROR, "no render loops: put idle motion in root.userData.tick = (t, dt) => {}", "move per-frame logic into userData.tick"),
    r"\bTextureLoader\b|\bImageLoader\b|\bFileLoader\b": (Severity.ERROR, "loaders/textures cannot be exported from node", "use MeshStandardMaterial colour/roughness/metalness instead"),
    r"\bnew\s+THREE\.(?:Perspective|Orthographic)Camera\b": (Severity.WARN, "cameras in object code are ignored (harness owns the camera)", "delete it"),
    r"\bnew\s+THREE\.[A-Za-z]*Light\s*\(": (Severity.WARN, "lights in object code are exported as extras and ignored by the harness lighting", "delete them"),
    r"\bnew\s+THREE\.Scene\s*\(": (Severity.WARN, "do not build a Scene; return a Group", "replace Scene with Group"),
}


def _strip(src: str) -> str:
    """Blank out comments and string contents (keeps newlines so line numbers survive)."""
    def repl(m: re.Match[str]) -> str:
        tok = m.group(0)
        nl = "\n" * tok.count("\n")
        return nl if tok.startswith("/") else '""' + nl
    return _TOKEN_RE.sub(repl, src)


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _rel(ws: Workspace, p: Path) -> str:
    try:
        return str(p.relative_to(ws.root))
    except ValueError:
        return str(p)


def list_sources(ws: Workspace) -> list[Path]:
    if not ws.src.is_dir():
        return []
    return sorted(p for p in ws.src.rglob("*.js") if "node_modules" not in p.parts and not p.name.startswith("."))


def _lint_imports(ws: Workspace, path: Path, src: str, findings: list[GateFinding]) -> set[Path]:
    """Validate import specifiers; return the set of resolved relative targets."""
    rel = _rel(ws, path)

    def make_finding(v: ImportVerdict, spec: str, line: int) -> GateFinding:
        if v.kind is ImportKind.ESCAPES:
            return GateFinding(gate=GATE, severity=Severity.ERROR, target=rel,
                message=f"{rel}:{line}: import '{spec}' escapes src/", fix_hint="keep all files under src/")
        if v.kind is ImportKind.MISSING:
            assert v.target is not None
            return GateFinding(gate=GATE, severity=Severity.ERROR, target=rel,
                message=f"{rel}:{line}: imported file does not exist: '{spec}'",
                fix_hint=f"create {_rel(ws, v.target)} or fix the path (extension '.js' is required)")
        kind = "URL" if v.kind is ImportKind.URL else "package"
        return GateFinding(gate=GATE, severity=Severity.ERROR, target=rel,
            message=f"{rel}:{line}: import of {kind} '{spec}' is not allowed",
            fix_hint="only 'three', 'three/addons/...' and relative './' imports are available")

    new, targets = check_imports(src, path, ws.src, make_finding=make_finding)
    findings.extend(new)
    return targets


def _lint_forbidden(ws: Workspace, path: Path, src: str, findings: list[GateFinding]) -> None:
    stripped = _strip(src)
    rel = _rel(ws, path)
    for pattern, (sev, msg, hint) in _FORBIDDEN.items():
        m = re.search(pattern, stripped)
        if m:
            findings.append(GateFinding(gate=GATE, severity=sev, target=rel,
                message=f"{rel}:{_line_of(stripped, m.start())}: {msg}", fix_hint=hint))


def _exports(src: str) -> set[str]:
    names = {m.group(1) for m in _EXPORT_BUILD_RE.finditer(src)}
    names |= {m.group(1) for m in _EXPORT_CONST_BUILD_RE.finditer(src)}
    for m in _EXPORT_LIST_RE.finditer(src):
        for item in m.group(1).split(","):
            item = item.strip()
            if not item:
                continue
            alias = item.split(" as ")[-1].strip()
            if alias.startswith("build"):
                names.add(alias)
    return names


def _load_plan(ws: Workspace) -> StaticPlan | None:
    data = read_json_or_none(ws.plan_path)
    if data is None or "parts" not in data or "joints" in data:
        return None
    try:
        return StaticPlan.model_validate(data)
    except ValueError:
        return None


def lint_workspace(ws: Workspace) -> GateReport:
    """Run every static check and return the ``lint:threejs`` GateReport."""
    t0 = time.time()
    findings: list[GateFinding] = []
    entry = ws.src / "object.js"
    sources = list_sources(ws)
    syntax = check_syntax(sources)

    if not entry.is_file():
        findings.append(GateFinding(gate=GATE, severity=Severity.ERROR, target="src/object.js",
            message="src/object.js is missing", fix_hint="create src/object.js with `export function build(THREE) { ... return root; }`"))

    imported: set[Path] = set()
    for path in sources:
        src = path.read_text(errors="replace")
        rel = _rel(ws, path)
        syn = syntax.get(path)
        if syn is not None:
            findings.append(GateFinding(gate=GATE, severity=Severity.ERROR, target=rel,
                message=f"{rel}:{syn.line or '?'}: {syn.message}", fix_hint="fix the syntax error at that line"))
            continue  # other checks are noise on a file that does not parse
        imported |= _lint_imports(ws, path, src, findings)
        _lint_forbidden(ws, path, src, findings)
        exports = _exports(src)
        if path == entry:
            if "build" not in exports:
                findings.append(GateFinding(gate=GATE, severity=Severity.ERROR, target=rel,
                    message="src/object.js must `export function build(THREE)`", fix_hint="add `export function build(THREE) { const root = new THREE.Group(); ...; return root; }`"))
        elif path.parent == ws.src / "parts":
            expected = "build" + to_pascal(path.stem)
            if expected not in exports:
                named = sorted(e for e in exports if e != "build")
                sev = Severity.WARN if any(e != "build" for e in exports) else Severity.ERROR
                findings.append(GateFinding(gate=GATE, severity=sev, target=rel,
                    message=f"{rel}: expected `export function {expected}(THREE)` (found {sorted(exports) or 'no build export'})",
                    fix_hint=f"rename the export to {expected} or the file to parts/{to_snake(named[0][5:]) if named else path.stem}.js"))

    part_files = [p for p in sources if p.parent == ws.src / "parts"]
    for p in part_files:
        if p not in imported:
            findings.append(GateFinding(gate=GATE, severity=Severity.WARN, target=_rel(ws, p),
                message=f"{_rel(ws, p)} is not imported by any module (dead part file?)", fix_hint="import and add it in src/object.js or delete it"))

    plan = _load_plan(ws)
    if plan is not None:
        for part in plan.parts:
            pf = ws.src / "parts" / f"{to_snake(part.name)}.js"
            if not pf.is_file():
                findings.append(GateFinding(gate=GATE, severity=Severity.WARN, target=part.name,
                    message=f"planned part {part.name} has no file src/parts/{to_snake(part.name)}.js",
                    fix_hint=f"create it with `export function build{to_pascal(part.name)}(THREE)`"))
            elif pf not in imported and entry.is_file():
                findings.append(GateFinding(gate=GATE, severity=Severity.WARN, target=part.name,
                    message=f"planned part {part.name} is not assembled by src/object.js",
                    fix_hint=f"import {{ build{to_pascal(part.name)} }} from './parts/{to_snake(part.name)}.js' and root.add(...)"))

    passed = not any(f.severity == Severity.ERROR for f in findings)
    return GateReport(gate=GATE, passed=passed, findings=findings, duration_ms=int((time.time() - t0) * 1000))


# ===================================================================== skeleton
def part_file(ws: Workspace, part_name: str) -> Path:
    """``src/parts/<snake>.js`` for a plan part name."""
    return ws.src / "parts" / f"{to_snake(part_name)}.js"


def _fmt(v: float) -> str:
    return f"{float(v):.4g}"


def render_part_stub(part, index: int) -> str:
    """JS source of one placeholder part file."""
    c, e = part.bbox.center, part.bbox.extents
    material_line = f"\n// Material: {part.material}" if part.material else ""
    return PART_TEMPLATE.format(
        pascal=to_pascal(part.name),
        role=part.role,
        description=part.description.replace("\n", " "),
        center=f"({_fmt(c[0])}, {_fmt(c[1])}, {_fmt(c[2])})",
        extents=f"({_fmt(e[0])}, {_fmt(e[1])}, {_fmt(e[2])})",
        material_line=material_line,
        ex=_fmt(max(e[0], 0.001)),
        ey=_fmt(max(e[1], 0.001)),
        ez=_fmt(max(e[2], 0.001)),
        cx=_fmt(c[0]),
        cy=_fmt(c[1]),
        cz=_fmt(c[2]),
        color=PLACEHOLDER_COLORS[index % len(PLACEHOLDER_COLORS)],
    )


def render_object(plan: StaticPlan) -> str:
    imports = "\n".join(
        f"import {{ build{to_pascal(p.name)} }} from './parts/{to_snake(p.name)}.js';" for p in plan.parts
    )
    adds = "\n".join(f"  root.add(build{to_pascal(p.name)}(THREE));" for p in plan.parts)
    return OBJECT_HEADER.format(object_name=to_pascal(plan.object_name), imports=imports, adds=adds)


def write_skeleton(ws: Workspace, plan: Plan, *, overwrite: bool = False) -> list[Path]:
    """Write ``src/package.json``, ``src/object.js`` and ``src/parts/*.js``; return written paths.

    Existing files are kept unless ``overwrite`` (so re-running on a refined
    workspace never clobbers agent work).
    """
    if not isinstance(plan, StaticPlan):
        raise TypeError(f"threejs skeleton needs a StaticPlan, got {type(plan).__name__}")
    written: list[Path] = []
    (ws.src / "parts").mkdir(parents=True, exist_ok=True)

    def _put(path: Path, text: str) -> None:
        if path.exists() and not overwrite:
            return
        path.write_text(text)
        written.append(path)

    _put(ws.src / "package.json", PACKAGE_JSON)
    _put(ws.src / "object.js", render_object(plan))
    for i, part in enumerate(plan.parts):
        _put(part_file(ws, part.name), render_part_stub(part, i))
    return written


# ===================================================================== runtime
ENTRY = "src/object.js"
GLB_NAME = "object.glb"
CENSUS_NAME = "census.json"
BUILD_JSON = "build.json"
NODE_MEM_LIMIT_GB = 8.0  # RLIMIT_AS for the export process (geometry-bomb protection)


class ThreeJsRuntime(RuntimeDocs):
    """LanguageRuntime for ``Language.THREEJS`` (raw ESM three.js, exported via node)."""

    language = Language.THREEJS
    entry_globs: tuple[str, ...] = (ENTRY_FILE[Language.THREEJS], "src/parts/*.js")

    # ------------------------------------------------------------------ skeleton / lint
    def skeleton(self, ws: Workspace, plan: Plan) -> list[Path]:
        return write_skeleton(ws, plan)

    def lint(self, ws: Workspace) -> GateReport:
        return lint_workspace(ws)

    # ------------------------------------------------------------------ build
    def build(self, ws: Workspace, *, timeout_s: int | None = None) -> BuildResult:
        """Run ``runtime_js/export_glb.mjs`` on ``src/object.js`` → ``artifacts/object.glb``.

        Never raises for agent-code failures: they come back as ``ok=False`` with
        ``error_type/message/file/line`` and the stdout/stderr tails.  Harness
        misconfiguration (missing node, missing script) still raises.
        """
        settings = get_settings()
        timeout_s = timeout_s or settings.limits.build_timeout_s
        ws.artifacts.mkdir(parents=True, exist_ok=True)
        glb = ws.artifacts / GLB_NAME
        census = ws.artifacts / CENSUS_NAME
        # build.json is in the wipe too: the node-missing raise below returns before
        # _write_build_json, and a stale ok:true build.json must not survive it
        ws.stage_artifacts(GLB_NAME, CENSUS_NAME, BUILD_JSON, "export_error.json").invalidate()
        self._ensure_module_type(ws)

        t0 = time.time()
        args = ["--ws", str(ws.root), "--entry", ENTRY, "--out", str(glb), "--census", str(census)]
        try:
            res = run_node(
                runtime_js_dir() / "export_glb.mjs", args, cwd=ws.root, timeout_s=timeout_s,
                three_hook=True, node_args=["--max-old-space-size=4096"], check=False,
                mem_limit_gb=NODE_MEM_LIMIT_GB,
            )
        except NodeError as e:
            if e.result is None:
                raise  # harness problem (no node binary / script)
            result = self._failure(e.result, BUILD_TIMEOUT if e.result.timed_out else "NodeError", str(e))
            self._write_build_json(ws, result)
            return result

        if res.rc != 0 or not (res.last_json or {}).get("ok") or not glb.is_file():
            result = self._from_error_record(res, ws)
        else:
            census_data: dict[str, Any] = {}
            if census.is_file():
                census_data = json.loads(census.read_text())
            result = BuildResult(
                ok=True, language=self.language.value, glb_path=str(glb),
                stdout_tail=res.stdout_tail, stderr_tail=res.stderr_tail,
                duration_ms=int((time.time() - t0) * 1000), census=census_data,
                extra_paths={"census": str(census)},
            )
        self._write_build_json(ws, result)
        return result

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _ensure_module_type(ws: Workspace) -> None:
        """``src/package.json`` {type: module} so ``.js`` files are parsed as ESM."""
        ws.src.mkdir(parents=True, exist_ok=True)
        pj = ws.src / "package.json"
        if not pj.is_file():
            pj.write_text(PACKAGE_JSON)

    def _failure(self, res: NodeResult, etype: str, message: str) -> BuildResult:
        return BuildResult(
            ok=False, language=self.language.value, stdout_tail=res.stdout_tail, stderr_tail=res.stderr_tail,
            error_type=etype, error_message=message[:2000], duration_ms=res.duration_ms,
        )

    def _from_error_record(self, res: NodeResult, ws: Workspace | None = None) -> BuildResult:
        rec = res.last_json or {}
        err = rec.get("error") if isinstance(rec.get("error"), dict) else None
        if err is None:
            tail = res.stderr_tail.strip().splitlines()
            return self._failure(res, "ExportError", tail[-1] if tail else f"export_glb exited {res.rc} without an error record")
        result = self._failure(res, str(err.get("type", "Error")), str(err.get("message", "")))
        result.error_file = str(err.get("file", "") or self._part_file(ws, err.get("part")) or ("" if err.get("frames") else ENTRY))
        result.error_line = err.get("line") if isinstance(err.get("line"), int) else None
        result.census = {"frames": err.get("frames", []), "stack": err.get("stack", ""), "part": str(err.get("part", "") or "")}
        return result

    @staticmethod
    def _part_file(ws: Workspace | None, part: object) -> str:
        """``src/parts/<snake>.js`` for the plan part named in a validation error (when that file exists).

        Contract errors raised by the exporter (NaN geometry, empty bbox) carry no src
        frame — the throw site is export_glb.mjs — but name the offending part; the
        naming convention (``conventions.to_snake``) turns that into the file to repair.
        """
        if ws is None or not isinstance(part, str) or not part.strip():
            return ""
        rel = Path("src") / "parts" / f"{to_snake(part)}.js"
        return rel.as_posix() if (ws.root / rel).is_file() else ""

    @staticmethod
    def _write_build_json(ws: Workspace, result: BuildResult) -> None:
        ws.write_json(ws.artifacts / BUILD_JSON, result)

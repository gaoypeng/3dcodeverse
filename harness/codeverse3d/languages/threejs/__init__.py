"""three.js (static object) language runtime: raw ESM, GLB export + census by ``runtime_js/export_glb.mjs``."""

from __future__ import annotations

import re
import time
from pathlib import Path

from codeverse3d.config import get_settings
from codeverse3d.contracts.artifacts import BuildResult, GateFinding, GateReport, LintKind, Severity
from codeverse3d.contracts.common import ENTRY_FILE, Language
from codeverse3d.contracts.plan import StaticPlan
from codeverse3d.conventions import to_pascal, to_snake
from codeverse3d.languages._common import BUILD_TIMEOUT, line_of, missing_entry, ws_rel
from codeverse3d.languages._js_lint import (
    ExportsCache,
    ImportKind,
    ImportVerdict,
    check_imports,
    check_named_imports,
    describe_exports,
    js_sources,
    module_exports,
    strip_js,
)
from codeverse3d.languages._js_lint import (
    node_check_syntax as check_syntax,  # module-level name: tests monkeypatch it
)
from codeverse3d.languages.base import RuntimeLayout
from codeverse3d.proc import read_json_or_none
from codeverse3d.spatial.node import NodeError, NodeResult, run_node, runtime_js_dir
from codeverse3d.spatial.tool_common import plan_or_none
from codeverse3d.workspace import Workspace

# ===================================================================== templates
PACKAGE_JSON = '{ "type": "module", "private": true }\n'


# ===================================================================== lint
GATE = "lint:threejs"

# token -> (severity, message, fix hint, kind)
_FORBIDDEN: dict[str, tuple[Severity, str, str, LintKind]] = {
    r"\bfetch\s*\(": (Severity.ERROR, "network access (fetch) is not allowed in object code", "build geometry procedurally; no downloads", LintKind.SANDBOX),
    r"\bXMLHttpRequest\b": (Severity.ERROR, "network access (XMLHttpRequest) is not allowed", "remove it", LintKind.SANDBOX),
    r"\bWebSocket\b": (Severity.ERROR, "network access (WebSocket) is not allowed", "remove it", LintKind.SANDBOX),
    r"\bdocument\b": (Severity.ERROR, "DOM access (document) is not available: code runs in node", "remove DOM/canvas usage; no textures", LintKind.SANDBOX),
    r"\bwindow\b": (Severity.ERROR, "DOM access (window) is not available: code runs in node", "remove it", LintKind.SANDBOX),
    r"\bnavigator\b": (Severity.ERROR, "navigator is not available in node", "remove it", LintKind.SANDBOX),
    r"\blocalStorage\b": (Severity.ERROR, "localStorage is not available", "remove it", LintKind.SANDBOX),
    r"\brequire\s*\(": (Severity.ERROR, "CommonJS require() is not allowed; use ESM imports of 'three' only", "import * as THREE from 'three'", LintKind.SANDBOX),
    r"\bprocess\.": (Severity.ERROR, "node process access is not allowed in object code", "remove it", LintKind.SANDBOX),
    r"\beval\s*\(": (Severity.ERROR, "eval is not allowed", "remove it", LintKind.SANDBOX),
    r"\bWebGLRenderer\b": (Severity.ERROR, "do not create renderers: the harness renders", "return the Group from build(THREE); delete renderer code", LintKind.HARNESS_OWNED),
    r"\brequestAnimationFrame\b": (Severity.ERROR, "no render loops: the harness renders the still object build(THREE) returns", "delete the loop; return the Group from build(THREE)", LintKind.HARNESS_OWNED),
    r"\bTextureLoader\b|\bImageLoader\b|\bFileLoader\b": (Severity.ERROR, "loaders/textures cannot be exported from node", "use MeshStandardMaterial colour/roughness/metalness instead", LintKind.SANDBOX),
    r"\bnew\s+THREE\.(?:Perspective|Orthographic)Camera\b": (Severity.WARN, "cameras in object code are ignored (harness owns the camera)", "delete it", LintKind.HARNESS_OWNED),
    r"\bnew\s+THREE\.[A-Za-z]*Light\s*\(": (Severity.WARN, "lights in object code are exported as extras and ignored by the harness lighting", "delete them", LintKind.HARNESS_OWNED),
    r"\bnew\s+THREE\.Scene\s*\(": (Severity.WARN, "do not build a Scene; return a Group", "replace Scene with Group", LintKind.HARNESS_OWNED),
}


def _f(kind: LintKind, sev: Severity, target: str, message: str, hint: str) -> GateFinding:
    return GateFinding(gate=GATE, severity=sev, target=target, message=message, fix_hint=hint, data={"kind": kind.value})


def _lint_imports(ws: Workspace, path: Path, src: str, findings: list[GateFinding], export_cache: ExportsCache) -> set[Path]:
    """Validate import specifiers and imported names; return the set of resolved relative targets."""
    rel = ws_rel(ws, path)

    def make_finding(v: ImportVerdict, spec: str, line: int) -> GateFinding:
        if v.kind is ImportKind.ESCAPES:
            return _f(LintKind.BAD_IMPORT, Severity.ERROR, rel, f"{rel}:{line}: import '{spec}' escapes src/",
                      "keep all files under src/")
        if v.kind is ImportKind.MISSING:
            assert v.target is not None
            return _f(LintKind.BAD_IMPORT, Severity.ERROR, rel, f"{rel}:{line}: imported file does not exist: '{spec}'",
                      f"create {ws_rel(ws, v.target)} or fix the path (extension '.js' is required)")
        kind = "URL" if v.kind is ImportKind.URL else "package"
        return _f(LintKind.BAD_IMPORT, Severity.ERROR, rel, f"{rel}:{line}: import of {kind} '{spec}' is not allowed",
                  "only 'three', 'three/addons/...' and relative './' imports are available")

    def missing_name(spec: str, name: str, line: int, names: frozenset[str]) -> GateFinding:
        return _f(LintKind.BAD_IMPORT, Severity.ERROR, rel,
                  f"{rel}:{line}: import {{ {name} }} from '{spec}': that module exports no '{name}' ({describe_exports(names)})",
                  "import only names the module actually exports (a part file exports build<Pascal>(THREE))")

    new, targets = check_imports(src, path, ws.src, make_finding=make_finding)
    findings.extend(new)
    findings.extend(check_named_imports(src, path, ws.src, make_finding=missing_name, exports=export_cache))
    return targets


def _lint_forbidden(ws: Workspace, path: Path, src: str, findings: list[GateFinding]) -> None:
    stripped = strip_js(src)
    rel = ws_rel(ws, path)
    for pattern, (sev, msg, hint, kind) in _FORBIDDEN.items():
        m = re.search(pattern, stripped)
        if m:
            findings.append(_f(kind, sev, rel, f"{rel}:{line_of(stripped, m.start())}: {msg}", hint))


def _exports(src: str) -> set[str]:
    """The ``build*`` names ``src`` exports (what the entry and every part file must provide)."""
    return {n for n in module_exports(src) or () if n.startswith("build")}


def lint_workspace(ws: Workspace) -> GateReport:
    """Run every static check and return the ``lint:threejs`` GateReport."""
    t0 = time.time()
    findings: list[GateFinding] = []
    entry = ws.src / "object.js"
    sources = js_sources(ws.src)
    syntax = check_syntax(sources)
    export_cache: ExportsCache = {}

    if not entry.is_file():
        findings.append(_f(LintKind.MISSING_FILE, Severity.ERROR, "src/object.js",
            "src/object.js is missing", "create src/object.js with `export function build(THREE) { ... return root; }`"))

    imported: set[Path] = set()
    for path in sources:
        src = path.read_text(errors="replace")
        rel = ws_rel(ws, path)
        syn = syntax.get(path)
        if syn is not None:
            findings.append(_f(LintKind.SYNTAX, Severity.ERROR, rel, f"{rel}:{syn.line or '?'}: {syn.message}",
                               "fix the syntax error at that line"))
            continue  # other checks are noise on a file that does not parse
        imported |= _lint_imports(ws, path, src, findings, export_cache)
        _lint_forbidden(ws, path, src, findings)
        exports = _exports(src)
        if path == entry:
            if "build" not in exports:
                findings.append(_f(LintKind.LAYOUT, Severity.ERROR, rel,
                    "src/object.js must `export function build(THREE)`", "add `export function build(THREE) { const root = new THREE.Group(); ...; return root; }`"))
        elif path.parent == ws.src / "parts":
            # matched by snake: the plan's `TVStand` lives in tv_stand.js and exports buildTVStand
            expected = "build" + to_pascal(path.stem)
            if expected not in exports and not any(
                    e.startswith("build") and len(e) > 5 and to_snake(e[5:]) == path.stem for e in exports):
                named = sorted(e for e in exports if e != "build")
                sev = Severity.WARN if any(e != "build" for e in exports) else Severity.ERROR
                findings.append(_f(LintKind.LAYOUT, sev, rel,
                    f"{rel}: expected `export function {expected}(THREE)` (found {sorted(exports) or 'no build export'})",
                    f"rename the export to {expected} or the file to parts/{to_snake(named[0][5:]) if named else path.stem}.js"))

    part_files = [p for p in sources if p.parent == ws.src / "parts"]
    for p in part_files:
        if p not in imported:
            findings.append(_f(LintKind.PART_NOT_IMPORTED, Severity.WARN, ws_rel(ws, p),
                f"{ws_rel(ws, p)} is not imported by any module (dead part file?)", "import and add it in src/object.js or delete it"))

    plan = plan_or_none(ws.plan_path)
    if type(plan) is StaticPlan:  # an articulated plan's parts are not threejs part files
        for part in plan.parts:
            pf = ws.src / "parts" / f"{to_snake(part.name)}.js"
            if not pf.is_file():
                findings.append(_f(LintKind.MISSING_FILE, Severity.WARN, part.name,
                    f"planned part {part.name} has no file src/parts/{to_snake(part.name)}.js",
                    f"create it with `export function build{to_pascal(part.name)}(THREE)`"))
            elif pf not in imported and entry.is_file():
                findings.append(_f(LintKind.PART_NOT_IMPORTED, Severity.WARN, part.name,
                    f"planned part {part.name} is not assembled by src/object.js",
                    f"import {{ build{to_pascal(part.name)} }} from './parts/{to_snake(part.name)}.js' and root.add(...)"))

    return GateReport.of(GATE, findings, duration_ms=int((time.time() - t0) * 1000))


# ===================================================================== skeleton
def part_file_rel(part_name: str) -> str:
    """``'Seat Cushion'`` → ``'src/parts/seat_cushion.js'`` (workspace-relative)."""
    return f"src/parts/{to_snake(part_name)}.js"


# ===================================================================== runtime
ENTRY = ENTRY_FILE[Language.THREEJS]
GLB_NAME = "object.glb"
CENSUS_NAME = "census.json"
BUILD_JSON = "build.json"
NODE_MEM_LIMIT_GB = 8.0  # RLIMIT_AS for the export process (geometry-bomb protection)


class ThreeJsRuntime(RuntimeLayout):
    """LanguageRuntime for ``Language.THREEJS`` (raw ESM three.js, exported via node)."""

    language = Language.THREEJS
    entry_globs: tuple[str, ...] = (ENTRY_FILE[Language.THREEJS], "src/parts/*.js")
    part_file = staticmethod(part_file_rel)


    def lint(self, ws: Workspace) -> GateReport:
        return lint_workspace(ws)

    # ------------------------------------------------------------------ build
    def build(self, ws: Workspace, *, timeout_s: int | None = None) -> BuildResult:
        """Run ``runtime_js/export_glb.mjs`` on ``src/object.js`` → ``artifacts/object.glb``.

        Agent-code failures return ``ok=False``; harness misconfiguration raises."""
        settings = get_settings()
        timeout_s = timeout_s or settings.limits.build_timeout_s
        ws.artifacts.mkdir(parents=True, exist_ok=True)
        glb = ws.artifacts / GLB_NAME
        census = ws.artifacts / CENSUS_NAME
        # build.json is wiped too: the node-missing raise below skips _write_build_json
        ws.stage_artifacts(GLB_NAME, CENSUS_NAME, BUILD_JSON, "export_error.json").invalidate()
        if (missing := missing_entry(ws, self.language)) is not None:
            return missing
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
            census_data = read_json_or_none(census) or {}
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
        return result

    @staticmethod
    def _part_file(ws: Workspace | None, part: object) -> str:
        """``src/parts/<snake>.js`` for the plan part named in a validation error (when that file exists)."""
        if ws is None or not isinstance(part, str) or not part.strip():
            return ""
        rel = part_file_rel(part)
        return rel if (ws.root / rel).is_file() else ""

    @staticmethod
    def _write_build_json(ws: Workspace, result: BuildResult) -> None:
        ws.write_json(ws.artifacts / BUILD_JSON, result)

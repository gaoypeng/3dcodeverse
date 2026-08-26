"""Static lint for three.js static-object code (no execution of agent code).

Checks: ESM syntax (``node --check``), allowed imports (``three``,
``three/addons/*``, relative files that exist), forbidden browser/network/node
APIs, the ``build`` / ``build<Pascal>`` entry points, and (when a plan exists)
that every planned part has a file and is assembled by ``object.js``.
"""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path

from codeverse.config import get_settings
from codeverse.contracts.artifacts import GateFinding, GateReport, Severity
from codeverse.contracts.plan import StaticPlan
from codeverse.conventions import to_pascal, to_snake
from codeverse.proc import read_json_or_none
from codeverse.workspace import Workspace

GATE = "lint:threejs"

_IMPORT_RE = re.compile(
    r"""(?:^|\n)\s*(?:import\s+(?:[^'";]*?\s+from\s+)?|export\s+(?:\*|\{[^}]*\})\s+from\s+)['"]([^'"]+)['"]"""
)
_DYN_IMPORT_RE = re.compile(r"""\bimport\s*\(\s*['"]([^'"]+)['"]\s*\)""")
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


def check_syntax(path: Path, node_bin: str) -> tuple[int | None, str] | None:
    """``None`` when the file parses as ESM, else ``(line, message)``."""
    try:
        proc = subprocess.run(
            [node_bin, "--input-type=module", "--check"],
            input=path.read_bytes(), capture_output=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return (None, f"syntax check could not run: {e}")
    if proc.returncode == 0:
        return None
    err = proc.stderr.decode(errors="replace")
    m = re.search(r"\[stdin\]:(\d+)", err)
    msg = re.search(r"^(SyntaxError:.*)$", err, re.M)
    return (int(m.group(1)) if m else None, msg.group(1) if msg else err.strip().splitlines()[-1:] or "syntax error")


def _lint_imports(ws: Workspace, path: Path, src: str, findings: list[GateFinding]) -> set[Path]:
    """Validate import specifiers; return the set of resolved relative targets."""
    targets: set[Path] = set()
    specs = [(m.group(1), m.start(1)) for m in _IMPORT_RE.finditer(src)]
    specs += [(m.group(1), m.start(1)) for m in _DYN_IMPORT_RE.finditer(src)]
    rel = _rel(ws, path)
    for spec, pos in specs:
        line = _line_of(src, pos)
        if spec == "three" or spec.startswith("three/addons/") or spec.startswith("three/examples/jsm/"):
            continue
        if spec.startswith("./") or spec.startswith("../"):
            target = (path.parent / spec).resolve()
            if not target.is_relative_to(ws.src.resolve()):
                findings.append(GateFinding(gate=GATE, severity=Severity.ERROR, target=rel,
                    message=f"{rel}:{line}: import '{spec}' escapes src/", fix_hint="keep all files under src/"))
            elif not target.is_file():
                findings.append(GateFinding(gate=GATE, severity=Severity.ERROR, target=rel,
                    message=f"{rel}:{line}: imported file does not exist: '{spec}'",
                    fix_hint=f"create {_rel(ws, target)} or fix the path (extension '.js' is required)"))
            else:
                targets.add(target)
            continue
        kind = "URL" if re.match(r"https?://", spec) else "package"
        findings.append(GateFinding(gate=GATE, severity=Severity.ERROR, target=rel,
            message=f"{rel}:{line}: import of {kind} '{spec}' is not allowed",
            fix_hint="only 'three', 'three/addons/...' and relative './' imports are available"))
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
    node_bin = get_settings().binaries.node
    entry = ws.src / "object.js"
    sources = list_sources(ws)

    if not entry.is_file():
        findings.append(GateFinding(gate=GATE, severity=Severity.ERROR, target="src/object.js",
            message="src/object.js is missing", fix_hint="create src/object.js with `export function build(THREE) { ... return root; }`"))

    imported: set[Path] = set()
    for path in sources:
        src = path.read_text(errors="replace")
        rel = _rel(ws, path)
        syn = check_syntax(path, node_bin)
        if syn is not None:
            line, msg = syn
            findings.append(GateFinding(gate=GATE, severity=Severity.ERROR, target=rel,
                message=f"{rel}:{line or '?'}: {msg}", fix_hint="fix the syntax error at that line"))
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
                sev = Severity.WARN if any(e != "build" for e in exports) else Severity.ERROR
                findings.append(GateFinding(gate=GATE, severity=sev, target=rel,
                    message=f"{rel}: expected `export function {expected}(THREE)` (found {sorted(exports) or 'no build export'})",
                    fix_hint=f"rename the export to {expected} or the file to parts/{to_snake(exports.pop()[5:]) if exports else path.stem}.js"))

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

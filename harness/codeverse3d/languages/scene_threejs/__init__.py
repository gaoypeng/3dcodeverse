"""scene_threejs: multi-file three.js scenes (+GLSL, +GLB assets) — lint, probe-build, skeleton, assembler."""

from __future__ import annotations

import math
import re
import time
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from codeverse3d.contracts.artifacts import (
    BuildResult,
    GateFinding,
    GateReport,
    LintKind,
    RenderSet,
    Severity,
)
from codeverse3d.contracts.common import ENTRY_FILE, Language
from codeverse3d.contracts.plan import AssetPlan, CameraPlan, Plan, ScenePlan, ZonePlan
from codeverse3d.conventions import OBJECT_VIEWS_QUICK, to_pascal, to_snake
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
MAX_LINES = 800

_CREATE_SCENE_RE = re.compile(r"export\s+(?:async\s+)?function\s+createScene\b|export\s+(?:const|let)\s+createScene\b|export\s*\{[^}]*\bcreateScene\b[^}]*\}")
_BUILD_RE = re.compile(r"export\s+(?:async\s+)?function\s+build\b|export\s+(?:const|let)\s+build\b|export\s*\{[^}]*\bbuild\b[^}]*\}")
_BUILD_ANY_RE = re.compile(r"export\s+(?:async\s+)?function\s+build[A-Za-z0-9_]*\b|export\s+(?:const|let)\s+build[A-Za-z0-9_]*\b")

#: (regex, severity, message, hint, kind)
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
    """Is this one of the harness's own effect modules (``src/lib/*.js``)?

    They ship into every workspace (D51) and are verified by the harness's own
    suite — ``tests/scene_runtime/lib/`` — not by the workspace gate, whose job
    is to police the code the AGENT writes.  Linting them as agent code was
    three hard ERRORs (signage.js's node-only font fallback imports
    ``node:module`` / ``node:fs/promises`` / ``node:url``, which the browser
    half never touches) plus twelve "large file" WARNs telling the agent to
    split files it does not own.  Syntax is still checked below: a library file
    the agent broke must still fail loudly.
    """
    return path.parent == ws.src / "lib"


def _syntax_finding(p: SyntaxProblem, rel: str) -> GateFinding:
    return _f(LintKind.SYNTAX, Severity.ERROR, f"syntax: {p.message}", target=f"{rel}:{p.line}" if p.line else rel,
              hint="fix the syntax error at the quoted line (node --check)", line=p.line, detail=p.stderr_tail)


def _check_imports(rel: str, text: str, ws: Workspace, path: Path, export_cache: ExportsCache) -> list[GateFinding]:
    """The import policy on one agent file: the specifier kinds, then — for a relative target
    that exists — every imported name against what the target exports.  ``src/lib`` modules
    are never linted as agent code, but they ARE read here as targets (``export_cache`` holds
    each one's surface for the whole lint): a name the catalog does not list is a SyntaxError
    at module link, which drops the whole zone at assembly."""
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
    """Run all static checks; passed iff no ERROR findings."""
    t0 = time.time()
    findings: list[GateFinding] = []
    files = js_sources(ws.src, dotfiles=True, mjs=True)
    scene = ws.src / "scene.js"
    if not scene.is_file():
        findings.append(_f(LintKind.MISSING_FILE, Severity.ERROR, "src/scene.js is missing", target="src/scene.js",
                           hint="create src/scene.js exporting createScene({THREE, renderer, loaders})"))
    big: list[str] = []
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
        code = strip_js(text, strings=False)   # a comment quoting the contract is not a call
        for pat, sev, msg, hint, kind in _PATTERNS:
            m = pat.search(code)
            if m:
                line = line_of(code, m.start())
                findings.append(_f(kind, sev, msg, target=f"{rel}:{line}", hint=hint))
        n_lines = text.count("\n") + 1
        if n_lines > MAX_LINES:
            big.append(f"{rel} ({n_lines} lines)")
        if path == scene and not _CREATE_SCENE_RE.search(text):
            findings.append(_f(LintKind.LAYOUT, Severity.ERROR, "src/scene.js does not export createScene", target=rel,
                               hint="export function createScene({ THREE, renderer, loaders }) { ... return { scene, cameras, update } }"))
        if path.parent == ws.src / "zones" and not _BUILD_RE.search(text):
            findings.append(_f(LintKind.LAYOUT, Severity.ERROR, "zone module does not export build(ctx)", target=rel,
                               hint="export function build(ctx) { const g = new THREE.Group(); g.name = 'ZoneName'; ...; return g; }"))
        if path.parent == ws.src / "assets" and not _BUILD_ANY_RE.search(text):
            findings.append(_f(LintKind.LAYOUT, Severity.WARN, "asset module exports no build<Pascal>(THREE) factory", target=rel,
                               hint="export function buildLamp(THREE) { ... return group; }"))
    for b in big:
        findings.append(_f(LintKind.LAYOUT, Severity.WARN, f"large file {b} > {MAX_LINES} lines", target=b.split(" ")[0],
                           hint="a file over 800 lines is reported; how (or whether) to split it is yours"))
    return GateReport.of(GATE, findings, duration_ms=int((time.time() - t0) * 1000))


# ===================================================================== skeleton
STARTER_DIR = Path(__file__).resolve().parent / "starter" / "src"
#: the harness-owned effect library (``src/lib/*.js``), shipped into EVERY workspace by both
#: skeleton paths (a planned run once got the effects catalog in its prompt and no ``src/lib/``
#: to import from — every ``from './lib/grass.js'`` a 2026-09-01 agent wrote was a build error).
#: The example scene's own files (``zones/``, ``assets/``, ``shaders/``) ship ONLY without a plan:
#: a planned workspace used to carry a pine tree, a windmill and a pond shader whatever the brief,
#: and its stubs cited them as the pattern (the de-templating of 2026-10-15).
LIB_DIR = STARTER_DIR / "lib"


def lib_files() -> list[Path]:
    """The effect-library modules shipped into a workspace, sorted."""
    return sorted(LIB_DIR.glob("*.js"))


def _write(path: Path, text: str, written: list[Path]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    written.append(path)


def _write_lib(ws: Workspace, written: list[Path]) -> None:
    for src in lib_files():
        _write(ws.src / "lib" / src.name, src.read_text(), written)


def write_example(ws: Workspace) -> list[Path]:
    """Copy the full example scene into ws.src (overwrites).

    Raises when the starter tree is not there: rglob over a missing directory yields
    nothing, so this used to write ZERO files and report success — which is exactly what
    a non-editable (wheel) install produced before the starter tree was added to
    package-data, and it surfaced much later as an empty scene rather than a bad install.
    """
    if not STARTER_DIR.is_dir():
        raise FileNotFoundError(
            f"scene_threejs starter tree missing: {STARTER_DIR} — this install has no package data "
            f"(a non-editable install needs [tool.setuptools.package-data] to ship languages/**/starter/**/*)")
    written: list[Path] = []
    for src in sorted(STARTER_DIR.rglob("*.js")):
        rel = src.relative_to(STARTER_DIR)
        _write(ws.src / rel, src.read_text(), written)
    if not written:
        raise FileNotFoundError(f"scene_threejs starter tree at {STARTER_DIR} contains no .js files")
    return written


#: the stub plate's side as a multiple of the plan span: its edge then sits 1.5 spans from the
#: centre — past the 2 × span the ``fog_short`` gate measures once the author's fog is in — so no
#: authored camera reads a plate edge before the land is built.  Derived from the plan alone: no
#: minimum world size.
PLACEHOLDER_GROUND_SPANS = 3.0

_AZIMUTH_RE = re.compile(r"azimuth[^0-9\-]{0,12}(-?\d+(?:\.\d+)?)", re.I)
_ELEVATION_RE = re.compile(r"elevation[^0-9\-]{0,12}(-?\d+(?:\.\d+)?)", re.I)
#: the overview rig's own guess when a scene states no sun azimuth (``sun_azimuth`` below)
SUN_AZIMUTH_GUESS_DEG = 45.0


def sun_from_plan(plan: ScenePlan) -> tuple[float | None, float | None]:
    """The sun azimuth and elevation (degrees) the plan's ``environment`` paragraph states
    ("sun azimuth 250° elevation 8°"), ``None`` for each it does not: the skeleton carries the
    PLAN's light into the env stub rather than a light of its own (the de-templating of
    2026-10-15: a hardcoded azimuth 60 / elevation 38 / mood 'day' shipped in every env)."""
    text = plan.environment
    az = _AZIMUTH_RE.search(text)
    el = _ELEVATION_RE.search(text)
    return (float(az.group(1)) if az else None, float(el.group(1)) if el else None)


_ENV_STUB = """// src/env.js — the environment: the ground every zone seats on, the light, the sky / backdrop, the atmosphere.
//   export function heightAt(x, z) → ground height (m): the ONE function every zone seats on.
//   export function buildEnv(ctx) → {{ update?(t, dt), ...whatever zones may share as ctx.env (sunDir, materials, a water level …) }}
//   export const SUN_AZIMUTH_DEG → where the light comes from (0 = +X, 90 = +Z); the assembler reads it (regex) to fit cameras on the lit side.
//
// PLACEHOLDER: nothing below decides the brief's world.  It ships ONE thing — a level plate named Ground reaching past
// the plan span, so the census has a surface to seat on and to measure the fog against — and NO light, NO sky and NO
// atmosphere: as written this module FAILS the gates (no_fog / no_background on the census, dark_frame on every frame)
// until buildEnv builds the brief's.  What is measured:
//   • every zone seats on heightAt, so the ground mesh and heightAt must agree (scene_placement: floating > 0.05 m
//     outdoors / 0.02 m indoors, sunken > 0.10 m); the plan's cameras expect the ground near their look_at at the
//     height the plan states, and each eye above heightAt at its own (x, z) (camera_underground, cap 0.50).
//   • scene.fog and scene.background set (no_fog / no_background, ERROR); fog far ≥ 2 × the plan span or
//     1/density ≥ span / 1.5 (fog_short); the land reaches past the fog from every authored camera; no world edge
//     in any authored frame (judged world_edge_visible; the census: standing geometry past 1.25 × the half-extent).
//   • scene.environment is an environment texture (PBR metals and glass render near black without one).
//   • the judged frames sit in the exposure band (scene_frames: dark_frame / blown_frame / flat_frame).
//   • the ground mesh is named 'Ground…' / 'Terrain…' (the census classifies the ground by name; the placement gate
//     seats on it); backdrop geometry is named 'Sky…' (it then never inflates the content bounds).
// Raw three.js is the baseline.  src/lib is an OPTIONAL toolbox the effects catalog lists: lib/terrain.js ground()
// displaces and textures a ground from a height field, lib/sky.js makeSky, lib/environment.js sunRig / roomShell,
// lib/atmosphere.js makeHeightFog — use a call when it fits the brief, write your own when it does not.
import * as THREE from 'three';

export const SUN_AZIMUTH_DEG = {azimuth};    // 0 = +X, 90 = +Z — {azimuth_note}
export const SUN_ELEVATION_DEG = {elevation};  // {elevation_note}
export const GROUND_SIZE = {ground};       // the placeholder plate's side (m): 3 × the plan span, past the 2 × the fog gate measures
// An INTERIOR plan (filled from the plan): the room box the plan gives, {{ center, extents, openings, glazed? }}.  The
// enclosure the brief describes — walls, openings, ceiling, materials as named — is yours; lib/environment.js
// roomShell(INTERIOR) is one way to build it.  An interior reads black without openings or practical lights (dark_frame).
export const INTERIOR = {interior};

/** Ground height at (x, z) — every placement seats on this.  PLACEHOLDER: level; the brief's landform is yours. */
export function heightAt(x, z) {{
  return 0;
}}

export function buildEnv(ctx) {{
  const {{ scene }} = ctx;
  // PLACEHOLDER ground: a level plate following heightAt — REPLACE with the brief's land (named Ground… / Terrain…)
  const ground = new THREE.Mesh(new THREE.PlaneGeometry(GROUND_SIZE, GROUND_SIZE, 1, 1),
                                new THREE.MeshStandardMaterial({{ color: 0x6f6a60, roughness: 0.95 }}));
  ground.rotation.x = -Math.PI / 2;
  ground.receiveShadow = true;
  ground.name = 'Ground';
  scene.add(ground);
  // NO light, sky, fog or scene.environment here: the brief's go in this function (see the header for what is measured)
  return {{ ground, update(t, dt) {{}} }};
}}
"""


def _env_for_plan(plan: ScenePlan) -> str:
    """``src/env.js`` as an interface stub under the plan's data: the plate side from the span, the
    sun azimuth / elevation the plan states, the room box for an interior — and no light, sky or
    atmosphere.  The starter's finished landscape (a level disc inside the bounds' radius with
    hills and a ridge beyond) was kept byte for byte by 29 of 30 iteration-0 scenes, and the
    passing placeholder that replaced it (a day sun rig at azimuth 60, a grey fog) built and
    passed for any brief, so it was kept too: a placeholder that passes is a template by another
    name (the de-templating of 2026-10-15)."""
    ex = plan.bounds.extents
    span = max(ex[0], ex[2], 1.0)
    az, el = sun_from_plan(plan)
    interior = "null"
    if plan.interior:
        c = [round(float(v), 2) for v in plan.bounds.center]
        e = [round(float(v), 2) for v in ex]
        glazed = ", glazed: true" if plan.glazed else ""
        interior = f"{{ center: {c}, extents: {e}, openings: []{glazed} }}"
    header = (
        f"// ENV PLAN: {plan.environment.strip()}\n"
        f"// setting: {plan.setting.strip()}  mood: {plan.mood.strip()}\n"
        f"// bounds: centre {tuple(round(c, 1) for c in plan.bounds.center)} extents {tuple(round(e, 1) for e in ex)} m — the extent\n"
        "//   of the PLANNED content and the cameras' subject, not the edge of the world: land, backdrop and scenery\n"
        "//   continue beyond it as the brief implies.\n"
        "// Keep the buildEnv(ctx), heightAt(x, z) and SUN_AZIMUTH_DEG exports; everything else is yours to write for the plan above.\n"
    )
    body = _ENV_STUB.format(
        azimuth=f"{az:g}" if az is not None else f"{SUN_AZIMUTH_GUESS_DEG:g}",
        azimuth_note="the plan's light" if az is not None else "the plan states none: this is only the rig's guess — set the brief's",
        elevation=f"{el:g}" if el is not None else "null",
        elevation_note="the plan's" if el is not None else "the plan states none: set the brief's (a number)",
        ground=int(span * PLACEHOLDER_GROUND_SPANS), interior=interior)
    return header + body


# ===================================================================== layout
#: what a whole-scene session edits: the entry and the environment (every zone and asset module
#: has its own session and its own file below)
SCENE_FILES: tuple[str, ...] = ("src/scene.js", "src/env.js")
#: refine targets that are the environment's, whatever the plan calls its zones
ENV_WORDS = frozenset({"env", "environment", "lighting", "sky", "fog", "ground", "water", "light"})


def zone_file(name: str) -> str:
    """``src/zones/<snake>.js`` — the module that builds zone ``name``."""
    return f"src/zones/{to_snake(name)}.js"


def asset_file(asset: AssetPlan) -> str:
    """A three.js asset's factory module; a Blender hero's compiled GLB (built in its own sub-workspace)."""
    snake = to_snake(asset.name)
    return f"src/assets/{snake}.js" if asset.kind == "threejs" else f"public/assets/{snake}.glb"


def scene_files_for(plan: Plan | None, target: str, *, alias: dict[str, str] | None = None) -> list[str]:
    """The files that own a scene refine target.  A three.js asset is fixed in its factory — a merged
    asset's (``alias``: the dedupe note) in its survivor's, its own file being only the variant shim; a
    hero ships as a GLB, so it is fixed where it is placed: the zones that list it.  Cameras belong to
    ``scene.js``, env words to ``env.js``; "Zone/Asset" (the placement gate) to the zone's file."""
    zone_plans = list(getattr(plan, "zones", None) or [])
    zones = {to_snake(z.name) for z in zone_plans}
    assets = {to_snake(a.name): a for a in (getattr(plan, "assets", None) or [])}
    survivor = {to_snake(k): v for k, v in (alias or {}).items()}
    key = to_snake(target)
    if key in zones:
        return [zone_file(key)]
    if key in assets:
        if assets[key].kind == "threejs":
            return [asset_file(assets[key].model_copy(update={"name": survivor.get(key, key)}))]
        return [zone_file(z.name) for z in zone_plans if key in {to_snake(c) for c in z.contents}]
    if key in {to_snake(c.name) for c in (getattr(plan, "cameras", None) or [])} or key in ("camera", "cameras", "composition"):
        return ["src/scene.js"]
    if key in ENV_WORDS:
        return ["src/env.js"]
    if "/" in target and to_snake(target.split("/", 1)[0]) in zones:
        return [zone_file(target.split("/", 1)[0])]
    return []


def _asset_stub(a: AssetPlan) -> str:
    snake, pascal = to_snake(a.name), to_pascal(a.name)
    w, h, d = (max(0.05, float(v)) for v in a.approx_size_m)
    return f'''// src/assets/{snake}.js — asset "{pascal}": {a.description.strip()}
// approx size {w:g} x {h:g} x {d:g} m (w x h x d); expected instances: ~{a.instances_hint}
// CONTRACT: export function build{pascal}(THREE) → THREE.Group, origin at the base (y = 0), +Y up, +Z front, meters.
//   It must read as the plan describes it at the judge's camera distance (judged) and measure about the size
//   above (scene_placement: scale).  Parts, materials and construction are yours.
//   OPTIONAL: export {snake}Parts(THREE) → [{{ geometry, material, castShadow }}] if a zone wants to instance
//   repeats of it (the triangle budget is measured on the census; THREE.InstancedMesh is one way to afford them).
import * as THREE from 'three';

export function build{pascal}(T = THREE) {{
  const g = new T.Group();
  g.name = '{pascal}';
  // PLACEHOLDER blockout at the planned size — REPLACE with the real thing (a single box fails the asset check).
  const box = new T.Mesh(new T.BoxGeometry({w:g}, {h:g}, {d:g}), new T.MeshStandardMaterial({{ color: 0x9a9a9a, roughness: 0.8 }}));
  box.position.y = {h / 2:g};
  box.castShadow = box.receiveShadow = true;
  g.add(box);
  return g;
}}
'''


def _zone_stub(z: ZonePlan, assets: dict[str, AssetPlan]) -> str:
    """The zone's interface and a TODO naming the plan's contents — no positions.  The stub used
    to tile the contents on a quarter-extent grid inside the bbox and say "keep everything inside
    the plan bbox": the seed of the blocks-in-a-rectangle look (the de-templating of 2026-10-15)."""
    snake, pascal = to_snake(z.name), to_pascal(z.name)
    c, e = z.bbox.center, z.bbox.extents
    imports, todo = [], []
    for name in z.contents:
        a = assets.get(to_snake(name))
        if a is None:
            todo.append(f"//   - {name}: not an asset of the plan — built here")
        elif a.kind == "threejs":
            asn, asp = to_snake(a.name), to_pascal(a.name)
            imports.append(f"import {{ build{asp} }} from '../assets/{asn}.js';")
            todo.append(f"//   - {a.name}: build{asp}(THREE) → a Group with its origin at the base; ~{a.instances_hint} planned")
        else:
            asn = to_snake(a.name)
            todo.append(f"//   - {a.name}: ctx.assets.{asn} — the hero GLB public/assets/{asn}.glb preloaded by scene.js; place .clone()s")
    imp = ("\n".join(imports) + "\n") if imports else ""
    todo_block = "\n".join(todo) if todo else "//   (the plan lists no contents)"
    return f'''// src/zones/{snake}.js — zone "{pascal}": {z.description.strip()}
// PLAN bbox: centre ({c[0]:g}, {c[1]:g}, {c[2]:g}) extents ({e[0]:g} x {e[1]:g} x {e[2]:g}) m.
// CONTRACT: export function build(ctx) → THREE.Group named '{pascal}'.  ctx = {{ THREE, scene, renderer, loaders, env, heightAt, assets }}
//   • seat things on ctx.heightAt(x, z) (measured: scene_placement floating > 0.05 m outdoors / 0.02 m indoors, sunken > 0.10 m)
//   • the planned contents are measured against the plan bounds and this bbox (scene_placement: present, at the plan's
//     size, not outside the bounds); scenery may continue past the bbox as the brief implies
//   • animation → zone.userData.update = (t, dt) => {{ ... }} (scene.js fans update() out; motion between t = 0 and 1.5 s is measured)
//   • the triangle budget is measured on the census; THREE.InstancedMesh is one way to afford repeats
// TODO — this zone's planned contents, none placed yet:
{todo_block}
import * as THREE from 'three';
{imp}
export function build(ctx) {{
  const zone = new THREE.Group();
  zone.name = '{pascal}';
  // TODO: build the contents listed above (seat everything with ctx.heightAt)
  zone.userData.update = (t, dt) => {{}};   // the animation hook; delete it if the zone is still
  return zone;
}}
'''


def _scene_for_plan(plan: ScenePlan) -> str:
    """The skeleton's entry point IS the assembled shape (``render_scene_js`` over the
    plan's zones, cameras and GLB assets) under the starter's contract header: the
    assemble stage overwrites this file with that very emitter, so the env / zone
    sessions author against the ``createScene`` they will actually ship with."""
    header = (STARTER_DIR / "scene.js").read_text().split("import * as THREE")[0]
    glbs = [to_snake(a.name) for a in plan.assets if a.kind == "blender_glb"]
    body = render_scene_js([to_snake(z.name) for z in plan.zones], list(plan.cameras[:6]), glbs, env_ok=True)
    return (f"{header}// PLAN: {plan.title} — {plan.summary.strip()}\n"
            f"// setting: {plan.setting.strip()}   animation: {'; '.join(plan.animation) or '-'}\n" + body)


def write_skeleton(ws: Workspace, plan: Plan | None = None) -> list[Path]:
    """Write starter files into ``ws.src``; returns the written paths."""
    if plan is None or not isinstance(plan, ScenePlan):
        return write_example(ws)
    written: list[Path] = []
    _write_lib(ws, written)
    _write(ws.src / "env.js", _env_for_plan(plan), written)
    assets = {to_snake(a.name): a for a in plan.assets}
    for a in plan.assets:
        if a.kind == "threejs":
            _write(ws.root / asset_file(a), _asset_stub(a), written)
    for z in plan.zones:
        _write(ws.root / zone_file(z.name), _zone_stub(z, assets), written)
    _write(ws.src / "scene.js", _scene_for_plan(plan), written)
    (ws.public / "assets").mkdir(parents=True, exist_ok=True)
    return written


# ===================================================================== assemble
PROBE_REL = "src/_c3v_assemble_probe.js"
_PREFIX = "[3dcode-assemble]"
_SUN_RE = re.compile(r"export\s+const\s+SUN_AZIMUTH_DEG\s*=\s*(-?\d+(?:\.\d+)?)")


class ZoneProbe(BaseModel):
    name: str
    file: str
    ok: bool
    error: str = ""
    bbox: dict[str, Any] | None = None
    meshes: int = 0
    triangles: int = 0


class ZoneProbeReport(BaseModel):
    """``probe_zone_modules`` outcome; ``camera_specs`` carries the driver-fitted cameras
    (``{azimuth, overview, zones:[{group, position, lookAt, fov}]}``)."""

    probes: dict[str, ZoneProbe] = Field(default_factory=dict)
    census: dict[str, Any] = Field(default_factory=dict)
    other_errors: list[str] = Field(default_factory=list)
    camera_specs: dict[str, Any] = Field(default_factory=dict)


class AssembleResult(BaseModel):
    scene_path: str
    zones_included: list[str] = Field(default_factory=list)
    zones_failed: dict[str, str] = Field(default_factory=dict)
    env_ok: bool = True
    cameras: list[CameraPlan] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    census: dict[str, Any] = Field(default_factory=dict)


def zone_files(ws: Workspace) -> list[Path]:
    d = ws.src / "zones"
    return sorted(p for p in d.glob("*.js") if not p.name.startswith("_")) if d.is_dir() else []


def glb_assets(ws: Workspace) -> list[str]:
    d = ws.public / "assets"
    return sorted(p.stem for p in d.glob("*.glb")) if d.is_dir() else []


def sun_azimuth(ws: Workspace, default: float = 45.0) -> float:
    env = ws.src / "env.js"
    if env.is_file():
        m = _SUN_RE.search(env.read_text(errors="replace"))
        if m:
            return float(m.group(1))
    return default


# --------------------------------------------------------------------------- probing
#: How a Blender-built asset is preloaded in EVERY assembled entry point.  One
#: spelling, because there are three of them (the assembled `src/scene.js`, the
#: assembler's probe module, and `render_scene_js`) and they had drifted into three.
#:
#: The loaded root is NAMED after its asset.  A glTF root arrives called whatever the
#: exporter wrote — Blender writes "Scene" — and every check that reads the scene
#: graph by name then cannot see it.  Measured on bench/out/scene_baseline
#: (2026-09-05): rooftop_garden's `lounge_sofa.glb` loaded fine (`glb_assets:
#: meshes_in_scene 1, in_scene true`) and `scene_placement` still reported
#: "zone PergolaLounge is missing planned contents: LoungeSofa", because the object
#: in the zone was called "Scene".  `to_pascal` on the file key rebuilds exactly the
#: plan's asset name, which is where the key came from.
def _glb_preload_js(glbs: list[str], *, target: str, on_error: str, indent: str = "  ") -> str:
    """The `assetFiles` literal + preload loop, or "" when the plan has no GLB asset.

    ``target`` is the assets object to fill (``assets`` / ``ctx.assets``); ``on_error``
    is the JS statement run in the catch, with ``e`` and ``a`` (the asset entry) bound.
    """
    if not glbs:
        return ""
    files = ", ".join(f"{g}: {{ name: '{to_pascal(g)}', url: '/assets/{g}.glb' }}" for g in glbs)
    body = [
        f"const assetFiles = {{ {files} }};",
        "for (const [key, a] of Object.entries(assetFiles)) {",
        "  try {",
        "    const gltf = await loaders.gltf.loadAsync(a.url);",
        "    const root = gltf.scene;",
        "    root.animations = gltf.animations || [];   // a keyframed Blender part is a clip; .clone() keeps it (lib/place.js clipPlayer)",
        "    root.name = a.name;   // a glTF root is called 'Scene' until someone names it",
        # what every procedural asset sets on its meshes; GLTFLoader leaves both false, so a
        # hero neither cast nor received a shadow in any recorded scene (8/8 GLBs, 2026-09-07)
        "    root.traverse((o) => { if (o.isMesh) o.castShadow = o.receiveShadow = true; });",
        f"    {target}[key] = root;",
        "  } catch (e) { " + on_error + " }",
        "}",
    ]
    return "\n".join(indent + line for line in body)


def _probe_module(zones: list[Path], glbs: list[str]) -> str:
    entries = ",\n".join(f"    ['{to_snake(p.stem)}', './zones/{p.name}']" for p in zones)
    glb_preload = _glb_preload_js(
        glbs, target="ctx.assets",
        on_error=f"console.error('{_PREFIX} asset ' + key + ' failed: ' + ((e && e.message) || e));",
    )
    return f"""// generated by codeverse3d assembler — probes env + zone modules independently; deleted after use
import * as THREE from 'three';
const ZONES = [
{entries}
];
export async function createScene({{ renderer, loaders }}) {{
  const scene = new THREE.Scene();
  const ctx = {{ THREE, scene, renderer, loaders, heightAt: () => 0, assets: {{}}, env: {{}} }};
{glb_preload}
  let env = null;
  try {{
    const m = await import('./env.js');
    if (typeof m.heightAt === 'function') ctx.heightAt = m.heightAt;
    if (typeof m.buildEnv === 'function') env = await m.buildEnv(ctx);
    ctx.env = env || {{}};
  }} catch (e) {{ console.error('{_PREFIX} env failed: ' + ((e && e.stack) || e)); }}
  const wraps = [];
  for (const [name, rel] of ZONES) {{
    try {{
      const m = await import(rel);
      if (typeof m.build !== 'function') throw new Error('module has no export build(ctx)');
      const g = await m.build(ctx);
      if (!g || !g.isObject3D) throw new Error('build(ctx) did not return a THREE.Object3D');
      const wrap = new THREE.Group();
      wrap.name = '__zone__' + name;
      wrap.add(g);
      scene.add(wrap);
      wraps.push(wrap);
    }} catch (e) {{ console.error('{_PREFIX} zone ' + name + ' failed: ' + ((e && e.stack) || e)); }}
  }}
  return {{
    scene,
    cameras: [{{ name: 'probe', position: [30, 20, 30], lookAt: [0, 0, 0], fov: 50 }}],
    update(t, dt) {{
      try {{ if (env && env.update) env.update(t, dt); }} catch (e) {{ console.error('{_PREFIX} env update failed: ' + e); }}
      for (const w of wraps) {{
        const z = w.children[0];
        try {{ if (z && z.userData.update) z.userData.update(t, dt); }} catch (e) {{ console.error('{_PREFIX} zone ' + w.name.slice(8) + ' update failed: ' + e); }}
      }}
    }},
  }};
}}
"""


def probe_zone_modules(ws: Workspace, *, timeout_s: float = 90.0, sun_azimuth_deg: float | None = None) -> ZoneProbeReport:
    """Probe env + every zone module independently in the browser host.

    Returns a :class:`ZoneProbeReport` (zone probes by snake name, census, other
    console errors).  When
    ``sun_azimuth_deg`` is given the driver also fits overview + per-group
    cameras from the measured bboxes (``camera_specs``).
    """
    from codeverse3d.spatial.render_scene import run_scene_script

    zones = zone_files(ws)
    probe_path = ws.root / PROBE_REL
    probe_path.write_text(_probe_module(zones, glb_assets(ws)))
    from codeverse3d.spatial.render_scene import _bounds_json, plan_bounds, probe_env_args

    args = ["--ws", str(ws.root), "--scene", PROBE_REL, "--timeout-ms", str(int(timeout_s * 1000))]
    args += probe_env_args()  # the zone probe sees the same settle/repair/exposure world as the renders
    if sun_azimuth_deg is not None:
        args += ["--sun-azimuth", str(sun_azimuth_deg)]
        # the overview is framed like the render rig's: inside the plan's bounds
        if (bounds := plan_bounds(ws)) is not None:
            args += ["--bounds", _bounds_json(bounds)]
    try:
        res = run_scene_script("probe_scene.mjs", args, timeout_s=timeout_s + 20)
    finally:
        probe_path.unlink(missing_ok=True)
    s = res.summary
    boot = s.get("boot") or {}
    errors: list[str] = list(s.get("console_errors", []))
    if not boot.get("ok"):
        errors.insert(0, f"probe scene did not boot [{boot.get('stage')}]: {boot.get('error', '')}")
    failed: dict[str, str] = {}
    other: list[str] = []
    for e in errors:
        m = re.match(rf"(?:console: )?{re.escape(_PREFIX)} zone (\S+) failed: (.*)", e, re.S)
        if m:
            failed[m.group(1)] = m.group(2).strip()[:600]
        else:
            other.append(e)
    census = s.get("census") or {}
    groups = {g["name"]: g for g in census.get("groups", [])}
    probes: dict[str, ZoneProbe] = {}
    for p in zones:
        name = to_snake(p.stem)
        g = groups.get(f"__zone__{name}")
        if name in failed or g is None:
            probes[name] = ZoneProbe(name=name, file=f"src/zones/{p.name}", ok=False,
                                     error=failed.get(name, "zone not present after probe (build returned nothing visible?)"))
        else:
            probes[name] = ZoneProbe(name=name, file=f"src/zones/{p.name}", ok=True, bbox=g.get("bbox"),
                                     meshes=g.get("meshes", 0), triangles=g.get("triangles", 0))
    return ZoneProbeReport(probes=probes, census=census, other_errors=other, camera_specs=s.get("fitted_cameras") or {})


# --------------------------------------------------------------------------- cameras
def fallback_camera(plan: ScenePlan | None, azimuth_deg: float = SUN_AZIMUTH_GUESS_DEG) -> CameraPlan:
    """An overview when nothing was measurable: on the lit side (``azimuth_deg`` is the scene's
    ``SUN_AZIMUTH_DEG``, 0 = +X, 90 = +Z), at the bounds' span from their centre, looking at it —
    derived, not the fixed +X+Z corner shot every example once showed."""
    if plan is None:
        c, r = (0.0, 0.0, 0.0), 30.0
    else:
        c = tuple(float(v) for v in plan.bounds.center)
        r = max(float(plan.bounds.extents[0]), float(plan.bounds.extents[2]), 10.0)
    a = math.radians(azimuth_deg)
    return CameraPlan(name="overview", position=(c[0] + 0.9 * r * math.cos(a), c[1] + 0.45 * r, c[2] + 0.9 * r * math.sin(a)),
                      look_at=(c[0], c[1] + 1.0, c[2]), fov=50.0, purpose="fallback: no measurable zones")


def _plan_from_spec(spec: dict[str, Any], *, name: str, purpose: str) -> CameraPlan:
    return CameraPlan(name=name, position=tuple(spec["position"]), look_at=tuple(spec["lookAt"]),
                      fov=float(spec.get("fov", 50.0)), purpose=purpose)


def cameras_from_specs(specs: dict[str, Any], probes: dict[str, ZoneProbe], *, max_cameras: int = 6,
                       plan: ScenePlan | None = None, azimuth_deg: float = SUN_AZIMUTH_GUESS_DEG) -> list[CameraPlan]:
    """Convert driver-fitted camera specs (``probe_scene.mjs --sun-azimuth``;
    math owned by ``runtime_js/lib/orbit.mjs``) into CameraPlans: overview
    first, then per-zone cameras, largest footprints first.  Falls back to a
    fixed overview when nothing was measurable."""
    healthy = [p for p in probes.values() if p.ok and p.bbox]
    overview = (specs or {}).get("overview")
    if not healthy or not overview:
        return [fallback_camera(plan, azimuth_deg)]
    cams = [_plan_from_spec(overview, name="overview", purpose="fallback: fitted to the measured content (the plan named no camera)")]
    by_zone = {str(z.get("group", "")).removeprefix("__zone__"): z for z in (specs or {}).get("zones", [])}
    by_area = sorted(healthy, key=lambda p: -(p.bbox["size"][0] * p.bbox["size"][2]))
    for p in by_area[: max_cameras - 1]:
        spec = by_zone.get(p.name)
        if spec:
            cams.append(_plan_from_spec(spec, name=f"{to_snake(p.name)}_view",
                                        purpose=f"fallback: zone {to_pascal(p.name)} fitted to its measured bbox (the plan named no camera)"))
    return cams


# --------------------------------------------------------------------------- scene.js
def _cam_literal(c: CameraPlan) -> str:
    p, la = c.position, c.look_at
    return (f"    {{ name: '{to_snake(c.name)}', position: [{p[0]:g}, {p[1]:g}, {p[2]:g}], "
            f"lookAt: [{la[0]:g}, {la[1]:g}, {la[2]:g}], fov: {c.fov:g} }},")


def render_scene_js(zones: list[str], cameras: list[CameraPlan], glbs: list[str], *, env_ok: bool = True) -> str:
    """Deterministic src/scene.js source for the given healthy zone snake names."""
    imports = "\n".join(f"import {{ build as build{to_pascal(z)} }} from './zones/{z}.js';" for z in zones)
    calls = "\n".join(f"  await addZone(build{to_pascal(z)}, '{to_pascal(z)}');" for z in zones)
    env_import = "import { buildEnv, heightAt } from './env.js';" if env_ok else ""
    # buildEnv/build may be async (lint + the assembler probe both accept it): await consistently
    env_build = "  const env = (await buildEnv(ctx)) || {};\n  ctx.env = env;" if env_ok else "  const env = {};"
    height = "heightAt" if env_ok else "() => 0"
    glb_preload = _glb_preload_js(
        glbs, target="ctx.assets",
        on_error="console.error(`[assets] failed to load ${a.url}: ${e && e.message}`);",
    )
    cams = "\n".join(_cam_literal(c) for c in cameras)
    return f"""// src/scene.js — ASSEMBLED BY THE HARNESS (codeverse3d assembler). Edit zones/env instead; re-assembly overwrites this file.
import * as THREE from 'three';
{env_import}
{imports}

export async function createScene({{ renderer, loaders }}) {{
  const scene = new THREE.Scene();
  const ctx = {{ THREE, scene, renderer, loaders, heightAt: {height}, assets: {{}} }};
{glb_preload}
{env_build}

  const zones = [];
  const addZone = async (build, name) => {{
    const g = await build(ctx);
    if (!g || !g.isObject3D) throw new Error(`zone ${{name}}: build(ctx) must return a THREE.Group`);
    if (!g.name) g.name = name;
    scene.add(g);
    zones.push(g);
  }};
{calls}

  // a placed GLB clone that carries clips (a keyframed Blender part; `.clone()` copies them)
  // plays them here, driven by ABSOLUTE time so t=0 / t=1.5 are deterministic frames; a zone
  // de-phases copies with `clone.userData.clipOffset` (seconds).  Measured 2026-09-07: told
  // how to play a clip itself, the windmill zone placed the clone and played nothing.
  const players = [];
  scene.traverse((o) => {{
    if (!o.animations || !o.animations.length) return;
    const mixer = new THREE.AnimationMixer(o);
    for (const clip of o.animations) mixer.clipAction(clip).play();
    players.push([mixer, +(o.userData.clipOffset || 0)]);
  }});

  const cameras = [
{cams}
  ];

  function update(t, dt) {{
    if (env.update) env.update(t, dt);
    for (const z of zones) if (z.userData.update) z.userData.update(t, dt);
    for (const [mixer, offset] of players) mixer.setTime(t + offset);
  }}
  return {{ scene, cameras, update }};
}}
"""


def assemble(ws: Workspace, plan: ScenePlan | None = None, *, cameras: str = "derive", probe: bool = True,
             timeout_s: float = 90.0) -> AssembleResult:
    """Probe zones, derive cameras, write ``src/scene.js``.  ``cameras='plan'`` keeps the plan's."""
    warnings: list[str] = []
    zones = zone_files(ws)
    if not zones:
        raise FileNotFoundError(f"no zone modules under {ws.src / 'zones'}")
    env_ok = (ws.src / "env.js").is_file()
    if not env_ok:
        warnings.append("src/env.js missing: scene assembled without env (flat ground at y=0, no lights unless zones add them)")
    probes: dict[str, ZoneProbe] = {}
    census: dict[str, Any] = {}
    camera_specs: dict[str, Any] = {}
    if probe:
        report = probe_zone_modules(ws, timeout_s=timeout_s, sun_azimuth_deg=sun_azimuth(ws))
        probes, census, camera_specs = report.probes, report.census, report.camera_specs
        for o in report.other_errors:
            if _PREFIX in o and "env failed" in o:
                env_ok = False
                warnings.append(f"env.js failed in probe; assembled without it: {o[:300]}")
            else:
                warnings.append(o[:300])
    else:
        probes = {to_snake(p.stem): ZoneProbe(name=to_snake(p.stem), file=f"src/zones/{p.name}", ok=True) for p in zones}
    healthy = [n for n, p in probes.items() if p.ok]
    failed = {n: p.error for n, p in probes.items() if not p.ok}
    if not healthy:
        warnings.append("no zone module survived probing; scene.js assembled with zero zones")
    if cameras == "plan" and plan is not None and plan.cameras:
        cams = list(plan.cameras[:6])
    else:
        cams = cameras_from_specs(camera_specs, probes, plan=plan, azimuth_deg=sun_azimuth(ws))
    text = render_scene_js(healthy, cams, glb_assets(ws), env_ok=env_ok)
    out = ws.src / "scene.js"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    result = AssembleResult(scene_path=str(out), zones_included=healthy, zones_failed=failed, env_ok=env_ok,
                            cameras=cams, warnings=warnings, census=census)
    ws.write_json(ws.artifacts / "assemble.json", result)
    return result


# ===================================================================== runtime


class SceneThreeJsRuntime:
    language = Language.SCENE_THREEJS
    entry_globs: tuple[str, ...] = (ENTRY_FILE[Language.SCENE_THREEJS], "src/zones/*.js", "src/assets/*.js", "src/env.js", "src/shaders/*.js")
    #: ``SceneRuntime.env_file``: the module the env stage writes (``SCENE_FILES[1]``)
    env_file: str = SCENE_FILES[1]

    zone_file = staticmethod(zone_file)
    asset_file = staticmethod(asset_file)

    def expected_files(self, plan: Plan | None) -> list[str]:
        return list(SCENE_FILES)

    def assemble(self, ws: Workspace, plan: ScenePlan) -> AssembleResult:
        return assemble(ws, plan, cameras="plan" if getattr(plan, "cameras", None) else "derive")

    def probe(self, ws: Workspace, *, timeout_s: float = 60.0) -> SceneProbeResult:
        from codeverse3d.spatial.probes import probe_scene

        return probe_scene(ws, timeout_s=timeout_s)

    def render_scene(self, ws: Workspace, out_dir: Path, *, cameras: list[CameraPlan] | None = None, orbit: bool = True,
                     times: Sequence[float] = (0.0, 1.5), width: int = 1024, height: int = 576,
                     sheet: bool = True) -> RenderSet:
        from codeverse3d.spatial.render_scene import render_scene

        return render_scene(ws, out_dir, cameras=cameras, orbit=orbit, times=tuple(times), width=width, height=height, sheet=sheet)

    def files_for(self, plan: Plan | None, target: str, *, alias: dict[str, str] | None = None) -> list[str]:
        return scene_files_for(plan, target, alias=alias)

    def skeleton(self, ws: Workspace, plan: Plan | None) -> list[Path]:
        return write_skeleton(ws, plan)

    def lint(self, ws: Workspace) -> GateReport:
        return lint(ws)  # the module-level gates function (class ns is not in method scope)

    def render_asset(self, ws: Workspace, name: str, out_dir: Path, *, timeout_s: float = 180.0) -> RenderSet:
        """Quick sheet of ONE procedural asset module, on the rig a Blender hero's GLB gets.

        ``src/assets/<snake>.js`` is exported to a GLB by the object track's own build
        (``ThreeJsRuntime.build`` on a scratch workspace whose two-line entry calls
        ``build<Pascal>``: the same memory cap, heap flag and typed errors) and rendered with
        ``spatial.render.render_glb``.  This is the hook ``scene_assets`` looks
        for before judging a threejs asset; nothing defined it until 2026-09-07, so no
        procedural asset was ever judged (0 of 860 recorded) while every hero was.
        """
        from codeverse3d.languages.threejs import ThreeJsRuntime
        from codeverse3d.spatial.render import RenderError, render_glb

        snake, pascal = to_snake(name), to_pascal(name)
        tmp = Workspace(ws.artifacts / "asset_export" / snake)
        tmp.src.mkdir(parents=True, exist_ok=True)
        module = (ws.src / "assets" / f"{snake}.js").resolve().as_posix()
        (tmp.src / "object.js").write_text(
            f"import {{ build{pascal} }} from '{module}';\n"
            f"export function build(THREE) {{ return build{pascal}(THREE, {{}}); }}\n")
        res = ThreeJsRuntime().build(tmp, timeout_s=int(timeout_s))
        if not res.ok or not res.glb_path:
            raise RenderError(f"asset export failed for {name}: {res.error_type}: {res.error_message}")
        return render_glb(res.glb_path, out_dir, views=list(OBJECT_VIEWS_QUICK), width=512, height=512, sheet=True)

    def build(self, ws: Workspace, *, timeout_s: int | None = None) -> BuildResult:
        """Probe + shader preflight (one browser boot); ok iff the module loads
        and no shader errors.  The two GateReports are the build's own
        (``BuildResult.gates``, which the round appends to its gates) and land
        under ``artifacts/gates/`` as well."""
        t0 = time.time()
        ws.artifacts.mkdir(parents=True, exist_ok=True)
        # a probe/driver crash must not leave the previous round's census or the root
        # driver outputs (scene_probe.json / shader_preflight.json) looking current;
        # census.json is only rewritten `if census:` below, so it MUST be wiped here
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
            # a driver that could not run is not a defect in the scene; `probes.probe_report`
            # and `run_probe` already mark it, and `build_with_repair` reads this
            # so the round does not spend its repair budget rewriting working code
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


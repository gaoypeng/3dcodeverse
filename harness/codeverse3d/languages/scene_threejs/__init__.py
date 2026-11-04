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
from codeverse3d.conventions import OBJECT_VIEWS_QUICK, ZONE_ROOT_KEY, to_pascal, to_snake
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


def _write_lib(ws: Workspace, written: list[Path]) -> None:
    for src in lib_files():
        _write(ws.src / "lib" / src.name, src.read_text(), written)


def write_example(ws: Workspace) -> list[Path]:
    """Copy the full example scene into ws.src (overwrites); raises when the starter tree is missing
    (an rglob over an absent directory would silently write nothing)."""
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


#: the stub plate's side as a multiple of the plan span
PLACEHOLDER_GROUND_SPANS = 3.0

_AZIMUTH_RE = re.compile(r"azimuth[^0-9\-]{0,12}(-?\d+(?:\.\d+)?)", re.I)
_ELEVATION_RE = re.compile(r"elevation[^0-9\-]{0,12}(-?\d+(?:\.\d+)?)", re.I)
#: the overview rig's guess when a scene states no sun azimuth
SUN_AZIMUTH_GUESS_DEG = 45.0


def sun_from_plan(plan: ScenePlan) -> tuple[float | None, float | None]:
    """The sun azimuth and elevation (degrees) the plan's ``environment`` paragraph states, ``None`` for each it does not."""
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
// displaces and textures a ground from a height field, lib/sky.js makeSky, lib/environment.js sunRig / roomShell —
// use a call when it fits the brief, write your own when it does not.
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
    """``src/env.js`` as an interface stub under the plan's data: plate side, stated sun angles, the
    room box for an interior; no light, sky or atmosphere."""
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
#: what a whole-scene session edits: the entry and the environment
SCENE_FILES: tuple[str, ...] = ("src/scene.js", "src/env.js")
#: refine targets that are the environment's, whatever the plan calls its zones
ENV_WORDS = frozenset({"env", "environment", "lighting", "sky", "fog", "ground", "water", "light"})


def zone_file(name: str) -> str:
    return f"src/zones/{to_snake(name)}.js"


def asset_file(asset: AssetPlan) -> str:
    snake = to_snake(asset.name)
    return f"src/assets/{snake}.js" if asset.kind == "threejs" else f"public/assets/{snake}.glb"


def scene_files_for(plan: Plan | None, target: str, *, alias: dict[str, str] | None = None) -> list[str]:
    """The files that own a scene refine target: an aliased asset in its survivor, a hero where it is placed, cameras in ``scene.js``, env words in ``env.js``."""
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
    """The skeleton's entry is the assembled shape under the starter's contract header."""
    header = (STARTER_DIR / "scene.js").read_text().split("import * as THREE")[0]
    glbs = [to_snake(a.name) for a in plan.assets if a.kind == "blender_glb"]
    body = render_scene_js([to_snake(z.name) for z in plan.zones], list(plan.cameras[:6]), glbs, env_ok=True)
    return (f"{header}// PLAN: {plan.title} — {plan.summary.strip()}\n"
            f"// setting: {plan.setting.strip()}   animation: {'; '.join(plan.animation) or '-'}\n" + body)


def write_skeleton(ws: Workspace, plan: Plan | None = None) -> list[Path]:
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


#: How a Blender-built asset is preloaded in every assembled entry point.  The loaded root is
#: NAMED after its asset (``to_pascal`` of the file key): a glTF root arrives as "Scene", which
#: name-based checks cannot see.
def _glb_preload_js(glbs: list[str], *, target: str, on_error: str, indent: str = "  ") -> str:
    """The `assetFiles` literal + preload loop, or "" when the plan has no GLB asset; ``on_error`` is the JS catch statement."""
    if not glbs:
        return ""
    files = ", ".join(f"{g}: {{ name: '{to_pascal(g)}', url: '/assets/{g}.glb' }}" for g in glbs)
    body = [
        f"const assetFiles = {{ {files} }};",
        "for (const [key, a] of Object.entries(assetFiles)) {",
        "  try {",
        "    const gltf = await loaders.gltf.loadAsync(a.url);",
        "    const root = gltf.scene;",
        "    root.animations = gltf.animations || [];   // a keyframed Blender part is a clip; .clone() keeps it",
        "    root.name = a.name;   // a glTF root is called 'Scene' until someone names it",
        # GLTFLoader leaves castShadow / receiveShadow false; set them like procedural assets
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
      g.userData['{ZONE_ROOT_KEY}'] = name;   // as createScene marks it: the settle at boot moves zone assets only
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
    from codeverse3d.spatial.render_scene import run_scene_script

    zones = zone_files(ws)
    probe_path = ws.root / PROBE_REL
    probe_path.write_text(_probe_module(zones, glb_assets(ws)))
    from codeverse3d.spatial.render_scene import _bounds_json, plan_bounds, probe_env_args

    args = ["--ws", str(ws.root), "--scene", PROBE_REL, "--timeout-ms", str(int(timeout_s * 1000))]
    args += probe_env_args()
    if sun_azimuth_deg is not None:
        args += ["--sun-azimuth", str(sun_azimuth_deg)]
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


# --------------------------------------------------------------------------- the env module alone
#: the env check's probe entry (deleted after use)
ENV_PROBE_REL = "src/_c3v_env_probe.js"


def lib_interface(ws: Workspace) -> str:
    """The workspace's ``src/lib/`` surface, one line per module: exported names and parameter lists."""
    from codeverse3d.languages._js_lint import export_signatures

    lib = ws.src / "lib"
    rows = [f"lib/{p.name}: " + ", ".join(sigs) for p in sorted(lib.glob("*.js"))
            if (sigs := export_signatures(p.read_text(errors="replace")))] if lib.is_dir() else []
    return "\n".join(rows)


def check_env(ws: Workspace, *, timeout_s: float = 90.0) -> str:
    """``""`` when ``src/env.js`` lints clean, runs ``buildEnv`` alone in the browser host and lights what
    it builds; else the first failure, quoted.  A probe that cannot run gives ``""`` (logged)."""
    import logging

    from codeverse3d.spatial.probes import unlit
    from codeverse3d.spatial.render_scene import SceneRenderError, probe_env_args, run_scene_script

    rel = SCENE_FILES[1]
    if not (ws.root / rel).is_file():
        return f"{rel} does not exist"
    own = [f for f in lint(ws).errors if (f.target or "").split(":")[0] == rel]
    if own:
        return "\n".join(f"{f.target}: {f.message}" + (f" ({f.fix_hint})" if f.fix_hint else "") for f in own[:6])
    probe_path = ws.root / ENV_PROBE_REL
    probe_path.write_text(_probe_module([], []))
    args = ["--ws", str(ws.root), "--scene", ENV_PROBE_REL, "--timeout-ms", str(int(timeout_s * 1000)), *probe_env_args()]
    try:
        res = run_scene_script("probe_scene.mjs", args, timeout_s=timeout_s + 20)
    except SceneRenderError as e:
        if e.scene_timed_out:
            return f"{rel}: buildEnv did not finish inside the {timeout_s:.0f} s probe budget"
        logging.getLogger(__name__).warning("env check could not run: %s", e)
        return ""
    finally:
        probe_path.unlink(missing_ok=True)
    s = res.summary
    # only the env module and its library run here: every console error is the env's
    errors = [e for e in s.get("console_errors", []) if not e.startswith("boot[")]
    boot = s.get("boot") or {}
    if not boot.get("ok"):
        errors.insert(0, f"the env probe did not boot [{boot.get('stage')}]: {boot.get('error', '')}")
    if errors:
        return "\n".join(e[:900] for e in errors[:3])
    dark = unlit(s.get("census") or {}, offline=False)
    return f"{rel}: {dark.message}" if dark is not None else ""


# --------------------------------------------------------------------------- cameras
def fallback_camera(plan: ScenePlan | None, azimuth_deg: float = SUN_AZIMUTH_GUESS_DEG) -> CameraPlan:
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
    """Convert driver-fitted camera specs into CameraPlans (overview first, then zones by footprint);
    falls back to a fixed overview when nothing was measurable."""
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
    imports = "\n".join(f"import {{ build as build{to_pascal(z)} }} from './zones/{z}.js';" for z in zones)
    calls = "\n".join(f"  await addZone(build{to_pascal(z)}, '{to_pascal(z)}');" for z in zones)
    env_import = "import { buildEnv, heightAt } from './env.js';" if env_ok else ""
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
    g.userData['{ZONE_ROOT_KEY}'] = name;   // the harness's zone mark: the placement table measures its children
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
    env_file: str = SCENE_FILES[1]

    zone_file = staticmethod(zone_file)
    asset_file = staticmethod(asset_file)

    def expected_files(self, plan: Plan | None) -> list[str]:
        return list(SCENE_FILES)

    def assemble(self, ws: Workspace, plan: ScenePlan) -> AssembleResult:
        return assemble(ws, plan, cameras="plan" if getattr(plan, "cameras", None) else "derive")

    def probe(self, ws: Workspace, *, timeout_s: float | None = None) -> SceneProbeResult:
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
        return lint(ws)

    def lib_interface(self, ws: Workspace) -> str:
        return lib_interface(ws)

    def check_env(self, ws: Workspace) -> str:
        return check_env(ws)

    def render_asset(self, ws: Workspace, name: str, out_dir: Path, *, timeout_s: float = 180.0) -> RenderSet:
        """Quick sheet of ONE procedural asset module: exported to a GLB by the object track's build
        on a scratch workspace and rendered like a hero GLB."""
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


"""Cheap scene-asset generation: single-shot first, deterministic check, dedupe.

Measured on the 2026-08-23 scene battery: every asset was a full coding-agent
session (26–44 turns, 3–4 whole-scene ``build`` + ``scene_probe`` calls each) —
$0.11–0.25 and 1–8 minutes **per asset file**, 45 % of the whole run's cost.  An
asset module is one self-contained file; it does not need a tool loop.

So the default here is:

1. **single-shot** the file (one chat call), then
2. run a deterministic node check (import the module, call the builder, inspect
   the returned group: exports, NaN vertices, empty bbox, triangle budget) —
   ~0.08 s, no browser, precise error text with line numbers, then
3. **one** error-feedback single-shot repair, then
4. only if that still fails: escalate to a real agent session.

``blender_glb`` assets are heroes and keep their agent session.
``select_assets`` caps the list by plan priority and, only when the plan is over
that cap, folds near-identical props (two rock variants) into ONE factory that
zones call with an ``opts.variant`` — a rescue for props truncation would drop.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path

from pydantic import BaseModel, Field

from codeverse.contracts.plan import AssetPlan
from codeverse.conventions import to_pascal, to_snake
from codeverse.proc import write_json_atomic, write_text_atomic
from codeverse.tracks.common import RunContext
from codeverse.tracks.generation import SINGLE_SHOT_PREFIX, is_single_shot
from codeverse.workspace import Workspace

log = logging.getLogger(__name__)

#: max triangles for ONE asset instance (the scene contract's budget)
ASSET_MAX_TRIS = 15_000


# ----------------------------------------------------------------------------- strategy
def single_shot_agent_id(agent_id: str, chat_model_id: str = "") -> str:
    """The single-shot strategy id for this run, or "" when there is no chat model.

    Single-shot is ONE api call that returns the asset file — the cheap path the asset
    stage tries before escalating to a full agent session.  It needs a chat model, and
    the coding agent is always a vendor CLI (which exposes none), so the model comes
    from ``chat_model_id`` — the run's planner backend, which is always an API model.
    Until 2026-08-28 it was derived from the in-process ``api-agent`` generator id;
    that backend is gone."""
    if is_single_shot(agent_id):
        return agent_id
    return SINGLE_SHOT_PREFIX + chat_model_id if chat_model_id.count(":") >= 1 else ""


def single_shot_ctx(ctx: RunContext) -> RunContext | None:
    """A copy of ``ctx`` bound to the single-shot strategy, or None when the
    generator has no usable chat model (CLI agents, tests with fake services)."""
    cached = ctx.extra.get("_single_shot_ctx")
    if cached is not None:
        return cached or None  # False = known-unavailable
    sid = single_shot_agent_id(ctx.agent_id, getattr(ctx.spec.backends, "planner", ""))
    sub: RunContext | None = None
    if sid:
        try:
            model = ctx.model if is_single_shot(ctx.agent_id) else ctx.services.chat_model(sid[len(SINGLE_SHOT_PREFIX):])
        except Exception as e:  # noqa: BLE001 — no chat model → keep the agent path
            log.info("single-shot generation unavailable for %s: %s", ctx.agent_id, e)
            model = None
        if model is not None:
            sub = replace(ctx, agent_id=sid, model=model, agent=None)
    ctx.extra["_single_shot_ctx"] = sub or False
    return sub


# ----------------------------------------------------------------------------- deterministic check
class AssetCheck(BaseModel):
    """Result of importing the asset module and calling its builder."""

    ok: bool = False
    ran: bool = Field(default=False, description="False = the checker itself could not run (node missing)")
    fatal: bool = Field(default=False, description="the module does not import / build at all — zones must not use it")
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    size_m: tuple[float, float, float] | None = None
    min_y: float | None = None
    tris: int = 0
    meshes: int = 0
    materials: int = 0

    def report(self, limit: int = 6) -> str:
        return "\n".join(f"- {e}" for e in (self.errors + self.warnings)[:limit])


_CHECK_JS = r"""
import * as THREE from 'three';
const [, , fileUrl, exportName] = process.argv;
const out = { ok: false, errors: [], warnings: [], size_m: null, tris: 0, meshes: 0, materials: 0 };
try {
  const mod = await import(fileUrl);
  const fn = mod[exportName] ?? mod.default;
  if (typeof fn !== 'function') {
    out.errors.push(`missing export: this file must \`export function ${exportName}(THREE, opts = {})\` (found: ${Object.keys(mod).join(', ') || 'nothing'})`);
  } else {
    const g = fn(THREE, {});
    if (!g || !g.isObject3D) {
      out.errors.push(`${exportName}(THREE) must return a THREE.Group / Object3D (got ${Object.prototype.toString.call(g)})`);
    } else {
      const box = new THREE.Box3().setFromObject(g);
      const mats = new Set();
      let bad = 0;
      g.traverse((o) => {
        if (!o.isMesh) return;
        out.meshes += 1;
        mats.add(o.material?.uuid ?? o.material);
        const pos = o.geometry?.attributes?.position;
        if (!pos) return;
        const arr = pos.array;
        for (let i = 0; i < arr.length; i++) if (!Number.isFinite(arr[i])) { bad += 1; break; }
        const idx = o.geometry.index;
        out.tris += ((idx ? idx.count : pos.count) / 3) * (o.isInstancedMesh ? o.count : 1);
      });
      out.materials = mats.size;
      if (bad) out.errors.push(`${bad} mesh(es) have NaN/Infinity vertex positions`);
      if (out.meshes === 0) out.errors.push('the returned group contains no meshes');
      if (box.isEmpty()) out.errors.push('the returned group has an empty bounding box');
      else {
        const s = new THREE.Vector3(); box.getSize(s);
        if (!Number.isFinite(s.x + s.y + s.z)) out.errors.push('the bounding box is not finite');
        else { out.size_m = [+s.x.toFixed(3), +s.y.toFixed(3), +s.z.toFixed(3)]; out.min_y = +box.min.y.toFixed(3); }
      }
      out.tris = Math.round(out.tris);
    }
  }
} catch (e) {
  out.errors.push(`${e?.name || 'Error'}: ${e?.message || String(e)}`);
  const stack = String(e?.stack || '').split('\n').slice(1, 4).filter((l) => l.includes(fileUrl.replace('file://', '')));
  if (stack.length) out.errors.push('at ' + stack.map((l) => l.trim()).join(' / '));
}
out.ok = out.errors.length === 0;
console.log(JSON.stringify(out));
"""


def _checker_path(ctx: RunContext) -> Path:
    """The checker script lives in the harness cache, never in the workspace."""
    p = Path(ctx.settings.cache_dir) / "scene_asset_check.mjs"
    if not p.is_file() or p.read_text() != _CHECK_JS:
        # run_asset_stage fans the assets out over a thread pool, so every thread
        # used to write the SAME '<cache>/scene_asset_check.mjs.tmp' and the loser's
        # replace() raised FileNotFoundError -- swallowed below into ok=True, ran=False.
        write_text_atomic(p, _CHECK_JS)
    return p


def check_threejs_asset(ctx: RunContext, rel: str, pascal: str, *, timeout_s: float = 60.0,
                        expected_size_m: tuple[float, float, float] | None = None) -> AssetCheck:
    """Import ``rel`` in node, call ``build<Pascal>(THREE, {})`` and inspect the group.

    Never raises: an unavailable node runtime yields ``ran=False, ok=True`` so the
    strategy falls back to "the scene build will catch it"."""
    path = ctx.ws.root / rel
    if not path.is_file():
        return AssetCheck(ok=False, ran=True, fatal=True, errors=[f"{rel} was not written"])
    try:
        from codeverse.spatial.node import run_node

        res = run_node(_checker_path(ctx), [path.resolve().as_uri(), f"build{pascal}"], cwd=ctx.ws.root,
                       three_hook=True, timeout_s=timeout_s, check=False)
        data = res.last_json
        if data is None:
            tail = (res.stderr or res.stdout or "").strip().splitlines()[-3:]
            return AssetCheck(ok=False, ran=True, fatal=True, errors=[f"the module failed to load: {' / '.join(tail)[:300]}"])
        chk = AssetCheck(ok=bool(data.get("ok")), ran=True, errors=list(data.get("errors") or []),
                         warnings=list(data.get("warnings") or []),
                         size_m=tuple(data["size_m"]) if data.get("size_m") else None,  # type: ignore[arg-type]
                         min_y=data.get("min_y"), tris=int(data.get("tris") or 0),
                         meshes=int(data.get("meshes") or 0), materials=int(data.get("materials") or 0))
    except Exception as e:  # noqa: BLE001 — the checker is an optimisation, never a blocker
        log.warning("asset check could not run for %s: %s", rel, e)
        return AssetCheck(ok=True, ran=False, warnings=[f"checker unavailable: {type(e).__name__}"])
    chk.fatal = not chk.ok  # everything the JS side reports is a hard failure to import/build
    _soft_findings(chk, expected_size_m)   # …the rest is wrong-but-usable: worth a repair, not a veto
    return chk


def _soft_findings(chk: AssetCheck, expected: tuple[float, float, float] | None) -> None:
    """Findings that make the asset WRONG but not broken → errors worth one repair."""
    if not chk.ok:
        return
    if chk.min_y is not None and chk.min_y < -0.02:
        chk.errors.append(f"the group sinks {abs(chk.min_y):.2f} m below y=0 — its lowest point must sit at y=0")
    if chk.tris > ASSET_MAX_TRIS:
        chk.errors.append(f"{chk.tris} triangles exceeds the {ASSET_MAX_TRIS} budget per asset — lower the segment counts")
    if chk.meshes == 1 and chk.materials <= 1 and chk.tris < 200:
        chk.errors.append("the asset is still a single low-poly box (one mesh, one material): build it from several "
                          "parts with distinct materials so it reads as the described object")
    if expected and chk.size_m:
        for axis, got, want in zip("whd", chk.size_m, expected, strict=False):
            if want > 0.05 and (got > want * 2.5 or got < want * 0.4):
                chk.errors.append(f"measured {axis}={got:.2f} m but the plan says {want:.2f} m — rescale to the planned size (±5 %)")
                break
    chk.ok = not chk.errors


def repair_feedback(chk: AssetCheck, rel: str) -> str:
    """The error-feedback block for the ONE single-shot repair attempt."""
    return (f"## The file you wrote did NOT pass the deterministic asset check\n"
            f"The harness imported `{rel}` in node and called its builder. Fix exactly these, "
            f"return the COMPLETE file again:\n{chk.report()}\n")


# ----------------------------------------------------------------------------- dedupe
#: head noun → canonical family (near-identical scene props collapse into one factory)
_FAMILY = {
    "rock": "rock", "boulder": "rock", "stone": "rock", "pebble": "rock", "rubble": "rock", "cobble": "rock",
    "tree": "tree", "sapling": "tree", "pine": "tree", "conifer": "tree", "palm": "tree",
    "bush": "bush", "shrub": "bush", "hedge": "bush", "scrub": "bush", "fern": "bush", "grass": "bush",
    "planter": "planter", "pot": "planter", "urn": "planter", "trough": "planter", "tub": "planter",
    "lamp": "lamp", "lantern": "lamp", "light": "lamp", "lightpost": "lamp", "sconce": "lamp",
    "bench": "bench", "seat": "bench", "stool": "bench", "chair": "bench",
    "crate": "crate", "box": "crate", "carton": "crate", "barrel": "crate",
    "post": "post", "pole": "post", "pillar": "post", "column": "post", "bollard": "post", "stake": "post",
    "fence": "fence", "railing": "fence", "rail": "fence", "balustrade": "fence",
    "table": "table", "desk": "table",
}


def _family(name: str) -> str:
    head = to_snake(name).split("_")[-1].rstrip("s") or to_snake(name)
    return _FAMILY.get(head, head)


def select_assets(assets: list[AssetPlan], cap: int) -> tuple[list[AssetPlan], dict[str, str]]:
    """The assets to actually build, in plan (= priority) order, plus the merge map.

    Dedupe is a *rescue*, not a default: while the plan fits under ``cap`` every planned
    prop is built (single-shot assets are nearly free, and a distinct prop is worth more
    than a variant flag).  Only an over-long list is folded — turning "drop the 9th
    asset" into "the 9th asset is a variant of the 3rd" — and whatever is still over the
    cap is truncated by priority."""
    alias: dict[str, str] = {}
    if len(assets) > cap:
        assets, alias = dedupe_assets(assets)
    if len(assets) > cap:
        kept = {a.name for a in assets[:cap]}
        assets = assets[:cap]
        alias = {k: v for k, v in alias.items() if v in kept}
    return assets, alias


def dedupe_assets(assets: list[AssetPlan]) -> tuple[list[AssetPlan], dict[str, str]]:
    """Fold near-identical props into ONE parameterised factory.

    Two assets merge when their head nouns belong to the same family AND their
    largest dimensions are within 3× — "HeroBoulder" (2.5 m) and "TalusRock"
    (0.5 m) are different props; "SteppingStone" and "PondRock" are one factory
    with a variant.  Plan order is priority: the first one survives.
    Returns ``(kept_assets, {dropped_name: kept_name})``."""
    kept: list[AssetPlan] = []
    alias: dict[str, str] = {}
    for a in assets:
        fam = _family(a.name)
        twin = next((k for k in kept if k.kind == a.kind and _family(k.name) == fam
                     and _ratio(max(k.approx_size_m), max(a.approx_size_m)) <= 3.0), None)
        if twin is None:
            kept.append(a)
            continue
        alias[a.name] = twin.name
    if alias:
        kept = [_with_variants(k, [n for n, t in alias.items() if t == k.name],
                               {n: a for n, a in ((x.name, x) for x in assets)}) for k in kept]
    return kept, alias


def _ratio(a: float, b: float) -> float:
    lo, hi = sorted((abs(a) or 1e-6, abs(b) or 1e-6))
    return hi / lo


def _with_variants(asset: AssetPlan, dropped: list[str], by_name: dict[str, AssetPlan]) -> AssetPlan:
    """Tell the kept asset it must also serve as its merged twins (opts.variant)."""
    if not dropped:
        return asset
    lines = [f"variant {i + 1} = {n} ({by_name[n].description.strip()})" for i, n in enumerate(dropped) if n in by_name]
    extra = (f" This ONE factory must also cover: {'; '.join(lines)}. Read `opts.variant` (integer, 0 = the base "
             f"asset above, 1..{len(lines)} = the variants in that order) and `opts.scale` (number, default 1) and "
             f"change proportions/detail/colour accordingly — zone code calls the same builder with different opts.")
    return asset.model_copy(update={"description": asset.description.rstrip() + extra})


def variant_index(alias: dict[str, str], name: str) -> int:
    """1-based variant index of a merged asset within its surviving factory."""
    kept = alias.get(name)
    if not kept:
        return 0
    return sorted(n for n, t in alias.items() if t == kept).index(name) + 1


def write_dedupe_note(ws: Workspace, alias: dict[str, str]) -> None:
    """Persist the merge map next to the other stage artifacts (flywheel + debugging)."""
    if alias:
        write_json_atomic(ws.root / "stages" / "asset_aliases.json", {"alias": alias})


def write_variant_shims(ws: Workspace, alias: dict[str, str], available: set[str]) -> list[str]:
    """Replace a merged asset's skeleton stub with a shim onto the surviving factory.

    Zone code is TOLD to call ``build<Kept>(THREE, {variant: n})``, but a zone that
    imports the old name anyway must get the real prop, not the placeholder blockout
    box the skeleton left behind."""
    written: list[str] = []
    for dropped, kept in sorted(alias.items()):
        if kept not in available:
            continue
        rel = f"src/assets/{to_snake(dropped)}.js"
        path = ws.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"// {dropped} is a variant of {kept} (assets were merged into one factory).\n"
            f"import {{ build{to_pascal(kept)} }} from './{to_snake(kept)}.js';\n\n"
            f"export function build{to_pascal(dropped)}(THREE, opts = {{}}) {{\n"
            f"  return build{to_pascal(kept)}(THREE, {{ variant: {variant_index(alias, dropped)}, ...opts }});\n}}\n")
        written.append(rel)
    return written

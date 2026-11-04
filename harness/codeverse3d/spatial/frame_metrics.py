"""Frame-quality gate for scene renders: ``scene_frames``.

Turns the ``camera_checks`` in ``metrics.json`` (luminance, coverage, nearest geometry, eye height) into
``GateFinding``s with fix hints.  ``frame_findings`` / ``frame_gate_from_renders`` build the report,
``frame_summary_text`` the per-camera table.  Authored cameras get ERRORs; harness orbit views only WARN.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from codeverse3d.contracts.artifacts import GateFinding, GateReport, RenderSet, Severity
from codeverse3d.proc import read_json_or_none
from codeverse3d.spatial.frame_motion import MotionRow, motion_summary_text, scene_moves

FRAME_GATE = "scene_frames"

# thresholds (fractions of the frame / of full white)
DARK_MEAN_LUM = 0.12
#: a dark-mean frame with at least this luminance std is low-key by design, not unlit
LOWKEY_MIN_STD = 0.12
DARK_FRAC = 0.35
BLOWN_FRAC = 0.20
FLAT_MODAL_FRAC = 0.85
FLAT_MODAL_ERROR = 0.92
NEAR_HIT_M = 0.5
CONTENT_MIN_ESTABLISHING = 0.20
CONTENT_MIN_AUTHORED = 0.10
CONTENT_MIN_OVERVIEW = 0.05
EYE_MIN_ABOVE_GROUND_M = 0.3
TARGET_BLOCKED_FRAC = 0.5      # the line of sight to lookAt is cut before this fraction of the distance
BLOCKED_RAYS_MIN = 6           # of the 3 x 3 sight rays, within near_limit_m (host BLOCKED_M)
EYE_MAX_ABOVE_GROUND_M = 80.0

#: hint for a frame that is dark and flat (nothing is lit)
_DARK_HINT = (
    "the frame is too dark (the gate wants mean luminance >= 0.12, or real contrast with <= 35 % near-black pixels): "
    "raise the key and the fill until the authored frames measure over the line without flattening them (scene_views: "
    "camera_checks.mean_lum) — a stronger sun / hemisphere, or emissive practicals where the brief has lamps (an emissive "
    "face plus a PointLight each; `PRACTICAL` in lib/environment.js is one); the library's `sunRig({ mood, intensity, "
    "fill })` is one way to a balanced rig, and a set sun (elevation < 0) is its night rig; a fog colour that matches "
    "the sky keeps the distance readable; dusk and night are colour, not black"
)
#: hint for a frame that is dark but contrasty (something is lit; flat fill would destroy it)
_LOWKEY_HINT = (
    "this frame is DIM BUT LIT — it has real contrast, so flat fill (an AmbientLight / HemisphereLight, a stronger "
    "DirectionalLight) is the wrong move: it turns a night scene into a grey wash, washes dark materials out to "
    "near-white and drowns emissive/shader effects. Lift the picture where the light actually comes from: raise "
    "emissiveIntensity on the lamps/lanterns themselves, add or brighten a PointLight at each practical light, give dark "
    "materials a low but non-zero base colour so they read as material and not as void, and let the shadowed areas "
    "stay dark. Only a frame that is ALSO flat (no contrast) needs key/fill."
)
_BLOWN_HINT = (
    "the frame is blown out (the limit is 20 % pure-white pixels): lower the exposure until it measures under — "
    "typically a key light <= 3, hemisphere <= 1, no flat-white unlit planes, sky below 0.9 white, emissive <= 4 on "
    "large surfaces"
)
_FLAT_HINT = (
    "one luminance band dominates the frame: aim the camera at its subject (not at a wall, the sky or bare ground) and "
    "give the frame contrast — a shadow-casting key light, varied materials, a sky / fog gradient rather than one flat tint"
)
_NEAR_HINT = ("move the camera >= 0.5 m away from every surface (raise it above ground/terrain, pull it out of walls and "
              "trees) and keep lookAt on its subject")
_STATIC_HINT = (
    "nothing moved between the animation times (measured: under 0.4 % of an authored frame's pixels changed by more than "
    "8/255, and under 0.08 % by more than 60): the planned motion has to be visible from an authored camera at t = 0 and "
    "t = 1.5 s — for example sway of ±0.1-0.2 rad, >= 3 cm of water displacement or a scrolling normal/uv, >= 0.6 m of "
    "particle travel, >= 20°/s of rotation — and driven from the one hook the harness calls: the object createScene "
    "returns (update(t, dt)) → env.update → each zone group's userData.update(t, dt), as a pure function of t"
)
_SMALL_HINT = (
    "the frame is mostly sky / bare ground: aim this camera at what the brief says this viewpoint looks at, and make "
    "sure that subject is built and in the line of sight (a camera_target_blocked finding names what cuts it); the "
    "gate counts built geometry other than ground and water as content — check with scene_views"
)


_EYE_HINT = ("stand the camera on the surface under it: position[1] = heightAt(x, z) at its standpoint plus the height "
             "the brief gives this viewpoint (eye level ~1.6 m, a balcony, a tower) — never below the terrain")
_LOW_HINT = ("the eye has to be >= 0.3 m above the surface beneath it (measured: ground_below_m): set position[1] = "
             "heightAt(x, z) + the height the brief describes for this viewpoint — eye level (~1.6 m), a balcony, a "
             "tower — and look at the subject")
_HIGH_HINT = ("bring the camera below 80 m above the surface it stands over (measured: ground_below_m), to a height the "
              "brief's viewpoint justifies — a tower top, a ridge, a rooftop, eye level — still aimed at its subject")

#: scene_blender's wording of the same fixes (bpy lamps / world nodes / keyframes, z up)
_BPY_HINTS: dict[str, str] = {
    "dark": (
        "the frame is too dark (the gate wants mean luminance >= 0.12, or real contrast with <= 35 % near-black pixels): "
        "raise the light in build_env (src/env.py) until the authored frames measure over the line without flattening them "
        "(scene_views: camera_checks.mean_lum) — the Sun lamp's strength (`sun.data.energy`, typically 3-5 by day, 1-2 at "
        "golden hour; a sun below the horizon is a night rig) and the world Background / Sky Texture strength (the fill), "
        "practical lights where the brief has lamps (a Point lamp of 20-200 W plus an Emission shader on the lamp mesh); "
        "a world Volume thicker than ~0.02 swallows the light; dusk and night are colour, not black"),
    "lowkey": (
        "this frame is DIM BUT LIT — it has real contrast, so flat fill (a stronger world Background, a big area / sun "
        "lamp) is the wrong move: it turns a night scene into a grey wash, washes dark materials out and drowns emissive "
        "effects. Lift the picture where the light actually comes from: raise the Emission Strength of the lamps/lanterns, "
        "add or brighten a Point lamp at each practical light, give dark materials a low but non-zero Base Color so they "
        "read as material and not as void, and let the shadowed areas stay dark. Only a frame that is ALSO flat (no "
        "contrast) needs key/fill."),
    "blown": (
        "the frame is blown out (the limit is 20 % pure-white pixels): lower the exposure until it measures under — "
        "typically a Sun lamp strength <= 5, world Background / Sky Texture strength <= 1.5, no white Emission planes, "
        "Emission Strength <= 5 on large surfaces; the harness renders AgX at exposure 0 and owns the view settings"),
    "flat": (
        "one luminance band dominates the frame: aim the camera at its subject (not at a wall, the sky or bare ground) and "
        "give the frame contrast — a Sun lamp that casts shadows, varied materials, a world (Sky Texture / Background) and "
        "Volume that are not one flat tint"),
    "near": ("move the camera >= 0.5 m away from every surface (raise it above ground/terrain: z = ctx.height_at(x, y) + its "
             "height, pull it out of walls and trees) and keep look_at on its subject"),
    "static": (
        "nothing moved between the animation times (measured: under 0.4 % of an authored frame's pixels changed by more "
        "than 8/255, and under 0.08 % by more than 60): the planned motion has to be visible from an authored camera at "
        "t = 0 and t = 1.5 s — for example sway of ±0.1-0.2 rad, >= 3 cm of water displacement, >= 0.6 m of particle "
        "travel, >= 20°/s of rotation — as DATA the .blend plays by itself: keyframes (object location/rotation, node "
        "inputs, shape keys), a simple-expression driver on `frame` (python-function drivers never run here) or the "
        "geometry-nodes Scene Time node.  The harness renders t = 0 s and t = 1.5 s as frames 1 and 46 (30 fps)"),
    "small": _SMALL_HINT.replace("lookAt", "look_at"),
    "eye": ("stand the camera on the surface under it: z = ctx.height_at(x, y) at its standpoint plus the height the brief "
            "gives this viewpoint (eye level ~1.6 m, a balcony, a tower) — never below the terrain"),
    "low": ("the eye has to be >= 0.3 m above the surface beneath it (measured: ground_below_m): set z = ctx.height_at(x, y) "
            "+ the height the brief describes for this viewpoint — eye level (~1.6 m), a balcony, a tower — and look at "
            "the subject"),
    "high": _HIGH_HINT,
    "unused_glb": (
        "the hero GLB is imported into ctx.assets['<key>'] and nothing places it — add a collection-instance Empty of "
        "ctx.assets['<key>'] inside the zone the plan puts it in.  If a refine round replaced the hero with a procedural "
        "rebuild, say so in the plan: `assets[].kind: blender_glb` must not claim a hero the rendered scene does not contain."),
}

#: fix hints per scene language (``metrics["language"]``; none means three.js)
HINTS: dict[str, dict[str, str]] = {
    "scene_threejs": {"dark": _DARK_HINT, "lowkey": _LOWKEY_HINT, "blown": _BLOWN_HINT, "flat": _FLAT_HINT,
                      "near": _NEAR_HINT, "static": _STATIC_HINT, "small": _SMALL_HINT, "eye": _EYE_HINT, "low": _LOW_HINT,
                      "high": _HIGH_HINT},
    "scene_blender": _BPY_HINTS,
}

#: a judged Blender frame slower than this (GPU seconds; CPU gets ``SLOW_FRAME_CPU_FACTOR`` x) is a WARN
SLOW_FRAME_S = 10.0
SLOW_FRAME_CPU_FACTOR = 3.3


def hints_for(metrics: dict[str, Any]) -> dict[str, str]:
    """The fix-hint table for ``metrics``' scene language."""
    return HINTS.get(str(metrics.get("language") or "scene_threejs"), HINTS["scene_threejs"])


def _view_kind(chk: dict[str, Any]) -> str:
    return str(chk.get("kind") or "authored")


def _f(sev: Severity, target: str, msg: str, hint: str, **data: Any) -> GateFinding:
    return GateFinding(gate=FRAME_GATE, severity=sev, target=target, message=msg, fix_hint=hint, data=data)


def _num(chk: dict[str, Any], key: str) -> float | None:
    v = chk.get(key)
    return float(v) if isinstance(v, (int, float)) else None


def _exposure_findings(chk: dict[str, Any], *, authored: bool, hints: dict[str, str]) -> list[GateFinding]:
    name = str(chk.get("name", "?"))
    sev = Severity.ERROR if authored else Severity.WARN
    out: list[GateFinding] = []
    # the orbit rig is the harness's camera: every message says so, or the judge blames the scene
    rig = "" if authored else "harness rig view (not an authored camera): "
    mean, dark, blown, modal = (_num(chk, k) for k in ("mean_lum", "dark_frac", "blown_frac", "modal_frac"))
    std = _num(chk, "lum_std")
    is_dark = (mean is not None and mean < DARK_MEAN_LUM) or (dark is not None and dark > DARK_FRAC)
    # dim but lit: real contrast; a night scene must not be told to add fill light
    low_key = is_dark and std is not None and std >= LOWKEY_MIN_STD and (dark is None or dark <= DARK_FRAC)
    if is_dark:
        msg = (f"frame too dark: mean luminance {mean:.2f}, {dark or 0:.0%} of pixels near black" if mean is not None
               else f"frame too dark: {dark:.0%} of pixels near black")
        if low_key:
            msg = (f"frame is dim but lit: mean luminance {mean:.2f} with contrast {std:.2f} "
                   f"({dark or 0:.0%} of pixels near black) — low-key by design, not unlit")
        out.append(_f(Severity.WARN if low_key else sev, name, rig + msg,
                      hints["lowkey"] if low_key else hints["dark"],
                      kind="dark_frame", view=name, mean_lum=mean, dark_frac=dark))
    if blown is not None and blown > BLOWN_FRAC:
        out.append(_f(sev, name, rig + f"frame blown out: {blown:.0%} of pixels pure white", hints["blown"],
                      kind="blown_frame", view=name, blown_frac=blown))
    if modal is not None and modal > FLAT_MODAL_FRAC and not is_dark:
        flat_sev = sev if modal > FLAT_MODAL_ERROR else Severity.WARN
        out.append(_f(flat_sev, name, rig + f"flat frame: one luminance band holds {modal:.0%} of the pixels", hints["flat"],
                      kind="flat_frame", view=name, modal_frac=modal, mean_lum=mean))
    return out


def _geometry_findings(chk: dict[str, Any], *, authored: bool, ground_y: float | None,
                       hints: dict[str, str]) -> list[GateFinding]:
    name = str(chk.get("name", "?"))
    sev = Severity.ERROR if authored else Severity.WARN
    out: list[GateFinding] = []
    near = _num(chk, "nearest_hit_m")
    inside = list(chk.get("inside_mesh_bbox") or [])
    rig = "" if authored else "harness rig view (not an authored camera): "
    if chk.get("camera_in_geometry") or (near is not None and near < NEAR_HIT_M):
        where = f"inside {inside[:3]}" if inside else f"nearest surface {near:.2f} m ({chk.get('nearest_hit_name', '')})"
        out.append(_f(sev, name, rig + f"camera inside / touching geometry: {where}", hints["near"],
                      kind="camera_in_geometry", view=name, nearest_hit_m=near, inside=inside[:5]))
    else:
        # the shot is a surface: most of the 3 x 3 sight rays end within arm's reach
        near_rays, rays_total = _num(chk, "near_rays"), _num(chk, "rays_total")
        if authored and near_rays is not None and rays_total and near_rays >= BLOCKED_RAYS_MIN:
            limit = _num(chk, "near_limit_m") or 1.5
            out.append(_f(Severity.ERROR, name,
                          f"camera blocked: {near_rays:.0f} of {rays_total:.0f} sight rays end within {limit:g} m "
                          f"(nearest {chk.get('nearest_hit_name', '')} at {near:.2f} m) — the frame is that surface, not the shot",
                          "move the camera back or aside until its subject is clear, or move the blocking object out of the "
                          "sightline; render this view and look before re-judging",
                          kind="camera_blocked", view=name, near_rays=near_rays, nearest_hit_m=near,
                          nearest_hit_name=chk.get("nearest_hit_name", "")))
        # or the line of sight to the plan's lookAt is cut well before it
        t_dist, t_hit = _num(chk, "target_distance_m"), _num(chk, "target_hit_m")
        if authored and t_dist and t_hit is not None and t_hit < TARGET_BLOCKED_FRAC * t_dist:
            cutter = str(chk.get("target_hit_name") or "geometry")
            out.append(_f(Severity.WARN, name,
                          f"line of sight from {name} to its lookAt is cut by {cutter} at {t_hit:.2f} m of {t_dist:.1f} m "
                          f"({100 * t_hit / t_dist:.0f}% of the way) — the subject may be hidden behind it",
                          f"look at this view: if {cutter} hides the subject, move the camera aside or the object out of the sightline",
                          kind="camera_target_blocked", view=name, target_hit_m=t_hit, target_distance_m=t_dist, cutter=cutter))
    eye = _num(chk, "eye_height_m")
    below = _num(chk, "ground_below_m")
    above_g = _num(chk, "ground_above_m")
    if authored and above_g is not None:
        # a ground surface straight above the lens: the frame looks up at back faces and renders "fine"
        under = str(chk.get("ground_above_name") or "a ground surface")
        buried = _frame_looks_buried(chk)
        out.append(_f(Severity.ERROR if buried else Severity.WARN, name,
                      f"camera is {above_g:.1f} m under {under} — the frame is its underside",
                      hints["eye"],
                      kind="camera_underground" if buried else "camera_under_ground_mesh",
                      view=name, eye_height_m=eye, ground_above_m=above_g))
    elif authored and below is not None:
        # the ray straight down from the eye: the surface this lens stands over (scene-wide ground_y is wrong on relief terrain)
        under = str(chk.get("ground_below_name") or "the surface beneath it")
        if below < EYE_MIN_ABOVE_GROUND_M:
            out.append(_f(Severity.WARN, name, f"camera eye only {below:.2f} m above {under} — ant's-eye view",
                          hints["low"],
                          kind="camera_low", view=name, eye_height_m=eye, ground_below_m=below))
        elif below > EYE_MAX_ABOVE_GROUND_M:
            out.append(_f(Severity.WARN, name, f"camera {below:.0f} m above {under} — satellite view, not a shot",
                          hints["high"],
                          kind="camera_high", view=name, eye_height_m=eye, ground_below_m=below))
    elif authored and eye is not None and ground_y is not None:
        # nothing beneath the lens: compare with the scene's highest ground surface
        above = eye - ground_y
        if above < -0.2:
            # ground_y is the top of every ground mesh, so terrain can sit above an open camera:
            # only call it buried when the frame agrees
            buried = _frame_looks_buried(chk)
            out.append(_f(Severity.ERROR if buried else Severity.WARN, name,
                          f"camera is {-above:.1f} m below the scene's highest ground surface ({ground_y:.2f} m)"
                          + ("" if buried else " — the frame itself renders fine, so this is probably terrain"
                             " (a hill / raised bed / backdrop) reaching above the camera, not a buried camera"),
                          hints["eye"],
                          kind="camera_underground" if buried else "camera_below_high_ground",
                          view=name, eye_height_m=eye, ground_y=ground_y))
        elif above < EYE_MIN_ABOVE_GROUND_M:
            out.append(_f(Severity.WARN, name, f"camera eye only {above:.2f} m above ground — ant's-eye view",
                          hints["low"],
                          kind="camera_low", view=name, eye_height_m=eye, ground_y=ground_y))
        elif above > EYE_MAX_ABOVE_GROUND_M:
            out.append(_f(Severity.WARN, name, f"camera {above:.0f} m above ground — satellite view, not a shot",
                          hints["high"],
                          kind="camera_high", view=name, eye_height_m=eye, ground_y=ground_y))
    return out


def _frame_looks_buried(chk: dict[str, Any]) -> bool:
    """Does the rendered frame corroborate "this camera is under the ground"?"""
    if chk.get("camera_in_geometry"):
        return True
    content, mean, modal = (_num(chk, k) for k in ("content_frac", "mean_lum", "modal_frac"))
    if mean is not None and mean < DARK_MEAN_LUM:
        return True
    if content is not None and content < CONTENT_MIN_AUTHORED:
        return True
    return modal is not None and modal > FLAT_MODAL_ERROR


def _coverage_finding(chk: dict[str, Any], *, role: str, hints: dict[str, str]) -> GateFinding | None:
    """role: 'establishing' | 'authored' | 'overview' | 'eye'."""
    content = _num(chk, "content_frac")
    if content is None or role == "eye":
        return None
    name = str(chk.get("name", "?"))
    sky, ground = _num(chk, "sky_frac"), _num(chk, "ground_frac")
    detail = f"content {content:.0%} of the frame (sky {sky or 0:.0%}, ground {ground or 0:.0%})"
    if role == "establishing" and content < CONTENT_MIN_ESTABLISHING:
        return _f(Severity.ERROR, name, f"establishing shot (the first authored camera) shows too little content: {detail}",
                  hints["small"],
                  kind="content_small", view=name, content_frac=content, sky_frac=sky, ground_frac=ground, role=role)
    if role == "authored" and content < CONTENT_MIN_AUTHORED:
        return _f(Severity.WARN, name, f"shot shows little content: {detail}", hints["small"],
                  kind="content_small", view=name, content_frac=content, sky_frac=sky, ground_frac=ground, role=role)
    if role == "overview" and content < CONTENT_MIN_OVERVIEW:
        return _f(Severity.WARN, name, f"overview rig sees sparse content: {detail}",
                  "the overview rig sees little built geometry inside the plan bounds: check that the planned zones and "
                  "assets exist where the plan puts them (zone_empty / missing_content name what is absent), or that the "
                  "bounds describe the area actually built", kind="content_small", view=name, content_frac=content, role=role)
    return None


def stored_motion(metrics: dict[str, Any]) -> list[MotionRow]:
    """Motion rows persisted into ``metrics.json`` by ``render_scene`` (``[]`` when absent)."""
    out: list[MotionRow] = []
    for m in metrics.get("motion") or []:
        if not isinstance(m, dict):
            continue
        try:
            out.append(MotionRow(name=str(m["name"]), kind=str(m.get("kind") or "authored"),
                                 t0=float(m.get("t0", 0.0)), t1=float(m.get("t1", 0.0)),
                                 changed_frac=float(m.get("changed_frac", 0.0)),
                                 strong_frac=float(m.get("strong_frac", 0.0)),
                                 max_delta=int(m.get("max_delta", 0)), mean_delta=float(m.get("mean_delta", 0.0))))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _motion_findings(rows: list[MotionRow], hints: dict[str, str]) -> list[GateFinding]:
    """One finding for the whole scene: frozen (ERROR) or the measured per-camera table (INFO)."""
    moves = scene_moves(rows)
    if moves is None:
        return []
    if moves is False:
        worst = max((r.changed_frac for r in rows if r.authored), default=0.0)
        return [_f(Severity.ERROR, "overall",
                   f"nothing moves: the largest change on an authored camera is {worst:.2%} of pixels between "
                   f"t={rows[0].t0:g}s and t={rows[0].t1:g}s (measured, not perceived)", hints["static"],
                   kind="no_motion", changed_frac=round(worst, 5),
                   views=[r.name for r in rows if r.authored][:6])]
    moving = [r for r in rows if r.authored and r.moving]
    return [_f(Severity.INFO, "overall",
               "measured motion between the animation times: "
               + ", ".join(f"{r.name} {r.changed_frac:.1%}" for r in rows if r.authored)[:220], "",
               kind="motion", moving=[r.name for r in moving], n_moving=len(moving))]


_UNUSED_GLB_HINT = (
    "the GLB is preloaded by the assembled scene.js and nothing places it — clone `ctx.assets['<key>']` "
    "inside the zone the plan puts it in (every GLB under public/assets is preloaded; a zone never loads one itself). "
    "If a refine round replaced the asset with a procedural rebuild, DELETE the dead load: leaving it in "
    "makes plan.json's `assets[].kind: blender_glb` claim a Blender hero the rendered scene does not contain."
)


HINTS["scene_threejs"]["unused_glb"] = _UNUSED_GLB_HINT


def _glb_findings(census: dict[str, Any], hints: dict[str, str]) -> list[GateFinding]:
    """A GLB that was loaded and contributed no geometry to the rendered scene.

    WARN, not ERROR: deep-copied geometry would read as unused.
    """
    rows = census.get("glb_assets")
    if not isinstance(rows, list):
        return []
    out: list[GateFinding] = []
    for r in rows:
        if not isinstance(r, dict) or r.get("in_scene") is not False:
            continue
        url = str(r.get("url", "?"))
        out.append(_f(Severity.WARN, "overall",
                      f"{url} is loaded but no geometry from it reaches the rendered scene "
                      f"({r.get('meshes', 0)} mesh(es) in the file, 0 in the frame)",
                      hints["unused_glb"], kind="unused_glb_asset", url=url,
                      meshes=r.get("meshes"), meshes_in_scene=r.get("meshes_in_scene")))
    return out


HERO_MIN_FRAC = 0.005            # of the frame, best authored camera: below it no camera sees the hero
HERO_DETAIL_MIN_FRAC = 0.02      # of the frame, in the camera NAMED for the hero


def _hero_findings(checks: list[dict[str, Any]], census: dict[str, Any]) -> list[GateFinding]:
    """A Blender hero that is in the scene but in no authored frame, or nearly absent from its named camera (``camera_checks[].glb_frac``)."""
    rows = census.get("glb_assets")
    if not isinstance(rows, list):
        return []
    authored = [c for c in checks if _view_kind(c) == "authored"]
    # which camera is named for which hero is the host's rule (scene_host.mjs namesHero)
    named = {str(c.get("name", "?")): c.get("hero_for") or [] for c in authored}
    out: list[GateFinding] = []
    for r in rows:
        if not isinstance(r, dict) or not r.get("in_scene"):
            continue
        url = str(r.get("url", "?"))
        seen: dict[str, float] = {}
        for c in authored:
            fr = c.get("glb_frac")
            v = _num(fr, url) if isinstance(fr, dict) else None
            if v is not None:
                seen[str(c.get("name", "?"))] = v
        if not seen:
            continue
        base = url.rsplit("/", 1)[-1]
        best = max(seen.values())
        listing = ", ".join(f"{n} {v:.1%}" for n, v in list(seen.items())[:6])
        # one instance's extent; the copy count explains why frame fraction and size can disagree
        size = _num(r, "size_m")
        copies = int(r.get("instances") or 0)
        placed = f" ({size:.1f} m across as placed{f', {copies} copies' if copies > 1 else ''})" if size else ""
        if best < HERO_MIN_FRAC:
            out.append(_f(Severity.ERROR, base,
                          f"{base}{placed} is in the scene but fills at most {best:.1%} of any authored frame ({listing}) — "
                          "no camera sees the hero: it is far from every shot or something stands between, not small",
                          "aim the camera named for it at the hero's placement with a clear line of sight (a "
                          "camera_target_blocked finding names what cuts it) or move the hero into a shot; a hero the "
                          "frames never show cannot pass its must-have",
                          kind="hero_unseen", url=url, best_frac=best, per_camera=seen))
            continue
        for cam, v in seen.items():
            if url in named.get(cam, ()) and v < HERO_DETAIL_MIN_FRAC:
                out.append(_f(Severity.WARN, cam,
                              f"{cam} is named for {base} but shows it at {v:.1%} of the frame (its best view is {best:.1%})",
                              "the camera named for a hero shows it plainly (the floor is 2 % of the frame): move closer "
                              "along the sightline or clear what stands between, until the hero is the subject of that shot",
                              kind="hero_small_in_its_camera", url=url, frac=v, best_frac=best))
    return out


def _render_time_findings(metrics: dict[str, Any]) -> list[GateFinding]:
    """WARN when the median judged Blender frame took longer than ``SLOW_FRAME_S``, naming the heaviest zone; silent for three.js."""
    b = metrics.get("blender")
    if not isinstance(b, dict):
        return []
    ms = sorted(float(v["render_ms"]) for v in metrics.get("views") or []
                if isinstance(v, dict) and v.get("judge", True) and isinstance(v.get("render_ms"), int | float))
    if not ms:
        return []
    median_s = ms[len(ms) // 2] / 1000.0
    limit = SLOW_FRAME_S * (SLOW_FRAME_CPU_FACTOR if str(b.get("device")).upper() == "CPU" else 1.0)
    if median_s <= limit:
        return []
    groups = [g for g in (metrics.get("census") or {}).get("groups") or []
              if isinstance(g, dict) and g.get("kind") == "content" and isinstance(g.get("triangles"), int | float)]
    heavy = max(groups, key=lambda g: g["triangles"], default=None)
    where = f"; the heaviest zone is {heavy.get('name')} ({int(heavy['triangles']):,} triangles)" if heavy else ""
    return [_f(Severity.WARN, str(heavy.get("name")) if heavy else "overall",
               f"a judged frame takes {median_s:.1f} s to render (median, {b.get('engine')} {b.get('samples')} spp on the "
               f"{b.get('device')}; the ceiling is {limit:.0f} s){where}",
               "thin what costs render time: instance repeated geometry (collection instances / geometry-nodes Instance on "
               "Points) instead of copying meshes, fewer lights (a Point lamp per lantern only where it is seen), fewer "
               "transmissive/glass and volume objects, lower subdivision levels on what is far from every camera",
               kind="slow_frame", median_s=round(median_s, 2), limit_s=limit, device=b.get("device"),
               zone=heavy.get("name") if heavy else None)]


def frame_findings(metrics: dict[str, Any]) -> GateReport:
    """``scene_frames`` gate from a ``metrics.json`` payload (``camera_checks`` + ``census`` + ``motion``)."""
    checks: list[dict[str, Any]] = [c for c in metrics.get("camera_checks") or [] if isinstance(c, dict)]
    ground_y = (metrics.get("census") or {}).get("ground_y")
    ground_y = float(ground_y) if isinstance(ground_y, (int, float)) else None
    findings: list[GateFinding] = []
    first_authored = next((c for c in checks if _view_kind(c) == "authored"), None)
    hints = hints_for(metrics)
    for chk in checks:
        authored = _view_kind(chk) == "authored"
        findings += _exposure_findings(chk, authored=authored, hints=hints)
        findings += _geometry_findings(chk, authored=authored, ground_y=ground_y, hints=hints)
        if authored:
            role = "establishing" if chk is first_authored else "authored"
        else:
            role = "overview" if str(chk.get("name", "")).startswith("overview") else "eye"
        cov = _coverage_finding(chk, role=role, hints=hints)
        if cov is not None:
            findings.append(cov)
    findings += _motion_findings(stored_motion(metrics), hints)
    findings += _glb_findings(metrics.get("census") or {}, hints)
    findings += _hero_findings(checks, metrics.get("census") or {})
    findings += _render_time_findings(metrics)
    if checks and not [f for f in findings if f.severity != Severity.INFO]:
        n = len(checks)
        findings.append(_f(Severity.INFO, "overall", f"{n} camera frame(s) checked: exposure, geometry and coverage within limits", "",
                           kind="frames_ok", n_views=n))
    return GateReport.of(FRAME_GATE, findings)


def frame_gate_from_renders(source: RenderSet | Path | str) -> GateReport:
    """``scene_frames`` gate from a RenderSet, a render directory or a metrics.json path; missing metrics → empty passing report."""
    from codeverse3d.spatial.render_scene import metrics_path_for

    if isinstance(source, RenderSet):
        path = metrics_path_for(source)
    else:
        p = Path(source)
        path = p if p.is_file() else (p / "metrics.json" if (p / "metrics.json").is_file() else None)
    metrics = read_json_or_none(path) if path is not None else None
    if metrics is None:
        return GateReport.of(FRAME_GATE)
    return frame_findings(metrics)


def frame_summary_text(metrics: dict[str, Any], *, max_rows: int = 12) -> str:
    """Compact per-camera table for tool observations / prompts:
    ``name: lum 0.07 dark 41% blown 0% content 48% (sky 25% ground 27%) eye 5.2 m — TOO DARK``."""
    rows: list[str] = []
    report = frame_findings(metrics)
    flagged: dict[str, list[str]] = {}
    for f in report.findings:
        if f.severity != Severity.INFO and f.target:
            flagged.setdefault(f.target, []).append(str(f.data.get("kind", "")).replace("_", " ").upper())
    for chk in (metrics.get("camera_checks") or [])[:max_rows]:
        if not isinstance(chk, dict):
            continue
        name = str(chk.get("name", "?"))
        bits = [f"{name} [{_view_kind(chk)}]:"]
        for key, label, pct in (("mean_lum", "lum", False), ("dark_frac", "dark", True), ("blown_frac", "blown", True),
                                ("content_frac", "content", True), ("sky_frac", "sky", True), ("ground_frac", "ground", True)):
            v = _num(chk, key)
            if v is not None:
                bits.append(f"{label} {v:.0%}" if pct else f"{label} {v:.2f}")
        eye, near = _num(chk, "eye_height_m"), _num(chk, "nearest_hit_m")
        if eye is not None:
            bits.append(f"eye {eye:.1f} m")
        if near is not None:
            bits.append(f"nearest {near:.1f} m")
        if name in flagged:
            bits.append("— " + ", ".join(flagged[name]))
        rows.append(" ".join(bits))
    head = f"frame checks ({'ok' if report.passed else 'FAILED'}):"
    motion = motion_summary_text(stored_motion(metrics))
    body = "\n".join([head, *rows]) if rows else "frame checks: no camera_checks in metrics"
    return body + ("\n\n" + motion if motion else "")

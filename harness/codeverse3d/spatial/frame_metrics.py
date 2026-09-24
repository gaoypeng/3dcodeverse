"""Frame-quality gate for scene renders: ``scene_frames``.

Reads the deterministic ``camera_checks`` that ``render_scene`` leaves in
``metrics.json`` (per camera: mean luminance, dark/blown/modal fractions,
content/ground/sky coverage, nearest geometry, eye height) and turns them into
``GateFinding``s with copyable fix hints, so a too-dark dusk, a blown sky, a
camera buried in a wall or an establishing shot that shows a tiny island of
content become gate findings (→ judge caps + refine instructions) instead of
silently lowering a VLM score.

* ``frame_findings(metrics)``                → GateReport
* ``frame_gate_from_renders(rs_or_path)``    → GateReport (RenderSet / out dir / metrics.json)
* ``frame_summary_text(metrics)``            → per-camera table for tool observations / prompts

Authored cameras get ERRORs; harness orbit views only WARN (the agent does not
place them) except for the content-sparse overview, which is informational.
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
#: a frame this dark in the MEAN is still correctly exposed when it has real contrast --
#: a lantern-lit night is dim on average and bright where the lanterns are.  Measured
#: 2026-08-25 on tsr_scn_temple_night r01: the three authored cameras sat at lum_std
#: 0.171 / 0.199 / 0.249 while the flat overview rig sat at 0.058, so lum_std separates
#: "low-key by design" from "unlit or broken" cleanly.  ``lum_std`` has been in the
#: camera_checks payload from host_metrics.mjs all along; this gate never read it.
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

#: for a frame that is dark AND flat -- nothing is lit, so adding light is right
_DARK_HINT = (
    "raise the light through the rig env.js already builds — `sunRig({ mood: 'day'|'golden'|'night'|'overcast', "
    "intensity, fill, … })`: pick the brief's mood (a set sun, elevation < 0, is the night rig), raise `intensity` (key, "
    "the rig clamps requests below its floor UP) and `fill` (hemisphere, 1.0–2.0) — do not add a second sun on top; "
    "add emissive lights where the brief has them (lantern glow: MeshStandardMaterial emissive + emissiveIntensity 2–6, "
    "a PointLight 0.5–2 per lantern); scene.fog colour must match the sky colour; dusk/night is orange/purple/deep blue, "
    "NOT black — keep mean luminance ≥ 0.15 (check with scene_views: camera_checks.mean_lum)"
)
#: for a frame that is dark but CONTRASTY -- something IS lit, so adding fill is the
#: wrong move and actively destroys the picture.  Measured 2026-08-25: temple_night was
#: told by _DARK_HINT to add a HemisphereLight, did so, and the resulting flat fill
#: washed the granite paving to near-white, flattened the scene to "snow at dawn" and
#: drowned a working KoiWater ShaderMaterial -- the effect was running the whole time.
#: The judge then scored the washed-out version 0.7287 and passed it.
_LOWKEY_HINT = (
    "this frame is DIM BUT LIT — it has real contrast, so do NOT add AmbientLight or HemisphereLight and do NOT "
    "raise a DirectionalLight to fix it: flat fill is what turns a night scene into a grey wash, washes dark "
    "materials out to near-white and drowns emissive/shader effects. Instead lift the picture where the light "
    "actually comes from: raise emissiveIntensity on the lamps/lanterns themselves, add or brighten a PointLight "
    "at each practical light, give dark materials a low but non-zero base colour so they read as material and not "
    "as void, and let the shadowed areas stay dark. Only if the frame is ALSO flat (no contrast) does it need key/fill."
)
_BLOWN_HINT = (
    "lower exposure: DirectionalLight ≤ 3, HemisphereLight ≤ 1.0, no MeshBasicMaterial white planes; "
    "sky colour below 0.9 white; emissiveIntensity ≤ 4 on large surfaces"
)
_FLAT_HINT = (
    "one colour dominates the frame: aim the camera at content (lookAt the zone centre), add contrast with a shadow-casting "
    "DirectionalLight, vary materials, and make sure the sky gradient / fog is not a single flat tint"
)
_NEAR_HINT = "move the camera ≥ 0.5 m away from every surface (raise it above ground/terrain, pull it out of walls and trees) and keep lookAt on the content"
_STATIC_HINT = (
    "nothing moved between the animation times: give the planned motion a visible amplitude — foliage/banner sway "
    "±0.10–0.20 rad, water surface ≥ 3 cm vertical or a scrolling normal/uv, particles ≥ 0.6 m of travel per 1.5 s, "
    "rotating parts ≥ 20°/s — and drive it from the ONE hook the harness calls: the object returned by createScene "
    "(update(t, dt)) → env.update → each zone's group.userData.update(t, dt).  Everything must be a pure function of t"
)
_SMALL_HINT = (
    "the shot shows mostly sky/ground: move the camera closer to the content (distance ≈ 1.2 × the content's widest span), "
    "lower it toward eye level, point lookAt at the content centre, or fill the bounds with more zones/assets"
)


def _view_kind(chk: dict[str, Any]) -> str:
    return str(chk.get("kind") or "authored")


def _f(sev: Severity, target: str, msg: str, hint: str, **data: Any) -> GateFinding:
    return GateFinding(gate=FRAME_GATE, severity=sev, target=target, message=msg, fix_hint=hint, data=data)


def _num(chk: dict[str, Any], key: str) -> float | None:
    v = chk.get(key)
    return float(v) if isinstance(v, (int, float)) else None


def _exposure_findings(chk: dict[str, Any], *, authored: bool) -> list[GateFinding]:
    name = str(chk.get("name", "?"))
    sev = Severity.ERROR if authored else Severity.WARN
    out: list[GateFinding] = []
    # the orbit rig is the HARNESS's camera, not the scene's picture: say so in every
    # message, or a dark ground-level rig shot of a rooftop reads to the judge as the
    # scene being unlit (measured: it cost the rooftop battery a 0.60 cap three rounds running)
    rig = "" if authored else "harness rig view (not an authored camera): "
    mean, dark, blown, modal = (_num(chk, k) for k in ("mean_lum", "dark_frac", "blown_frac", "modal_frac"))
    std = _num(chk, "lum_std")
    is_dark = (mean is not None and mean < DARK_MEAN_LUM) or (dark is not None and dark > DARK_FRAC)
    # dim BUT lit: real contrast and not mostly black.  A night scene must not be failed
    # for being a night scene, and must never be told to flatten itself with fill light.
    low_key = is_dark and std is not None and std >= LOWKEY_MIN_STD and (dark is None or dark <= DARK_FRAC)
    if is_dark:
        msg = (f"frame too dark: mean luminance {mean:.2f}, {dark or 0:.0%} of pixels near black" if mean is not None
               else f"frame too dark: {dark:.0%} of pixels near black")
        if low_key:
            msg = (f"frame is dim but lit: mean luminance {mean:.2f} with contrast {std:.2f} "
                   f"({dark or 0:.0%} of pixels near black) — low-key by design, not unlit")
        out.append(_f(Severity.WARN if low_key else sev, name, rig + msg,
                      _LOWKEY_HINT if low_key else _DARK_HINT,
                      kind="dark_frame", view=name, mean_lum=mean, dark_frac=dark))
    if blown is not None and blown > BLOWN_FRAC:
        out.append(_f(sev, name, rig + f"frame blown out: {blown:.0%} of pixels pure white", _BLOWN_HINT,
                      kind="blown_frame", view=name, blown_frac=blown))
    if modal is not None and modal > FLAT_MODAL_FRAC and not is_dark:
        flat_sev = sev if modal > FLAT_MODAL_ERROR else Severity.WARN
        out.append(_f(flat_sev, name, rig + f"flat frame: one luminance band holds {modal:.0%} of the pixels", _FLAT_HINT,
                      kind="flat_frame", view=name, modal_frac=modal, mean_lum=mean))
    return out


def _geometry_findings(chk: dict[str, Any], *, authored: bool, ground_y: float | None) -> list[GateFinding]:
    name = str(chk.get("name", "?"))
    sev = Severity.ERROR if authored else Severity.WARN
    out: list[GateFinding] = []
    near = _num(chk, "nearest_hit_m")
    inside = list(chk.get("inside_mesh_bbox") or [])
    rig = "" if authored else "harness rig view (not an authored camera): "
    if chk.get("camera_in_geometry") or (near is not None and near < NEAR_HIT_M):
        where = f"inside {inside[:3]}" if inside else f"nearest surface {near:.2f} m ({chk.get('nearest_hit_name', '')})"
        out.append(_f(sev, name, rig + f"camera inside / touching geometry: {where}", _NEAR_HINT,
                      kind="camera_in_geometry", view=name, nearest_hit_m=near, inside=inside[:5]))
    else:
        # The shot is a surface: most of the 3 x 3 sight rays end within arm's reach.  cmp6's
        # crypt (2026-09-09): a squat stone pillar 0.8 m in front of AthanorDetail filled the
        # frame, the hero behind it was never seen, and the judge called the HERO "a massive
        # untextured grey box" for two rounds — nothing measured said the lens was blocked.
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
        # …or the line of sight to the plan's lookAt is cut well before it (a pillar between the
        # lens and its subject; a close-up meets its own subject near the full distance).
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
        # A ground surface straight above the lens: a camera under the terrain looks up at back
        # faces, so its frame renders "fine" (structures floating over a void) — cmp6's
        # lighthouse shot its slipway from under the headland for three rounds.  The camera
        # repair lifts it when it runs; this is the verdict when it did not.
        under = str(chk.get("ground_above_name") or "a ground surface")
        buried = _frame_looks_buried(chk)
        out.append(_f(Severity.ERROR if buried else Severity.WARN, name,
                      f"camera is {above_g:.1f} m under {under} — the frame is its underside",
                      "set position[1] = heightAt(x, z) + 1.6 (eye level) — never below the terrain",
                      kind="camera_underground" if buried else "camera_under_ground_mesh",
                      view=name, eye_height_m=eye, ground_above_m=above_g))
    elif authored and below is not None:
        # The ray straight down from the eye (``nearGeometry``): the surface this lens
        # actually stands over.  Scene-wide ``ground_y`` is the top of the HIGHEST ground
        # mesh, and on relief terrain it called a camera at eye level on a low patch an
        # "ant's-eye view" (loop 21 ski station: eye 3.20 m, snow mound 3.14 m) — a false
        # WARN the judge then repeated as a major issue.
        under = str(chk.get("ground_below_name") or "the surface beneath it")
        if below < EYE_MIN_ABOVE_GROUND_M:
            out.append(_f(Severity.WARN, name, f"camera eye only {below:.2f} m above {under} — ant's-eye view",
                          "human shots: position[1] = heightAt(x, z) + 1.6; establishing: 6–20 m above ground looking down 15–30°",
                          kind="camera_low", view=name, eye_height_m=eye, ground_below_m=below))
        elif below > EYE_MAX_ABOVE_GROUND_M:
            out.append(_f(Severity.WARN, name, f"camera {below:.0f} m above {under} — satellite view, not a shot",
                          "bring the camera down: establishing 6–20 m above ground at ~1.2 × the content span",
                          kind="camera_high", view=name, eye_height_m=eye, ground_below_m=below))
    elif authored and eye is not None and ground_y is not None:
        # Nothing beneath the lens (under the terrain, off the ground's edge) or an older
        # census: compare with the scene's highest ground surface.
        above = eye - ground_y
        if above < -0.2:
            # ``ground_y`` is the TOP of every ground-classified mesh in the scene (census),
            # so a hill, a terrace or a raised backdrop ring puts it metres above a camera
            # that is standing in the open.  Only call the camera buried when the frame
            # agrees: a buried camera renders dark, empty or from inside geometry.
            buried = _frame_looks_buried(chk)
            out.append(_f(Severity.ERROR if buried else Severity.WARN, name,
                          f"camera is {-above:.1f} m below the scene's highest ground surface ({ground_y:.2f} m)"
                          + ("" if buried else " — the frame itself renders fine, so this is probably terrain"
                             " (a hill / raised bed / backdrop) reaching above the camera, not a buried camera"),
                          "set position[1] = heightAt(x, z) + 1.6 (eye level) — never below the terrain",
                          kind="camera_underground" if buried else "camera_below_high_ground",
                          view=name, eye_height_m=eye, ground_y=ground_y))
        elif above < EYE_MIN_ABOVE_GROUND_M:
            out.append(_f(Severity.WARN, name, f"camera eye only {above:.2f} m above ground — ant's-eye view",
                          "human shots: position[1] = heightAt(x, z) + 1.6; establishing: 6–20 m above ground looking down 15–30°",
                          kind="camera_low", view=name, eye_height_m=eye, ground_y=ground_y))
        elif above > EYE_MAX_ABOVE_GROUND_M:
            out.append(_f(Severity.WARN, name, f"camera {above:.0f} m above ground — satellite view, not a shot",
                          "bring the camera down: establishing 6–20 m above ground at ~1.2 × the content span",
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


def _coverage_finding(chk: dict[str, Any], *, role: str) -> GateFinding | None:
    """role: 'establishing' | 'authored' | 'overview' | 'eye'."""
    content = _num(chk, "content_frac")
    if content is None or role == "eye":
        return None
    name = str(chk.get("name", "?"))
    sky, ground = _num(chk, "sky_frac"), _num(chk, "ground_frac")
    detail = f"content {content:.0%} of the frame (sky {sky or 0:.0%}, ground {ground or 0:.0%})"
    if role == "establishing" and content < CONTENT_MIN_ESTABLISHING:
        return _f(Severity.ERROR, name, f"establishing shot shows too little content: {detail}", _SMALL_HINT,
                  kind="content_small", view=name, content_frac=content, sky_frac=sky, ground_frac=ground, role=role)
    if role == "authored" and content < CONTENT_MIN_AUTHORED:
        return _f(Severity.WARN, name, f"shot shows little content: {detail}", _SMALL_HINT,
                  kind="content_small", view=name, content_frac=content, sky_frac=sky, ground_frac=ground, role=role)
    if role == "overview" and content < CONTENT_MIN_OVERVIEW:
        return _f(Severity.WARN, name, f"overview rig sees sparse content: {detail}",
                  "the plan bounds are mostly empty ground: add the planned zones/assets, or densify (instanced scatter, props) "
                  "so the content fills the bounds", kind="content_small", view=name, content_frac=content, role=role)
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


def _motion_findings(rows: list[MotionRow]) -> list[GateFinding]:
    """One finding for the whole scene: frozen (ERROR) or the measured per-camera table (INFO)."""
    moves = scene_moves(rows)
    if moves is None:
        return []
    if moves is False:
        worst = max((r.changed_frac for r in rows if r.authored), default=0.0)
        return [_f(Severity.ERROR, "overall",
                   f"nothing moves: the largest change on an authored camera is {worst:.2%} of pixels between "
                   f"t={rows[0].t0:g}s and t={rows[0].t1:g}s (measured, not perceived)", _STATIC_HINT,
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


def _glb_findings(census: dict[str, Any]) -> list[GateFinding]:
    """A GLB that was loaded and contributed no geometry to the rendered scene.

    Measured 2026-08-25 on tsr_scn_boat_workshop_v2: ``src/scene.js`` loads
    ``/assets/clinker_skiff.glb`` — a real Blender hero, authored, built and copied into
    ``public/assets`` — while ``src/zones/central_bay.js`` calls a procedural
    ``buildClinkerSkiff(THREE)`` that a refine round wrote over the asset slot.  The hull in
    every shipped frame is JavaScript.  Nothing flagged it, and the check the wave was
    using to verify the multi-language claim (``plan.json`` -> ``assets[].kind``) still
    reported ``blender_glb``, because the plan records what was PLANNED.

    WARN, not ERROR: the picture is fine, and an author who deep-copies geometry
    (``geometry.clone()``) rather than cloning the object would read as unused here.
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
                      _UNUSED_GLB_HINT, kind="unused_glb_asset", url=url,
                      meshes=r.get("meshes"), meshes_in_scene=r.get("meshes_in_scene")))
    return out


HERO_MIN_FRAC = 0.005            # of the frame, best authored camera: below it no camera sees the hero
HERO_DETAIL_MIN_FRAC = 0.02      # of the frame, in the camera NAMED for the hero


def _hero_findings(checks: list[dict[str, Any]], census: dict[str, Any]) -> list[GateFinding]:
    """A Blender hero that is IN the scene but in no authored frame — or nearly absent from
    the camera named for it.  ``camera_checks[].glb_frac`` (``glbCoverage``: a mask render
    per GLB per camera, occlusion included).  cmp6's crypt (2026-09-09): the athanor stood
    behind a squat pillar in every authored shot, the judge called the HERO "a massive
    untextured grey box", and two refine rounds rebuilt the wrong thing — "in the scene"
    (``glb_assets``) was known, "in the frame" was not."""
    rows = census.get("glb_assets")
    if not isinstance(rows, list):
        return []
    authored = [c for c in checks if _view_kind(c) == "authored"]
    # which camera is NAMED for which hero is the host's rule (scene_host.mjs namesHero — the one
    # camera repair re-aims by); a census from before 2026-09-23 carries no hero_for
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
            continue     # older census, or no authored camera
        base = url.rsplit("/", 1)[-1]
        best = max(seen.values())
        listing = ", ".join(f"{n} {v:.1%}" for n, v in list(seen.items())[:6])
        size = _num(r, "size_m")
        placed = f" ({size:.1f} m across as placed)" if size else ""
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
                              "a detail shot fills 10-40% of the frame with its subject: move the camera closer along "
                              "the sightline or clear what stands between",
                              kind="hero_small_in_its_camera", url=url, frac=v, best_frac=best))
    return out


def frame_findings(metrics: dict[str, Any]) -> GateReport:
    """``scene_frames`` gate from a ``metrics.json`` payload (``camera_checks`` + ``census`` + ``motion``)."""
    checks: list[dict[str, Any]] = [c for c in metrics.get("camera_checks") or [] if isinstance(c, dict)]
    ground_y = (metrics.get("census") or {}).get("ground_y")
    ground_y = float(ground_y) if isinstance(ground_y, (int, float)) else None
    findings: list[GateFinding] = []
    first_authored = next((c for c in checks if _view_kind(c) == "authored"), None)
    for chk in checks:
        authored = _view_kind(chk) == "authored"
        findings += _exposure_findings(chk, authored=authored)
        findings += _geometry_findings(chk, authored=authored, ground_y=ground_y)
        if authored:
            role = "establishing" if chk is first_authored else "authored"
        else:
            role = "overview" if str(chk.get("name", "")).startswith("overview") else "eye"
        cov = _coverage_finding(chk, role=role)
        if cov is not None:
            findings.append(cov)
    findings += _motion_findings(stored_motion(metrics))
    findings += _glb_findings(metrics.get("census") or {})
    findings += _hero_findings(checks, metrics.get("census") or {})
    if checks and not [f for f in findings if f.severity != Severity.INFO]:
        n = len(checks)
        findings.append(_f(Severity.INFO, "overall", f"{n} camera frame(s) checked: exposure, geometry and coverage within limits", "",
                           kind="frames_ok", n_views=n))
    return GateReport.of(FRAME_GATE, findings)


def frame_gate_from_renders(source: RenderSet | Path | str) -> GateReport:
    """``scene_frames`` gate from a RenderSet (locates its metrics.json), a render
    directory or a metrics.json path.  Missing metrics → an empty passing report."""
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

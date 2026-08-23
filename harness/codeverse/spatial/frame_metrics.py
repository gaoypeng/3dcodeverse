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

import json
from pathlib import Path
from typing import Any

from codeverse.contracts.artifacts import GateFinding, GateReport, RenderSet, Severity

FRAME_GATE = "scene_frames"

# thresholds (fractions of the frame / of full white)
DARK_MEAN_LUM = 0.12
DARK_FRAC = 0.35
BLOWN_FRAC = 0.20
FLAT_MODAL_FRAC = 0.85
FLAT_MODAL_ERROR = 0.92
NEAR_HIT_M = 0.5
CONTENT_MIN_ESTABLISHING = 0.20
CONTENT_MIN_AUTHORED = 0.10
CONTENT_MIN_OVERVIEW = 0.05
EYE_MIN_ABOVE_GROUND_M = 0.3
EYE_MAX_ABOVE_GROUND_M = 80.0

_DARK_HINT = (
    "raise the key + fill: DirectionalLight intensity 2–4, HemisphereLight 0.5–1.0 (sky colour / ground colour), "
    "add emissive lights where the brief has them (lantern glow: MeshStandardMaterial emissive + emissiveIntensity 2–6, "
    "a PointLight 0.5–2 per lantern); scene.fog colour must match the sky colour; dusk/night is orange/purple/deep blue, "
    "NOT black — keep mean luminance ≥ 0.15 (check with scene_views: camera_checks.mean_lum)"
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
    mean, dark, blown, modal = (_num(chk, k) for k in ("mean_lum", "dark_frac", "blown_frac", "modal_frac"))
    is_dark = (mean is not None and mean < DARK_MEAN_LUM) or (dark is not None and dark > DARK_FRAC)
    if is_dark:
        out.append(_f(sev, name, f"frame too dark: mean luminance {mean:.2f}, {dark or 0:.0%} of pixels near black" if mean is not None
                      else f"frame too dark: {dark:.0%} of pixels near black", _DARK_HINT,
                      kind="dark_frame", view=name, mean_lum=mean, dark_frac=dark))
    if blown is not None and blown > BLOWN_FRAC:
        out.append(_f(sev, name, f"frame blown out: {blown:.0%} of pixels pure white", _BLOWN_HINT,
                      kind="blown_frame", view=name, blown_frac=blown))
    if modal is not None and modal > FLAT_MODAL_FRAC and not is_dark:
        flat_sev = sev if modal > FLAT_MODAL_ERROR else Severity.WARN
        out.append(_f(flat_sev, name, f"flat frame: one luminance band holds {modal:.0%} of the pixels", _FLAT_HINT,
                      kind="flat_frame", view=name, modal_frac=modal, mean_lum=mean))
    return out


def _geometry_findings(chk: dict[str, Any], *, authored: bool, ground_y: float | None) -> list[GateFinding]:
    name = str(chk.get("name", "?"))
    sev = Severity.ERROR if authored else Severity.WARN
    out: list[GateFinding] = []
    near = _num(chk, "nearest_hit_m")
    inside = list(chk.get("inside_mesh_bbox") or [])
    if chk.get("camera_in_geometry") or (near is not None and near < NEAR_HIT_M):
        where = f"inside {inside[:3]}" if inside else f"nearest surface {near:.2f} m ({chk.get('nearest_hit_name', '')})"
        out.append(_f(sev, name, f"camera inside / touching geometry: {where}", _NEAR_HINT,
                      kind="camera_in_geometry", view=name, nearest_hit_m=near, inside=inside[:5]))
    eye = _num(chk, "eye_height_m")
    if authored and eye is not None and ground_y is not None:
        above = eye - ground_y
        if above < -0.2:
            out.append(_f(Severity.ERROR, name, f"camera is {-above:.1f} m BELOW the ground level ({ground_y:.2f} m)",
                          "set position[1] = heightAt(x, z) + 1.6 (eye level) — never below the terrain",
                          kind="camera_underground", view=name, eye_height_m=eye, ground_y=ground_y))
        elif above < EYE_MIN_ABOVE_GROUND_M:
            out.append(_f(Severity.WARN, name, f"camera eye only {above:.2f} m above ground — ant's-eye view",
                          "human shots: position[1] = heightAt(x, z) + 1.6; establishing: 6–20 m above ground looking down 15–30°",
                          kind="camera_low", view=name, eye_height_m=eye, ground_y=ground_y))
        elif above > EYE_MAX_ABOVE_GROUND_M:
            out.append(_f(Severity.WARN, name, f"camera {above:.0f} m above ground — satellite view, not a shot",
                          "bring the camera down: establishing 6–20 m above ground at ~1.2 × the content span",
                          kind="camera_high", view=name, eye_height_m=eye, ground_y=ground_y))
    return out


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


def frame_findings(metrics: dict[str, Any]) -> GateReport:
    """``scene_frames`` gate from a ``metrics.json`` payload (``camera_checks`` + ``census``)."""
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
    if checks and not findings:
        n = len(checks)
        findings.append(_f(Severity.INFO, "overall", f"{n} camera frame(s) checked: exposure, geometry and coverage within limits", "",
                           kind="frames_ok", n_views=n))
    passed = not any(f.severity == Severity.ERROR for f in findings)
    return GateReport(gate=FRAME_GATE, passed=passed, findings=findings)


def frame_gate_from_renders(source: RenderSet | Path | str) -> GateReport:
    """``scene_frames`` gate from a RenderSet (locates its metrics.json), a render
    directory or a metrics.json path.  Missing metrics → an empty passing report."""
    from codeverse.spatial.render_scene import metrics_path_for

    if isinstance(source, RenderSet):
        path = metrics_path_for(source)
    else:
        p = Path(source)
        path = p if p.is_file() else (p / "metrics.json" if (p / "metrics.json").is_file() else None)
    if path is None:
        return GateReport(gate=FRAME_GATE, passed=True, findings=[])
    try:
        metrics = json.loads(path.read_text())
    except (OSError, ValueError):
        return GateReport(gate=FRAME_GATE, passed=True, findings=[])
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
    head = f"frame checks ({'ok' if report.passed else 'FAILED'}; mean_lum ≥ 0.15, dark ≤ 35 %, blown ≤ 20 %, establishing content ≥ 20 %):"
    return "\n".join([head, *rows]) if rows else "frame checks: no camera_checks in metrics"

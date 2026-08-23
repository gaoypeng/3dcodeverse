"""Helpers that keep tool Observations compact and workspace-safe.

* ``fmt_numbers``   – one-line ``k=v`` rendering with sane float precision.
* ``truncate``      – head/tail truncation with a marker (never mid-line when possible).
* ``tail_lines``    – last ``n`` lines of a log.
* ``image_budget``  – cap image lists (contact sheet kept first).
* ``rel_path``      – workspace-relative display path (never leak host paths in text).
* ``gate_observation`` / ``render_observation`` – standard Observation builders.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from codeverse.contracts.artifacts import GateReport, RenderSet, Severity
from codeverse.spatial.registry import Observation

MAX_IMAGES = 6
MAX_TEXT = 2400


def fmt_float(v: float, digits: int = 4) -> str:
    if v == 0:
        return "0"
    if abs(v) >= 1000 or abs(v) < 1e-3:
        return f"{v:.3g}"
    return f"{v:.{digits}f}".rstrip("0").rstrip(".")


def fmt_numbers(numbers: dict[str, Any], max_items: int = 24) -> str:
    """``a=1.5 b=[0.1, 0.2] c=ok`` — nested dicts/lists are compacted, long ones elided."""
    bits = []
    for i, (k, v) in enumerate(numbers.items()):
        if i >= max_items:
            bits.append(f"…+{len(numbers) - max_items}")
            break
        bits.append(f"{k}={_fmt_value(v)}")
    return " ".join(bits)


def _fmt_value(v: Any, depth: int = 0) -> str:
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return fmt_float(v)
    if isinstance(v, (list, tuple)):
        if len(v) > 8:
            return "[" + ", ".join(_fmt_value(x, depth + 1) for x in v[:8]) + f", …+{len(v) - 8}]"
        return "[" + ", ".join(_fmt_value(x, depth + 1) for x in v) + "]"
    if isinstance(v, dict):
        if depth >= 2:
            return f"{{{len(v)} keys}}"
        return "{" + ", ".join(f"{k}:{_fmt_value(x, depth + 1)}" for k, x in list(v.items())[:12]) + "}"
    s = str(v)
    return s if len(s) <= 80 else s[:77] + "…"


def truncate(text: str, n: int = MAX_TEXT, tail_ratio: float = 0.3) -> str:
    """Keep the head and the tail of ``text`` (tail-biased errors survive)."""
    if len(text) <= n:
        return text
    tail_n = int(n * tail_ratio)
    head_n = n - tail_n - 24
    head, tail = text[:head_n], text[-tail_n:] if tail_n else ""
    return f"{head}\n… [{len(text) - head_n - tail_n} chars omitted] …\n{tail}"


def tail_lines(text: str, n: int = 30) -> str:
    lines = text.rstrip("\n").splitlines()
    return "\n".join(lines[-n:])


def image_budget(images: Sequence[str], max_n: int = MAX_IMAGES) -> list[str]:
    """Keep at most ``max_n`` images; the first (contact sheet) always survives."""
    imgs = [str(p) for p in images if p]
    if len(imgs) <= max_n:
        return imgs
    return imgs[:max_n]


def rel_path(path: Path | str | None, root: Path) -> str:
    """Display path relative to the workspace root (falls back to the file name)."""
    if path is None:
        return ""
    p = Path(path)
    try:
        return str(p.resolve().relative_to(Path(root).resolve()))
    except ValueError:
        return p.name


def sanitize_text(text: str, root: Path) -> str:
    """Replace absolute workspace paths in free text with workspace-relative ones."""
    r = str(Path(root).resolve())
    return text.replace(r + "/", "").replace(r, ".")


# --------------------------------------------------------------------------- builders
_SEV_ORDER = {Severity.ERROR: 0, Severity.WARN: 1, Severity.INFO: 2}
_SEV_TAG = {Severity.ERROR: "ERROR", Severity.WARN: "WARN", Severity.INFO: "info"}


def gate_observation(report: GateReport, *, title: str = "", max_findings: int = 20, images: Iterable[str] = ()) -> Observation:
    """Errors first, each with its fix hint; counts in ``numbers``."""
    findings = sorted(report.findings, key=lambda f: _SEV_ORDER.get(f.severity, 3))
    n_err = sum(1 for f in findings if f.severity == Severity.ERROR)
    n_warn = sum(1 for f in findings if f.severity == Severity.WARN)
    head = title or f"{report.gate}: {'PASS' if report.passed else 'FAIL'}"
    lines = [f"{head} — {n_err} error(s), {n_warn} warning(s)"]
    for f in findings[:max_findings]:
        tgt = f" [{f.target}]" if f.target else ""
        lines.append(f"- {_SEV_TAG[f.severity]}{tgt}: {f.message}")
        if f.fix_hint and f.severity != Severity.INFO:
            lines.append(f"    fix: {f.fix_hint}")
    if len(findings) > max_findings:
        lines.append(f"- … {len(findings) - max_findings} more finding(s)")
    numbers: dict[str, Any] = {"passed": report.passed, "errors": n_err, "warnings": n_warn}
    for f in findings:
        if f.severity == Severity.ERROR and f.data:
            numbers.setdefault("error_data", {})[f.target or f.message[:40]] = f.data
    return Observation(ok=report.passed, text=truncate("\n".join(lines)), numbers=numbers, images=list(images))


def render_observation(rs: RenderSet, root: Path, *, note: str = "", max_individual: int = 4) -> Observation:
    """Contact sheet first, individual views when few; text lists views + cameras."""
    images: list[str] = []
    if rs.contact_sheet:
        images.append(rs.contact_sheet)
    if len(rs.views) <= max_individual:
        images.extend(v.path for v in rs.views)
    lines = [note] if note else []
    lines.append(f"{len(rs.views)} view(s)" + (f" via {rs.renderer}" if rs.renderer else "") + (", contact sheet first" if rs.contact_sheet else ""))
    for v in rs.views:
        cam = ""
        if v.camera_position is not None:
            cam = " cam=(" + ", ".join(fmt_float(c, 2) for c in v.camera_position) + ")"
        if v.fov:
            cam += f" fov={v.fov:g}"
        t = f" t={v.time_s:g}s" if v.time_s is not None else ""
        lines.append(f"- {v.name} [{v.mode}{t}]{cam} → {rel_path(v.path, root)}")
    if rs.console_errors:
        lines.append(f"console errors ({len(rs.console_errors)}):")
        lines.extend(f"  ! {e[:200]}" for e in rs.console_errors[:8])
    if rs.fps is not None:
        lines.append(f"fps ≈ {rs.fps:.0f}")
    numbers: dict[str, Any] = {"n_views": len(rs.views), "views": [v.name for v in rs.views]}
    if rs.fps is not None:
        numbers["fps"] = rs.fps
    if rs.console_errors:
        numbers["console_errors"] = len(rs.console_errors)
    return Observation(ok=not rs.console_errors, text=truncate("\n".join(lines)), numbers=numbers,
                       images=image_budget(images))

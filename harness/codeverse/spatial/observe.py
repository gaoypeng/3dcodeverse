"""Helpers that keep tool Observations compact and workspace-safe.

* ``fmt_numbers``   – one-line ``k=v`` rendering with sane float precision.
* ``truncate``      – head/tail truncation with a marker (never mid-line when possible).
* ``tail_lines``    – last ``n`` lines of a log.
* ``image_budget``  – cap image lists (contact sheet kept first).
* ``rel_path``      – workspace-relative display path (never leak host paths in text).
* ``lint_lines`` / ``build_failure_lines`` – the one lint / BUILD FAILED report
  format shared by the ``build`` and ``gl_probe`` / ``gl_frames`` tools.
* ``text_observation`` / ``gate_observation`` / ``render_observation`` – the
  standard Observation builders (truncation + image budget applied once).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from codeverse.contracts.artifacts import BuildResult, GateReport, RenderSet, Severity
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
def text_observation(
    lines: Sequence[str] | str,
    *,
    ok: bool = True,
    numbers: dict[str, Any] | None = None,
    images: Sequence[str | Path] = (),
    limit: int = MAX_TEXT,
) -> Observation:
    """Observation from text lines: joined, truncated to ``limit``, images capped.

    The plain-text counterpart of :func:`gate_observation` / :func:`render_observation`
    — every tool that assembles its own report (build, gl_probe, gl_frames,
    texture_pass, …) goes through here so truncation and the image budget are
    applied exactly once, in one place.
    """
    body = lines if isinstance(lines, str) else "\n".join(str(x) for x in lines)
    return Observation(ok=ok, text=truncate(body, limit), numbers=dict(numbers or {}),
                       images=image_budget([str(i) for i in images]))



_SEV_ORDER = {Severity.ERROR: 0, Severity.WARN: 1, Severity.INFO: 2}
_SEV_TAG = {Severity.ERROR: "ERROR", Severity.WARN: "WARN", Severity.INFO: "info"}


# --------------------------------------------------------------------------- build reports
def lint_lines(report: GateReport, root: Path, *, errors_only: bool) -> list[str]:
    """``- ERROR (src/x.py): msg`` (+ ``    fix: …``) for the errors or the warnings
    of a lint report — INFO findings are never shown to the agent."""
    want = Severity.ERROR if errors_only else Severity.WARN
    out = []
    for f in report.findings:
        if f.severity != want:
            continue
        loc = f" ({rel_path(f.target, root)})" if f.target else ""
        out.append(f"- {_SEV_TAG[f.severity]}{loc}: {sanitize_text(f.message, root)}")
        if f.fix_hint:
            out.append(f"    fix: {sanitize_text(f.fix_hint, root)}")
    return out


def error_file_display(error_file: str, root: Path) -> str:
    """``BuildResult.error_file`` for the agent: runtimes report either an absolute
    path (→ workspace-relative) or an already-relative one (kept — ``rel_path``
    would resolve it against the cwd and fall back to the bare file name)."""
    if not error_file or not Path(error_file).is_absolute():
        return error_file
    return rel_path(error_file, root)


def build_failure_lines(br: BuildResult, root: Path, lint_warns: Sequence[str], *, tail_n: int) -> list[str]:
    """``BUILD FAILED: <type>: <message> at <file>:<line>`` + stderr tail (when it
    adds to the message) + up to 10 lint hints — the failure report of every
    build-running tool."""
    where = f" at {error_file_display(br.error_file, root)}:{br.error_line}" if br.error_file else ""
    # the location stays on the headline even when the message is multi-line (GLSL)
    head, _, rest = sanitize_text(br.error_message, root).partition("\n")
    lines = [f"BUILD FAILED: {br.error_type or 'Error'}: {head}{where}"] + ([rest] if rest else [])
    tail = tail_lines(sanitize_text(br.stderr_tail, root), tail_n)
    if tail and tail not in br.error_message:
        lines.append("stderr (tail):\n" + tail)
    if lint_warns:
        lines.append("lint hints:\n" + "\n".join(lint_warns[:10]))
    return lines


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

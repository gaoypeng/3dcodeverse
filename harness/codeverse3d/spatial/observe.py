"""Helpers that keep tool Observations compact and workspace-safe: number/path formatting,
truncation, the shared lint / BUILD FAILED report, and the standard Observation builders."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from codeverse3d import proc
from codeverse3d.contracts.artifacts import BuildResult, GateReport, RenderSet, Severity
from codeverse3d.spatial.registry import Observation

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
    failed: bool = False,
    numbers: dict[str, Any] | None = None,
    images: Sequence[str | Path] = (),
    limit: int = MAX_TEXT,
) -> Observation:
    """Observation from text lines, truncated to ``limit``.  ``ok=False`` is a verdict; ``failed`` means the tool could not run."""
    body = lines if isinstance(lines, str) else "\n".join(str(x) for x in lines)
    return Observation(ok=ok, failed=failed, text=truncate(body, limit), numbers=dict(numbers or {}),
                       images=[str(i) for i in images if i])



_SEV_ORDER = {Severity.ERROR: 0, Severity.WARN: 1, Severity.INFO: 2}
_SEV_TAG = {Severity.ERROR: "ERROR", Severity.WARN: "WARN", Severity.INFO: "info"}


# --------------------------------------------------------------------------- build reports
def lint_lines(report: GateReport, root: Path, *, errors_only: bool) -> list[str]:
    """``- ERROR (src/x.py): msg`` (+ fix hint) lines for the errors or the warnings; INFO is never shown."""
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
    """``error_file`` for the agent: absolute paths become workspace-relative, relative ones are kept."""
    if not error_file or not Path(error_file).is_absolute():
        return error_file
    return rel_path(error_file, root)


def build_failure_lines(br: BuildResult, root: Path, lint_warns: Sequence[str], *, tail_n: int) -> list[str]:
    """``BUILD FAILED: <type>: <message> at <file>:<line>`` + stderr tail + up to 10 lint hints."""
    where = f" at {error_file_display(br.error_file, root)}:{br.error_line}" if br.error_file else ""
    # the location stays on the headline when the message is multi-line
    head, _, rest = sanitize_text(br.error_message, root).partition("\n")
    lines = [f"BUILD FAILED: {br.error_type or 'Error'}: {head}{where}"] + ([rest] if rest else [])
    tail = proc.tail(sanitize_text(br.stderr_tail, root).rstrip("\n"), max_lines=tail_n)
    if tail and tail not in br.error_message:
        lines.append("stderr (tail):\n" + tail)
    if lint_warns:
        lines.append("lint hints:\n" + "\n".join(lint_warns[:10]))
    return lines


def gate_observation(report: GateReport, *, title: str = "", max_findings: int = 20,
                     images: Iterable[str] = (), failed: bool = False) -> Observation:
    """Errors first, each with its fix hint; counts in ``numbers``.

    The PASS/FAIL verdict leads the text.  ``failed`` marks a tool that could not run and forces ``ok=False``.
    """
    findings = sorted(report.findings, key=lambda f: _SEV_ORDER.get(f.severity, 3))
    n_err = sum(1 for f in findings if f.severity == Severity.ERROR)
    n_warn = sum(1 for f in findings if f.severity == Severity.WARN)
    head = f"{title or report.gate}: {'PASS' if report.passed else 'FAIL'}"
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
    return Observation(ok=report.passed and not failed, failed=failed,
                       text=truncate("\n".join(lines)), numbers=numbers, images=list(images))


def render_observation(rs: RenderSet, root: Path, *, note: str = "", max_individual: int = 4) -> Observation:
    """Contact sheet first, individual views when few; text lists views + cameras.

    Console errors are a verdict (``ok=False``); no views and nothing logged is a failure (``failed=True``).
    """
    images: list[str] = []
    if rs.contact_sheet:
        images.append(rs.contact_sheet)
    if len(rs.views) <= max_individual:
        images.extend(v.path for v in rs.views)
    lines: list[str] = []
    if rs.console_errors:
        lines.append(f"RENDER: FAIL — {len(rs.console_errors)} console error(s)"
                     + (" and no view rendered; the errors below say why" if not rs.views
                        else "; the views below rendered anyway"))
    elif not rs.views:
        lines.append("RENDER PRODUCED NO VIEWS" + (f" ({rs.renderer})" if rs.renderer else "")
                     + " — nothing to look at and nothing logged: the renderer failed, "
                       "there is no finding here to fix in src/")
    if note:
        lines.append(note)
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
    return Observation(ok=bool(rs.views) and not rs.console_errors,
                       failed=not rs.views and not rs.console_errors,
                       text=truncate("\n".join(lines)), numbers=numbers, images=[str(i) for i in images if i])

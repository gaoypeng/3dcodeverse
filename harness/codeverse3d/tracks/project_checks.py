"""Project checks (``Spec.checks``): deterministic checks a project registers by import path, run on every round.

The harness gates what it can know about any object — the build, the plan contract, connectivity, budgets.  A
project often knows more: the true geometry of the thing (a surveyed building, a catalogue part), definitions
of its own (a building's eaves line) and thresholds.  A registered check's reports join the round's gates under
``project:<name>``: an ERROR becomes a refine task with its ``fix_hint``, the judge reads it in the gates section,
and a rubric ``CapRule(when="gate", gate="project:*")`` caps the score.

A check is ``fn(*, glb, workspace, spec, measurement, round_index, args) -> GateReport | list[GateReport]``.  It
must be deterministic and must not call a model (Law 3).  A check that raises costs the round nothing but a WARN
saying so: a project's bug must not fail or cap the run.
"""

from __future__ import annotations

import importlib
import logging
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

from codeverse3d.contracts.artifacts import GateFinding, GateReport, Measurement, Severity
from codeverse3d.contracts.spec import PROJECT_GATE_PREFIX as PREFIX
from codeverse3d.contracts.spec import ProjectCheck, Spec

log = logging.getLogger(__name__)



class CheckFn(Protocol):
    def __call__(self, *, glb: Path, workspace: Path, spec: Spec, measurement: Measurement | None,
                 round_index: int, args: dict[str, Any]) -> GateReport | Sequence[GateReport]: ...


def load_check(entry: str) -> CheckFn:
    """The function ``entry`` (``package.module:function``) names."""
    module, _, attr = entry.partition(":")
    fn = getattr(importlib.import_module(module), attr)
    if not callable(fn):
        raise TypeError(f"{entry} is not callable")
    return fn


def _named(report: GateReport, check: ProjectCheck) -> GateReport:
    """``report`` under ``project:<name>`` (a check returning several reports keeps them apart as
    ``project:<name>.<its gate>``), its findings renamed with it, so caps and refine keys see one namespace."""
    own = report.gate or ""
    if own.startswith(PREFIX):
        gate = own
    elif own in ("", check.name):
        gate = PREFIX + check.name
    else:
        gate = f"{PREFIX}{check.name}.{own}"
    findings = [f.model_copy(update={"gate": gate}) for f in report.findings]
    return report.model_copy(update={"gate": gate, "findings": findings})


def run_project_checks(spec: Spec, workspace: Path, glb: Path, measurement: Measurement | None,
                       round_index: int) -> list[GateReport]:
    """Every check of ``spec`` on this round's GLB, in the spec's order."""
    out: list[GateReport] = []
    for check in spec.checks:
        t0 = time.time()
        try:
            got = load_check(check.entry)(glb=glb, workspace=workspace, spec=spec, measurement=measurement,
                                          round_index=round_index, args=dict(check.args))
            reports = [got] if isinstance(got, GateReport) else list(got)
            out += [_named(r, check) for r in reports]
        except Exception as e:  # a project's bug must not fail or cap the run
            log.warning("project check %s failed: %s", check.entry, e)
            gate = PREFIX + check.name
            out.append(GateReport.of(gate, [GateFinding(
                gate=gate, severity=Severity.WARN,
                message=f"project check {check.entry} raised {type(e).__name__}: {str(e)[:300]}")],
                duration_ms=int((time.time() - t0) * 1000)))
    return out

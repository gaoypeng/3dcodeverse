"""Re-export shim: the fan-out helper moved to ``codeverse.fanout`` so packages
outside the orchestrator (judges, texturing) can use it without a cross-package
dependency.  Import from ``codeverse.fanout`` in new code."""

from codeverse.fanout import FanOutReport, fan_out, split_results

__all__ = ["FanOutReport", "fan_out", "split_results"]

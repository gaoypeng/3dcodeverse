"""Measure the *real* Gemini throughput ceiling of this box's key pool.

Runs one fixed workload (N identical-shape calls) at several in-flight levels and
reports completed calls per minute, error rate by class, latency and cost.  The
knee of the throughput curve is what ``Limits.max_parallel_agents`` /
``max_model_calls`` and the bench ``--parallel`` defaults are set from
(``docs/COST.md`` Part III) — never guess a concurrency number, measure it here::

    python bench/concurrency_probe.py --levels 4,8,16,32,64 --calls 48 --out bench/out/concurrency

Each level uses a FRESH :class:`~codeverse3d.models.retry.KeyPool` over the same
keys so the per-level ``ok / 429 / 5xx`` counters are exact deltas, and a
prompt-size knob (``--in-tokens``) so a judge-sized call and a caption-sized call
can be measured apart.
"""

from __future__ import annotations

import argparse
import json
import statistics
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from codeverse3d.config import get_settings
from codeverse3d.contracts.chat import ChatMessage, ChatRequest
from codeverse3d.cost.guard import CHARS_PER_TOKEN
from codeverse3d.models.gemini import GeminiModel
from codeverse3d.models.retry import KeyPool, StormGate
from codeverse3d.proc import fan_out

#: filler that is cheap to build, incompressible enough not to be cached away, and
#: shaped like the code the generator really sends
_FILLER_LINE = "# axis={i:05d} bpy.ops.mesh.primitive_cube_add(size=0.1, location=(0.0, 0.0, {i}))\n"


def filler(tokens: int) -> str:
    """A prompt body of about ``tokens`` estimated tokens."""
    want = int(tokens * CHARS_PER_TOKEN)
    out: list[str] = []
    n = 0
    i = 0
    while n < want:
        line = _FILLER_LINE.format(i=i)
        out.append(line)
        n += len(line)
        i += 1
    return "".join(out)


@dataclass
class LevelResult:
    level: int
    n_calls: int
    wall_s: float
    n_ok: int
    n_failed: int
    calls_per_min: float
    tokens_per_min: float
    mean_inflight: float
    peak_inflight: int
    p50_s: float
    p90_s: float
    max_s: float
    attempts_ok: int
    attempts_429: int
    attempts_5xx: int
    attempts_error: int
    input_tokens: int
    output_tokens: int
    usd: float
    storm_hits: int = 0
    storm_probes: int = 0
    storm_parked_s: float = 0.0
    errors: list[str] = field(default_factory=list)


class _Gauge:
    """Thread-safe in-flight counter with a time-weighted mean."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.cur = 0
        self.peak = 0
        self._area = 0.0
        self._t = time.monotonic()

    def _tick(self) -> None:
        now = time.monotonic()
        self._area += self.cur * (now - self._t)
        self._t = now

    def enter(self) -> None:
        with self._lock:
            self._tick()
            self.cur += 1
            self.peak = max(self.peak, self.cur)

    def exit(self) -> None:
        with self._lock:
            self._tick()
            self.cur -= 1

    def mean(self, wall_s: float) -> float:
        with self._lock:
            self._tick()
            return self._area / wall_s if wall_s > 0 else 0.0


def run_level(
    level: int, *, model: str, keys: list[str], calls: int, in_tokens: int, out_tokens: int,
    rpm_per_key: int, tpm_per_key: int | None, storm_gate: bool = True,
) -> LevelResult:
    pool = KeyPool(keys, rpm_per_key=rpm_per_key, tpm_per_key=tpm_per_key)
    gate = StormGate(f"probe:{model}:c{level}") if storm_gate else None
    m = GeminiModel(model, pool=pool)
    # assign directly: passing storm_gate=None to the constructor means "use the
    # process-wide gate", and this probe has to be able to switch it off entirely
    m.storm_gate = gate
    body = filler(in_tokens)
    gauge = _Gauge()
    lat: list[float] = []
    lat_lock = threading.Lock()
    usage = {"in": 0, "out": 0, "usd": 0.0}

    def one(i: int) -> str:
        req = ChatRequest(
            messages=[ChatMessage.user(
                f"{body}\nTask {i}: reply with the single word OK and nothing else.")],
            temperature=0.0, max_output_tokens=out_tokens, thinking="off",
            label=f"probe:c{level}:{i}")
        gauge.enter()
        t0 = time.monotonic()
        try:
            r = m.generate(req)
        finally:
            dt = time.monotonic() - t0
            gauge.exit()
            with lat_lock:
                lat.append(dt)
        with lat_lock:
            usage["in"] += r.usage.input_tokens
            usage["out"] += r.usage.output_tokens + r.usage.thoughts_tokens
            usage["usd"] += r.usage.cost_usd
        return r.text

    t_all = time.monotonic()
    results = fan_out(range(calls), one, max_workers=level, label=f"probe-c{level}")
    wall = time.monotonic() - t_all
    bad = [r for r in results if isinstance(r, Exception)]
    st = pool.stats()
    lat.sort()

    def pct(q: float) -> float:
        return lat[min(len(lat) - 1, int(q * len(lat)))] if lat else 0.0

    n_ok = calls - len(bad)
    return LevelResult(
        level=level, n_calls=calls, wall_s=round(wall, 2), n_ok=n_ok, n_failed=len(bad),
        calls_per_min=round(60 * n_ok / wall, 1) if wall else 0.0,
        tokens_per_min=round(60 * (usage["in"] + usage["out"]) / wall, 0) if wall else 0.0,
        mean_inflight=round(gauge.mean(wall), 2), peak_inflight=gauge.peak,
        p50_s=round(pct(0.5), 2), p90_s=round(pct(0.9), 2), max_s=round(max(lat), 2) if lat else 0.0,
        attempts_ok=st["ok"], attempts_429=st["429"], attempts_5xx=st["5xx"],
        attempts_error=st["error"], input_tokens=usage["in"], output_tokens=usage["out"],
        usd=round(usage["usd"], 4),
        storm_hits=gate.n_hits if gate else 0, storm_probes=gate.n_probes if gate else 0,
        storm_parked_s=round(gate.parked_s, 1) if gate else 0.0,
        errors=[f"{type(e).__name__}: {e}"[:200] for e in bad[:5]],
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", default="gemini-3.7-flash")
    ap.add_argument("--levels", default="4,8,16,32,64")
    ap.add_argument("--calls", type=int, default=48, help="calls per level (the fixed workload)")
    ap.add_argument("--in-tokens", type=int, default=12000, help="prompt size (a generator call)")
    ap.add_argument("--out-tokens", type=int, default=512)
    ap.add_argument("--rpm-per-key", type=int, default=1000)
    ap.add_argument("--tpm-per-key", type=int, default=0, help="0 = no TPM bucket")
    ap.add_argument("--storm-gate", dest="storm_gate", action="store_true", default=True)
    ap.add_argument("--no-storm-gate", dest="storm_gate", action="store_false")
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    keys = list(get_settings().gemini_api_keys)
    if not keys:
        raise SystemExit("no gemini keys configured")
    levels = [int(x) for x in a.levels.split(",") if x.strip()]
    rows: list[LevelResult] = []
    print(f"{len(keys)} keys · model {a.model} · {a.calls} calls/level · ~{a.in_tokens} in-tokens")
    hdr = (f"{'lvl':>4s} {'wall_s':>7s} {'ok':>4s} {'fail':>5s} {'calls/min':>10s} "
           f"{'ktok/min':>9s} {'mean_if':>8s} {'p50':>6s} {'p90':>7s} {'max':>7s} "
           f"{'429':>5s} {'5xx':>5s} {'err':>5s} {'park_s':>7s} {'$':>7s}")
    print(hdr)
    print("-" * len(hdr))
    for lv in levels:
        r = run_level(lv, model=a.model, keys=keys, calls=a.calls, in_tokens=a.in_tokens,
                      out_tokens=a.out_tokens, rpm_per_key=a.rpm_per_key,
                      tpm_per_key=a.tpm_per_key or None, storm_gate=a.storm_gate)
        rows.append(r)
        print(f"{r.level:4d} {r.wall_s:7.1f} {r.n_ok:4d} {r.n_failed:5d} {r.calls_per_min:10.1f} "
              f"{r.tokens_per_min / 1000:9.0f} {r.mean_inflight:8.2f} {r.p50_s:6.1f} {r.p90_s:7.1f} "
              f"{r.max_s:7.1f} {r.attempts_429:5d} {r.attempts_5xx:5d} {r.attempts_error:5d} "
              f"{r.storm_parked_s:7.0f} {r.usd:7.3f}",
              flush=True)
        if r.errors:
            print(f"     errors: {r.errors[0]}", flush=True)
    if a.out:
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "levels.json").write_text(json.dumps(
            {"model": a.model, "n_keys": len(keys), "calls": a.calls, "in_tokens": a.in_tokens,
             "storm_gate": a.storm_gate,
             "rows": [asdict(r) for r in rows]}, indent=2))
        print(f"wrote {out / 'levels.json'}")
    best = max(rows, key=lambda r: r.calls_per_min)
    print(f"\npeak throughput {best.calls_per_min:.1f} calls/min at level {best.level} "
          f"(median latency {statistics.median([r.p50_s for r in rows]):.1f}s)")


if __name__ == "__main__":
    main()

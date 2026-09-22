"""Is a model actually serving right now?

A batch job is the worst place to discover a provider outage.  On 2026-08-24 a
``gemini-3.7-flash`` capacity storm ran for hours; eight bench cells were launched
into it and each spent 56-87 minutes losing to 503s before recording a failure, and
the judge model went down with it.  The provider was unreachable within seconds of
the first call — the cost was entirely in not asking.

``probe`` asks once per key, in parallel, with a short timeout and no retries: the
point is a fast verdict, not a successful call.  Callers use it as a preflight gate
before spending real money.

The probe prompt is deliberately **the size of real work**, not five tokens.  Measured
2026-08-24: during the flash degradation a trivial prompt answered while the 12-24 k
token planner calls beside it were still 503-ing, so a tiny probe passed the preflight
and the battery then lost two cells to the wall at 15 min each.  A probe that does not
resemble the workload does not measure the workload.
"""

from __future__ import annotations

import concurrent.futures as cf
import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

#: enough of the pool to tell an outage from one unlucky key, without a long probe
DEFAULT_SAMPLE = 6
#: a healthy model answers a workload-sized prompt well inside this
DEFAULT_TIMEOUT_S = 30.0
#: prompt size the probe sends, in tokens.  Sized like a real planner/generator call
#: (docs/COST.md §20 measured the corpus at ~12 k input): a 5-token probe answers when
#: a 12 k one does not, which is exactly the false green light this gate must not give.
#: 4 probes x 8 k tokens is ~$0.003 on flash — irrelevant next to one wasted battery.
PROBE_TOKENS = 8000
#: below this share of answering calls, treat the model as down.
#:
#: NOT 0.5.  A preflight guards a BATTERY, and one cell is dozens of model calls that
#: must all land: at p per call a 20-call cell completes with p**20, so p = 0.5 is not
#: "half healthy", it is zero.  Retries soften it — roughly p >= 0.7 for a coin-flip
#: chance at a cell — so the bar sits above that with margin.  Measured 2026-08-24: the
#: gate passed flash at 2/4 and the battery then lost both its first cells to the retry
#: deadline at ~15 min each.  The asymmetry decides the tie: refusing wrongly costs one
#: 2-minute re-probe, starting wrongly costs hours.  A judgement call, not a measured
#: optimum.
DEFAULT_MIN_OK = 0.75


@dataclass(frozen=True)
class Health:
    model: str
    n_ok: int
    n_tried: int
    reasons: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.n_tried > 0 and self.n_ok / self.n_tried >= DEFAULT_MIN_OK

    def __str__(self) -> str:
        head = f"{self.model}: {self.n_ok}/{self.n_tried} keys answering"
        return (
            head if self.ok else f"{head} — {'; '.join(self.reasons[:2]) or 'no reason reported'}"
        )


def probe(
    model: str = "gemini:gemini-3.7-flash",
    *,
    sample: int = DEFAULT_SAMPLE,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> Health:
    """One workload-sized call (``PROBE_TOKENS``) per sampled key, in parallel, no retries.  Never raises."""
    from codeverse3d.contracts.chat import ChatMessage, ChatRequest

    prompt = _probe_prompt()

    def one(_: int) -> str | None:
        try:
            # built directly, not via get_chat_model: a probe must NOT retry or back off
            m = _bare_model(model, timeout_s)
            m.generate(
                ChatRequest(
                    messages=[ChatMessage.user(prompt)],
                    temperature=0.0,
                    max_output_tokens=256,
                    thinking="off",
                    label="health",
                )
            )
            return None
        except Exception as e:  # noqa: BLE001 — a probe reports, it does not propagate
            return f"{type(e).__name__}: {str(e)[:100]}"

    n = max(1, sample)
    with cf.ThreadPoolExecutor(max_workers=n) as pool:
        out = list(pool.map(one, range(n)))
    reasons = tuple(dict.fromkeys(r for r in out if r))  # de-duplicated, order kept
    return Health(model=model, n_ok=sum(r is None for r in out), n_tried=n, reasons=reasons)


def _probe_prompt(tokens: int = PROBE_TOKENS) -> str:
    """A workload-sized prompt whose answer is still one word.

    Filler carries the token weight; the instruction at the end keeps the RESPONSE
    tiny, so the probe costs input tokens (cheap, and the thing under test) rather
    than output tokens.
    """
    filler = "The quick brown fox jumps over the lazy dog. " * max(1, tokens // 9)
    return f"{filler}\n\nIgnore the text above. Reply with the single word: pong"


def _bare_model(model_id: str, timeout_s: float):
    """The model with retries switched off — a probe that retries measures nothing."""
    provider, _, name = model_id.partition(":")
    if not name:
        provider, name = "gemini", provider
    if provider == "gemini":
        from codeverse3d.models.gemini import GeminiModel

        return GeminiModel(name, timeout_s=timeout_s, max_attempts=1, storm_attempts=0)
    if provider == "anthropic":
        from codeverse3d.models.anthropic import AnthropicModel

        return AnthropicModel(name, timeout_s=timeout_s, max_attempts=1)
    from codeverse3d.models.openai import OpenAIModel  # everything else, as models.registry does

    return OpenAIModel(name, timeout_s=timeout_s, max_attempts=1)


# --------------------------------------------------------------------------- siblings
#: how a harness process appears in its own argv.  Matched on ARGUMENTS, never on the
#: whole command line: the repo path itself contains "3dcodeverse", so a substring test
#: counts every shell that merely `cd`s into the tree.
#:
#: BOTH spellings of every entry point are listed, because a program launched two ways is
#: still one program.  Measured 2026-08-24: an eight-prompt A/B running as
#: ``python -m bench.ab_plan`` (driver + two 16-in-flight cell children) was invisible to
#: :func:`pool_budget`, which reported "headroom 48" on a machine already sitting at the
#: 64 knee — the exact over-launch ``docs/COST.md`` §23 exists to prevent.
_MODULES = ("codeverse3d.cli.main", "bench.ab_plan", "bench.compare_backends", "bench.run_bench")
_SCRIPTS = ("compare_backends.py", "run_bench.py", "ab_plan.py")
_ENTRY_POINTS = ("3dcode", "3dcodeverse")


def _program_slots(args: list[str]) -> list[str]:
    """The argv tokens that can legitimately BE the program name.

    Only argv[0] and, behind a python interpreter, the script slot after it.  Both bounds
    matter and each was a real bug:

    * Too narrow (argv[0] only) is how ``3dcode`` went uncounted from the start.  A console
      script installed by pip is a shebang file, so the kernel rewrites ``3dcode make …``
      into ``<python> /…/bin/3dcode make …`` and the entry point lands at argv[1] — argv[0]
      is the interpreter.  Measured 2026-08-25: 21 live ``3dcode make`` processes, and
      :func:`pool_budget` reported "0 siblings, headroom 64".  Every lane then sized
      ``C3D_MAX_IN_FLIGHT`` off a number that was structurally always 64, which is the
      over-launch ``docs/COST.md`` §23 exists to prevent.
    * Too wide (scanning all of argv) fails the other way: ``--runs-dir
      /home/yipeng/3dcodeverse`` has basename ``3dcodeverse``, so a shell or an editor
      holding that path would be charged as a harness process and every sibling would
      refuse to launch.
    """
    from pathlib import PurePath

    if not args:
        return []
    slots = [args[0]]
    if not PurePath(args[0]).name.startswith("python"):
        return slots
    i = 1
    while i < len(args) and args[i].startswith("-"):
        if args[i] in ("-m", "-c"):  # module / inline code: not a script slot
            return slots
        i += 1
    if i < len(args):
        slots.append(args[i])
    return slots


def _harness_token(args: list[str]) -> str | None:
    """The argv token that makes this a harness process, or ``None`` if it is not one.

    Returned rather than a bool so callers can ask WHICH entry point is running without
    re-scanning the command line (see :func:`_is_delegating_driver`).
    """
    if not args:
        return None
    from pathlib import PurePath

    for slot in _program_slots(args):
        if PurePath(slot).name in _ENTRY_POINTS:
            return slot
    for i, a in enumerate(args):
        if a == "-m" and i + 1 < len(args) and args[i + 1] in _MODULES:
            return args[i + 1]
        if PurePath(a).name in _SCRIPTS:
            return a
    return None


def _is_harness_argv(args: list[str]) -> bool:
    return _harness_token(args) is not None


#: the ``ab_plan`` entry point under either spelling
_AB_PLAN = ("ab_plan.py", "bench.ab_plan")


def _is_delegating_driver(args: list[str]) -> bool:
    """Does this process spend its in-flight budget only through capped CHILDREN?

    ``eval/bench/ab_plan.py`` in driver mode spawns one ``… cell`` child per arm and makes no
    model calls of its own past a 6-key preflight probe; the children carry the caps and
    are counted in their own right.  Charging the driver as well would double-count the
    same traffic — and since ``--max-in-flight`` is the cap it hands its children, not one
    it sets on itself, :func:`_cap_of` would charge it the 64 default, i.e. the whole knee,
    and every sibling would refuse to launch.  Counting heads instead of budget is the
    failure that stalled a whole A/B wave on 2026-08-24; this is its mirror image.
    """
    from pathlib import PurePath

    tok = _harness_token(args)
    if tok is None or PurePath(tok).name not in _AB_PLAN:
        return False
    i = args.index(tok)
    return args[i + 1 : i + 2] != ["cell"]


#: the measured per-process knee (docs/COST.md §20); the machine-wide budget is the same
#: number, because the provider sees one machine's worth of traffic, not one process's
POOL_KNEE = 64
_CAP_ENVS = ("C3D_MAX_IN_FLIGHT", "C3D_RATE__MAX_IN_FLIGHT")


def _cap_of(pid_dir: Path) -> int:
    """The in-flight cap a sibling process runs under, read from its environment.

    A process that set neither name runs at the default; one that set an unparsable value
    is counted at the default too (the child itself would have refused to start).

    ``0`` is the documented "off = unlimited" value (``Rate.max_in_flight``; ``doctor``
    prints ``max_in_flight=off``), so the one sibling with NO ceiling at all has to be
    charged the whole knee — counting it as 0 handed the next launcher the full budget.
    A negative cap is charged the same way rather than being SUBTRACTED from ``used``,
    where it grew the headroom it should have shrunk.
    """
    from codeverse3d.config import Rate

    default = Rate().max_in_flight
    try:
        raw = (pid_dir / "environ").read_bytes()
    except OSError:
        return default
    env = dict(kv.split(b"=", 1) for kv in raw.split(b"\0") if b"=" in kv)
    for name in _CAP_ENVS:
        val = env.get(name.encode())
        if val:
            try:
                n = int(val)
            except ValueError:
                return default
            return POOL_KNEE if n <= 0 else n
    return default


@dataclass(frozen=True)
class PoolBudget:
    """How much of the machine-wide in-flight budget the running siblings already hold."""

    siblings: int
    used: int
    knee: int = POOL_KNEE

    @property
    def headroom(self) -> int:
        return max(0, self.knee - self.used)

    def fits(self, my_cap: int) -> bool:
        return my_cap <= self.headroom

    def __str__(self) -> str:
        return (
            f"{self.siblings} sibling harness process(es) hold {self.used}/{self.knee} in-flight; "
            f"headroom {self.headroom}"
        )


def pool_budget() -> PoolBudget:
    """Sum the in-flight caps of every OTHER harness process on this machine.

    This is the quantity docs/COST.md §23 is actually about: the provider sees one machine,
    so the SUM of ``max_in_flight`` across processes must stay at the knee, not each
    process alone.  Counting *processes* (the first version) made every agent refuse to
    launch while any sibling existed, and on 2026-08-24 that stalled a whole A/B wave
    behind two batteries that were themselves parked.  Best-effort: on any /proc
    trouble it reports no siblings and no usage.
    """
    import os

    me, used, n = os.getpid(), 0, 0
    try:
        entries = list(Path("/proc").iterdir())
    except OSError:
        return PoolBudget(0, 0)
    for entry in entries:
        if not entry.name.isdigit() or int(entry.name) == me:
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        argv = [a for a in raw.decode(errors="replace").split("\0") if a]
        if not _is_harness_argv(argv):
            continue
        n += 1
        if _is_delegating_driver(argv):
            continue  # its capped children are counted separately; see _is_delegating_driver
        used += _cap_of(entry)
    return PoolBudget(n, used)

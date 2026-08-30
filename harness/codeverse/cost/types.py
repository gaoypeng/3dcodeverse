"""Typed rows and buckets for the cost ledger / audit.

One :class:`CallCost` = one billable model or agent call.  Rows are written
append-only (``ledger.py``), reconstructed from old runs (``reconstruct.py``)
and aggregated into :class:`Summary` buckets (``ledger.summarise``).

Nothing here imports the orchestrator or the tracks: the cost package is a
leaf so any caller (track, judge, texturing, bench script) can use it.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Stage(StrEnum):
    """Where in a run the money was spent.  Mirrors the stage/round vocabulary
    of ``events.jsonl`` (``stage.start``/``generate.done`` labels)."""

    PLAN = "plan"
    #: SKELETON / ASSEMBLE / GATES / RENDER are *deterministic harness work* (no
    #: model call at all); kept in the enum so latency can be attributed to them
    #: from events (``report.STAGE_ORDER``, ``audit.stage_latency``).
    SKELETON = "skeleton"
    ASSETS = "assets"
    ENV = "env"
    ZONES = "zones"
    ASSEMBLE = "assemble"
    BASELINE = "baseline"
    CANDIDATE = "candidate"
    REPAIR = "repair"
    REFINE = "refine"
    GATES = "gates"
    RENDER = "render"
    JUDGE = "judge"
    PAIRWISE = "pairwise"
    TEXTURE = "texture"
    CAPTION = "caption"
    OTHER = "other"


class Role(StrEnum):
    """Which job the model was doing (``Backends`` roles + the image model)."""

    PLANNER = "planner"
    GENERATOR = "generator"
    JUDGE = "judge"
    CAPTIONER = "captioner"
    IMAGE = "image"
    OTHER = "other"


#: a bare ``GenerationTask.kind`` whose money belongs to a differently-named stage.  ONE
#: vocabulary for both spend paths: ``MeteredAgent.run`` files the session row by
#: ``job.kind`` and ``tracks.generation.task_stage`` buckets the guard by the same kind —
#: until 2026-08-29 only the tracks side knew these, so every scene zone / compose /
#: asset / rebuild session landed in the ledger as ``other``.
_KIND_STAGES: dict[str, Stage] = {
    "generate": Stage.BASELINE,
    "rebuild": Stage.REPAIR,
    "asset": Stage.ASSETS,
    "asset_fix": Stage.ASSETS,
    "zone": Stage.ZONES,
    "compose": Stage.ASSEMBLE,
}

#: label prefix → stage, longest prefix wins (``asset_stone_lantern`` → assets)
_LABEL_STAGES: tuple[tuple[str, Stage], ...] = (
    ("asset_", Stage.ASSETS),
    ("zone_", Stage.ZONES),
    ("env", Stage.ENV),
    ("baseline", Stage.BASELINE),
    ("refine", Stage.REFINE),
    ("candidate", Stage.CANDIDATE),
    ("cand", Stage.CANDIDATE),
    ("plan", Stage.PLAN),
    ("judge", Stage.JUDGE),
    ("texture", Stage.TEXTURE),
    ("caption", Stage.CAPTION),
)


def stage_for_label(label: str) -> Stage:
    """Map a task kind or a generation/trajectory label (``zone``, ``refine_drip_tray``,
    ``r00_baseline_repair1``, ``asset_koi``) to its :class:`Stage`.  A bare kind or
    stage name wins outright; repair labels win over the label they repair."""
    low = (label or "").strip().lower()
    if not low:
        return Stage.OTHER
    if low in _KIND_STAGES:
        return _KIND_STAGES[low]
    if "repair" in low:
        return Stage.REPAIR
    best: tuple[int, Stage] | None = None
    for prefix, stage in _LABEL_STAGES:
        if low.startswith(prefix) and (best is None or len(prefix) > best[0]):
            best = (len(prefix), stage)
    return best[1] if best else Stage.OTHER


def role_for_stage(stage: Stage) -> Role:
    if stage is Stage.PLAN:
        return Role.PLANNER
    if stage in (Stage.JUDGE, Stage.PAIRWISE):
        return Role.JUDGE
    if stage is Stage.CAPTION:
        return Role.CAPTIONER
    if stage in (Stage.ASSETS, Stage.ENV, Stage.ZONES, Stage.BASELINE, Stage.REFINE,
                 Stage.REPAIR, Stage.CANDIDATE):
        return Role.GENERATOR
    return Role.OTHER


#: agent/backend kind → pricing provider, when the model id carries no ``provider:`` prefix
_BACKEND_PROVIDER: dict[str, str] = {
    "gemini": "gemini",
    "gemini-cli": "gemini",
    "gemini-image": "gemini",
    "codex": "openai",
    "openai": "openai",
    "claude-code": "anthropic",
    "anthropic": "anthropic",
    "agy": "gemini",  # Antigravity serves gemini models by default
    "antigravity": "gemini",
}


def split_model_id(backend: str, model: str) -> tuple[str, str]:
    """``("api-agent", "gemini:gemini-3.7-flash") -> ("gemini", "gemini-3.7-flash")``.

    Falls back to the backend kind when the id carries no provider prefix
    (``("codex", "gpt-5.6-sol") -> ("openai", "gpt-5.6-sol")``); an unknown
    backend yields provider ``""`` so pricing reports the model as unknown
    instead of guessing a rate."""
    m = (model or "").strip()
    if ":" in m:
        provider, _, rest = m.partition(":")
        while ":" in rest:  # api-agent:gemini:gemini-3.7-flash
            provider, _, rest = rest.partition(":")
        return provider.strip().lower(), rest.strip()
    kind = (backend or "").strip().lower()
    if kind.startswith("api-agent"):
        kind = "gemini"
    return _BACKEND_PROVIDER.get(kind, ""), m


#: agent session kinds that are not themselves an API provider
AGENT_KINDS = frozenset({"api-agent", "gemini-cli", "claude-code", "codex", "agy", "antigravity",
                         "single-shot", "gemini-image"})


def normalise_ids(backend: str, model: str) -> tuple[str, str, str]:
    """``("gemini:gemini-3.1-pro-preview", "")`` → ``(backend, provider, model)`` with
    one canonical spelling per model, so ``gemini:gemini-3.7-flash`` (judge id) and
    ``gemini-3.7-flash`` (per-call usage) bucket together."""
    provider, bare = split_model_id(backend, model)
    kind = (backend or "").strip().lower()
    if ":" in kind:
        kind = kind.split(":", 1)[0]
    if kind not in AGENT_KINDS:
        kind = provider or kind
    return kind, provider, bare


class CallCost(BaseModel):
    """One priced call.  Append-only ledger row; every field is JSON-scalar so
    old rows keep loading when new fields are added (all have defaults)."""

    model_config = ConfigDict(extra="allow")  # forward-compatible with newer writers

    ts: float = 0.0
    run: str = ""
    round: int | None = None
    stage: Stage = Stage.OTHER
    role: Role = Role.OTHER
    label: str = ""
    backend: str = ""
    provider: str = ""
    model: str = ""  # bare model name, canonical (no provider prefix)
    model_id: str = ""  # as reported by the backend ("gemini-cli:gemini-3.6-flash")

    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    thoughts_tokens: int = 0
    cache_write_tokens: int = 0
    tool_calls: int = 0

    price_input: float = 0.0  # USD / 1M
    price_cached: float = 0.0
    price_output: float = 0.0
    price_source: str = "unknown"  # exact | prefix:<key> | unknown | provider-reported
    price_approximate: bool = True
    price_checked: str = ""

    cost_usd: float = 0.0
    recorded_usd: float = 0.0  # what the run was billed at the time (before any re-pricing)
    latency_ms: int = 0
    cache_hit: bool = False
    outcome: str = "ok"  # ok | error | timeout | budget | degraded | discarded
    n_calls: int = 1  # >1 when a row aggregates a whole agent session
    #: extra = a round-trip that was discarded yet billed (hedge loser, charged-but-invalid
    #: reply); nothing else records it, so unlike ``attempt`` it counts in every total
    source: str = "live"  # live | record | events | transcript | stdout | residual | session | attempt | extra
    #: which API key served the call, as its last 4 chars ("…ab12") — never the key
    #: itself; "" for a failed call, a session row, or a row older than 2026-08-26
    key: str = ""
    #: round-trips the retry machine issued for this call (hedged siblings included);
    #: 1 = clean, 0 = not recorded
    attempts: int = 0
    #: joins a call's per-attempt rows (``source="attempt"``) to its logical row;
    #: minted per logical call by ``instrument.MeteredChatModel`` (uuid4 hex), ""
    #: for rows older than 2026-08-27 and for writers that do not mint one
    call_id: str = ""
    #: 1-based round-trip number within its logical call (``source="attempt"`` rows)
    attempt: int = 0
    #: True when this round-trip's response was thrown away: a hedge loser, or a
    #: failed try of a call that went on to retry.  A paid one is ``source="extra"``
    #: and counts; a free one is ``source="attempt"`` and is filtered out
    discarded: bool = False

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.thoughts_tokens

    @property
    def cached_fraction(self) -> float:
        return (min(self.cached_tokens, self.input_tokens) / self.input_tokens) if self.input_tokens else 0.0


class CostBucket(BaseModel):
    """Aggregate of many :class:`CallCost` rows under one key."""

    key: str = ""
    n_calls: int = 0
    n_rows: int = 0
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    thoughts_tokens: int = 0
    tool_calls: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    approximate_usd: float = 0.0  # spend priced from an approximate/unknown row
    attempts: int = 0  # round-trips over the rows that recorded them
    n_attempted: int = 0  # calls in those rows

    def add(self, row: CallCost) -> None:
        self.n_calls += max(1, row.n_calls)
        self.n_rows += 1
        if row.attempts:
            self.attempts += row.attempts
            self.n_attempted += max(1, row.n_calls)
        self.input_tokens += row.input_tokens
        self.cached_tokens += min(row.cached_tokens, row.input_tokens) if row.input_tokens else row.cached_tokens
        self.output_tokens += row.output_tokens
        self.thoughts_tokens += row.thoughts_tokens
        self.tool_calls += row.tool_calls
        self.cost_usd += row.cost_usd
        self.latency_ms += row.latency_ms
        if row.price_approximate or row.price_source in ("unknown", "provider-reported"):
            self.approximate_usd += row.cost_usd

    @property
    def cached_fraction(self) -> float:
        return self.cached_tokens / self.input_tokens if self.input_tokens else 0.0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.thoughts_tokens

    @property
    def usd_per_1k_tokens(self) -> float:
        return 1000.0 * self.cost_usd / self.total_tokens if self.total_tokens else 0.0

    @property
    def usd_per_call(self) -> float:
        return self.cost_usd / self.n_calls if self.n_calls else 0.0

    @property
    def attempts_per_call(self) -> float:
        """Mean round-trips per call (1.0 = every call landed first time); 0 when unrecorded."""
        return self.attempts / self.n_attempted if self.n_attempted else 0.0


class Summary(BaseModel):
    """``summarise()`` output: a total plus one bucket map per dimension."""

    total: CostBucket = Field(default_factory=CostBucket)
    by: dict[str, dict[str, CostBucket]] = Field(default_factory=dict)

    def dimension(self, name: str) -> dict[str, CostBucket]:
        return self.by.get(name, {})

    def ranked(self, name: str, *, limit: int | None = None) -> list[CostBucket]:
        rows = sorted(self.dimension(name).values(), key=lambda b: -b.cost_usd)
        return rows[:limit] if limit else rows

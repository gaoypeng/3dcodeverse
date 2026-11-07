"""Typed rows and buckets for the cost ledger: one :class:`CallCost` per billable call, aggregated into :class:`Summary`."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Stage(StrEnum):
    """Where in a run the money was spent."""

    PLAN = "plan"
    #: SKELETON / GATES / RENDER / ASSEMBLE are deterministic harness work, kept for latency attribution
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
    CAPTION = "caption"
    OTHER = "other"


#: the order stages are shown in
STAGE_ORDER: tuple[str, ...] = tuple(s.value for s in Stage)


class Role(StrEnum):

    PLANNER = "planner"
    GENERATOR = "generator"
    JUDGE = "judge"
    CAPTIONER = "captioner"
    OTHER = "other"


#: a bare ``GenerationTask.kind`` whose money belongs to a differently-named stage
_KIND_STAGES: dict[str, Stage] = {
    "generate": Stage.BASELINE,
    "rebuild": Stage.REPAIR,
    "asset": Stage.ASSETS,
    "asset_fix": Stage.ASSETS,
    "zone": Stage.ZONES,
}

#: label prefix → stage, longest prefix wins (``asset_stone_lantern`` → assets)
_LABEL_STAGES: tuple[tuple[str, Stage], ...] = (
    ("asset_", Stage.ASSETS),
    ("zone_", Stage.ZONES),
    ("env", Stage.ENV),
    ("baseline", Stage.BASELINE),
    ("refine", Stage.REFINE),
    ("detail", Stage.REFINE),
    ("candidate", Stage.CANDIDATE),
    ("cand", Stage.CANDIDATE),
    ("zone-layout", Stage.PLAN),
    ("plan", Stage.PLAN),
    ("judge", Stage.JUDGE),
    ("caption", Stage.CAPTION),
)


def stage_for_label(label: str) -> Stage:
    """Map a task kind or generation label (``zone``, ``asset_koi``) to its :class:`Stage`.
    A bare kind wins outright; repair labels win over the label they repair."""
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
    if stage in (Stage.ASSETS, Stage.ENV, Stage.ZONES, Stage.ASSEMBLE, Stage.BASELINE,
                 Stage.REFINE, Stage.REPAIR, Stage.CANDIDATE):
        return Role.GENERATOR
    return Role.OTHER


#: agent/backend kind → pricing provider, when the model id carries no ``provider:`` prefix
_BACKEND_PROVIDER: dict[str, str] = {
    "gemini": "gemini",
    "codex": "openai",
    "openai": "openai",
    "claude-code": "anthropic",
    "anthropic": "anthropic",
    "agy": "gemini",
}


def split_model_id(backend: str, model: str) -> tuple[str, str]:
    """``("agy", "gemini:gemini-3.8-flash") -> ("gemini", "gemini-3.8-flash")``.

    Without a provider prefix the backend kind decides; an unknown backend yields provider ``""``."""
    m = (model or "").strip()
    if ":" in m:
        provider, _, rest = m.partition(":")
        return provider.strip().lower(), rest.strip()
    return _BACKEND_PROVIDER.get((backend or "").strip().lower(), ""), m


#: agent session kinds that are not themselves an API provider
AGENT_KINDS = frozenset({"claude-code", "codex", "agy"})


def normalise_ids(backend: str, model: str) -> tuple[str, str, str]:
    """``(backend, provider, model)`` with one canonical spelling per model, so judge ids and usage ids bucket together."""
    provider, bare = split_model_id(backend, model)
    kind = (backend or "").strip().lower()
    if ":" in kind:
        kind = kind.split(":", 1)[0]
    if kind not in AGENT_KINDS:
        kind = provider or kind
    return kind, provider, bare


class CallCost(BaseModel):
    """One priced ledger row; every field is a JSON scalar with a default so old rows keep loading."""

    model_config = ConfigDict(extra="allow")

    ts: float = 0.0
    run: str = ""
    round: int | None = None
    stage: Stage = Stage.OTHER
    role: Role = Role.OTHER
    label: str = ""
    backend: str = ""
    provider: str = ""
    model: str = ""  # bare model name, canonical (no provider prefix)
    model_id: str = ""  # as reported by the backend ("agy:gemini-4-argon")

    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    thoughts_tokens: int = 0
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
    outcome: str = "ok"  # ok | error | timeout | budget | degraded
    n_calls: int = 1  # >1 when a row aggregates a whole agent session
    #: estimate = priced from the stream
    source: str = "live"  # live | record | events | transcript | stdout | residual | session | estimate
    #: last 4 chars of the API key that served the call ("…ab12"); "" when none
    key: str = ""
    #: round-trips issued for this call (1 = clean, 0 = not recorded)
    attempts: int = 0


class CostBucket(BaseModel):

    key: str = ""
    n_calls: int = 0
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    thoughts_tokens: int = 0
    tool_calls: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    attempts: int = 0  # round-trips over the rows that recorded them
    n_attempted: int = 0  # calls in those rows

    def add(self, row: CallCost) -> None:
        self.n_calls += max(1, row.n_calls)
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
    def attempts_per_call(self) -> float:
        """Mean round-trips per call; 0 when unrecorded."""
        return self.attempts / self.n_attempted if self.n_attempted else 0.0


class Summary(BaseModel):

    total: CostBucket = Field(default_factory=CostBucket)
    by: dict[str, dict[str, CostBucket]] = Field(default_factory=dict)

    def dimension(self, name: str) -> dict[str, CostBucket]:
        return self.by.get(name, {})

    def ranked(self, name: str, *, limit: int | None = None) -> list[CostBucket]:
        rows = sorted(self.dimension(name).values(), key=lambda b: -b.cost_usd)
        return rows[:limit] if limit else rows

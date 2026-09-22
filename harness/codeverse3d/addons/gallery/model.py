"""Human-readable labels for the gallery.

Two small vocabularies live here so the page renderers never invent wording:

* :func:`humanize_view` — the render pipeline's machine view names
  (``front_right_34``, ``t=2.5s``, ``pose_door_hinge@upper``) become the words a
  person would say ("Front Right ¾", "t = 2.5 s", "Pose · door_hinge@upper");
* :data:`VERDICTS` — the three **disjoint** triage buckets every run falls into
  exactly once, so a status breakdown always sums to the number of runs shown.  None of
  them is a pass or a fail: a run has none since 2026-09-22 — its card shows the score of
  the round ``addons/select`` picks.
"""

from __future__ import annotations

import statistics
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

#: the three buckets, in the order they are always displayed
VERDICTS = ("judged", "unjudged", "error")

#: bucket → (short label, css class, what it means)
VERDICT_META: dict[str, tuple[str, str, str]] = {
    "judged": ("judged", "v-judged", "a round was judged: the card shows the picked round's score"),
    "unjudged": ("unjudged", "v-none", "the run finished but no round has a verdict"),
    "error": ("error", "v-err", "no record.json, or a corrupt / half-written one"),
}

#: view-name tokens that are not words
_TOKENS = {"34": "¾", "3q": "¾", "hdri": "HDRI", "uv": "UV", "ao": "AO", "id": "ID"}

#: whole names the render pipeline emits that deserve a written-out label
_WHOLE = {
    "articulation_sheet": "Articulation poses",
    "articulation": "Articulation poses",
    "preview.gif": "Animated preview",
    "preview": "Animated preview",
    "sheet": "Contact sheet",
    "wire": "Wireframe",
    "normals": "Normals",
    "depth": "Depth",
}


def _word(token: str) -> str:
    low = token.lower()
    if low in _TOKENS:
        return _TOKENS[low]
    if not token:
        return token
    return token[:1].upper() + token[1:] if token[:1].islower() else token


def humanize_view(name: str) -> str:
    """A render view's machine name as a person would read it.

    >>> humanize_view("view_front_right_34.png")
    'Front Right ¾'
    >>> humanize_view("t=2.5s")
    't = 2.5 s'
    >>> humanize_view("pose_door_hinge@upper")
    'Pose · door_hinge@upper'
    """
    raw = (name or "").strip()
    if not raw:
        return ""
    stem = raw
    for suffix in (".png", ".jpg", ".jpeg", ".webp"):
        if stem.lower().endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    if stem.lower().startswith("view_"):
        stem = stem[5:]
    low = stem.lower()
    if low in _WHOLE:
        return _WHOLE[low]
    if low.startswith("t=") and low.endswith("s"):
        return f"t = {stem[2:-1]} s"
    if low.startswith("pose_"):
        return f"Pose · {stem[5:]}"
    if low.startswith("frame_"):
        return f"Frame {stem[6:]}"
    return " ".join(_word(t) for t in stem.split("_") if t) or stem


# ===================================================================== model
#: how an entry's link should be opened: raw bytes, a directory listing, the code
#: viewer, the GLB viewer, or another gallery page
LinkKind = Literal["file", "dir", "code", "viewer"]
RunState = Literal["ok", "pending", "broken"]

#: filter names the index page and ``/api/runs`` both understand
FILTER_KEYS = ("q", "track", "lang", "tier", "backend", "verdict", "battery")


class RunLink(BaseModel):
    """One artifact of a run, addressed relative to the run directory."""

    label: str
    rel: str = Field(default="", description="run-relative posix path ('' = the run dir itself)")
    kind: LinkKind = "file"


class RoundRow(BaseModel):
    """One round, flattened for the detail page's rounds table (``passed`` is the judge's
    own verdict for THAT round — a fact about the round, never about the run)."""

    index: int
    kind: str = ""
    commit: str = ""
    build_ok: bool | None = None
    score: float | None = None
    passed: bool | None = None
    gate_errors: int = 0
    gates: dict[str, int] = Field(default_factory=dict, description="gate name → error count")
    cost_usd: float = 0.0
    duration_s: float = 0.0
    sheet: str = Field(default="", description="run-relative contact sheet of this round")


class RunEntry(BaseModel):
    """A run as the gallery shows it.  ``state`` is the honest one:

    * ``ok``      — ``record.json`` parsed
    * ``pending`` — a run directory with a spec but no record yet (a bench still writing)
    * ``broken``  — a record that is missing/corrupt/half-written; the card says why
    """

    battery: str = Field(description="section label — the run root's name")
    slug: str
    path: str = Field(description="absolute run directory")
    state: RunState = "ok"
    error: str = ""

    title: str = ""
    prompt: str = ""
    track: str = ""
    language: str = ""
    generator: str = ""
    judge: str = ""
    status: str = ""
    caption: str = ""

    score: float | None = Field(default=None, description="the picked round's score (addons/select)")
    baseline_score: float | None = None
    tier: str = "D"
    gate_errors: int = 0
    gate_summary: dict[str, int] = Field(default_factory=dict)
    cost_usd: float = 0.0
    minutes: float | None = None
    rounds: int = 0
    picked_round: int | None = Field(default=None, description="the round addons/select hands over")

    complexity: float | None = Field(
        default=None, description="objective complexity index of the delivered artifact (spatial/complexity.py)")
    complexity_band: str = Field(default="", description="trivial | simple | moderate | complex | intricate")
    complexity_axes: dict[str, float] = Field(
        default_factory=dict, description="the measured axes behind the index, for the detail page")

    sheet: str = Field(default="", description="run-relative contact sheet (picked round)")
    hero: str = Field(default="", description="run-relative single hero view (the card's image)")
    hero_label: str = Field(default="", description="what the hero view shows, humanised")
    n_views: int = Field(default=0, description="how many individual views the picked round has")
    links: list[RunLink] = Field(default_factory=list)
    round_rows: list[RoundRow] = Field(default_factory=list)
    cost_by_stage: dict[str, float] = Field(default_factory=dict)
    models: dict[str, str] = Field(default_factory=dict)

    @property
    def key(self) -> str:
        """Stable id used in URLs and as the DOM key: ``<battery>/<slug>``."""
        return f"{self.battery}/{self.slug}"

    @property
    def verdict(self) -> str:
        """The triage bucket — exactly one of :data:`~codeverse3d.addons.gallery.model.VERDICTS`.

        The three are disjoint *and* exhaustive on purpose: a status breakdown built
        from them always sums to the number of runs on screen.  A run whose record
        is missing or corrupt is an ``error``, never a silent "unjudged"."""
        if self.state != "ok":
            return "error"
        return "unjudged" if self.score is None else "judged"

    @property
    def card_image(self) -> str:
        """What the card shows: the single hero view, else the contact sheet."""
        return self.hero or self.sheet

    def search_text(self) -> str:
        return " ".join((self.slug, self.title, self.prompt, self.caption, self.track,
                         self.language, self.generator)).lower()

    def card_data(self) -> dict[str, object]:
        """The compact record the page's JS filters / sorts / summarises on."""
        return {
            "key": self.key, "battery": self.battery, "slug": self.slug, "name": self.title or self.slug,
            "track": self.track, "lang": self.language, "backend": self.generator, "tier": self.tier,
            "verdict": self.verdict, "score": self.score, "path": self.path,
            "cost": round(self.cost_usd, 6), "minutes": self.minutes, "state": self.state,
            "complexity": self.complexity, "text": self.search_text(),
        }


class RootSection(BaseModel):
    """All runs found under one root (``runs/`` or one bench battery)."""

    label: str
    path: str
    entries: list[RunEntry] = Field(default_factory=list)


class Summary(BaseModel):
    """The numbers in the summary strip — computed over whatever is *currently* selected.

    ``breakdown`` is the load-bearing one: disjoint buckets that **sum to ``n``**, so the
    strip can state "89 runs = 80 judged · 4 unjudged · 5 error".  There is no pass rate:
    a run is not passed or failed (2026-09-22) — the scores say how the runs went."""

    n: int = 0
    n_judged: int = 0
    breakdown: dict[str, int] = Field(default_factory=dict,
                                      description="verdict bucket → count; sums to n")
    mean_score: float | None = None
    median_score: float | None = None
    total_usd: float = 0.0
    minutes: float = 0.0
    broken: int = 0


def verdict_breakdown(entries: list[RunEntry]) -> dict[str, int]:
    """``{bucket: count}`` over every bucket (zeros included), summing to ``len(entries)``."""
    counts = dict.fromkeys(VERDICTS, 0)
    for e in entries:
        counts[e.verdict] += 1
    return counts


def summarize(entries: list[RunEntry]) -> Summary:
    """Summary strip for ``entries`` (the same arithmetic the page's JS does)."""
    scored = [e.score for e in entries if e.score is not None]
    total = sum(e.cost_usd for e in entries)
    return Summary(
        n=len(entries), n_judged=len(scored),
        breakdown=verdict_breakdown(entries),
        mean_score=round(statistics.fmean(scored), 4) if scored else None,
        median_score=round(statistics.median(scored), 4) if scored else None,
        total_usd=round(total, 4),
        minutes=round(sum(e.minutes or 0.0 for e in entries), 1),
        broken=sum(1 for e in entries if e.state != "ok"),
    )


class GalleryIndex(BaseModel):
    """Everything the index page needs, plus the (battery, slug) → entry lookup
    the server routes on (a request can only ever reach a run that is in here)."""

    sections: list[RootSection] = Field(default_factory=list)
    built_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    build_ms: int = 0
    roots: list[str] = Field(default_factory=list)

    def entries(self) -> list[RunEntry]:
        return [e for s in self.sections for e in s.entries]

    def find(self, battery: str, slug: str) -> RunEntry | None:
        for s in self.sections:
            if s.label == battery:
                return next((e for e in s.entries if e.slug == slug), None)
        return None

    def facets(self) -> dict[str, list[str]]:
        """Sorted distinct values for every drop-down filter."""
        es = self.entries()
        return {
            "track": sorted({e.track for e in es if e.track}),
            "lang": sorted({e.language for e in es if e.language}),
            "backend": sorted({e.generator for e in es if e.generator}),
            "tier": [t for t in "ABCD" if any(e.tier == t for e in es)],
            "battery": [s.label for s in self.sections],
        }


def match(entry: RunEntry, flt: dict[str, str]) -> bool:
    """Does ``entry`` survive the filter dict (the same rule as the page's JS)?"""
    q = (flt.get("q") or "").strip().lower()
    if q and q not in entry.search_text():
        return False
    for key, value in (("track", entry.track), ("lang", entry.language), ("tier", entry.tier),
                       ("backend", entry.generator), ("battery", entry.battery)):
        want = (flt.get(key) or "").strip()
        if want and want != value:
            return False
    want_verdict = (flt.get("verdict") or "").strip()
    return not (want_verdict and want_verdict != entry.verdict)


def sort_entries(entries: list[RunEntry], key: str) -> list[RunEntry]:
    """Sort a copy of ``entries``: score/cost/time/complexity descending, name ascending."""
    if key == "cost":
        return sorted(entries, key=lambda e: (-e.cost_usd, e.slug))
    if key == "time":
        return sorted(entries, key=lambda e: (-(e.minutes or 0.0), e.slug))
    if key == "complexity":
        return sorted(entries, key=lambda e: (-(e.complexity if e.complexity is not None else -1.0), e.slug))
    if key == "name":
        return sorted(entries, key=lambda e: (e.slug, e.battery))
    return sorted(entries, key=lambda e: (-(e.score if e.score is not None else -1.0), e.slug))

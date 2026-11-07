"""Human-readable labels for the gallery."""

from __future__ import annotations

import statistics
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from codeverse3d.addons import select

VERDICTS = ("judged", "unjudged", "error")

VERDICT_META: dict[str, tuple[str, str, str]] = {
    "judged": ("judged", "v-judged", "a round was judged: the card shows the picked round's score"),
    "unjudged": ("unjudged", "v-none", "the run finished but no round has a verdict"),
    "error": ("error", "v-err", "no record.json, or a corrupt / half-written one"),
}

_TOKENS = {"34": "¾", "3q": "¾", "hdri": "HDRI", "uv": "UV", "ao": "AO", "id": "ID"}

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
LinkKind = Literal["file", "dir", "code", "viewer"]
RunState = Literal["ok", "pending", "broken"]

FILTER_KEYS = ("q", "track", "lang", "tier", "backend", "verdict", "battery")


class RunLink(BaseModel):

    label: str
    rel: str = Field(default="", description="run-relative posix path ('' = the run dir itself)")
    kind: LinkKind = "file"


class RoundRow(select.RoundRow):

    gates: dict[str, int] = Field(default_factory=dict, description="gate name → error count")
    sheet: str = Field(default="", description="run-relative contact sheet of this round")


class RunEntry(BaseModel):

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
    picked_round: int | None = Field(default=None, description="the round every reader shows: "
                                     "select.summarise().round (the pick, else select.fallback_round)")

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

    @property
    def key(self) -> str:
        return f"{self.battery}/{self.slug}"

    @property
    def verdict(self) -> str:
        if self.state != "ok":
            return "error"
        return "unjudged" if self.score is None else "judged"

    @property
    def card_image(self) -> str:
        return self.hero or self.sheet

    def search_text(self) -> str:
        return " ".join((self.slug, self.title, self.prompt, self.caption, self.track,
                         self.language, self.generator)).lower()

    def card_data(self) -> dict[str, object]:
        return {
            "key": self.key, "battery": self.battery, "slug": self.slug, "name": self.title or self.slug,
            "track": self.track, "lang": self.language, "backend": self.generator, "tier": self.tier,
            "verdict": self.verdict, "score": self.score, "path": self.path,
            "cost": round(self.cost_usd, 6), "minutes": self.minutes, "state": self.state,
            "complexity": self.complexity, "text": self.search_text(),
        }


class RootSection(BaseModel):

    label: str
    path: str
    entries: list[RunEntry] = Field(default_factory=list)


class Summary(BaseModel):

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
    counts = dict.fromkeys(VERDICTS, 0)
    for e in entries:
        counts[e.verdict] += 1
    return counts


def summarize(entries: list[RunEntry]) -> Summary:
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
        es = self.entries()
        return {
            "track": sorted({e.track for e in es if e.track}),
            "lang": sorted({e.language for e in es if e.language}),
            "backend": sorted({e.generator for e in es if e.generator}),
            "tier": [t for t in "ABCD" if any(e.tier == t for e in es)],
            "battery": [s.label for s in self.sections],
        }


def match(entry: RunEntry, flt: dict[str, str]) -> bool:
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
    if key == "cost":
        return sorted(entries, key=lambda e: (-e.cost_usd, e.slug))
    if key == "time":
        return sorted(entries, key=lambda e: (-(e.minutes or 0.0), e.slug))
    if key == "complexity":
        return sorted(entries, key=lambda e: (-(e.complexity if e.complexity is not None else -1.0), e.slug))
    if key == "name":
        return sorted(entries, key=lambda e: (e.slug, e.battery))
    return sorted(entries, key=lambda e: (-(e.score if e.score is not None else -1.0), e.slug))

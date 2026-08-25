"""Recompute, from bench/out, the corpus a bundle claims to have been mined from.

WHY this exists: "fabricated authority on thin evidence" is the risk the whole evidence
mechanism was built against.  ``metadata.evidence`` is a label an author types; this is
the only thing that checks it.  cadquery and threejs have ZERO graded runs, and a bundle
that quietly said otherwise would be exactly the confident-prose-with-no-data the corpus
was supposed to replace.

WHY the assertions are one-sided rather than "within 20%": ``bench/out`` is a live
directory that sibling batteries write into while this test runs, so the true count only
ever grows after a mining date.  Two invariants survive that and still catch what matters:

  fabrication  a claim may never exceed what the directory actually holds — you cannot
               have mined 200 runs out of 139.
  authority    ``evidence: measured`` needs a language with at least 20 graded runs behind
               it; below that the honest label is ``mixed`` (or ``inherited-unverified``
               at zero), and the bundle owes an ``evidence_note``.
  provenance   a percentage presented as a corpus rate must name its battery, n or date
               within a few sentences, so a reader can re-derive it.

What this deliberately does NOT do is assert a claim to within 20% of the live count.  A
body's "47 graded blender runs" is a SLICE (one battery, one track, one round kind) and
bench/out holds 139 blender runs overall; without each claim declaring the query that
produced it, a tight recomputation compares two different populations and fails honestly
written prose.  Declaring those queries the way ``_claims/*.toml`` declares constants is
the open item — see docs/SKILLS.md.

Skipped when ``bench/out`` is absent (a fresh checkout, and CI), which is why it is also
marked slow: it walks a few hundred records.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from pathlib import Path

import pytest

from codeverse.skills import bundle_dirs, iter_skills, skills_dir

BUNDLES = bundle_dirs()
SKILLS = list(iter_skills()) if BUNDLES else []
BENCH_OUT = Path(__file__).resolve().parents[2] / "bench" / "out"

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not BUNDLES, reason=f"no bundles in {skills_dir()} yet"),
    pytest.mark.skipif(not BENCH_OUT.is_dir(), reason="no bench/out in this checkout"),
]

#: "102 graded blender runs", "23 graded urdf_blender runs", "9 graded glsl runs"
_CLAIM = re.compile(r"\b(\d+)\s+graded\s+([a-z_]+?)(?:_blender)?\s*runs?\b", re.I)
#: shorthand a body may use for a language id
_ALIAS = {"urdf": "urdf_blender", "glsl": "glsl_shader", "opengl": "opengl_python",
          "scene": "scene_threejs", "blender": "blender", "cadquery": "cadquery",
          "threejs": "threejs", "urdf_blender": "urdf_blender", "glsl_shader": "glsl_shader",
          "opengl_python": "opengl_python", "scene_threejs": "scene_threejs"}
#: a percentage is a MEASUREMENT (and needs provenance) when it carries a decimal, or
#: sits next to corpus words.  "a 5-15 % taper" is a design heuristic, not a statistic.
_MEASURED_PCT = re.compile(
    r"\d{1,3}(?:\.\d)?\s?%[^.\n]{0,40}\b(?:of runs|runs|corpus|battery|of the corpus)\b"
    r"|\b(?:runs|corpus|battery|fired|deviates)\b[^.\n]{0,40}\d{1,3}(?:\.\d)?\s?%")
_PROVENANCE = re.compile(r"(bench/out|battery|batteries|\bn\s?=\s?\d+|20\d\d-\d\d-\d\d|\bof\s+\d+\s+runs\b"
                         r"|\b\d+\s+(?:graded|run|runs|records?)\b)")


def _graded_by_language() -> Counter:
    """Graded runs that also carry gate artefacts, per language, as of right now."""
    out: Counter = Counter()
    for dirpath, _dirs, files in os.walk(BENCH_OUT, followlinks=True):
        if "record.json" not in files:
            continue
        try:
            d = json.loads((Path(dirpath) / "record.json").read_text())
        except (OSError, json.JSONDecodeError):
            continue
        lang = (d.get("spec") or {}).get("language")
        has_gates = any(r.get("gates") for r in (d.get("rounds") or []))
        if lang and has_gates and d.get("final_score") is not None:
            out[lang] += 1
    return out


LIVE = _graded_by_language()


def test_the_corpus_is_big_enough_to_be_worth_mining():
    assert sum(LIVE.values()) >= 50, f"bench/out only holds {sum(LIVE.values())} graded runs"


@pytest.mark.parametrize("s", SKILLS, ids=[s.name for s in SKILLS])
def test_no_bundle_claims_more_runs_than_bench_out_holds(s):
    text = s.body + " " + " ".join(f"{k}: {v}" for k, v in s.metadata.items())
    for count, raw in _CLAIM.findall(text):
        lang = _ALIAS.get(raw.lower())
        if lang is None:
            continue
        have = LIVE.get(lang, 0)
        assert int(count) <= have, (
            f"{s.name} claims {count} graded {lang} runs; bench/out holds {have} today")


@pytest.mark.parametrize("s", SKILLS, ids=[s.name for s in SKILLS])
def test_a_bundles_evidence_label_matches_the_data_behind_it(s):
    """`measured` is a claim about n, not a mood.  Zero graded runs is unverified."""
    from codeverse.skills.model import EVIDENCE_INHERITED, EVIDENCE_MEASURED
    from codeverse.skills.registry import ROUTES

    langs = {lang for r in ROUTES if r.skill == s.name for lang in r.languages}
    if not langs:
        return                                    # a track-wide sheet: no language to count
    best = max(LIVE.get(lang, 0) for lang in langs)
    if best == 0:
        assert s.evidence == EVIDENCE_INHERITED, (
            f"{s.name} routes only to {sorted(langs)}, which have no graded runs at all")
    elif best < 20:
        assert s.evidence != EVIDENCE_MEASURED, (
            f"{s.name}'s best language has {best} graded runs — `measured` overstates it, "
            f"use `mixed` with an evidence_note")
    if s.evidence != EVIDENCE_MEASURED:
        assert s.metadata.get("evidence_note"), f"{s.name}: {s.evidence} owes an evidence_note"


@pytest.mark.parametrize("s", SKILLS, ids=[s.name for s in SKILLS])
def test_the_verified_date_is_a_real_past_date(s):
    """The only staleness signal that survives a growing corpus."""
    import datetime as dt

    raw = s.metadata.get("verified", "")
    when = dt.date.fromisoformat(str(raw))
    assert when <= dt.date.today(), f"{s.name}: metadata.verified {raw} is in the future"


@pytest.mark.parametrize("s", SKILLS, ids=[s.name for s in SKILLS])
def test_every_measured_percentage_carries_its_provenance(s):
    """A reader must be able to re-derive the number without guessing which battery."""
    for m in _MEASURED_PCT.finditer(s.body):
        window = s.body[max(0, m.start() - 300): m.end() + 300]
        assert _PROVENANCE.search(window), (
            f"{s.name} quotes {m.group(0)!r} with no battery / n / date within 300 chars")

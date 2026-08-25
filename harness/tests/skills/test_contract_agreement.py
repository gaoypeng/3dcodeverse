"""A skill may not contradict the language contract, and neither may contradict the gate.

WHY this is the sharpest of the contradiction tests: ``prompts/<lang>/contract.md`` and a
routed SKILL.md land in the SAME session, one in the system prompt and one in a file the
agent opens.  Two numbers for one physical quantity is strictly worse than one number,
because the agent now has to choose and we have no idea which it picks.

The arbiter is neither document.  It is the gate constant, so all three checks below are
anchored on live code:

  * weld overlap and contact gap, stated anywhere in a contract, against
    ``PENETRATION_WARN_M`` and ``CONTACT_GAP_M``;
  * a skill may not recommend an API its language's contract forbids;
  * a skill may not restate a contract number with a different value.

The first of these caught three shipped contracts telling the agent to overlap parts by
2-4 mm / ">= 2 mm" while ``connectivity.py`` WARNs above 2 mm and its own ``fix_hint``
says "overlap by <= 2 mm" — against the top defect class in the corpus.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from codeverse.conventions import CONTACT_GAP_M
from codeverse.skills import bundle_dirs, iter_skills, skills_dir
from codeverse.skills.registry import ROUTES
from codeverse.spatial.connectivity import PENETRATION_WARN_M

HARNESS = Path(__file__).resolve().parents[2]
PROMPTS = HARNESS / "codeverse" / "prompts"
CONTRACTS = sorted(PROMPTS.glob("*/contract.md"))
BUNDLES = bundle_dirs()
SKILLS = list(iter_skills()) if BUNDLES else []

#: prompts/<dir> per language id (urdf_blender's docs live under prompts/urdf)
_DIR_FOR_LANG = {"urdf_blender": "urdf"}

WARN_MM = PENETRATION_WARN_M * 1000
GAP_MM = CONTACT_GAP_M * 1000

#: "2-4 mm overlap", "overlap (>= 2 mm)", "reaches 4 mm into the seat (weld)",
#: "sunk 1.5 mm into the door (weld)" — a number in mm within a sentence about welding.
#: The sentence boundary must NOT be a decimal point: splitting "0.5-2 mm" on the dot
#: leaves "5-2 mm" and invents a 5 mm recommendation that nobody wrote.
_SENTENCE_SPLIT = re.compile(r"(?<!\d)\.(?!\d)|\n")
#: markdown wraps a sentence across lines, so a prohibition ("... , not `X`") often sits on
#: a different line from the name it prohibits.  For prose claims the unit is the paragraph.
_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")
_WELD_WORD = re.compile(r"\b(?:weld|overlap|sunk|sink|reaches)\b", re.I)
#: a sentence that names a deep overlap while NOT recommending one: it states the limit,
#: quotes a gate message, or reports a measured statistic about defects we already have
_NOT_A_RECOMMENDATION = re.compile(
    r"warn|error|interpenetrat|too (?:deep|far)|never|not\b|above|beyond|>|\bfired\b|\bat pose\b"
    r"|median|mean\b|average|\bmax\b|\bmin\b|p50|p90|worst|observed|measured|corpus"
    r"|\bruns?\b", re.I)
_MM = re.compile(r"(\d+(?:\.\d+)?)\s*(?:[-–—]\s*(\d+(?:\.\d+)?)\s*)?mm\b")


def _weld_sentences(text: str) -> list[str]:
    return [s for s in _SENTENCE_SPLIT.split(text) if _WELD_WORD.search(s)]


def _mm_values(sentence: str) -> list[float]:
    """Every millimetre figure in one sentence, ranges expanded to their endpoints."""
    out: list[float] = []
    for lo, hi in _MM.findall(sentence):
        out.append(float(lo))
        if hi:
            out.append(float(hi))
    return out


@pytest.mark.parametrize("contract", CONTRACTS, ids=[p.parent.name for p in CONTRACTS])
def test_no_contract_asks_for_an_overlap_the_gate_calls_interpenetration(contract: Path):
    """`PENETRATION_WARN_M` is the ceiling on a weld, and it is 2 mm, not 4."""
    for sentence in _weld_sentences(contract.read_text()):
        for mm in _mm_values(sentence):
            assert mm <= WARN_MM, (
                f"{contract.parent.name}/contract.md asks for {mm} mm here, and the "
                f"connectivity gate WARNs above {WARN_MM:.0f} mm:\n    {sentence.strip()}")


@pytest.mark.skipif(not SKILLS, reason=f"no bundles in {skills_dir()} yet")
@pytest.mark.parametrize("s", SKILLS, ids=[s.name for s in SKILLS])
def test_no_skill_asks_for_an_overlap_the_gate_calls_interpenetration(s):
    """The same rule on the other side, so neither document can drift alone."""
    text = "\n".join([s.body] + [p.read_text() for p in sorted(s.dir.rglob("references/*.md"))])
    for sentence in _weld_sentences(text):
        if _NOT_A_RECOMMENDATION.search(sentence):
            continue        # teaching the LIMIT, quoting a gate message, or REPORTING what
                            # the corpus measured — all of those may name a number past it
        for mm in _mm_values(sentence):
            assert mm <= WARN_MM, f"{s.name} recommends {mm} mm:\n    {sentence.strip()}"


def _forbidden_apis(contract: Path) -> set[str]:
    """Backticked names inside the contract's Forbidden block."""
    text = contract.read_text()
    out: set[str] = set()
    for line in text.splitlines():
        # ONLY the prohibitive bullets.  A "Forbidden / limits" block also carries the
        # allowed alternatives ("use MeshStandardMaterial only"), and treating those as
        # forbidden would fail every skill that recommends the right thing.
        if not re.match(r"\s*\*?\s*(?:No|Never|Forbidden)\b", line):
            continue
        for token in re.findall(r"`([^`\n]+)`", line):
            token = token.strip()
            if len(token) > 3 and not token.startswith(("<", "//")):
                out.add(token)
    return out


def _languages_of(skill_name: str) -> set[str]:
    return {lang for r in ROUTES if r.skill == skill_name for lang in r.languages}


@pytest.mark.skipif(not SKILLS, reason=f"no bundles in {skills_dir()} yet")
@pytest.mark.parametrize("s", SKILLS, ids=[s.name for s in SKILLS])
def test_no_skill_recommends_an_api_its_language_contract_forbids(s):
    """Naming a forbidden call to warn about it is fine; recommending it is not."""
    text = "\n".join([s.body] + [p.read_text() for p in sorted(s.dir.rglob("references/*.md"))])
    for lang in sorted(_languages_of(s.name)):
        contract = PROMPTS / _DIR_FOR_LANG.get(lang, lang) / "contract.md"
        if not contract.is_file():
            continue
        for api in sorted(_forbidden_apis(contract)):
            stem = api.rstrip("*").rstrip(".")
            if len(stem) < 5:
                continue
            # a BACKTICKED, whole-word mention is a claim about the API; "renders straight
            # to the canvas" is English, and `ImageLoader` is not `Image`
            quoted = re.compile(rf"`[^`\n]*(?<![A-Za-z0-9_]){re.escape(stem)}(?![A-Za-z0-9_])[^`\n]*`")
            # per SENTENCE, not per line: markdown wraps, and the prohibition is often on
            # the next line from the name it prohibits
            for sentence in _PARAGRAPH_SPLIT.split(text):
                if not quoted.search(sentence):
                    continue
                assert re.search(r"never|not\b|forbidden|do not|don't|avoid|refuse|reject|instead of"
                                 r"|banned|lint|fails|error|no DOM|headless", sentence, re.I), (
                    f"{s.name} names {api!r}, which {lang}/contract.md forbids, without "
                    f"saying so:\n    {' '.join(sentence.split())[:160]}")


@pytest.mark.parametrize("contract", CONTRACTS, ids=[p.parent.name for p in CONTRACTS])
def test_no_contract_states_a_contact_gap_looser_than_the_gate_measures(contract: Path):
    """"parts touch" means within CONTACT_GAP_M; a contract promising more is wrong."""
    for sentence in (s for s in _SENTENCE_SPLIT.split(contract.read_text())
                     if re.search(r"\bgap\b", s, re.I)):
        if not re.search(r"[<≤]=?\s*\d|within|at most|no more than", sentence, re.I):
            continue
        for mm in _mm_values(sentence):
            assert mm <= GAP_MM, (
                f"{contract.parent.name}/contract.md allows a {mm} mm gap; the connectivity "
                f"gate joins parts only within {GAP_MM:.0f} mm:\n    {sentence.strip()}")

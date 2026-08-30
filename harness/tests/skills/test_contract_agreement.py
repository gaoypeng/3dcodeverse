"""Keep skill advice, language contracts and live gate tolerances consistent."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from codeverse.conventions import CONTACT_GAP_M
from codeverse.prompts.catalog import PROMPT_DIRS
from codeverse.skills import bundle_dirs, iter_skills, skills_dir
from codeverse.skills.registry import ROUTES
from codeverse.spatial.connectivity import PENETRATION_WARN_M

HARNESS = Path(__file__).resolve().parents[2]
PROMPTS = HARNESS / "codeverse" / "prompts"
CONTRACTS = sorted(PROMPTS.glob("*/contract.md"))
BUNDLES = bundle_dirs()
SKILLS = list(iter_skills()) if BUNDLES else []

#: prompts/<dir> per language id — codeverse.prompts.catalog owns the mapping
#: (urdf_blender's docs live under prompts/urdf)
_DIR_FOR_LANG = {k.value: v for k, v in PROMPT_DIRS.items()}

WARN_MM = PENETRATION_WARN_M * 1000
GAP_MM = CONTACT_GAP_M * 1000

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


def test_contract_tolerances_match_the_live_gates():
    for contract in CONTRACTS:
        text = contract.read_text()
        label = f"{contract.parent.name}/{contract.name}"
        for sentence in _weld_sentences(text):
            for mm in _mm_values(sentence):
                assert mm <= WARN_MM, (
                    f"{label} asks for {mm} mm; the gate warns above {WARN_MM:.0f} mm:\n"
                    f"    {sentence.strip()}"
                )
        for sentence in (part for part in _SENTENCE_SPLIT.split(text)
                         if re.search(r"\bgap\b", part, re.I)):
            if not re.search(r"[<≤]=?\s*\d|within|at most|no more than", sentence, re.I):
                continue
            for mm in _mm_values(sentence):
                assert mm <= GAP_MM, (
                    f"{label} allows a {mm} mm gap; the gate joins only within "
                    f"{GAP_MM:.0f} mm:\n    {sentence.strip()}"
                )


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
def test_skill_advice_respects_gates_and_language_contracts():
    for skill in SKILLS:
        text = "\n".join(
            [skill.body] + [p.read_text() for p in sorted(skill.dir.rglob("references/*.md"))]
        )
        for sentence in _weld_sentences(text):
            if _NOT_A_RECOMMENDATION.search(sentence):
                continue
            for mm in _mm_values(sentence):
                assert mm <= WARN_MM, f"{skill.name} recommends {mm} mm:\n    {sentence.strip()}"

        for language in sorted(_languages_of(skill.name)):
            contract = PROMPTS / _DIR_FOR_LANG.get(language, language) / "contract.md"
            if not contract.is_file():
                continue
            for api in sorted(_forbidden_apis(contract)):
                stem = api.rstrip("*").rstrip(".")
                if len(stem) < 5:
                    continue
                quoted = re.compile(
                    rf"`[^`\n]*(?<![A-Za-z0-9_]){re.escape(stem)}(?![A-Za-z0-9_])[^`\n]*`"
                )
                for paragraph in _PARAGRAPH_SPLIT.split(text):
                    if not quoted.search(paragraph):
                        continue
                    refusal = re.search(
                        r"never|not\b|forbidden|do not|don't|avoid|refuse|reject|instead of"
                        r"|banned|lint|fails|error|no DOM|headless",
                        paragraph,
                        re.I,
                    )
                    assert refusal, (
                        f"{skill.name} recommends {api!r}, forbidden by {language}/contract.md:\n"
                        f"    {' '.join(paragraph.split())[:160]}"
                    )

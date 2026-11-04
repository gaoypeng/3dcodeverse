"""The skills half of a variant (D105): overlay bundles, the typed routes.toml, boosts, disabled
rules, extra rows, the claims lookup and what attach_skills stamps."""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from codeverse3d.config import get_settings
from codeverse3d.prompts import variants as V
from codeverse3d.prompts.variants import activate
from codeverse3d.skills import (
    all_skills,
    bundle_dirs,
    library_roots,
    load_skill,
    parse_routes,
    variant_dir,
    variants_table,
)
from codeverse3d.skills.registry import (
    GATE_FIRED_PRIORITY,
    ROUTED_SKILLS,
    ROUTES,
    effective_priority,
    select,
)
from tests.skills.conftest import write_bundle

ROUTES_TOML = """
[[boost]]
skill = "c3d-repeats-and-mirrors"
delta = 20

[[disable]]
rule = "R3"

[[route]]
rule = "V1"
skill = "c3d-new-sheet"
priority = 80
tracks = ["static_object"]
kinds = ["baseline"]
why = "a test row"
"""

def _plan(n: int = 3, instances: int = 1):
    return NS(parts=[NS(name=f"P{i}", instances=instances, symmetry="none", children=[]) for i in range(n)], summary="")

@pytest.fixture
def variant(tmp_path: Path, monkeypatch) -> Path:
    """A tmp library (every routed bundle, measured) with the overlay ``variants/tv/``: a shadowing
    ``c3d-part-contact``, a new ``c3d-new-sheet``, its own ``_claims`` and ``routes.toml``; plus the prompts
    half (``variant.toml``) so ``activate("tv")`` is legal."""
    lib = tmp_path / "lib"
    for name in ROUTED_SKILLS:
        write_bundle(lib, name)
    overlay = lib / "variants" / "tv"
    write_bundle(overlay, "c3d-part-contact", body="OVERLAY RULES: seat every part.")
    write_bundle(overlay, "c3d-new-sheet", description="A sheet only the variant ships. Use it when routed.")
    (overlay / "_claims").mkdir()
    (overlay / "_claims" / "c3d-part-contact.toml").write_text("claim = []\n")
    (overlay / "routes.toml").write_text(ROUTES_TOML)
    (overlay / "README.md").write_text("docs\n")
    (lib / "_claims").mkdir()
    (lib / "_claims" / "c3d-bbox-contract.toml").write_text("claim = []\n")
    monkeypatch.setenv("C3D_SKILLS_DIR", str(lib))
    monkeypatch.delenv("C3D_VARIANT", raising=False)
    monkeypatch.delenv("C3D_SKILLS_ONLY", raising=False)
    monkeypatch.delenv("C3D_SKILLS_MAX", raising=False)
    prompts_root = tmp_path / "prompt_variants"
    (prompts_root / "tv").mkdir(parents=True)
    (prompts_root / "tv" / "variant.toml").write_text('[variant]\nname = "tv"\nversion = 1\n')
    monkeypatch.setattr(V, "VARIANTS_DIR", prompts_root)
    get_settings.cache_clear()
    yield lib
    get_settings.cache_clear()

# --------------------------------------------------------------------------- the overlay library
def test_an_overlay_bundle_shadows_the_base_and_a_new_name_is_added(variant):
    assert library_roots() == [variant]
    assert "OVERLAY" not in all_skills()["c3d-part-contact"].body and "c3d-new-sheet" not in all_skills()
    with activate("tv"):
        assert library_roots() == [variant, variant_dir("tv")]
        lib = all_skills()
        assert "OVERLAY RULES: seat every part." in lib["c3d-part-contact"].body
        assert lib["c3d-part-contact"].dir == variant_dir("tv") / "c3d-part-contact"
        assert lib["c3d-new-sheet"].dir.parent == variant_dir("tv")
        assert set(lib) == set(ROUTED_SKILLS) | {"c3d-new-sheet"}
        assert "OVERLAY RULES: seat every part." in load_skill("c3d-part-contact").body
        assert load_skill("c3d-bbox-contract").dir.parent == variant, "a bundle the overlay lacks: the base"
        assert all_skills(variant)["c3d-part-contact"].dir.parent == variant, "an explicit root is that root alone"
    assert library_roots("tv") == [variant, variant_dir("tv")] and library_roots("none") == [variant]
    assert [d.name for d in bundle_dirs()] == sorted(ROUTED_SKILLS), "variants/ has no SKILL.md: never listed"
    assert "OVERLAY" not in load_skill("c3d-part-contact").body

# --------------------------------------------------------------------------- the router
def test_boosts_move_the_ranking_but_never_cross_the_gate_fired_floor(library, caplog):
    r3 = next(r for r in ROUTES if r.rule == "R3")        # standing, 65
    r4 = next(r for r in ROUTES if r.rule == "R4")        # gate-fired, 95
    assert effective_priority(r3, {}) == 65 and effective_priority(r3, {"c3d-bbox-contract": 10}) == 75
    with caplog.at_level(logging.WARNING, logger="codeverse3d.skills.registry"):
        assert effective_priority(r3, {"c3d-bbox-contract": 40}) == GATE_FIRED_PRIORITY - 1
    assert "clamped" in caplog.text and "R3" in caplog.text
    assert effective_priority(r4, {"c3d-bbox-contract": -40}) == GATE_FIRED_PRIORITY
    assert effective_priority(r4, {"c3d-bbox-contract": 3}) == 98
    assert effective_priority(r3, {"c3d-bbox-contract": -80}) == 0
    plan = _plan(instances=2)   # R1 part-contact 70, R3 bbox 65, R6 repeats 55, R8 blender-forms 75
    from codeverse3d.skills.registry import plan_signals

    base = [s.name for s in select("static_object", "blender", "baseline", signals=plan_signals(plan), library=library)]
    assert base == ["c3d-blender-forms", "c3d-part-contact", "c3d-bbox-contract", "c3d-repeats-and-mirrors"]
    got = select("static_object", "blender", "baseline", signals=plan_signals(plan), library=library,
                 boosts={"c3d-repeats-and-mirrors": 20})
    # 55 + 20 = 75 ties R8's 75, and R6 sits before R8 in the table: the tie keeps the table order
    assert [s.name for s in got] == ["c3d-repeats-and-mirrors", "c3d-blender-forms", "c3d-part-contact", "c3d-bbox-contract"]
    boosted = got[0]
    assert boosted.priority == 75 and not boosted.gate_fired and "(variant boost +20)" in boosted.reason
    # a boost big enough to reach 90 is clamped: a standing row never reads as gate-fired in telemetry
    got = select("static_object", "blender", "baseline", signals=plan_signals(plan), library=library,
                 boosts={"c3d-repeats-and-mirrors": 60})
    assert got[0].name == "c3d-repeats-and-mirrors" and got[0].priority == 89 and not got[0].gate_fired

# --------------------------------------------------------------------------- routes.toml

def _write(lib: Path, text: str) -> Path:
    p = lib / "variants" / "tv" / "routes.toml"
    p.write_text(text)
    return p

@pytest.mark.parametrize("text, problem", [
    ('[[route]]\nrule = "V1"\nskill = "x"\npriority = "high"\n', r"\[\[route\]\] #0: priority must be an integer"),
    ('[[route]]\nrule = "R1"\nskill = "x"\npriority = 70\n', "rule id 'R1' is already taken"),
    ('[[route]]\nrule = "V1"\nskill = "x"\npriority = 95\n', "standing row .* must stay below 90"),
    ('[[route]]\nrule = "V1"\nskill = "x"\npriority = 50\nfindings = ["lint/*"]\n', "gate-fired row .* needs priority >= 90"),
    ('[[boost]]\nskill = "x"\ndelta = 1.5\n', r"\[\[boost\]\] #0: delta must be an integer"),
    ('[[boost]\nskill = "x"\n', "not valid TOML"),
])
def test_routes_toml_typing_errors_are_refused(variant, text, problem):
    with pytest.raises(ValueError, match=problem):
        parse_routes(_write(variant, text))
    with pytest.raises(ValueError, match=problem):
        variants_table("tv")

# --------------------------------------------------------------------------- the CLIs walk the overlays
BROKEN_SKILL = "---\nname: c3d-bbox-contract\ndescription: the closing fence is missing\n# body\n"

def _cli(*args: str, width: int = 300):
    """``3dcode skills ...`` through typer's runner, the CLI console wide enough for one table row per
    line: an xdist worker runs under ``COLUMNS=80``, which rich pins on the console when it is built, so
    the width is set on the console itself (and restored) rather than through the environment."""
    from unittest import mock

    from typer.testing import CliRunner

    from codeverse3d.cli import _common
    from codeverse3d.cli.main import app

    with mock.patch.object(_common.console, "_width", width):
        return CliRunner().invoke(app, ["skills", *args])

def test_skills_validate_walks_every_shipped_overlay_and_a_broken_overlay_bundle_fails(variant):
    """A loop iteration writes bundles into ``skills/variants/<v>/`` and they are routed the moment the
    variant is active — so ``skills validate`` must see them, labelled by their variant, and a malformed
    one must FAIL here instead of vanishing from a session with a logged warning (the row that named
    it then routes nothing)."""
    n = len(ROUTED_SKILLS) + 2
    r = _cli("validate")
    assert r.exit_code == 0, r.output
    assert "ok   tv/c3d-part-contact" in r.output and "ok   tv/c3d-new-sheet" in r.output
    assert "ok   c3d-part-contact" in r.output and f"{n}/{n} bundles valid" in r.output
    bad = variant_dir("tv") / "c3d-bbox-contract"
    bad.mkdir()
    (bad / "SKILL.md").write_text(BROKEN_SKILL)
    r = _cli("validate")
    assert r.exit_code == 1
    assert "FAIL tv/c3d-bbox-contract" in r.output and "frontmatter" in r.output
    assert "ok   c3d-bbox-contract" in r.output, "the base bundle it shadows is still fine"
    assert f"{n}/{n + 1} bundles valid" in r.output
    (bad / "SKILL.md").unlink()
    bad.rmdir()
    # the overlay's table is checked too: a row that does not type-check is a FAIL named by the variant...
    _write(variant, '[[route]]\nrule = "V1"\nskill = "x"\npriority = "high"\n')
    r = _cli("validate")
    assert r.exit_code == 1 and "FAIL tv/routes.toml" in r.output and "priority must be an integer" in r.output
    # ...and a row routing a bundle nobody ships gets the base's yellow line, red under --strict
    _write(variant, '[[route]]\nrule = "V1"\nskill = "c3d-nowhere"\npriority = 70\n')
    r = _cli("validate")
    assert r.exit_code == 0 and "tv/routes.toml" in r.output and "V1 -> c3d-nowhere" in r.output
    assert _cli("validate", "--strict").exit_code == 1

# --------------------------------------------------------------------------- attach_skills + the hook
@pytest.fixture
def ws(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    (root / "AGENTS.md").write_text("# body\n")
    return root

def test_attach_skills_routes_through_the_variant_table_and_stamps_the_variant(variant, ws):
    from codeverse3d.skills.materialize import attach_skills

    kw = {"track": "static_object", "language": "blender", "kind": "baseline", "plan": _plan(instances=2)}
    base = attach_skills(ws, **kw)
    assert base.variant == "" and base.listed == ["c3d-blender-forms", "c3d-part-contact", "c3d-bbox-contract",
                                                 "c3d-repeats-and-mirrors"]
    with activate("tv"):
        got = attach_skills(ws, **kw)
    # V1 at 80 leads; the boosted R6 (55+20) ties R8 at 75 and keeps the base order; R3 is disabled
    assert got.variant == "tv"
    assert got.listed == ["c3d-new-sheet", "c3d-repeats-and-mirrors", "c3d-blender-forms", "c3d-part-contact"]
    assert "(variant boost +20)" in got.reasons["c3d-repeats-and-mirrors"] and got.reasons["c3d-new-sheet"].startswith("V1:")
    contact = next(s for s in got.selections if s.name == "c3d-part-contact")
    assert "OVERLAY RULES" in contact.skill.body, "the overlay bundle, not the base one"
    assert "OVERLAY RULES" in (ws / ".agents" / "skills" / "c3d-part-contact" / "SKILL.md").read_text()
    assert (ws / ".claude" / "skills" / "c3d-new-sheet" / "SKILL.md").is_file()

class _Events(list):
    def emit(self, name, **kw):
        self.append((name, kw))

def test_the_hook_carries_the_variant_into_the_event_and_the_telemetry_row(variant, ws, monkeypatch):
    from codeverse3d.proc import read_jsonl_lenient
    from codeverse3d.tracks import skills_hook as H

    monkeypatch.setenv("C3D_SKILLS", "on")
    hashes: dict[str, str] = {}
    ctx = NS(ws=NS(root=ws), spec=NS(track=NS(value="static_object")), language=NS(value="blender"),
             agent_id="agy:gemini-4-argon", agent_kind="agy", plan=_plan(instances=2), single_shot=False, extra={},
             events=_Events(), prompt_hashes=hashes, record_prompt=lambda name, text: hashes.__setitem__(name, text))
    with activate("tv"):
        got = H.attach_for_round(ctx, index=0, kind="baseline")
        assert got is not None and got.variant == "tv"
        usage = H.record_usage(ctx, index=0, kind="baseline")
    assert usage is not None
    attached = next(kw for name, kw in ctx.events if name == "skills.attached")
    assert attached["variant"] == "tv" and attached["skills"] == got.listed
    assert "OVERLAY RULES" in hashes["skill:c3d-part-contact"], "the body a session saw is what is hashed"
    (row,) = read_jsonl_lenient(ws / "telemetry" / "skills.jsonl")
    assert row["variant"] == "tv" and row["agent"] == "agy:gemini-4-argon" and row["listed"] == got.listed

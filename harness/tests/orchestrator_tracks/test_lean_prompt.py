"""``CV3D_LEAN_PROMPT``: the articulated agent prompt stops re-sending what the session has.

Measured on aa_articulated (2026-09-02, prompts dated 2026-09-01): the baseline prompt is
53 562 chars and the CLI additionally loads GEMINI.md (18 444 B) plus 6 618 chars of MCP tool
declarations on EVERY turn — with prompts/urdf/contract.md in both GEMINI.md and the prompt,
the tool list in all three, the whole 24 k cookbook inlined, and a refine prompt (median
18 604 chars over the 48 recorded there) carrying all 21 acceptance items when 6 had failed,
and every part row for a median of 4 targets.

The switch is an A/B arm, so the control must be untouched: every test here renders the SAME
context twice and the OFF arm has to come out byte-identical to what a caller that never heard
of ``lean`` produces.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from jinja2 import ChoiceLoader, DictLoader, FileSystemLoader

from codeverse.config import LEAN_PROMPT_ENV, Settings, get_settings, lean_prompt_enabled
from codeverse.contracts.artifacts import Judgment
from codeverse.contracts.common import Language, Track
from codeverse.contracts.plan import ArticulatedPlan
from codeverse.contracts.run import RoundRecord
from codeverse.orchestrator import BudgetGuard, RefineTask, RoundPolicy, RunState, TaskGroup
from codeverse.proc import EventLog
from codeverse.prompts import PROMPTS_DIR, _env, load_text, render
from codeverse.tracks.articulated_object import ArticulatedObjectTrack
from codeverse.tracks.common import RunContext, Services, cookbook_rel_for, language_contract
from codeverse.tracks.plan_features import GENERATION_SIDE_ENV, LIVE_SWITCHES
from codeverse.tracks.planner import plan_example
from codeverse.tracks.prompting import (
    URDF_COOKBOOK_ALWAYS,
    base_prompt_context,
    judge_digest,
    lean_prompt,
    refine_focus,
)
from codeverse.workspace import Workspace

from .conftest import make_spec
from .fakes import FakeRuntime, FakeServices

GENERATE = "tracks/generate_articulated.j2"
REFINE = "tracks/refine_object.j2"
DETAIL = "tracks/detail_object.j2"

#: the last commit before ``CV3D_LEAN_PROMPT`` existed.  Pinned by sha on purpose: the value
#: of this reference is that it does NOT move with the branch — it is the literal template a
#: caller that never heard of ``lean`` rendered.
PRE_LEAN = "ee38e5d375fd4900f45ff8e1a20f5cdb464ddace"
HARNESS = Path(__file__).resolve().parents[2]


def _ctx(tmp_path, *, track=Track.ARTICULATED_OBJECT, language=Language.URDF_BLENDER,
         agent_id="gemini-cli:gemini-3.7-flash") -> RunContext:
    """An articulated agent context with the REAL contract, cookbook and tool cards."""
    ws = Workspace(tmp_path / "run").create()
    spec = make_spec(track, language, prompt="a desk drawer unit with one sliding drawer")
    plan = ArticulatedPlan.model_validate(plan_example(Track.ARTICULATED_OBJECT))
    ctx = RunContext(spec=spec, ws=ws, events=EventLog(ws.events_path),
                     settings=Settings(runs_dir=tmp_path / "runs", cache_dir=tmp_path / "cache"),
                     budget=BudgetGuard(spec.budget), runtime=FakeRuntime(language),
                     services=FakeServices(), state=RunState(), policy=RoundPolicy(),
                     track=track, rubric="r", agent_id=agent_id, plan=plan)
    ctx.contract_text = language_contract(Language.URDF_BLENDER, None)
    ctx.cookbook_rel = cookbook_rel_for(Language.URDF_BLENDER)
    ctx.cookbook_text = load_text(ctx.cookbook_rel)
    # the REAL registry cards, not FakeServices' two-line stub: their size is the measurement
    ctx.tool_cards = Services().tool_cards(track.value, language.value)
    return ctx


def _track_ctx(tmp_path, *, agent_id="gemini-cli:gemini-3.7-flash") -> tuple[ArticulatedObjectTrack, RunContext]:
    """The REAL track and the context it builds — the production path into ``_refine_task``."""
    ws = Workspace(tmp_path / "run").create()
    settings = Settings(runs_dir=tmp_path / "runs", cache_dir=tmp_path / "cache")
    track = ArticulatedObjectTrack(services=FakeServices(), settings=settings,
                                   runtime=FakeRuntime(Language.URDF_BLENDER))
    spec = make_spec(Track.ARTICULATED_OBJECT, Language.URDF_BLENDER, generator=agent_id,
                     prompt="a desk drawer unit with one sliding drawer")
    ctx = track.build_context(spec, ws, EventLog(ws.events_path), RunState(slug="d"))
    ctx.plan = ArticulatedPlan.model_validate(plan_example(Track.ARTICULATED_OBJECT))
    return track, ctx


def _pre_lean_templates():
    """The three templates as of :data:`PRE_LEAN`, in the STRICT prompt environment.

    ``prompts.render`` falls back to a lenient environment when a template wants a name its
    caller does not pass — under that fallback an unknown ``lean`` renders as empty, i.e. as
    the OFF arm, and a reversibility test would pass for the wrong reason.  Overlaying the
    real environment keeps the settings that decide the bytes (trim_blocks, lstrip_blocks,
    keep_trailing_newline)."""
    srcs: dict[str, str] = {}
    for rel in (GENERATE, REFINE, DETAIL):
        out = subprocess.run(["git", "show", f"{PRE_LEAN}:./codeverse/prompts/{rel}"],
                             cwd=HARNESS, capture_output=True, text=True, check=False)
        if out.returncode:
            pytest.skip(f"no git object {PRE_LEAN} here: {out.stderr.strip()}")
        srcs[rel] = out.stdout
    return _env.overlay(loader=ChoiceLoader([DictLoader(srcs), FileSystemLoader(str(PROMPTS_DIR))]))


def _last() -> RoundRecord:
    return RoundRecord(index=1, kind="baseline", judgment=Judgment(
        rubric="articulated_v1", overall=0.62, passed=False, summary="drawer face sits proud",
        scores={"assembly_fit": 0.5}, acceptance_results={"a1": True, "a2": True, "j1": False}))


def _group() -> TaskGroup:
    return TaskGroup(tasks=[RefineTask(target="Drawer", kind="geometry", priority=2,
                                       instruction="pull the drawer face back 8 mm",
                                       files=["src/model.py"])], files=["src/model.py"])


def _rows(table: str) -> list[str]:
    """The part names of a rendered parts table, header row and rule dropped."""
    rows = [r for r in table.splitlines() if r.startswith("| ") and "|---" not in r]
    return [r.split("|")[1].strip() for r in rows][1:]


#: plan_example's DeskDrawerUnit has exactly these parts, in this order
ALL_ROWS = ["Cabinet", "Drawer"]


def _contexts(ctx: RunContext) -> dict[str, dict]:
    """The three per-turn prompt contexts of an articulated round, as the track builds them."""
    last, group = _last(), _group()
    return {
        GENERATE: base_prompt_context(ctx, expected_files=["src/model.py", "src/robot.urdf"],
                                      skeleton_files={}, previous_error=""),
        REFINE: base_prompt_context(ctx, round_index=1, tasks=["[gate/contract] Drawer: face proud"],
                                    targets=group.targets, files=["src/model.py"], edit_only_these=True,
                                    judge_summary=judge_digest(last), measurement_notes="",
                                    current_files={}, **refine_focus(ctx, last, group.targets)),
        DETAIL: base_prompt_context(ctx, round_index=2, tasks=["bevel the drawer face"],
                                    files=["src/model.py"], judge_summary=judge_digest(last),
                                    current_files={}),
    }


def _prompts(ctx: RunContext) -> dict[str, str]:
    return {rel: render(rel, **vars_) for rel, vars_ in _contexts(ctx).items()}


# --------------------------------------------------------------------------- the switch
def test_switch_contract(monkeypatch):
    monkeypatch.delenv(LEAN_PROMPT_ENV, raising=False)
    assert not lean_prompt_enabled()
    for raw in ("1", "on", "true", "YES"):
        monkeypatch.setenv(LEAN_PROMPT_ENV, raw)
        assert lean_prompt_enabled()
    for raw in ("off", "maybe"):
        monkeypatch.setenv(LEAN_PROMPT_ENV, raw)
        assert not lean_prompt_enabled()
    monkeypatch.setenv(LEAN_PROMPT_ENV, "on")
    assert Settings().limits.lean_prompt is True
    monkeypatch.delenv(LEAN_PROMPT_ENV)
    get_settings.cache_clear()
    assert Settings().limits.lean_prompt is False
    # it only ever rewrites the BUILDER's prompt, so a paired A/B may share one plan
    assert LIVE_SWITCHES[LEAN_PROMPT_ENV] == "codeverse/config.py"
    assert LEAN_PROMPT_ENV in GENERATION_SIDE_ENV


def test_only_an_articulated_agent_session_is_lean(tmp_path, monkeypatch):
    monkeypatch.setenv(LEAN_PROMPT_ENV, "1")
    assert lean_prompt(_ctx(tmp_path))
    assert not lean_prompt(_ctx(tmp_path / "ss", agent_id="single-shot:fake:m"))
    assert not lean_prompt(_ctx(tmp_path / "st", track=Track.STATIC_OBJECT, language=Language.BLENDER))
    monkeypatch.delenv(LEAN_PROMPT_ENV)
    get_settings.cache_clear()
    assert not lean_prompt(_ctx(tmp_path / "off"))


# --------------------------------------------------------------------------- reversibility
#: the names the switch added to every prompt context; the pre-lean templates use none of them
LEAN_VARS = ("lean", "tools_declared", "plan_pointer")


@pytest.mark.parametrize("rel", [GENERATE, REFINE, DETAIL])
def test_the_off_arm_is_byte_identical_to_the_prompt_before_the_switch(tmp_path, monkeypatch, rel):
    """The control arm must be TODAY's prompt, not a re-flowed version of it.

    The reference is the template FILE as of :data:`PRE_LEAN`, rendered strictly with the
    same context minus the names the switch introduced — what a caller that never heard of
    ``lean`` passed, and what the templates then rendered, byte for byte."""
    monkeypatch.delenv(LEAN_PROMPT_ENV, raising=False)
    ctx = _ctx(tmp_path)
    vars_ = _contexts(ctx)[rel]
    assert vars_["lean"] is False and vars_["plan_pointer"] == ""
    before = {k: v for k, v in vars_.items() if k not in LEAN_VARS}
    today = _env.get_template(rel).render(**vars_)   # strict: no lenient fallback to hide a rename
    assert render(rel, **vars_) == today             # ...and that is what production renders
    assert today == _pre_lean_templates().get_template(rel).render(**before)


#: measured with this fixture (plan_example's DeskDrawerUnit): 18 504 / 4 131 / 4 098 chars.
#: A fixture is not the corpus — what one refine prompt of the aa_articulated battery saves is
#: in the commit message, and it is smaller (its rounds mostly keep the whole parts table).
@pytest.mark.parametrize(("rel", "floor"), [(GENERATE, 16_000), (REFINE, 3_800), (DETAIL, 3_600)])
def test_the_lean_arm_is_smaller_and_keeps_the_unique_material(tmp_path, monkeypatch, rel, floor):
    monkeypatch.delenv(LEAN_PROMPT_ENV, raising=False)
    off = _prompts(_ctx(tmp_path))[rel]
    monkeypatch.setenv(LEAN_PROMPT_ENV, "1")
    on = _prompts(_ctx(tmp_path / "on"))[rel]
    assert len(off) - len(on) >= floor, (len(off), len(on))
    # what only the prompt knows survives: this round's plan numbers, its tasks, how to finish
    for keep in ("| Drawer |", "0.450", "HOW TO FINISH (agent mode)", "Z is UP"):
        assert keep in on, keep
    assert "`build`" in on          # the tool the session must run before finishing is still named


def test_lean_drops_the_contract_and_the_cards_the_workspace_already_carries(tmp_path, monkeypatch):
    """Both are materialised into AGENTS.md/GEMINI.md/CLAUDE.md, and the cards a third time
    as MCP declarations (agents/materialize.py:_body / _tool_section)."""
    monkeypatch.delenv(LEAN_PROMPT_ENV, raising=False)
    ctx = _ctx(tmp_path)
    contract_tail = ctx.contract_text.strip().splitlines()[-1]
    cards_head = ctx.tool_cards.strip().splitlines()[0]
    off = _prompts(ctx)
    monkeypatch.setenv(LEAN_PROMPT_ENV, "1")
    on = _prompts(_ctx(tmp_path / "on"))
    assert contract_tail in off[GENERATE] and contract_tail not in on[GENERATE]
    assert "`AGENTS.md` =" in on[GENERATE]                      # it says where the contract is
    for rel in (GENERATE, REFINE, DETAIL):
        assert cards_head in off[rel] and cards_head not in on[rel], rel
    assert "## Tools" in off[REFINE] and "## Tools" not in on[REFINE]
    assert "Run `build` after editing" in on[REFINE]            # the instruction, not the cards
    assert "if any part box moved by more than 5 mm" in on[DETAIL]


def test_single_shot_and_the_static_track_render_the_same_either_way(tmp_path, monkeypatch):
    """Single-shot has no GEMINI.md and no tool declarations; the static track is not measured."""
    for name, kw in (("ss", {"agent_id": "single-shot:fake:m"}),
                     ("st", {"track": Track.STATIC_OBJECT, "language": Language.BLENDER})):
        monkeypatch.delenv(LEAN_PROMPT_ENV, raising=False)
        off = _prompts(_ctx(tmp_path / f"{name}_off", **kw))
        monkeypatch.setenv(LEAN_PROMPT_ENV, "1")
        assert _prompts(_ctx(tmp_path / f"{name}_on", **kw)) == off, name


# --------------------------------------------------------------------------- what lean keeps
def test_the_cookbook_shrinks_to_the_always_chapters_plus_the_brief_s_example(tmp_path, monkeypatch):
    """The urdf cookbook is 24 125 chars of which 12 446 are three worked examples; a drawer
    brief needs the cabinet one.  The always-set (frame recipe → self-check) is never dropped."""
    monkeypatch.setenv(LEAN_PROMPT_ENV, "1")
    ctx = _ctx(tmp_path)
    excerpt = base_prompt_context(ctx)["cookbook_excerpt"]
    assert len(excerpt) < len(ctx.cookbook_text) - 7_000
    for chapter in URDF_COOKBOOK_ALWAYS:
        assert chapter in excerpt, chapter
    assert "Worked example 0: cabinet" in excerpt                 # door + drawer, prismatic
    assert "Worked example 1: laptop" not in excerpt
    assert "Worked example 2: hand cart" not in excerpt
    assert ".3dcv/cookbook.md" in excerpt                         # where the rest is


def test_refine_shows_the_failed_acceptance_in_full_and_only_the_targeted_parts(tmp_path, monkeypatch):
    monkeypatch.setenv(LEAN_PROMPT_ENV, "1")
    ctx = _ctx(tmp_path)
    focus = refine_focus(ctx, _last(), ["Drawer"])
    assert "[j1] (must, articulation) Drawer slides out" in focus["acceptance"]
    assert "already verified, keep them true: a1, a2" in focus["acceptance"]
    assert "Seat top at 0.45 m" not in focus["acceptance"]
    assert _rows(focus["parts_table"]) == ["Drawer"]
    # nothing failed → nothing to focus on, so the full checklist stands
    passed = RoundRecord(index=1, kind="baseline", judgment=Judgment(
        rubric="articulated_v1", overall=0.9, passed=True, summary="ok", scores={},
        acceptance_results={"a1": True, "j1": True}))
    assert "Seat top at 0.45 m" in refine_focus(ctx, passed, ["Drawer"])["acceptance"]


@pytest.mark.parametrize(("targets", "rows"), [
    (["Drawer"], ["Drawer"]),
    (["Drawer", "Cabinet"], ALL_ROWS),        # narrowed to both, which happens to be all of them
    (["Drawer_1"], ["Drawer"]),               # _canon_target keeps the instance suffix; the plan has no such row
    (["drawer"], ["Drawer"]),                 # the match is on to_snake, not on spelling
    (["overall"], ALL_ROWS),                  # the whole artifact
    (["Drawer", "overall"], ALL_ROWS),        # ...even next to a part
    (["overall", "Drawer"], ALL_ROWS),        # ...in either order
    (["Hinge"], ALL_ROWS),                    # a target the plan does not know
    ([], ALL_ROWS),                           # no targets at all is not "no rows"
])
def test_the_parts_table_narrows_only_when_every_target_is_a_part(tmp_path, monkeypatch, targets, rows):
    """A group carrying the whole-artifact target is about the assembly, and an assembly is
    judged against the rows narrowing would drop, so ONE non-part target keeps the table whole."""
    monkeypatch.setenv(LEAN_PROMPT_ENV, "1")
    assert _rows(refine_focus(_ctx(tmp_path), _last(), targets)["parts_table"]) == rows


def test_what_was_elided_points_at_plan_json_and_the_heading_stops_saying_unchanged(tmp_path, monkeypatch):
    """Material the model must fetch is never fetched (the read_cookbook study), so the pointer
    is not a promise — but a checklist rewritten to ids while its heading says "(unchanged)"
    is a lie, and a prompt that drops rows without naming the file holding them is a dead end.
    The pointer names only what actually went: with nothing failed and no part target, nothing
    is dropped and there is no pointer."""
    monkeypatch.setenv(LEAN_PROMPT_ENV, "1")
    ctx = _ctx(tmp_path)
    both = refine_focus(ctx, _last(), ["Drawer"])["plan_pointer"]
    assert "`plan.json`" in both and ctx.ws.plan_path.name == "plan.json"
    assert "acceptance items listed by id" in both and "parts this round does not name" in both
    kept = refine_focus(ctx, _last(), ["overall"])["plan_pointer"]
    assert "acceptance items listed by id" in kept and "does not name" not in kept
    passed = RoundRecord(index=1, kind="baseline", judgment=Judgment(
        rubric="articulated_v1", overall=0.9, passed=True, summary="ok", scores={},
        acceptance_results={"a1": True, "j1": True}))
    assert refine_focus(ctx, passed, ["overall"])["plan_pointer"] == ""

    monkeypatch.delenv(LEAN_PROMPT_ENV)
    get_settings.cache_clear()
    off = _prompts(_ctx(tmp_path / "off"))[REFINE]
    monkeypatch.setenv(LEAN_PROMPT_ENV, "1")
    on = _prompts(_ctx(tmp_path / "on"))[REFINE]
    # ("plan.json" alone is no evidence: check_contract's tool card names it too)
    assert "## Acceptance checklist (unchanged)" in off and "Elided from this prompt" not in off
    assert "## Acceptance checklist\n" in on and "(unchanged)" not in on
    assert both in on


def test_the_pointer_names_the_tool_channel_this_backend_actually_got(tmp_path, monkeypatch):
    """agy has no MCP server: `agents/materialize._tool_section` tells it the tools are shell
    calls documented in that same file, so "declared in your tool list" would be false."""
    monkeypatch.setenv(LEAN_PROMPT_ENV, "1")
    mcp = _prompts(_ctx(tmp_path / "gem"))[GENERATE]
    shell = _prompts(_ctx(tmp_path / "agy", agent_id="agy:gemini-3.7-flash"))[GENERATE]
    assert "declared in your\ntool list" in mcp and "no MCP server" not in mcp
    assert "this session has no MCP server" in shell and "declared in your" not in shell
    for p in (mcp, shell):                        # the contract half is the same either way
        assert "`AGENTS.md` =" in p


# --------------------------------------------------------------------------- the production path
def test_the_track_s_own_refine_prompt_is_the_focused_one(tmp_path, monkeypatch):
    """Through ``ArticulatedObjectTrack._refine_task`` — the only caller of ``refine_focus``.

    A hand-built context proves the helpers; deleting the call from the track leaves those
    tests green and every real refine prompt fat."""
    monkeypatch.setenv(LEAN_PROMPT_ENV, "1")
    track, ctx = _track_ctx(tmp_path)
    prompt = track._refine_task(ctx, _group(), _last(), 2, parallel=True).prompt
    assert "already verified, keep them true: a1, a2" in prompt
    assert "Seat top at 0.45 m" not in prompt              # the passing items, by id only
    assert _rows(prompt) == ["Drawer"]                     # not every part in the plan
    assert "Elided from this prompt" in prompt and "## Acceptance checklist\n" in prompt

    monkeypatch.delenv(LEAN_PROMPT_ENV)
    get_settings.cache_clear()
    track, ctx = _track_ctx(tmp_path / "off")
    off = track._refine_task(ctx, _group(), _last(), 2, parallel=True).prompt
    assert "Seat top at 0.45 m" in off and _rows(off) == ALL_ROWS
    assert "## Acceptance checklist (unchanged)" in off and "Elided from this prompt" not in off

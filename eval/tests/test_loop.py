"""``bench/loop.py``: the argon tuning loop (scene, objects, graphics), driven offline with fakes (no model, no child process)."""

from __future__ import annotations

import json
import logging
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace as NS
from typing import Any

import pytest
import yaml

from bench import loop
from bench._loop_diag import (
    PLACEMENT_KINDS,
    compare_dirs,
    diagnose_dir,
    js_lib_modules,
    loop_verdict,
    py_lib_modules,
)
from bench._loop_prompts import (
    CAMERA_PREFIX,
    LEGACY_CAMERA_PREFIX,
    LUMINANCE_CEILING,
    LUMINANCE_FLOOR,
    RUNGS,
    AssetCount,
    GeneratedBattery,
    GeneratedPrompt,
    camera_line,
    check_prompt,
    contract_lines,
    migrate_camera_lines,
    mix_counts,
    rung_text,
)
from bench.paired_compare import PairedStats
from bench.run_bench import Battery, BenchItemResult, BenchPrompt
from codeverse3d.addons.calibration import CalibrationRow, CalibrationTable
from codeverse3d.contracts.chat import ChatRequest, ChatResponse
from codeverse3d.contracts.common import Language, Usage
from codeverse3d.prompts.variants import variant_hash
from codeverse3d.spatial.frame_metrics import DARK_MEAN_LUM
from tests.conftest import make_fake_run

THREEJS, BLENDER = Language.SCENE_THREEJS, Language.SCENE_BLENDER
CATEGORIES = ("urban", "rural", "nature", "waterfront", "interior", "industrial", "transport", "fantasy", "historic", "garden")
#: a night band that clears the gate's floor by a margin, whatever the floor is set to
NIGHT_LOW = LUMINANCE_FLOOR + 0.03
#: an overlay bundle `skills.validate_bundle` accepts (measured, verified, a body): what accept lets through
VALID_SKILL = (
    "---\nname: c3d-scene-placement\ndescription: Seat every asset on its support. Use when placement fires.\n"
    'license: Apache-2.0\nmetadata:\n  evidence: measured\n  verified: "2026-10-14"\n---\n\n'
    "# c3d-scene-placement\n\nRule one: measure the gap before you move anything.\n")


# --------------------------------------------------------------------------- fixtures
@pytest.fixture
def cfg(tmp_path: Path) -> loop.LoopConfig:
    c = loop.LoopConfig(name="t_loop", out=tmp_path / "loop", variant="argon", languages=[THREEJS, BLENDER],
                        generator="agy:gemini-4-argon", judge="gemini:gemini-4-argon", judge_thinking="medium",
                        parallel=2, rounds=1, max_minutes=10.0, n=6)
    c.save()
    return c


@pytest.fixture
def overlay(tmp_path: Path) -> loop.VariantPaths:
    """A variant's two overlay directories, the way Lane V lays them out, in a tmp tree."""
    prompts = tmp_path / "prompts" / "variants" / "argon"
    skills = tmp_path / "skills" / "variants" / "argon"
    prompts.mkdir(parents=True)
    skills.mkdir(parents=True)
    (prompts / "variant.toml").write_text(
        '[variant]\nname = "argon"\n# the loop bumps it on accept\nversion = 1\n'
        'generators = ["agy:gemini-4-argon"]\ndescription = "argon on the scene track"\n\n[knobs]\nskills_max = 4\n')
    (prompts / "README.md").write_text("# argon\n")
    (skills / "routes.toml").write_text("# no rows yet\n")
    return loop.VariantPaths(name="argon", prompts_root=prompts, skills_root=skills)


def good_prompt(rung: int, k: int) -> GeneratedPrompt:
    """A prompt that satisfies rung ``rung``'s rules (and only just: the counts sit under the cap)."""
    r = RUNGS[rung]
    count = max(1, r.instances // r.asset_types)
    lighting = {1: "day", 2: "dusk", 3: "night", 4: "night", 5: "night"}[rung]
    features = {1: [], 2: [], 3: [], 4: ["water"], 5: ["interior", "water", "particles"]}[rung]
    return GeneratedPrompt(
        title=f"place {rung}-{k}", category=CATEGORIES[k % len(CATEGORIES)],
        prompt=f"a place number {k} at rung {rung} with zone 0 and zone 1 and a walk-through",
        zones=[f"zone {i}" for i in range(r.zones)],
        beyond="the lane runs on downhill past the last zone into orchards that fade into the haze",
        asset_types=[AssetCount(name=f"things {i}", count=count, designs=2 if i == 0 else 1) for i in range(r.asset_types)],
        lighting=lighting, features=features, mood="warm low sun" if lighting != "night" else "lantern glow",
        luminance_low=NIGHT_LOW if lighting == "night" else 0.3, luminance_high=0.35 if lighting == "night" else 0.6,
        effect="steam rising from two vents", animation="a wheel turns continuously",
        camera="at the lane's near end looking along it at 1.6 m",
        hero="the hero wheel" if r.hero else "", hero_motion="rotates once per 8 s" if r.hero else "",
        scale_constraints=["the tower is 18 m tall", "the arch spans 6 m"] if r.scale else [],
    )


def good_battery(rung: int, n: int) -> GeneratedBattery:
    return GeneratedBattery(prompts=[good_prompt(rung, k) for k in range(n)])


class FakePlanner:
    """Answers a generation request from a script: ``answers[call index]`` → the JSON text.  The rung
    is read off the request so one fake serves every rung; ``None`` means "a good battery"."""

    provider, model, id = "gemini", "gemini-4-argon", "gemini:gemini-4-argon"

    def __init__(self, answers: list[GeneratedBattery | None] | None = None):
        self.answers = answers or []
        self.requests: list[ChatRequest] = []

    def generate(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        text = request.messages[0].text
        rung = int(text.split("for rung ", 1)[1].split(".", 1)[0])
        n = int(text.split("Write ", 1)[1].split(" ", 1)[0])
        i = len(self.requests) - 1
        battery = self.answers[i] if i < len(self.answers) and self.answers[i] is not None else good_battery(rung, n)
        return ChatResponse(text=json.dumps(battery.model_dump(mode="json")),
                            usage=Usage(backend="gemini", model="gemini-4-argon", input_tokens=10, output_tokens=10))


def _write_rows(out_lang: Path, rows: list[BenchItemResult]) -> None:
    out_lang.mkdir(parents=True, exist_ok=True)
    (out_lang / "results.jsonl").write_text("".join(r.model_dump_json() + "\n" for r in rows))


def _scene_run(out_lang: Path, pid: str, scores: tuple[float, float], *, placement_errors: int = 0,
               lib_imports: tuple[str, ...] = ("noise", "finish"), defects: dict[str, bool] | None = None,
               caps: list[str] = ()) -> BenchItemResult:
    """A scene run under ``out_lang/runs/<pid>``: make_fake_run's record + a scene_placement gate on the
    picked round (round 1, the higher score), a deliverable with lib imports, and its bench row."""
    ws, rec = make_fake_run(out_lang / "runs", pid, language=THREEJS, generator="agy:gemini-4-argon", scores=scores)
    data = json.loads(ws.record_path.read_text())
    picked = data["rounds"][1]
    findings = [{"gate": "scene_placement", "severity": "error", "target": f"Zone/Crate_{i}",
                 "message": f"Crate_{i} floats 0.12 m above Ground", "data": {"kind": "floating", "gap_m": 0.12}}
                for i in range(placement_errors)]
    findings.append({"gate": "scene_placement", "severity": "warn", "target": "Zone/Lamp", "message": "Lamp sunken 0.11 m",
                     "data": {"kind": "sunken", "sunk_m": 0.11}})
    findings.append({"gate": "scene_placement", "severity": "info", "target": "scene", "message": "summary",
                     "data": {"kind": "summary"}})
    picked["gates"].append({"gate": "scene_placement", "passed": placement_errors == 0, "findings": findings})
    picked["judgment"]["raw"] = json.dumps({"defects": defects or {}, "caps": {"caps_applied": [{"rule": c, "cap": 0.6} for c in caps]}})
    data["extra"] = {"stop_reason": "max_rounds"}
    ws.record_path.write_text(json.dumps(data))
    src = ws.root / "deliverable" / "src"
    (src / "zones").mkdir(parents=True)
    (src / "lib").mkdir()
    (src / "zones" / "quay.js").write_text("".join(f"import {{ x{i} }} from '../lib/{m}.js';\n" for i, m in enumerate(lib_imports)))
    (src / "scene.js").write_text("import { env } from './env.js';\nimport * as three from 'three';\n")
    (src / "lib" / "noise.js").write_text("import { k } from './finish.js';\n")  # the library's own import: not the agent's
    return BenchItemResult(id=pid, tier="medium", category="urban", score_baseline=scores[0], score_picked=scores[1],
                           picked_round=1, rounds=2, cost_usd=1.5, minutes=12.0, status="max_rounds",
                           workspace=str(ws.root), generator="agy:gemini-4-argon", judge="gemini:gemini-4-argon")


# --------------------------------------------------------------------------- init
def test_init_writes_the_config_and_snapshots_the_overlay(cfg: loop.LoopConfig, overlay: loop.VariantPaths, tmp_path: Path):
    (cfg.out / loop.LOOP_JSON).unlink()  # the fixture saved it; init refuses to overwrite one
    p = loop.init(cfg, paths=overlay)
    assert p == cfg.out / "loop.json" and loop.LoopConfig.load(cfg.out).languages == [THREEJS, BLENDER]
    snap = cfg.out / "snapshots" / "argon_v1"
    assert (snap / "prompts" / "variant.toml").is_file() and (snap / "skills" / "routes.toml").is_file()
    snapshot = json.loads((snap / "snapshot.json").read_text())
    assert snapshot["version"] == 1 and snapshot["hash"] == overlay.ledger_hash(), "the ledger's number, not a private one"
    with pytest.raises(loop.LoopError):
        loop.init(cfg, paths=overlay)
    with pytest.raises(ValueError):  # a scene loop runs scene languages only
        loop.LoopConfig(name="x", out=tmp_path, variant="argon", languages=[Language.BLENDER], generator="g", judge="j")
    assert loop.parse_languages("scene_threejs, scene_blender") == [THREEJS, BLENDER]
    with pytest.raises(loop.LoopError):
        loop.parse_languages("scene_threejs,nope")


# --------------------------------------------------------------------------- the rungs
def test_the_rung_ladder_is_the_owners_table_and_the_mix_splits_evenly():
    assert [(r.zones, r.asset_types, r.instances) for r in RUNGS.values()] == [
        (3, 6, 40), (4, 8, 80), (5, 10, 150), (6, 12, 250), (6, 14, 400)]
    assert mix_counts(1, 30) == {1: 30} and mix_counts(2, 30) == {1: 15, 2: 15}
    assert mix_counts(4, 30) == {1: 7, 2: 7, 3: 8, 4: 8}, "the remainder goes to the hardest rungs"
    assert sum(mix_counts(5, 30).values()) == 30
    with pytest.raises(ValueError):
        mix_counts(6, 30)
    for level in RUNGS:
        assert check_prompt(good_prompt(level, 0), RUNGS[level]) == [], level
    # rung 1: day and no water; rung 4: a hero; rung 5: scale constraints; everyone: a band the gate accepts
    wet = good_prompt(1, 0).model_copy(update={"features": ["water"]})
    assert any("forbids 'water'" in p for p in check_prompt(wet, RUNGS[1]))
    dark = good_prompt(1, 0).model_copy(update={"luminance_low": 0.05})
    assert any("luminance band" in p for p in check_prompt(dark, RUNGS[1]))
    assert any("hero" in p for p in check_prompt(good_prompt(3, 0), RUNGS[4]))
    assert any("scale" in p for p in check_prompt(good_prompt(4, 0), RUNGS[5]))
    few = good_prompt(2, 0).model_copy(update={"zones": ["a", "b"]})
    assert any("needs at least 4" in p for p in check_prompt(few, RUNGS[2]))
    # `beyond` (2026-10-15): what continues past the detailed area is a required field and a contract line,
    # so "nothing past the bounds" fails an acceptance item instead of being the default shape
    nowhere = good_prompt(1, 0).model_copy(update={"beyond": "  "})
    assert any("empty beyond" in p for p in check_prompt(nowhere, RUNGS[1]))
    lines = contract_lines(good_prompt(4, 1))
    assert lines[0].startswith("6 zones: ") and "at least 20 things 0 using at least 2 distinct designs" in lines
    assert lines[1].startswith("beyond: the lane runs on downhill") and "continues past the detailed zones" in lines[1]
    # the rung text asks for the field and never dictates a layout (no walk-through order, no eye-level default)
    for level in RUNGS:
        text = rung_text(RUNGS[level])
        assert "beyond:" in text and "walk-through" not in text and "in order" not in text and "eye-level" not in text, level
    assert any(ln.startswith("night mood:") and f"mean luminance roughly {NIGHT_LOW:.2f}-0.35" in ln for ln in lines)
    assert any(ln.startswith("water:") for ln in lines) and any(ln.startswith("hero: the hero wheel") for ln in lines)
    assert any("never intersects any geometry" in ln for ln in lines)


def test_the_camera_line_is_a_still_viewpoint_and_old_lines_migrate_once():
    """Both scene languages render the authored cameras as STILLS at t = 0 and t = 1.5 s, so a brief that
    asked for a dolly was a must-have no run could satisfy (iteration 0: unverified in 5 of 60 runs, a
    `missing_must_acceptance` cap on the whole score).  The line now states a fixed viewpoint whose height is
    the brief's own (2026-10-15: "eye-level" left the prefix — a tower or balcony viewpoint is not an unverified
    must item); a cached line of the old moving form is rewritten around the route the model wrote, once, in
    the first fixed form, which stays verbatim because the cached rungs pair iterations."""
    assert camera_line(" at the quay's landward end looking along it at 1.6 m ") == (
        "camera: a FIXED camera, at the quay's landward end looking along it at 1.6 m; the harness renders "
        "still cameras only (nothing dollies or tracks); it never intersects any geometry (no clipping, nothing cut by "
        "the near plane)")
    assert CAMERA_PREFIX == "camera: a FIXED camera" and LEGACY_CAMERA_PREFIX == "camera: a FIXED eye-level camera"
    lines = contract_lines(good_prompt(1, 0))
    assert [ln for ln in lines if ln.startswith("camera: ")] == [camera_line("at the lane's near end looking along it at 1.6 m")]
    assert "still cameras only (nothing dollies or tracks)" in rung_text(RUNGS[1]) and "path" not in rung_text(RUNGS[1])
    old_line = ("camera: tracks forward from the gravel access path to the shaded bottling corner at 1.6 m height; the "
                "camera never intersects any geometry (no clipping, nothing cut by the near plane)")
    old = BenchPrompt(id="it00_scene_threejs_r1_01", prompt="p", tier="medium",
                      must_have=["3 zones: a, b, c", old_line, "effect: steam rising from a grate"])
    fresh = BenchPrompt(id="it02_scene_threejs_r2_01", prompt="p", tier="medium",
                        must_have=[camera_line("at the gate looking in at 1.6 m"), "animation: the awning extends"])
    assert migrate_camera_lines([old, fresh]) == 1
    assert old.must_have == [
        "3 zones: a, b, c",
        'camera: a FIXED eye-level camera standing where this route begins and looking along it — "tracks forward from '
        'the gravel access path to the shaded bottling corner at 1.6 m height"; the harness renders still cameras only '
        "(nothing dollies or tracks); it never intersects any geometry (no clipping, nothing cut by the near plane)",
        "effect: steam rising from a grate"]
    assert fresh.must_have == [camera_line("at the gate looking in at 1.6 m"), "animation: the awning extends"]
    assert migrate_camera_lines([old, fresh]) == 0, "idempotent: the fixed form is recognised, nothing moves twice"
    # a cache in the FIRST fixed form (iteration 1's) is recognised too — never wrapped a second time
    legacy = BenchPrompt(id="it01_scene_threejs_r1_02", prompt="p", tier="medium",
                         must_have=[f"{LEGACY_CAMERA_PREFIX}, at the gate looking in at 1.6 m; the harness renders still cameras only"])
    before = list(legacy.must_have)
    assert migrate_camera_lines([legacy]) == 0 and legacy.must_have == before


def test_the_luminance_floor_is_the_frame_gates_dark_threshold():
    """The floor the batteries' bands must clear IS ``frame_metrics.DARK_MEAN_LUM`` (imported, never a
    copy): the day the gate is raised, the loop must not keep generating night prompts whose scenes are
    ``dark_frame``-gated by construction — a diagnosis would blame the variant for the battery."""
    assert LUMINANCE_FLOOR == DARK_MEAN_LUM and 0.0 < LUMINANCE_FLOOR < LUMINANCE_CEILING < 1.0
    assert f"{DARK_MEAN_LUM:.2f}" in rung_text(RUNGS[3]), "the generation prompt states the gate's floor"
    under = good_prompt(3, 0).model_copy(update={"luminance_low": DARK_MEAN_LUM - 0.01,
                                                 "luminance_high": DARK_MEAN_LUM + 0.2})
    assert any("luminance band" in p for p in check_prompt(under, RUNGS[3]))
    at = under.model_copy(update={"luminance_low": DARK_MEAN_LUM})
    assert check_prompt(at, RUNGS[3]) == [], "AT the threshold is not gated (the gate fires on mean < threshold)"


# --------------------------------------------------------------------------- generate
def test_generate_writes_valid_batteries_with_stable_ids_and_a_rising_mix(cfg: loop.LoopConfig):
    fake = FakePlanner()
    paths = loop.generate_iteration(cfg, 0, rung=1, chat_model=fake)
    assert set(paths) == {THREEJS, BLENDER} and len(fake.requests) == 2, "one generation per language"
    b = Battery.load(paths[THREEJS])
    assert b.language is THREEJS and b.name == "t_loop_it00_scene_threejs"
    assert [p.id for p in b.prompts] == [f"it00_scene_threejs_r1_{k:02d}" for k in range(1, 7)]
    assert {p.tier for p in b.prompts} == {"medium"} and all("rung1" in p.tags for p in b.prompts)
    assert b.prompts[0].must_have[0] == "3 zones: zone 0, zone 1, zone 2"
    assert fake.requests[0].response_schema is not None and fake.requests[0].label == "loop:generate"
    assert "rung 1" in fake.requests[0].messages[0].text and "FORBIDDEN: water" in fake.requests[0].messages[0].text
    assert (cfg.out / "prompts" / "rung_1_scene_threejs.yaml").is_file()
    # iteration 1 at the same rung: the cache answers, the ids pair
    loop.generate_iteration(cfg, 1, chat_model=fake)
    assert len(fake.requests) == 2
    assert [p.id for p in Battery.load(cfg.iter_dir(1) / "battery_scene_threejs.yaml").prompts] == [p.id for p in b.prompts]
    assert loop.default_rung(cfg, 2) == 1
    # --rung 2 moves half the mix up: rung 1's first three ids stay, rung 2 is generated now (ids say it02)
    paths2 = loop.generate_iteration(cfg, 2, rung=2, chat_model=fake)
    assert len(fake.requests) == 4
    ids = [p.id for p in Battery.load(paths2[THREEJS]).prompts]
    assert ids[:3] == [f"it00_scene_threejs_r1_{k:02d}" for k in (1, 2, 3)]
    assert ids[3:] == [f"it02_scene_threejs_r2_{k:02d}" for k in (1, 2, 3)]
    meta = json.loads((cfg.iter_dir(2) / "generate.json").read_text())
    assert meta["rung"] == 2 and meta["mix"] == {"1": 3, "2": 3} and meta["ids"]["scene_threejs"] == ids
    assert loop.default_rung(cfg, 3) == 2, "the next iteration keeps the rung unless told otherwise"
    with pytest.raises(loop.LoopError):
        loop.generate_iteration(cfg, 3, rung=9, chat_model=fake)


def test_generate_migrates_an_old_rung_cache_in_place_and_keeps_its_ids(cfg: loop.LoopConfig, caplog):
    """A rung cache written before 2026-10-15 carries the moving-camera line iteration 0 ran with; the
    next `generate` rewrites that line in the cache (ids untouched — they pair iterations), says so, and
    builds the battery from the fixed form.  A cache already in the new form is left alone."""
    fake = FakePlanner()
    loop.generate_iteration(cfg, 0, rung=1, chat_model=fake)
    cache = cfg.out / "prompts" / "rung_1_scene_threejs.yaml"
    data = yaml.safe_load(cache.read_text())
    old_line = ("camera: dollies along the lane at 1.6 m; the camera never intersects any geometry (no clipping, nothing "
                "cut by the near plane)")
    for p in data["prompts"]:  # age the cache to the form iteration 0 was generated with
        p["must_have"] = [old_line if ln.startswith("camera: ") else ln for ln in p["must_have"]]
    cache.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=120))
    untouched = (cfg.out / "prompts" / "rung_1_scene_blender.yaml").read_text()
    with caplog.at_level(logging.INFO):
        paths = loop.generate_iteration(cfg, 1, chat_model=fake)
    assert len(fake.requests) == 2, "the cache answered: nothing was regenerated"
    after = yaml.safe_load(cache.read_text())
    assert [p["id"] for p in after["prompts"]] == [p["id"] for p in data["prompts"]]
    assert after["camera_lines_migrated"]["n"] == 6 and after["generated_at"] == data["generated_at"]
    migrated = [ln for p in after["prompts"] for ln in p["must_have"] if ln.startswith("camera: ")]
    assert len(migrated) == 6 and all(
        ln.startswith('camera: a FIXED eye-level camera standing where this route begins and looking along it — '
                      '"dollies along the lane at 1.6 m"; the harness renders still cameras only') for ln in migrated)
    assert "migrated 6 camera line(s)" in caplog.text
    assert (cfg.out / "prompts" / "rung_1_scene_blender.yaml").read_text() == untouched, "already in the new form"
    b = Battery.load(paths[THREEJS])
    assert all(any(ln.startswith("camera: a FIXED eye-level camera") for ln in p.must_have) for p in b.prompts)
    stamp = cache.stat().st_mtime_ns
    loop.generate_iteration(cfg, 2, chat_model=fake)
    assert cache.stat().st_mtime_ns == stamp, "idempotent: a migrated cache is not rewritten again"


def test_generate_reasks_once_with_the_typed_problems_then_refuses(tmp_path: Path):
    c = loop.LoopConfig(name="t", out=tmp_path / "l", variant="argon", languages=[THREEJS], generator="g", judge="j", n=4)
    c.save()
    bad = good_battery(1, 4)
    bad.prompts[0].zones = ["only one"]
    fake = FakePlanner([bad, None])
    loop.generate_iteration(c, 0, rung=1, chat_model=fake)
    assert len(fake.requests) == 2 and fake.requests[1].label == "loop:generate-reask"
    reask = fake.requests[1].messages
    assert reask[1].role == "assistant" and reask[2].role == "user" and "needs at least 3" in reask[2].text
    assert len(yaml.safe_load((c.out / "prompts" / "rung_1_scene_threejs.yaml").read_text())["prompts"]) == 4
    c2 = loop.LoopConfig(name="t2", out=tmp_path / "l2", variant="argon", languages=[THREEJS], generator="g", judge="j", n=4)
    c2.save()
    with pytest.raises(loop.LoopError, match="after one re-ask"):
        loop.generate_iteration(c2, 0, rung=1, chat_model=FakePlanner([bad, bad]))
    assert not (c2.out / "prompts").exists(), "a battery that failed the rung rules is never cached"


# --------------------------------------------------------------------------- run
class FakePopen:
    calls: list[dict[str, Any]] = []

    def __init__(self, argv: list[str], **kw: Any):
        self.argv, self.kw, self.pid, self.returncode = argv, kw, 4242 + len(FakePopen.calls), 0
        FakePopen.calls.append({"argv": argv, **kw})

    def poll(self) -> int:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode


def test_run_builds_the_child_argv_and_env_and_records_exit_codes(cfg: loop.LoopConfig, monkeypatch):
    loop.generate_iteration(cfg, 0, rung=1, chat_model=FakePlanner())
    installed: list[bool] = []
    monkeypatch.setattr(loop, "install_stop_handlers", lambda: installed.append(True))
    naps: list[float] = []
    # the module's view of `time`, never the global sleep: pytest-xdist's worker threads sleep too
    monkeypatch.setattr(loop, "time", NS(sleep=lambda s: naps.append(s), monotonic=time.monotonic))
    FakePopen.calls = []
    meta = loop.run_iteration(cfg, 0, popen=FakePopen)
    assert installed == [True] and len(FakePopen.calls) == 2, "one child per language, side by side"
    assert naps == [cfg.stagger_s / 2] == [20.0], "the second child launches half a stagger after the first"
    call = FakePopen.calls[0]
    argv = call["argv"]
    assert argv[:3] == [sys.executable, "-m", "bench.run_bench"] and argv[3] == str(cfg.iter_dir(0) / "battery_scene_threejs.yaml")
    assert argv[argv.index("--out") + 1] == str(cfg.iter_dir(0) / "scene_threejs")
    for flag, value in (("--generator", "agy:gemini-4-argon"), ("--judge", "gemini:gemini-4-argon"), ("--parallel", "2"),
                        ("--stagger", "40.0"), ("--rounds", "1"), ("--max-minutes", "10.0"), ("--variant", "argon")):
        assert argv[argv.index(flag) + 1] == value, flag
    assert "--no-report" in argv and "--redo-status" not in argv and "--planner" not in argv
    env = call["env"]
    assert (env["C3D_JUDGE__THINKING"], env["C3D_VARIANT"], env["C3D_RENDER__BLENDER_GPU_SLOTS"], env["PYTHONUNBUFFERED"]) == \
        ("medium", "argon", "5", "1")
    assert call["cwd"] == str(loop.REPO) and call["start_new_session"] is True
    run = json.loads((cfg.iter_dir(0) / "run.json").read_text())
    assert run["children"]["scene_threejs"]["pid"] == 4242 and run["children"]["scene_blender"]["returncode"] == 0
    assert run["wall_s"] >= 0 and (cfg.iter_dir(0) / "run_scene_threejs.log").read_text().startswith("--- ")
    assert meta["children"]["scene_blender"]["argv"][3].endswith("battery_scene_blender.yaml")
    # --resume re-runs what the weather lost; one language only runs one child
    FakePopen.calls = []
    naps.clear()
    loop.run_iteration(cfg, 0, languages=[BLENDER], resume=True, popen=FakePopen)
    assert len(FakePopen.calls) == 1 and FakePopen.calls[0]["argv"][-2:] == ["--redo-status", "infra_failed"]
    assert naps == [], "one child: nothing to interleave with"
    with pytest.raises(loop.LoopError, match="generate"):
        loop.run_iteration(cfg, 5, popen=FakePopen)
    # the stagger is a loop knob: a loop.json from before it loads with the default, and 0 means no flag
    data = json.loads((cfg.out / loop.LOOP_JSON).read_text())
    del data["stagger_s"]
    (cfg.out / loop.LOOP_JSON).write_text(json.dumps(data))
    assert loop.LoopConfig.load(cfg.out).stagger_s == 40.0
    flat = cfg.model_copy(update={"stagger_s": 0.0})
    assert "--stagger" not in loop.child_argv(flat, THREEJS, Path("b.yaml"), Path("o"), resume=False)
    with pytest.raises(ValueError):
        loop.LoopConfig(name="x", out=cfg.out, variant="argon", languages=[THREEJS], generator="g", judge="j", stagger_s=-1)
    base = loop.child_env(loop.LoopConfig(name="b", out=cfg.out, variant="", languages=[THREEJS], generator="g", judge="j"),
                          base={"C3D_VARIANT": "stale"})
    assert "C3D_VARIANT" not in base, "no variant: the launching shell's cannot leak into the base run"


# --------------------------------------------------------------------------- diagnose
def test_lib_import_scanners_read_the_agents_files(tmp_path: Path):
    py = tmp_path / "zone.py"
    py.write_text("from lib.place import seat\nimport lib.noise\nfrom lib import scatter, motion\nfrom os import path\n"
                  "import math\nfrom .local import x\n")
    assert sorted(set(py_lib_modules(py))) == ["motion", "noise", "place", "scatter"]
    js = tmp_path / "zone.js"
    js.write_text("import { fbm } from '../lib/noise.js';\nimport { finish } from \"./lib/finish.js\";\n"
                  "import * as THREE from 'three';\nimport { x } from '../other/lib/x.js';\n")
    assert js_lib_modules(js) == ["noise", "finish"]
    (tmp_path / "broken.py").write_text("def (:\n")
    assert py_lib_modules(tmp_path / "broken.py") == []


def test_diagnose_reads_records_gates_library_use_and_recommends(cfg: loop.LoopConfig):
    out_lang = cfg.iter_dir(0) / "scene_threejs"
    rows = [
        _scene_run(out_lang, "it00_scene_threejs_r1_01", (0.5, 0.7), placement_errors=2,
                   defects={"floating_asset": True, "flat_ground": False}, caps=["floating_asset"]),
        _scene_run(out_lang, "it00_scene_threejs_r1_02", (0.4, 0.6), placement_errors=1, lib_imports=("noise",),
                   defects={"floating_asset": True}, caps=["floating_asset", "missing_must_acceptance"]),
        _scene_run(out_lang, "it00_scene_threejs_r1_03", (0.6, 0.8), lib_imports=("noise", "finish", "sky", "water")),
        _scene_run(out_lang, "it00_scene_threejs_r1_04", (0.55, 0.65)),
        BenchItemResult(id="it00_scene_threejs_r1_05", tier="medium", status="infra_failed", errors="ModelError: 503 overloaded"),
    ]
    _write_rows(out_lang, rows)
    # a redo archived an older tree beside run 04 (run_bench.archive_attempt): same spec id, sorts
    # after the live run — it must never stand in for it
    _scene_run(out_lang, "it00_scene_threejs_r1_04.attempt1", (0.1, 0.1), placement_errors=5)
    stale = json.loads((out_lang / "runs" / "it00_scene_threejs_r1_04.attempt1" / "record.json").read_text())
    stale["spec"]["id"] = "it00_scene_threejs_r1_04"
    (out_lang / "runs" / "it00_scene_threejs_r1_04.attempt1" / "record.json").write_text(json.dumps(stale))
    d = diagnose_dir("scene_threejs", out_lang)
    assert d.n == 5 and d.by_status == {"infra_failed": 1, "max_rounds": 4} and d.n_scored == 4
    assert d.picked["mean"] == pytest.approx(0.6875) and d.baseline["mean"] == pytest.approx(0.5125)
    assert set(d.criteria_mean) == {"fidelity", "geometry"} and d.criteria_mean["fidelity"] == pytest.approx(0.6875)
    assert d.gate_error_runs["scene_placement/floating"] == 2 and d.gate_error_total["scene_placement/floating"] == 3
    assert d.gate_warn_total["scene_placement/sunken"] == 4 and "scene_placement/summary" not in d.gate_warn_total
    assert d.defect_rates == {"floating_asset": 0.5} and d.caps_fired == {"floating_asset": 2, "missing_must_acceptance": 1}
    assert d.lib_module_hist == {"noise": 4, "finish": 3, "sky": 1, "water": 1}, "src/lib's own imports never count"
    assert d.lib_modules_mean == pytest.approx((2 + 1 + 4 + 2) / 4)
    assert d.metrics["floating_error_rate"] == 0.5 and d.metrics["infra_failed_rate"] == 0.2
    assert d.metrics["missing_must_rate"] == 0.25 and d.metrics["budget_stop_rate"] == 0.0
    fired = {r.rule for r in d.recommendations}
    assert {"floating", "library", "weather", "must"} <= fired and "lighting" not in fired and "budget" not in fired
    # minutes come from the record (RunRecord.minutes, THE number), never the row: make_fake_run's
    # plan step 24 s active + two 12 s rounds = 0.8 min, whatever the row says
    assert d.stop_reasons == {"max_rounds": 4} and d.cost_total == pytest.approx(6.0) and d.minutes_mean == pytest.approx(0.8)
    assert d.top_issues[0][0] == "geometry" and d.top_issues[0][1] == "Leg"
    # the command writes both files, every language in one diagnosis
    res = loop.diagnose_iteration(cfg, 0)
    assert set(res) == {"scene_threejs"}  # scene_blender never ran: skipped with a warning
    md = (cfg.iter_dir(0) / "diagnosis.md").read_text()
    assert "RECOMMENDATIONS" in md and "placement recipe" in md and "scene_placement/floating | 2 | 3 | 0" in md
    data = json.loads((cfg.iter_dir(0) / "diagnosis.json").read_text())
    assert data["scene_threejs"]["n"] == 5 and len(data["scene_threejs"]["runs"]) == 5
    with pytest.raises(loop.LoopError):
        loop.diagnose_iteration(cfg, 7)


# --------------------------------------------------------------------------- compare
def _stats(deltas: dict[str, float]) -> PairedStats:
    return PairedStats(harness_arm="a", oneshot_arm="b", n=len(deltas), deltas=deltas)


def test_the_loop_verdict_is_decide_plus_the_gate_veto():
    up = {f"p{i}": d for i, d in enumerate((0.10, 0.11, 0.09, 0.12))}
    assert loop_verdict(_stats(up), 0.0)[0] == "keep"
    assert loop_verdict(_stats(up), None)[0] == "keep"
    assert loop_verdict(_stats({k: -v for k, v in up.items()}), 0.0)[0] == "revert"
    assert loop_verdict(_stats({"p0": 0.25, "p1": 0.01, "p2": 0.0, "p3": 0.02}), 0.0)[0] == "inconclusive"
    assert loop_verdict(_stats({"p0": 0.3, "p1": 0.3}), 0.0)[0] == "inconclusive", "two pairs decide nothing"
    decision, reason = loop_verdict(_stats(up), 1.5)
    assert decision == "revert" and reason.startswith("gate veto") and "score said better" in reason
    assert loop_verdict(_stats(up), 1.0)[0] == "keep", "a rise AT the limit is not a veto"


def test_compare_pairs_by_id_and_the_placement_veto_overrides_the_score(cfg: loop.LoopConfig):
    a, b = cfg.iter_dir(1) / "scene_threejs", cfg.iter_dir(0) / "scene_threejs"
    ids = [f"it00_scene_threejs_r1_{k:02d}" for k in range(1, 5)]
    _write_rows(b, [_scene_run(b, pid, (0.4, 0.6)) for pid in ids] + [_scene_run(b, "it00_scene_threejs_r1_09", (0.4, 0.6))])
    _write_rows(a, [_scene_run(a, pid, (0.4, 0.6 + 0.1 * (i + 1) / (i + 1))) for i, pid in enumerate(ids)])
    c = compare_dirs("scene_threejs", a, b)
    assert c.paired.n == 4 and c.dropped_ids == ["it00_scene_threejs_r1_09"]
    assert c.paired.mean_delta == pytest.approx(0.1) and c.decision == "keep"
    assert c.criteria_delta == {"fidelity": pytest.approx(0.1), "geometry": pytest.approx(0.1)}
    assert c.placement_rise == 0.0 and c.gate_error_delta == {} and c.cost_delta == 0.0
    # the same scores, but the candidate's scenes gain two floating ERRORs each: the gate wins
    a2 = cfg.iter_dir(2) / "scene_threejs"
    _write_rows(a2, [_scene_run(a2, pid, (0.4, 0.7), placement_errors=2) for pid in ids])
    c2 = compare_dirs("scene_threejs", a2, b)
    assert c2.placement_rise == 2.0 and c2.decision == "revert" and "gate veto" in c2.reason
    assert c2.gate_error_delta == {"scene_placement/floating": 8} and PLACEMENT_KINDS[0] == "scene_placement/floating"
    res = loop.compare_iteration(cfg, 2, 0)
    assert res["scene_threejs"].decision == "revert"
    md = (cfg.iter_dir(2) / "compare_scene_threejs.md").read_text()
    assert "**revert**" in md and "| it00_scene_threejs_r1_01 | +0.100 |" in md
    assert json.loads((cfg.iter_dir(2) / "compare_scene_threejs.json").read_text())["paired"]["n"] == 4
    with pytest.raises(loop.LoopError):
        loop.compare_iteration(cfg, 2, 2)


# --------------------------------------------------------------------------- judge-calib
def test_judge_calib_rejudges_each_picked_round_in_fixed_order_and_summarises(cfg: loop.LoopConfig):
    out_lang = cfg.iter_dir(0) / "scene_threejs"
    _write_rows(out_lang, [_scene_run(out_lang, "p1", (0.5, 0.7), placement_errors=2), _scene_run(out_lang, "p2", (0.6, 0.9))])
    seen: list[dict[str, Any]] = []

    def fake_calibrate(run_dirs, **kw):
        seen.append({"run_dirs": run_dirs, **kw})
        pid = Path(run_dirs[0]).name
        mean, std = (0.65, 0.02) if pid == "p1" else (0.88, 0.04)
        row = CalibrationRow(run=pid, round=kw["rounds"][0], kind="refine", gate_errors=2 if pid == "p1" else 0,
                             gate_warnings=1, stored_overall=0.7 if pid == "p1" else 0.9, mean=mean, std=std,
                             uncapped=mean, defect_penalty=0.0, defects_present=["floating_asset"] if pid == "p1" else [],
                             caps=["floating_asset"] if pid == "p1" else [], passed=True, n_used=3, cost_usd=0.05,
                             duration_s=20.0)
        return CalibrationTable(model_id=kw["model_id"], n_samples=kw["n_samples"], fixed_order=True, rows=[row])

    res = loop.judge_calib(cfg, 0, calibrate_fn=fake_calibrate, workers=2)
    assert len(seen) == 2 and {s["rounds"][0] for s in seen} == {1}, "the picked round only"
    assert all(s["fixed_order"] and s["thinking"] == "medium" and s["model_id"] == "gemini:gemini-4-argon"
               and s["n_samples"] == 3 and s["geometry_mode"] is None for s in seen)
    assert all(str(s["out_dir"]).startswith(str(cfg.iter_dir(0) / "judge_calib" / "scene_threejs")) for s in seen)
    s = res["scene_threejs"]
    assert s.rounds == 2 and s.within_sd == pytest.approx(((0.02 ** 2 + 0.04 ** 2) / 2) ** 0.5, abs=1e-4)
    assert s.between_sd == pytest.approx(0.1626, abs=1e-3) and s.snr == pytest.approx(s.between_sd / s.within_sd, abs=1e-2)
    assert s.defects == {"floating_asset": 1} and s.caps == {"floating_asset": 1} and s.cost_usd == pytest.approx(0.1)
    assert s.mean_stored == pytest.approx(0.8) and s.pearson_errors_vs_score is None, "two rounds: no correlation"
    md = (cfg.iter_dir(0) / "judge_calib_scene_threejs.md").read_text()
    assert "within-sd" in md and "gemini:gemini-4-argon" in md
    assert json.loads((cfg.iter_dir(0) / "judge_calib_scene_threejs.json").read_text())["thinking"] == "medium"
    res2 = loop.judge_calib(cfg, 0, model_id="gemini:other", thinking="high", n_samples=5, calibrate_fn=fake_calibrate)
    assert seen[-1]["model_id"] == "gemini:other" and seen[-1]["thinking"] == "high" and res2["scene_threejs"].n_samples == 5


# --------------------------------------------------------------------------- accept / rollback / status
def test_accept_bumps_snapshots_and_rollback_restores(cfg: loop.LoopConfig, overlay: loop.VariantPaths, tmp_path: Path):
    data_dir = tmp_path / "data"
    (cfg.out / loop.LOOP_JSON).unlink()
    loop.init(cfg, paths=overlay)
    it1 = cfg.iter_dir(1)
    it1.mkdir(parents=True)
    (it1 / "battery_scene_threejs.yaml").write_text("name: x\n")
    (it1 / "compare_scene_threejs.json").write_text(json.dumps({"decision": "keep"}))
    # the iteration-1 edit of the overlay
    (overlay.prompts_root / "scene_threejs").mkdir()
    (overlay.prompts_root / "scene_threejs" / "system.md").write_text("# seat everything\n")
    (overlay.skills_root / "routes.toml").write_text('[[boost]]\nskill = "c3d-scene-placement"\ndelta = 20\n')
    before = overlay.overlay_hash()
    row = loop.accept(cfg, 1, note="placement recipe", paths=overlay, data_dir=data_dir)
    assert (row.decision, row.version_from, row.version_to, row.note) == ("accept", 1, 2, "placement recipe")
    assert row.verdicts == {"scene_threejs": "keep"} and row.languages == ["scene_threejs", "scene_blender"]
    assert row.variant_hash == overlay.overlay_hash() != before, "the bump is part of the hash"
    assert row.battery_sha and len(row.battery_sha) == 12 and row.cost_usd == 0.0
    assert overlay.version() == 2
    toml = overlay.toml.read_text()
    assert "# the loop bumps it on accept" in toml and "version = 2" in toml and "[knobs]" in toml
    snap2 = cfg.out / "snapshots" / "argon_v2"
    assert (snap2 / "prompts" / "scene_threejs" / "system.md").is_file() and "delta = 20" in (snap2 / "skills" / "routes.toml").read_text()
    assert json.loads((snap2 / "snapshot.json").read_text())["hash"] == row.variant_hash, "one number for the snapshot and the row"
    ledger = loop.ledger_path(cfg, data_dir)
    assert ledger == data_dir / "t_loop" / "ledger.jsonl" and len(loop.ledger_rows(ledger)) == 1
    # iteration 2 edits, then a rollback: the edits go, version 2 stays
    (overlay.prompts_root / "scene_threejs" / "system.md").write_text("# a worse idea\n")
    (overlay.prompts_root / "scene_threejs" / "extra.md").write_text("x\n")
    it2 = cfg.iter_dir(2)
    it2.mkdir()
    row2 = loop.rollback(cfg, 2, note="worse", paths=overlay, data_dir=data_dir)
    assert (row2.decision, row2.version_from, row2.version_to) == ("rollback", 2, 2)
    assert (overlay.prompts_root / "scene_threejs" / "system.md").read_text() == "# seat everything\n"
    assert not (overlay.prompts_root / "scene_threejs" / "extra.md").exists() and overlay.version() == 2
    # rolling back the accepted iteration itself un-accepts it: v1 comes back, the system.md overlay and boost gone
    row3 = loop.rollback(cfg, 1, paths=overlay, data_dir=data_dir)
    assert (row3.version_from, row3.version_to) == (2, 1) and overlay.version() == 1
    assert not (overlay.prompts_root / "scene_threejs").exists() and (overlay.skills_root / "routes.toml").read_text() == "# no rows yet\n"
    rows = loop.ledger_rows(ledger)
    assert [r.decision for r in rows] == ["accept", "rollback", "rollback"] and rows[0].ts < rows[-1].ts
    text = loop.status_text(cfg, paths=overlay, data_dir=data_dir)
    assert "variant argon v1" in text and "| 1 | accept | 1→2 |" in text and "scene_threejs=keep" in text
    with pytest.raises(loop.LoopError, match="snapshot"):
        loop.rollback(loop.LoopConfig(name="other", out=tmp_path / "o", variant="argon", languages=[THREEJS],
                                      generator="g", judge="j"), 1, paths=overlay, data_dir=data_dir)
    stray = loop.VariantPaths(name="argon", prompts_root=tmp_path / "not_an_overlay", skills_root=overlay.skills_root)
    (tmp_path / "not_an_overlay").mkdir()
    with pytest.raises(loop.LoopError, match="refusing"):
        stray.restore_from(cfg.out / "snapshots" / "argon_v1")


def test_the_snapshot_hash_is_the_ledger_hash_not_the_loops_own(cfg: loop.LoopConfig, overlay: loop.VariantPaths,
                                                               tmp_path: Path, monkeypatch):
    """snapshot.json, the ledger row and the iteration's run records must say ONE number — the variant
    layer's ``variant_hash`` (README.md left out) — so a snapshot pairs with the runs it was measured on;
    the loop's own ``overlay_hash`` (every file) is only the fallback for a tree the layer does not know."""

    class FakeVariants:
        """The D105 API, pointed at the tmp overlay: it knows the tree and hashes it its own way."""

        def variant_info(self, name: str) -> Any:
            return NS(name=name, version=1)

        def variant_root(self, name: str) -> Path:
            return overlay.prompts_root

        def variant_hash(self, name: str) -> str:
            return "feedfacecafe"

    monkeypatch.setattr(loop, "_variants_api_or_none", lambda: FakeVariants())
    assert overlay.ledger_hash() == "feedfacecafe" != overlay.overlay_hash()
    snap = overlay.snapshot_to(tmp_path / "snap")
    assert json.loads((snap / "snapshot.json").read_text())["hash"] == "feedfacecafe"
    (cfg.out / loop.LOOP_JSON).unlink()
    loop.init(cfg, paths=overlay)
    assert json.loads((cfg.out / "snapshots" / "argon_v1" / "snapshot.json").read_text())["hash"] == "feedfacecafe"
    cfg.iter_dir(1).mkdir()
    row = loop.accept(cfg, 1, paths=overlay, data_dir=tmp_path / "data")
    assert row.variant_hash == "feedfacecafe"
    assert json.loads((cfg.out / "snapshots" / "argon_v2" / "snapshot.json").read_text())["hash"] == row.variant_hash
    # a tree the layer does not know (another prompts_root): the fallback, and it is the same number
    # in the row and the snapshot
    stray = loop.VariantPaths(name="argon", prompts_root=tmp_path / "elsewhere" / "variants" / "argon",
                              skills_root=overlay.skills_root)
    stray.prompts_root.mkdir(parents=True)
    (stray.prompts_root / "variant.toml").write_text('[variant]\nname = "argon"\nversion = 1\n')
    assert stray.ledger_hash() == stray.overlay_hash() != "feedfacecafe"
    assert json.loads((stray.snapshot_to(tmp_path / "stray") / "snapshot.json").read_text())["hash"] == stray.overlay_hash()


def test_the_shipped_overlays_snapshot_hash_is_the_run_records_hash(tmp_path: Path):
    """The real ``argon`` overlay, read-only (``snapshot_to`` only copies): the snapshot's number IS
    ``prompts.variants.variant_hash`` — what a run record carries — and the loop-private fingerprint
    differs from it by the README.md the overlay ships."""
    real = loop.VariantPaths.default("argon")
    data = json.loads((real.snapshot_to(tmp_path / "real") / "snapshot.json").read_text())
    assert data["hash"] == real.ledger_hash() == variant_hash("argon") and len(data["hash"]) == 12
    if any(rel.endswith("/README.md") for rel, _ in real.files()):
        assert real.overlay_hash() != data["hash"], "README.md is in the tree but never in the record's hash"


def test_init_and_accept_refuse_a_skills_overlay_a_run_would_degrade(cfg: loop.LoopConfig, overlay: loop.VariantPaths,
                                                                     tmp_path: Path):
    """A run never refuses a broken overlay — ``variants_table`` logs a routes.toml that does not
    type-check and routes with the BASE table, ``iter_skills`` skips an invalid bundle — so the loop
    must, BEFORE a version, a snapshot or a ledger row claims the overlay was measured."""
    data_dir = tmp_path / "data"
    (cfg.out / loop.LOOP_JSON).unlink()
    routes = overlay.skills_root / "routes.toml"
    routes.write_text('[[route]]\nrule = "V1"\nskill = "c3d-scene-materials"\npriority = "85"\ntracks = ["scene"]\n')
    with pytest.raises(loop.LoopError, match="routes.toml does not type-check.*priority must be an integer"):
        loop.init(cfg, paths=overlay)
    assert not (cfg.out / loop.LOOP_JSON).exists() and not (cfg.out / "snapshots").exists(), "refused before anything was written"
    routes.write_text("# no rows yet\n")
    loop.init(cfg, paths=overlay)
    cfg.iter_dir(1).mkdir(parents=True)

    def refused(pattern: str) -> None:
        with pytest.raises(loop.LoopError, match=pattern):
            loop.accept(cfg, 1, paths=overlay, data_dir=data_dir)
        assert overlay.version() == 1 and not (cfg.out / "snapshots" / "argon_v2").exists()
        assert not loop.ledger_path(cfg, data_dir).exists()

    # a rule id the base table already uses; then an overlay bundle the run would skip: a frontmatter
    # naming another bundle, then one that parses but fails the library's standards
    routes.write_text('[[route]]\nrule = "R1"\nskill = "c3d-scene-materials"\npriority = 85\ntracks = ["scene"]\n')
    refused("already taken")
    routes.write_text('[[boost]]\nskill = "c3d-scene-materials"\ndelta = 20\n')
    bundle = overlay.skills_root / "c3d-scene-placement"
    bundle.mkdir()
    (bundle / "SKILL.md").write_text("---\nname: c3d-other\ndescription: x\n---\n\nbody\n")
    refused(r"skill bundle c3d-scene-placement .* is invalid .*directory name")
    (bundle / "SKILL.md").write_text("---\nname: c3d-scene-placement\ndescription: x\n---\n\nbody\n")
    refused("metadata.verified")
    # fixed: accept goes through, and the snapshot carries the bundle
    (bundle / "SKILL.md").write_text(VALID_SKILL)
    row = loop.accept(cfg, 1, paths=overlay, data_dir=data_dir)
    assert row.version_to == 2 and (cfg.out / "snapshots" / "argon_v2" / "skills" / "c3d-scene-placement" / "SKILL.md").is_file()
    assert overlay.preflight() is None and len(loop.ledger_rows(loop.ledger_path(cfg, data_dir))) == 1


def test_accept_needs_the_iteration_and_a_version_line(cfg: loop.LoopConfig, overlay: loop.VariantPaths, tmp_path: Path):
    with pytest.raises(loop.LoopError, match="does not exist"):
        loop.accept(cfg, 3, paths=overlay, data_dir=tmp_path / "d")
    overlay.toml.write_text('[variant]\nname = "argon"\n')
    with pytest.raises(loop.LoopError):
        overlay.version()
    overlay.toml.write_text('[variant]\nname = "argon"\nversion = 3\n[knobs]\nversion = 9\n')
    overlay.set_version(4)
    assert overlay.toml.read_text() == '[variant]\nname = "argon"\nversion = 4\n[knobs]\nversion = 9\n', "only [variant].version moves"


# --------------------------------------------------------------------------- CLI
def test_the_cli_dispatches_and_refuses_without_a_loop(tmp_path: Path, capsys, monkeypatch):
    assert loop.main(["status", "--out", str(tmp_path / "none")]) == 2
    assert "bench.loop init" in capsys.readouterr().err
    out = tmp_path / "l"
    monkeypatch.setattr(loop, "_variants_api_or_none", lambda: None)
    assert loop.main(["init", "--name", "t", "--out", str(out), "--variant", "argon", "--languages", "scene_threejs",
                      "--parallel", "3", "--stagger", "5", "--rounds", "1", "--max-minutes", "20", "--n", "4"]) == 0
    c = loop.LoopConfig.load(out)
    assert (c.parallel, c.stagger_s, c.rounds, c.max_minutes, c.n, c.languages) == (3, 5.0, 1, 20.0, 4, [THREEJS])
    assert c.out == out.resolve() and c.judge_thinking == "medium"
    assert loop.main(["init", "--name", "t", "--out", str(out), "--variant", "argon"]) == 2, "one loop per --out"
    assert loop.main(["status", "--out", str(out)]) == 0
    assert "loop t: variant argon" in capsys.readouterr().out


# =========================================================================== the scene path, pinned
#: sha256[:16] of the scene batteries `generate` wrote BEFORE the loop learned the other tracks
#: (main 9a8333e0, FakePlanner, the `cfg` fixture's knobs): iteration 0 at rung 1, iteration 2 at rung 2.
#: The multi-track change must leave the scene path byte-identical — same ids, same caches, same files.
SCENE_GOLDEN = {
    "iter_00/battery_scene_blender.yaml": "e86879fc2fee3a52",
    "iter_00/battery_scene_threejs.yaml": "bb12ccfa9863484f",
    "iter_02/battery_scene_blender.yaml": "311ce859a978527d",
    "iter_02/battery_scene_threejs.yaml": "f1c5102f944ff60d",
}


def test_the_scene_generate_path_is_byte_identical_to_before_the_other_tracks(cfg: loop.LoopConfig):
    import hashlib

    fake = FakePlanner()
    loop.generate_iteration(cfg, 0, rung=1, chat_model=fake)
    loop.generate_iteration(cfg, 2, rung=2, chat_model=fake)
    got = {rel: hashlib.sha256((cfg.out / rel).read_bytes()).hexdigest()[:16] for rel in SCENE_GOLDEN}
    assert got == SCENE_GOLDEN
    meta = json.loads((cfg.iter_dir(2) / "generate.json").read_text())
    assert sorted(meta) == ["batteries", "generated_at", "ids", "iteration", "languages", "mix", "n", "rung"], \
        "the scene generate.json keeps its keys: no track / tiers / sources"
    assert sorted(p.name for p in (cfg.out / "prompts").iterdir()) == [
        "rung_1_scene_blender.yaml", "rung_1_scene_threejs.yaml", "rung_2_scene_blender.yaml", "rung_2_scene_threejs.yaml"]


# =========================================================================== the other tracks
BLENDER_OBJ, THREEJS_OBJ, CADQUERY = Language.BLENDER, Language.THREEJS, Language.CADQUERY


def _battery_file(path: Path, track: str, language: str, prompts: list[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"name": path.stem, "track": track, "language": language, "prompts": prompts}))
    return path


def _p(pid: str, tier: str, text: str, *, must: int = 2, **kw: Any) -> dict[str, Any]:
    return {"id": pid, "tier": tier, "category": "furniture", "prompt": text,
            "must_have": [f"item {i}" for i in range(must)], **kw}


@pytest.fixture
def sources(tmp_path: Path) -> list[Path]:
    """Two small static_object batteries: a duplicate by text (b_med_1 = a_med_1, case and spaces
    aside), an id collision (b's a_hard_1 is another prompt), one three.js-only prompt, an easy one,
    a relative reference, and one medium with more acceptance items than the rest (the hardest)."""
    (tmp_path / "src" / "refs").mkdir(parents=True)
    (tmp_path / "src" / "refs" / "stool.png").write_bytes(b"png")
    a = _battery_file(tmp_path / "src" / "a.yaml", "static_object", "blender", [
        _p("a_easy_1", "easy", "a plain cube"),
        _p("a_med_1", "medium", "a wooden stool with three legs", references=["refs/stool.png"]),
        _p("a_med_2", "medium", "a ceramic teapot"),
        _p("a_hard_1", "hard", "a rolltop desk with pigeonholes"),
        _p("a_hard_2", "hard", "a penny-farthing bicycle"),
        _p("a_hard_3", "hard", "a lattice dome pavilion", language="threejs"),
    ])
    b = _battery_file(tmp_path / "src" / "b.yaml", "static_object", "blender", [
        _p("b_med_1", "medium", "  A wooden stool   with three LEGS "),
        _p("a_hard_1", "hard", "a completely different prompt under a taken id"),
        _p("b_hard_1", "hard", "a drill press on a cast base"),
        _p("b_med_3", "medium", "a brass desk lamp", must=5),
    ])
    return [a, b]


@pytest.fixture
def ocfg(tmp_path: Path, sources: list[Path]) -> loop.LoopConfig:
    c = loop.LoopConfig(name="t_static", out=tmp_path / "loop_static", variant="argon", track="static_object",
                        languages=[BLENDER_OBJ, THREEJS_OBJ], source=[str(p) for p in sources],
                        generator="agy:gemini-4-argon", judge="gemini:gemini-4-argon", parallel=2, rounds=1,
                        max_minutes=10.0, n=4)
    c.save()
    return c


def test_init_validates_the_languages_by_track_and_needs_sources_off_the_scene_track(
        tmp_path: Path, sources: list[Path], capsys, monkeypatch):
    src = [str(p) for p in sources]
    ok = loop.LoopConfig(name="s", out=tmp_path, variant="argon", track="static_object", languages=[BLENDER_OBJ],
                         source=src, generator="g", judge="j")
    assert ok.track is loop.Track.STATIC_OBJECT
    for bad in ({"track": "static_object", "languages": [BLENDER], "source": src},        # scene_blender: not static
                {"track": "static_object", "languages": [BLENDER_OBJ]},                  # no source
                {"track": "scene", "languages": [THREEJS], "source": src},               # scene generates its own
                {"track": "graphics", "languages": [Language.URDF_BLENDER], "source": src}):
        with pytest.raises(ValueError):
            loop.LoopConfig(name="s", out=tmp_path, variant="argon", generator="g", judge="j", **bad)
    # the running scene loop's loop.json (written before `source` and `stagger_s` existed) loads as a scene loop
    old = tmp_path / "old_scene"
    old.mkdir()
    (old / loop.LOOP_JSON).write_text(json.dumps({
        "schema_version": 1, "name": "argon_scene", "out": str(old), "variant": "argon",
        "languages": ["scene_threejs", "scene_blender"], "generator": "agy:gemini-4-argon",
        "judge": "gemini:gemini-4-argon", "judge_thinking": "medium", "planner": "", "parallel": 15, "rounds": 1,
        "max_minutes": 90.0, "n": 15, "track": "scene", "created_at": "2026-10-15T09:14:55.651617+00:00"}))
    sc = loop.LoopConfig.load(old)
    assert sc.track is loop.Track.SCENE and sc.source == [] and sc.languages == [THREEJS, BLENDER]
    # --source: a bare name resolves under bench/prompts/ and is stored relative to eval/; a file
    # outside eval/ stays absolute; a missing one is refused
    assert loop.parse_sources("compare_v4.yaml, bench/prompts/graphics_v1.yaml") == [
        "bench/prompts/compare_v4.yaml", "bench/prompts/graphics_v1.yaml"]
    assert loop.parse_sources(f"{sources[0]},{sources[0]}") == [str(sources[0].resolve())]
    with pytest.raises(loop.LoopError, match="no such battery"):
        loop.parse_sources("nope_v9.yaml")
    # the CLI: --track decides the default languages; a scene language on an object track is exit 2
    monkeypatch.setattr(loop, "_variants_api_or_none", lambda: None)
    out = tmp_path / "cli"
    assert loop.main(["init", "--name", "s", "--out", str(out), "--variant", "argon", "--track", "static_object",
                      "--source", ",".join(src)]) == 0
    c = loop.LoopConfig.load(out)
    assert c.languages == [BLENDER_OBJ, CADQUERY, THREEJS_OBJ] and c.source == [str(p.resolve()) for p in sources]
    assert loop.main(["init", "--name", "s", "--out", str(tmp_path / "cli2"), "--variant", "argon",
                      "--track", "static_object", "--languages", "scene_blender", "--source", src[0]]) == 2
    assert loop.main(["init", "--name", "s", "--out", str(tmp_path / "cli3"), "--variant", "argon",
                      "--track", "articulated_object"]) == 2
    assert "--source is required" in capsys.readouterr().err
    assert not (tmp_path / "cli2").exists() and not (tmp_path / "cli3").exists()
    assert loop.main(["status", "--out", str(out)]) == 0
    assert "track static_object" in capsys.readouterr().out


def test_generate_from_sources_dedupes_routes_and_selects_by_rung(ocfg: loop.LoopConfig, sources: list[Path], caplog):
    from bench._loop_sources import SOURCE_RUNGS, stable_rank

    def by_hash(ids: list[str]) -> list[str]:
        return sorted(ids, key=lambda i: stable_rank(ocfg.name, i))

    paths = loop.generate_iteration(ocfg, 0, rung=1)
    blender, threejs = Battery.load(paths[BLENDER_OBJ]), Battery.load(paths[THREEJS_OBJ])
    assert (blender.track.value, blender.language, threejs.language) == ("static_object", BLENDER_OBJ, THREEJS_OBJ)
    assert blender.name == "t_static_it00_blender"
    medium = by_hash(["a_med_1", "a_med_2", "b_med_3"])
    # rung 1: every medium first (hash order), then the hash-first hard of what the language may run
    assert [p.id for p in blender.prompts] == medium + by_hash(["a_hard_1", "a_hard_2", "b_hard_1"])[:1]
    assert [p.id for p in threejs.prompts] == medium + by_hash(["a_hard_1", "a_hard_2", "a_hard_3", "b_hard_1"])[:1]
    pooled = {p.id: p for b in (blender, threejs) for p in b.prompts}
    assert "b_med_1" not in pooled and "a_easy_1" not in pooled, "the duplicate by text and the easy tier never run"
    stool = next(p for p in blender.prompts if p.id == "a_med_1")
    assert stool.references == [str((sources[0].parent / "refs" / "stool.png").resolve())], "made absolute"
    meta = json.loads((ocfg.iter_dir(0) / "generate.json").read_text())
    assert meta["track"] == "static_object" and meta["rung"] == 1 and meta["n"] == 4
    assert meta["sources"] == ocfg.source and meta["tiers"] == [["medium", "hash"], ["hard", "hash"]]
    assert meta["pool"] == {"size": 8, "duplicates": ["b_med_1"], "id_collisions": ["a_hard_1"]}
    assert meta["tier_counts"]["blender"] == {"medium": 3, "hard": 1} and meta["short"] == {}
    assert meta["ids"]["blender"] == [p.id for p in blender.prompts]
    # the next iteration at the same rung: the same ids (the pairs), and the rung carries over
    assert loop.default_rung(ocfg, 1) == 1
    loop.generate_iteration(ocfg, 1)
    again = Battery.load(ocfg.iter_dir(1) / "battery_blender.yaml")
    assert [p.model_dump() for p in again.prompts] == [p.model_dump() for p in blender.prompts], "same ids, same prompts"
    # rung 2: hard first, then the HARDEST medium (b_med_3: five acceptance items); three.js has four hard
    loop.generate_iteration(ocfg, 2, rung=2)
    b2 = [p.id for p in Battery.load(ocfg.iter_dir(2) / "battery_blender.yaml").prompts]
    t2 = Battery.load(ocfg.iter_dir(2) / "battery_threejs.yaml").prompts
    assert b2 == by_hash(["a_hard_1", "a_hard_2", "b_hard_1"]) + ["b_med_3"]
    assert {p.tier for p in t2} == {"hard"} and len(t2) == 4
    assert next(p for p in t2 if p.id == "a_hard_3").language is THREEJS_OBJ, "the per-prompt language rides along"
    assert next(p for p in t2 if p.id == "a_hard_1").prompt == "a rolltop desk with pigeonholes", "the first id wins"
    # rung 3 and above: hard only — blender holds three, n is four: a shorter battery, said out loud
    with caplog.at_level(logging.WARNING):
        loop.generate_iteration(ocfg, 3, rung=5)
    meta3 = json.loads((ocfg.iter_dir(3) / "generate.json").read_text())
    assert meta3["short"] == {"blender": {"wanted": 4, "got": 3}} and "the battery runs 3" in caplog.text
    assert meta3["tiers"] == [["hard", "hash"]] and SOURCE_RUNGS[3] == SOURCE_RUNGS[max(SOURCE_RUNGS)]
    assert [p.id for p in Battery.load(ocfg.iter_dir(3) / "battery_blender.yaml").prompts] == b2[:3]
    assert not (ocfg.out / "prompts").exists(), "no planner, no rung cache: the batteries are the cache"
    with pytest.raises(loop.LoopError):
        loop.generate_iteration(ocfg, 4, rung=9)
    # a source battery of another track is refused
    wrong = _battery_file(sources[0].parent / "scene.yaml", "scene", "scene_threejs", [_p("s1", "hard", "a quay")])
    bad = ocfg.model_copy(update={"source": [*ocfg.source, str(wrong)]})
    with pytest.raises(loop.LoopError, match="scene battery"):
        loop.generate_iteration(bad, 5, rung=1)


def test_graphics_sources_route_an_unmarked_prompt_to_its_batterys_language(tmp_path: Path):
    """graphics_v1/v2: "per-prompt `language: opengl_python` marks the raw-OpenGL prompts (battery
    default is glsl_shader)" — an unmarked shader brief never lands in the opengl_python battery."""
    glsl = _battery_file(tmp_path / "g.yaml", "graphics", "glsl_shader", [
        _p("glsl_rain", "medium", "neon rain on a window"),
        _p("glsl_sea", "hard", "a raymarched sea at dusk"),
        _p("ogl_cubes", "hard", "instanced spinning cubes", language="opengl_python"),
    ])
    cfg = loop.LoopConfig(name="t_gfx", out=tmp_path / "loop_gfx", variant="argon", track="graphics",
                          languages=[Language.GLSL_SHADER, Language.OPENGL_PYTHON], source=[str(glsl)],
                          generator="g", judge="j", n=4)
    paths = loop.generate_iteration(cfg, 0, rung=1)
    by_lang = {lang: [p.id for p in Battery.load(path).prompts] for lang, path in paths.items()}
    assert sorted(by_lang[Language.GLSL_SHADER]) == ["glsl_rain", "glsl_sea"]
    assert by_lang[Language.OPENGL_PYTHON] == ["ogl_cubes"]
    assert {p.language for p in Battery.load(paths[Language.GLSL_SHADER]).prompts} == {Language.GLSL_SHADER}


def _object_run(out_lang: Path, pid: str, scores: tuple[float, float], findings: Sequence[tuple[str, str, str]] = (),
                *, language: Language = BLENDER_OBJ, ran: Sequence[str] = ("connectivity", "contract")) -> BenchItemResult:
    """An object run under ``out_lang/runs/<pid>``: make_fake_run's record + typed gate findings
    ``(gate, severity, data.kind)`` on the picked round (round 1), and its bench row.  The gates in
    ``ran`` report (passed when no finding names them), as a build that produced a mesh does."""
    ws, _ = make_fake_run(out_lang / "runs", pid, language=language, generator="agy:gemini-4-argon", scores=scores)
    data = json.loads(ws.record_path.read_text())
    by_gate: dict[str, list[dict[str, Any]]] = {g: [] for g in ran}
    for gate, sev, kind in findings:
        by_gate.setdefault(gate, []).append({"gate": gate, "severity": sev, "target": "Leg_1", "message": f"{kind} here",
                                             "data": {"kind": kind}})
    data["rounds"][1]["gates"] += [{"gate": g, "passed": not fs, "findings": fs} for g, fs in by_gate.items()]
    data["extra"] = {"stop_reason": "max_rounds"}
    ws.record_path.write_text(json.dumps(data))
    return BenchItemResult(id=pid, tier="hard", category="furniture", score_baseline=scores[0], score_picked=scores[1],
                           picked_round=1, rounds=2, cost_usd=1.0, minutes=10.0, status="max_rounds",
                           workspace=str(ws.root), generator="agy:gemini-4-argon", judge="gemini:gemini-4-argon")


def test_diagnose_reads_the_object_gates_by_typed_kind(ocfg: loop.LoopConfig):
    out_lang = ocfg.iter_dir(0) / "blender"
    _write_rows(out_lang, [
        _object_run(out_lang, "o1", (0.5, 0.7), [("connectivity", "error", "floating"), ("connectivity", "error", "floating"),
                                                 ("contract", "error", "overall_bbox")]),
        _object_run(out_lang, "o2", (0.5, 0.6), [("connectivity", "error", "penetration"),
                                                 ("connectivity", "warn", "stray_islands")]),
        _object_run(out_lang, "o3", (0.4, 0.5), [("contract", "error", "ground_gap"), ("lint:blender", "error", "api_trap"),
                                                 ("contract", "warn", "footprint_offset")]),
        _object_run(out_lang, "o4", (0.6, 0.8)),
        # every build failed: the fallback round ran lint only — out of the connectivity / contract denominators
        _object_run(out_lang, "o5", (0.2, 0.2), ran=()),
    ])
    # a scene run that strayed into the directory (no row) is another track's: never read
    make_fake_run(out_lang / "runs", "stray_scene", language=THREEJS, scores=(0.1, 0.1))
    d = diagnose_dir("blender", out_lang, track=loop.Track.STATIC_OBJECT)
    assert d.track == "static_object" and d.n == 5 and d.n_scored == 5
    assert d.runs[0].gates_run == ["connectivity", "contract", "lint"] and d.runs[4].gates_run == ["lint"]
    m = d.metrics
    assert (m["conn_floating_error_rate"], m["conn_penetration_error_rate"], m["conn_islands_rate"]) == (0.25, 0.25, 0.25)
    assert (m["connectivity_error_rate"], m["contract_error_rate"]) == (0.5, 0.5)
    assert (m["contract_bbox_error_rate"], m["contract_ground_error_rate"], m["contract_missing_error_rate"]) == (0.25, 0.25, 0.0)
    assert m["lint_error_rate"] == 0.2 and m["budget_stop_rate"] == 0.0 and m["infra_failed_rate"] == 0.0
    assert "floating_error_rate" not in m and "lib_modules_mean" not in m and "joint_sweep_error_rate" not in m
    assert d.gate_error_total["connectivity/floating"] == 2 and d.gate_warn_total["contract/footprint_offset"] == 1
    fired = {r.rule for r in d.recommendations}
    assert fired == {"connectivity", "penetration", "islands", "bbox", "ground", "lint"}, fired
    # a WARN-only contract kind (footprint_offset, instance_count) never counts as an ERROR rate
    assert not any("instance" in r.text for r in d.recommendations)
    # the command: the track named in the markdown, no scene-only library section
    loop.diagnose_iteration(ocfg, 0)
    md = (ocfg.iter_dir(0) / "diagnosis.md").read_text()
    assert md.startswith("# t_static — iteration 0 diagnosis (static_object track)") and "track `static_object`" in md
    assert "### library" not in md and "connectivity: contact band" in md and "bbox: dims" in md
    assert json.loads((ocfg.iter_dir(0) / "diagnosis.json").read_text())["blender"]["track"] == "static_object"


def test_the_articulated_and_graphics_rates_and_rules():
    from bench._loop_diag import RunDigest, diagnose_digests

    def run(pid: str, errors: dict[str, int], warns: dict[str, int] | None = None,
            ran: Sequence[str] = ("lint", "connectivity", "contract", "joint_sweep", "motion_direction", "gl_frames")) -> RunDigest:
        return RunDigest(id=pid, status="max_rounds", score_picked=0.5, picked_round=1, gate_errors=errors,
                         gate_warns=warns or {}, gates_run=list(ran))

    art = diagnose_digests("urdf_blender", Path("x"), [
        run("a1", {"joint_sweep/penetration": 2, "lint/urdf_joint": 1}),
        run("a2", {"motion_direction/wrong_direction": 1, "connectivity/floating": 1}),
        run("a3", {}), run("a4", {})], track=loop.Track.ARTICULATED_OBJECT)
    assert art.metrics["joint_sweep_error_rate"] == 0.25 and art.metrics["joint_axis_error_rate"] == 0.5
    assert {"joints", "connectivity"} <= {r.rule for r in art.recommendations} and "sweep" not in {r.rule for r in art.recommendations}
    gfx = diagnose_digests("glsl_shader", Path("x"), [
        run("g1", {"gl_frames/black": 1}), run("g2", {}, {"gl_frames/static": 1}),
        run("g3", {"gl_frames/nan": 1, "lint/syntax": 1}), run("g4", {}),
        run("g5", {}, ran=("lint",))], track=loop.Track.GRAPHICS)   # a render with no frames: no gl_frames report
    assert (gfx.metrics["frame_black_rate"], gfx.metrics["frame_no_motion_rate"], gfx.metrics["frame_broken_rate"]) == (0.25, 0.25, 0.25)
    assert gfx.metrics["lint_error_rate"] == 0.2
    assert {"black", "no_motion", "broken", "lint"} <= {r.rule for r in gfx.recommendations}
    assert "conn_floating_error_rate" not in gfx.metrics and "blown" not in {r.rule for r in gfx.recommendations}


def test_compare_on_an_object_track_vetoes_a_connectivity_contract_rise(ocfg: loop.LoopConfig):
    from bench._loop_diag import compare_digests

    ids = ["a_med_1", "a_med_2", "b_med_3", "b_hard_1"]
    b = ocfg.iter_dir(0) / "blender"
    _write_rows(b, [_object_run(b, pid, (0.4, 0.6)) for pid in ids])
    a = ocfg.iter_dir(1) / "blender"
    _write_rows(a, [_object_run(a, pid, (0.4, 0.7)) for pid in ids])
    c = compare_dirs("blender", a, b, track=loop.Track.STATIC_OBJECT)
    assert c.paired.n == 4 and c.decision == "keep" and c.placement_rise == 0.0 and c.track == "static_object"
    # the same scores, but every candidate run gains a floating part and a bbox ERROR: the gate wins
    a2 = ocfg.iter_dir(2) / "blender"
    _write_rows(a2, [_object_run(a2, pid, (0.4, 0.7), [("connectivity", "error", "floating"),
                                                         ("contract", "error", "part_bbox")]) for pid in ids])
    c2 = compare_dirs("blender", a2, b, track=loop.Track.STATIC_OBJECT)
    assert c2.placement_rise == 2.0 and c2.decision == "revert"
    assert c2.reason.startswith("gate veto: connectivity+contract ERRORs rose by +2.00 per run")
    assert c2.gate_error_delta == {"connectivity/floating": 4, "contract/part_bbox": 4}
    res = loop.compare_iteration(ocfg, 2, 0, languages=[BLENDER_OBJ])
    md = (ocfg.iter_dir(2) / "compare_blender.md").read_text()
    assert res["blender"].decision == "revert" and "connectivity+contract ERRORs Δ +2.00 per run (veto above +1)" in md
    # scene placement kinds mean nothing on an object track, and graphics has no veto at all
    da = {"p": _digest("p", {"scene_placement/floating": 5})}
    db = {"p": _digest("p", {})}
    st = _stats({"p": 0.1})
    assert compare_digests("blender", "a", "b", st, da, db, track=loop.Track.STATIC_OBJECT).placement_rise == 0.0
    g = compare_digests("glsl_shader", "a", "b", st, {"p": _digest("p", {"gl_frames/black": 3})}, db,
                        track=loop.Track.GRAPHICS)
    assert g.placement_rise is None and "gate veto" not in g.reason
    # one noisy object (0 → 14 penetrations, one per part pair) counts OBJECT_VETO_CAP, not 14: no veto alone
    from bench._loop_diag import OBJECT_VETO_CAP
    st15 = _stats({f"p{i}": 0.0 for i in range(15)})
    da15 = {f"p{i}": _digest(f"p{i}", {"connectivity/penetration": 14} if i == 0 else {}) for i in range(15)}
    db15 = {f"p{i}": _digest(f"p{i}", {}) for i in range(15)}
    noisy = compare_digests("blender", "a", "b", st15, da15, db15, track=loop.Track.STATIC_OBJECT)
    assert noisy.placement_rise == round(OBJECT_VETO_CAP / 15, 4) and "gate veto" not in noisy.reason
    # articulated: the pose sweep and a wrong-way joint are in the veto (the judge caps them too)
    art = compare_digests("urdf_blender", "a", "b", st, {"p": _digest("p", {"joint_sweep/penetration": 2,
                                                                            "motion_direction/wrong_direction": 1})},
                          db, track=loop.Track.ARTICULATED_OBJECT)
    assert art.placement_rise == 3.0 and art.reason.startswith("gate veto: connectivity+contract+joint ERRORs")
    assert compare_digests("blender", "a", "b", st, {"p": _digest("p", {"joint_sweep/penetration": 2})}, db,
                           track=loop.Track.STATIC_OBJECT).placement_rise == 0.0
    from bench._loop_diag import render_comparison
    assert "no gate veto on the graphics track" in render_comparison(g)


def _digest(pid: str, errors: dict[str, int]) -> Any:
    from bench._loop_diag import RunDigest

    return RunDigest(id=pid, status="max_rounds", score_picked=0.5, picked_round=1, gate_errors=errors)


def test_a_static_object_loop_end_to_end(ocfg: loop.LoopConfig, overlay: loop.VariantPaths, tmp_path: Path, monkeypatch):
    """init → generate → run → diagnose → compare → judge-calib → accept → status on static_object,
    with fakes for the children (FakePopen), the runs (make_fake_run records) and the re-judge."""
    data_dir = tmp_path / "data"
    (ocfg.out / loop.LOOP_JSON).unlink()
    loop.init(ocfg, paths=overlay)
    assert loop.LoopConfig.load(ocfg.out).source == ocfg.source
    for it in (0, 1):
        loop.generate_iteration(ocfg, it, rung=1 if it == 0 else None)
    monkeypatch.setattr(loop, "install_stop_handlers", lambda: None)
    monkeypatch.setattr(loop, "time", NS(sleep=lambda s: None, monotonic=time.monotonic))
    FakePopen.calls = []
    loop.run_iteration(ocfg, 1, popen=FakePopen)
    assert [c["argv"][3] for c in FakePopen.calls] == [str(ocfg.iter_dir(1) / f"battery_{x}.yaml") for x in ("blender", "threejs")]
    # the children's output, faked: iteration 1 scores higher on every pair with no new gate ERRORs
    for it, up in ((0, 0.0), (1, 0.15)):
        for lang in (BLENDER_OBJ, THREEJS_OBJ):
            ids = json.loads((ocfg.iter_dir(it) / "generate.json").read_text())["ids"][lang.value]
            d = ocfg.iter_dir(it) / lang.value
            _write_rows(d, [_object_run(d, pid, (0.4, 0.6 + up + 0.01 * k), language=lang) for k, pid in enumerate(ids)])
    diag = loop.diagnose_iteration(ocfg, 1)
    assert set(diag) == {"blender", "threejs"} and diag["blender"].n == 4 and diag["blender"].track == "static_object"
    cmp = loop.compare_iteration(ocfg, 1, 0)
    assert {k: v.paired.n for k, v in cmp.items()} == {"blender": 4, "threejs": 4}
    assert {v.decision for v in cmp.values()} == {"keep"}

    def fake_calibrate(run_dirs, **kw):
        pid = Path(run_dirs[0]).name
        row = CalibrationRow(run=pid, round=kw["rounds"][0], kind="refine", gate_errors=0, gate_warnings=0,
                             stored_overall=0.7, mean=0.7, std=0.01, uncapped=0.7, defect_penalty=0.0, defects_present=[],
                             caps=[], passed=True, n_used=3, cost_usd=0.01, duration_s=5.0)
        return CalibrationTable(model_id=kw["model_id"], n_samples=kw["n_samples"], fixed_order=True, rows=[row])

    calib = loop.judge_calib(ocfg, 1, calibrate_fn=fake_calibrate, workers=1)
    assert calib["blender"].rounds == 4, "the object records are read: every picked round re-judged"
    row = loop.accept(ocfg, 1, note="contact band", paths=overlay, data_dir=data_dir)
    assert row.verdicts == {"blender": "keep", "threejs": "keep"} and row.languages == ["blender", "threejs"]
    text = loop.status_text(ocfg, paths=overlay, data_dir=data_dir)
    assert "track static_object" in text and "| 1 | 1 | 2 |" in text and "blender=keep" in text

"""The argon tuning loop: generate → run → diagnose → compare → judge-calib → accept / rollback.

    cd eval
    python -m bench.loop init --name argon_scene --out bench/out/loop_argon --variant argon \\
        --languages scene_threejs,scene_blender --generator agy:gemini-4-argon \\
        --judge gemini:gemini-4-argon --judge-thinking medium --parallel 15 --rounds 2 --max-minutes 90
    python -m bench.loop generate    --out bench/out/loop_argon --iter 0 [--n 15] [--rung 1]
    python -m bench.loop run         --out bench/out/loop_argon --iter 0 [--languages scene_threejs] [--resume]
    python -m bench.loop diagnose    --out bench/out/loop_argon --iter 0
    python -m bench.loop compare     --out bench/out/loop_argon --iter 1 --against 0
    python -m bench.loop judge-calib --out bench/out/loop_argon --iter 1 [--model …] [--thinking medium] [--n 3]
    python -m bench.loop accept      --out bench/out/loop_argon --iter 1 [--note …]
    python -m bench.loop rollback    --out bench/out/loop_argon --iter 1
    python -m bench.loop status      --out bench/out/loop_argon

    # any other track: --track + --source (battery files under bench/prompts/), the rest is the same
    python -m bench.loop init --name argon_static --out bench/out/loop_argon_static --variant argon \\
        --track static_object --languages blender,threejs,cadquery --source compare_v4.yaml \\
        --judge gemini:gemini-4-argon --judge-thinking medium --parallel 8

One iteration = one edit of a per-model prompt/skill VARIANT (``codeverse3d/prompts/variants/<v>/`` +
``codeverse3d/skills/variants/<v>/``, D105) measured on ~15 prompts per language, run in
parallel on the generator under test, judged by ONE fixed judge at ONE thinking level.  ``init``
freezes every knob in ``<out>/loop.json``; everything else reads it, so two iterations never differ
in anything but the variant and (when ``--rung`` moves) the prompt mix.

The track decides where the prompts come from: the scene track GENERATES its briefs (a planner
model on a rung ladder, ``_loop_prompts``); every other track SELECTS from its hand-written
batteries (``--source``, ids verbatim, the rung picks the tier mix: ``_loop_sources``).  Diagnose
and compare read the track's own gates (``_loop_diag``); the rest of the loop does not care.

Layout of ``<out>``::

    loop.json                     the fixed configuration (init)
    prompts/rung_R_<lang>.yaml    a rung's prompts, generated ONCE and reused (stable ids → pairs; scene only)
    snapshots/<v>_v<N>/           the overlay dirs as accepted at version N (init writes v<current>)
    iter_NN/battery_<lang>.yaml   the iteration's battery (a rung mix), generate.json its composition
    iter_NN/<lang>/               one ``bench.run_bench`` output per language (runs/, results.jsonl)
    iter_NN/run.json              child pids, exit codes, wall; run_<lang>.log their stdout
    iter_NN/diagnosis.{md,json}   diagnose;  compare_<lang>.{md,json}  compare;  judge_calib_<lang>.{md,json}

The ledger of decisions lives IN the repo — ``bench/data/loop/<name>/ledger.jsonl`` (format:
``bench/data/loop/README.md``) — because an accepted variant version is a claim about the prompts,
and a claim whose evidence is under the ignored ``bench/out/`` is not reproducible.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
import tomllib
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator

for _p in (Path(__file__).resolve().parents[2] / "harness", Path(__file__).resolve().parents[1]):
    if str(_p) not in sys.path:  # this tree's codeverse3d (harness/) + the `bench` package (eval/)
        sys.path.insert(0, str(_p))

from bench._jsonl import read_jsonl, seal_for_append  # noqa: E402
from bench._loop_diag import (  # noqa: E402
    CalibSummary,
    Comparison,
    Diagnosis,
    calib_summary,
    compare_dirs,
    diagnose_dir,
    digests_for,
    render_calib,
    render_comparison,
    render_diagnosis,
)
from bench._loop_prompts import (  # noqa: E402
    RUNGS,
    GeneratedBattery,
    build_request,
    check_battery,
    contract_lines,
    migrate_camera_lines,
    mix_counts,
    parse_battery,
)
from bench._loop_sources import (  # noqa: E402
    pool_sources,
    route,
    rung_steps,
    rung_table_text,
    select,
)
from bench.run_bench import Battery, BenchPrompt  # noqa: E402
from codeverse3d.config import get_settings  # noqa: E402
from codeverse3d.contracts.common import TRACK_LANGUAGES, Language, Track  # noqa: E402
from codeverse3d.proc import install_stop_handlers, write_json_atomic  # noqa: E402

log = logging.getLogger(__name__)

REPO = Path(__file__).resolve().parents[1]           # eval/ — the children run `python -m bench.run_bench` here
HARNESS = REPO.parent / "harness"                     # the tree whose variant the loop edits
DATA_DIR = REPO / "bench" / "data" / "loop"           # the ledgers (in git)
PROMPTS_DIR = REPO / "bench" / "prompts"              # where --source names resolve
LOOP_JSON = "loop.json"
VARIANT_TOML = "variant.toml"
#: the machine-wide Cycles slots the children share: 30 parallel runs render on ONE GPU, and
#: `Render.blender_gpu_slots` defaults to 2 for an interactive box (D104 measured 0.21-0.64 s/frame
#: on the GPU vs 2.3 s on the CPU, so a third slot is worth more than a third CPU render; five slots fit the
#: memory the renders measured)
BLENDER_GPU_SLOTS = "5"   # 3 → 5 on 2026-10-15: 24 GB of GPU memory sat at 2.7 GB with 30 runs; the owner asked for more parallelism
#: how long a stopped driver waits for a child to record its stop before SIGKILL: the child's own
#: stop handler (`run_bench.main` installs it) unwinds within `proc.STOP_GRACE_S` = 30 s
CHILD_STOP_GRACE_S = 45.0

Thinking = Literal["off", "low", "medium", "high"]


class LoopError(RuntimeError):
    """A misconfiguration or a missing prerequisite — exit 2, never a traceback."""


# --------------------------------------------------------------------------- configuration
class LoopConfig(BaseModel):
    """Everything an iteration runs with, frozen by ``init`` into ``<out>/loop.json``."""

    schema_version: int = 1
    name: str
    out: Path
    variant: str
    languages: list[Language]
    generator: str
    judge: str
    judge_thinking: Thinking = "medium"
    planner: str = Field(default="", description="the battery-generation model; '' = Settings.default_planner")
    parallel: int = Field(default=15, ge=1, description="run_bench workers PER LANGUAGE (the languages run side by side)")
    stagger_s: float = Field(default=40.0, ge=0.0,
                             description="seconds between the launches of each child's first wave of workers "
                                         "(run_bench --stagger); the children themselves launch stagger_s/2 apart")
    rounds: int = Field(default=2, ge=0)
    max_minutes: float = Field(default=90.0, gt=0)
    n: int = Field(default=15, ge=1, description="prompts per battery")   # 30 → 15 (owner, 2026-10-15)
    track: Track = Track.SCENE
    source: list[str] = Field(default_factory=list,
                              description="the battery files a non-scene track selects from (relative to eval/, "
                                          "absolute only outside it); the scene track generates its own and takes none")
    created_at: str = ""

    @model_validator(mode="after")
    def _track_languages(self) -> LoopConfig:
        """``--track`` decides: the languages must be that track's, and every track but scene names
        its source batteries (a loop.json written before ``source`` existed is a scene loop and loads)."""
        allowed = TRACK_LANGUAGES[self.track]
        v = self.languages
        bad = [x.value for x in v if x not in allowed]
        if not v or bad:
            raise ValueError(f"languages must be non-empty {self.track.value} languages {[a.value for a in allowed]}, "
                             f"got {bad or v}")
        if len(set(v)) != len(v):
            raise ValueError(f"duplicate languages: {[x.value for x in v]}")
        if self.track is Track.SCENE and self.source:
            raise ValueError("the scene track generates its own briefs: --source is for the other tracks")
        if self.track is not Track.SCENE and not self.source:
            raise ValueError(f"a {self.track.value} loop selects from battery files: --source is required")
        return self

    def iter_dir(self, iteration: int) -> Path:
        return self.out / f"iter_{iteration:02d}"

    def save(self) -> Path:
        self.out.mkdir(parents=True, exist_ok=True)
        write_json_atomic(self.out / LOOP_JSON, self.model_dump(mode="json"))
        return self.out / LOOP_JSON

    @classmethod
    def load(cls, out: Path | str) -> LoopConfig:
        p = Path(out) / LOOP_JSON
        if not p.is_file():
            raise LoopError(f"no {LOOP_JSON} under {out}: run `bench.loop init --out {out} …` first")
        return cls.model_validate_json(p.read_text())


def parse_languages(text: str) -> list[Language]:
    try:
        return [Language(x.strip()) for x in text.split(",") if x.strip()]
    except ValueError as e:
        raise LoopError(f"unknown language in {text!r}: {e}") from e


def source_path(entry: str) -> Path:
    """A ``LoopConfig.source`` entry on disk (relative entries are relative to eval/)."""
    p = Path(entry)
    return p if p.is_absolute() else REPO / p


def parse_sources(text: str) -> list[str]:
    """``--source a.yaml,bench/prompts/b.yaml,/abs/c.yaml`` → the entries ``LoopConfig.source`` keeps:
    a bare name resolves under ``bench/prompts/``, a relative path under eval/; a file under eval/ is
    stored relative to it (loop.json stays portable), anything else absolute.  A missing file is a
    configuration error here, before init writes anything."""
    out: list[str] = []
    for raw in (x.strip() for x in text.split(",")):
        if not raw:
            continue
        given = Path(raw).expanduser()
        candidates = [given] if given.is_absolute() else [REPO / given, PROMPTS_DIR / given]
        hit = next((c.resolve() for c in candidates if c.is_file()), None)
        if hit is None:
            raise LoopError(f"--source {raw!r}: no such battery file (looked at {[str(c) for c in candidates]})")
        entry = hit.relative_to(REPO.resolve()).as_posix() if hit.is_relative_to(REPO.resolve()) else str(hit)
        if entry not in out:
            out.append(entry)
    return out


# --------------------------------------------------------------------------- the variant overlay
def _variants_api() -> Any:
    """``codeverse3d.prompts.variants`` (Lane V, D105) or a clear error — the loop reads a variant's
    root and hash through it, and `run` hands the name to the children."""
    try:
        from codeverse3d.prompts import variants
    except ImportError as e:
        raise LoopError("this tree has no codeverse3d.prompts.variants (the D105 variant layer): "
                        "the loop's variant plumbing needs it") from e
    missing = [n for n in ("variant_info", "variant_root", "variant_hash") if not hasattr(variants, n)]
    if missing:
        raise LoopError(f"codeverse3d.prompts.variants lacks {missing} (the design §0 contract)")
    return variants


def _variants_api_or_none() -> Any | None:
    try:
        return _variants_api()
    except LoopError as e:
        log.warning("%s", e)
        return None


def _check_variant_name(name: str) -> None:
    """An unknown variant name is a configuration error (exit 2), caught where the layer is present."""
    api = _variants_api_or_none()
    if api is None or not name:
        return
    try:
        api.variant_info(name)
    except ValueError as e:
        raise LoopError(str(e)) from e


@dataclass(frozen=True)
class VariantPaths:
    """The two overlay directories of one variant, and the operations the loop does on them.
    Injectable (tests point them at a tmp tree); :meth:`default` resolves the real ones."""

    name: str
    prompts_root: Path
    skills_root: Path

    @classmethod
    def default(cls, name: str) -> VariantPaths:
        from codeverse3d.prompts import PROMPTS_DIR
        from codeverse3d.skills import skills_dir

        api = _variants_api_or_none()
        prompts_root = Path(api.variant_root(name)) if api is not None else PROMPTS_DIR / "variants" / name
        return cls(name=name, prompts_root=prompts_root, skills_root=skills_dir() / "variants" / name)

    @property
    def toml(self) -> Path:
        return self.prompts_root / VARIANT_TOML

    def version(self) -> int:
        if not self.toml.is_file():
            raise LoopError(f"no {VARIANT_TOML} under {self.prompts_root}: is {self.name!r} a variant?")
        data = tomllib.loads(self.toml.read_text())
        try:
            return int(data["variant"]["version"])
        except (KeyError, TypeError, ValueError) as e:
            raise LoopError(f"{self.toml}: [variant].version is missing or not an integer ({e})") from e

    def set_version(self, new: int) -> None:
        """Rewrite ONLY the ``version = N`` line of the ``[variant]`` table (comments and the other keys
        stay as the author wrote them), and read it back through tomllib before trusting it."""
        out: list[str] = []
        table, done = "", False
        for line in self.toml.read_text().splitlines(keepends=True):
            s = line.strip()
            if s.startswith("[") and s.endswith("]"):
                table = s[1:-1].strip()
            elif table == "variant" and not done and not s.startswith("#") and s.split("=", 1)[0].strip() == "version":
                indent = line[: len(line) - len(line.lstrip())]
                line = f"{indent}version = {new}\n"
                done = True
            out.append(line)
        if not done:
            raise LoopError(f"{self.toml}: no `version = N` line in [variant] to bump")
        self.toml.write_text("".join(out))
        if self.version() != new:
            raise LoopError(f"{self.toml}: wrote version {new} but read back {self.version()}")

    def files(self) -> list[tuple[str, bytes]]:
        """``(relpath, bytes)`` of every overlay file, sorted — ``prompts/…`` and ``skills/…``."""
        out: list[tuple[str, bytes]] = []
        for label, root in (("prompts", self.prompts_root), ("skills", self.skills_root)):
            if not root.is_dir():
                continue
            for p in sorted(root.rglob("*")):
                if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc":
                    out.append((f"{label}/{p.relative_to(root).as_posix()}", p.read_bytes()))
        return out

    def overlay_hash(self) -> str:
        """sha256[:12] over the sorted (relpath, bytes) of EVERY overlay file — the loop's own
        fingerprint, used ONLY as the fallback of :meth:`ledger_hash` for a tree the variant layer
        does not know (the tests' tmp overlays).  It is not the number a run record carries: Lane V's
        ``variant_hash`` leaves ``README.md`` out (documentation, not prompt material), so the two
        differ for the shipped overlay by exactly that file."""
        h = hashlib.sha256()
        for rel, data in self.files():
            h.update(rel.encode("utf-8") + b"\0" + data + b"\0")
        return h.hexdigest()[:12]

    def ledger_hash(self) -> str:
        """THE hash of this overlay: ``prompts.variants.variant_hash`` — what every run record of the
        iteration carries (``extra.variant.hash``, ``SettingsSnapshot.variant_hash``) — whenever these
        paths are the real overlay; :meth:`overlay_hash` only for a tmp tree.  The ledger row and the
        snapshot both write this one, so a snapshot can be paired with the runs it was measured on."""
        api = _variants_api_or_none()
        if api is not None:
            try:
                if Path(api.variant_root(self.name)).resolve() == self.prompts_root.resolve():
                    return str(api.variant_hash(self.name))
            except Exception as e:  # noqa: BLE001 — an unknown name in a tmp tree: fall back
                log.debug("variant_hash(%s) unavailable: %s", self.name, e)
        return self.overlay_hash()

    def preflight(self) -> None:
        """Refuse a broken skills overlay BEFORE it is versioned, snapshotted or written to the ledger.

        At run time the harness degrades silently — ``skills.variants_table`` logs a ``routes.toml``
        that does not type-check and routes with the BASE table, ``iter_skills(strict=False)`` skips
        an invalid overlay bundle — so an iteration whose overlay carries a typo measures the base
        routing, its verdict is about nothing, and nothing would stop ``accept`` recording it as a new
        version.  This is where the loop says no, with the parsers the run uses: ``skills.parse_routes``
        on the overlay's table (what ``variants_table`` calls on that file) and ``skills.validate_bundle``
        on every bundle directory under the overlay.  The first problem is a :class:`LoopError`."""
        from codeverse3d.skills import ROUTES_FILE, bundle_dirs, parse_routes, validate_bundle

        routes = self.skills_root / ROUTES_FILE
        if routes.is_file():
            try:
                parse_routes(routes, variant=self.name)
            except ValueError as e:
                raise LoopError(f"variant {self.name!r}: {ROUTES_FILE} does not type-check (a run would route "
                                f"with the base table): {e}") from e
        for bundle in bundle_dirs(self.skills_root):
            problems = validate_bundle(bundle)
            if problems:
                raise LoopError(f"variant {self.name!r}: skill bundle {bundle.name} ({bundle}) is invalid (a run "
                                f"would skip it): " + "; ".join(problems))

    def snapshot_to(self, dest: Path) -> Path:
        """Copy both overlay dirs under ``dest/{prompts,skills}`` (replacing an older copy).  The
        ``snapshot.json`` beside them carries :meth:`ledger_hash` — the same number as the ledger row
        and the iteration's run records, never the loop-private :meth:`overlay_hash`."""
        if dest.exists():
            shutil.rmtree(dest)
        for label, root in (("prompts", self.prompts_root), ("skills", self.skills_root)):
            if root.is_dir():
                shutil.copytree(root, dest / label, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        (dest / "snapshot.json").write_text(json.dumps({
            "variant": self.name, "version": self.version(), "hash": self.ledger_hash(),
            "taken_at": datetime.now(UTC).isoformat(), "prompts_root": str(self.prompts_root),
            "skills_root": str(self.skills_root)}, indent=2))
        return dest

    def restore_from(self, src: Path) -> None:
        """Make both overlay dirs equal to the snapshot (a part the snapshot lacks is removed)."""
        if not (src / "snapshot.json").is_file():
            raise LoopError(f"{src} is not a loop snapshot (no snapshot.json)")
        for label, root in (("prompts", self.prompts_root), ("skills", self.skills_root)):
            if root.parent.name != "variants" or root.name != self.name:
                raise LoopError(f"refusing to replace {root}: not a variants/<{self.name}> overlay directory")
            if root.exists():
                shutil.rmtree(root)
            if (src / label).is_dir():
                shutil.copytree(src / label, root)


def snapshot_dir(cfg: LoopConfig, version: int) -> Path:
    return cfg.out / "snapshots" / f"{cfg.variant}_v{version}"


# --------------------------------------------------------------------------- init
def init(cfg: LoopConfig, *, paths: VariantPaths | None = None) -> Path:
    """Write ``loop.json`` and snapshot the overlay as it stands (``snapshots/<v>_v<current>``), so the
    first ``rollback`` has a state to return to.  A missing variant layer only costs the snapshot; a
    skills overlay that would not route (:meth:`VariantPaths.preflight`) is refused before anything
    is written."""
    if (cfg.out / LOOP_JSON).is_file():
        raise LoopError(f"{cfg.out / LOOP_JSON} exists: one loop per --out (status / iterations read it)")
    missing = [s for s in cfg.source if not source_path(s).is_file()]
    if missing:
        raise LoopError(f"source batteries not found: {missing}")
    _check_variant_name(cfg.variant)  # before any money is spent
    vp = paths or VariantPaths.default(cfg.variant)
    vp.preflight()
    cfg.created_at = datetime.now(UTC).isoformat()
    p = cfg.save()
    if vp.toml.is_file():
        dest = snapshot_dir(cfg, vp.version())
        if not dest.exists():
            vp.snapshot_to(dest)
    else:
        log.warning("no %s under %s: no snapshot taken", VARIANT_TOML, vp.prompts_root)
    return p


# --------------------------------------------------------------------------- generate
def rung_cache(cfg: LoopConfig, lang: Language, rung: int) -> Path:
    return cfg.out / "prompts" / f"rung_{rung}_{lang.value}.yaml"


def generation_meta(cfg: LoopConfig, iteration: int) -> dict[str, Any] | None:
    p = cfg.iter_dir(iteration) / "generate.json"
    return json.loads(p.read_text()) if p.is_file() else None


def default_rung(cfg: LoopConfig, iteration: int) -> int:
    """No ``--rung``: the previous iteration's (so the pairs are complete), else 1."""
    for i in range(iteration - 1, -1, -1):
        meta = generation_meta(cfg, i)
        if meta is not None:
            return int(meta["rung"])
    return 1


def rung_prompts(cfg: LoopConfig, lang: Language, rung: int, *, iteration: int,
                 chat_model: Any = None) -> list[BenchPrompt]:
    """The rung's prompts for ``lang`` — from the cache, else generated once (one re-ask on a typed
    failure) and cached under ``prompts/rung_R_<lang>.yaml`` with the ids they will keep forever."""
    cache = rung_cache(cfg, lang, rung)
    if cache.is_file():
        data = yaml.safe_load(cache.read_text())
        prompts = [BenchPrompt.model_validate(p) for p in data["prompts"]]
        migrated = migrate_camera_lines(prompts)
        if migrated:
            # a cache written before 2026-10-15 carries the moving-camera line (iteration 0 ran with
            # it): the ids stay — they pair iterations — and only that line moves, in the file too
            data["prompts"] = [p.model_dump(mode="json", exclude_defaults=True) for p in prompts]
            data["camera_lines_migrated"] = {"n": migrated, "at": datetime.now(UTC).isoformat()}
            _write_cache(cache, data)
            log.info("rung %d %s: migrated %d camera line(s) to the fixed-camera form in %s",
                     rung, lang.value, migrated, cache)
        return prompts
    r = RUNGS[rung]
    if chat_model is None:
        from codeverse3d.models.registry import get_chat_model

        chat_model = get_chat_model(cfg.planner or get_settings().backends().planner)
    resp = chat_model.generate(build_request(r, cfg.n, lang))
    battery, problems = parse_battery(resp.text, resp.parsed)
    if battery is not None:
        problems = check_battery(battery, r, cfg.n)
    if problems:
        log.warning("rung %d %s: %d problem(s), re-asking once: %s", rung, lang.value, len(problems), problems[:3])
        previous = resp.text or (json.dumps(resp.parsed) if resp.parsed is not None else "")
        resp = chat_model.generate(build_request(r, cfg.n, lang, previous_text=previous, problems=problems,
                                                 label="loop:generate-reask"))
        battery, problems = parse_battery(resp.text, resp.parsed)
        if battery is not None:
            problems = check_battery(battery, r, cfg.n)
        if problems or battery is None:
            raise LoopError(f"rung {rung} prompts for {lang.value} failed the rung rules after one re-ask:\n- "
                            + "\n- ".join(problems))
    prompts = _bench_prompts(battery, lang, rung, iteration=iteration, n=cfg.n)
    _write_cache(cache, {
        "rung": rung, "language": lang.value, "n": len(prompts), "generated_at_iteration": iteration,
        "model": str(getattr(chat_model, "id", "")), "generated_at": datetime.now(UTC).isoformat(),
        "prompts": [p.model_dump(mode="json", exclude_defaults=True) for p in prompts],
    })
    return prompts


def _write_cache(cache: Path, data: dict[str, Any]) -> None:
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=120))


def _bench_prompts(battery: GeneratedBattery, lang: Language, rung: int, *, iteration: int, n: int) -> list[BenchPrompt]:
    r = RUNGS[rung]
    return [BenchPrompt(id=f"it{iteration:02d}_{lang.value}_r{rung}_{k:02d}", prompt=" ".join(gp.prompt.split()),
                        tier=r.tier, category=gp.category, must_have=contract_lines(gp),
                        tags=[f"rung{rung}", gp.lighting, *gp.features, gp.title])
            for k, gp in enumerate(battery.prompts[:n], 1)]


def _write_battery(path: Path, battery: Battery) -> None:
    data = battery.model_dump(mode="json")
    Battery.model_validate(data)  # what the file will load as, not what we built
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=120))


def generate_iteration(cfg: LoopConfig, iteration: int, *, rung: int | None = None, n: int | None = None,
                       chat_model: Any = None) -> dict[Language, Path]:
    """Write ``iter_NN/battery_<lang>.yaml`` per language.  Scene: a mix of the cached rungs up to
    ``rung`` (:func:`bench._loop_prompts.mix_counts`); any other track: a selection from its source
    batteries (:func:`generate_from_sources`).  Validated through ``Battery`` before it is written."""
    rung = default_rung(cfg, iteration) if rung is None else rung
    if rung not in RUNGS:
        raise LoopError(f"--rung must be one of {sorted(RUNGS)}, got {rung}")
    if cfg.track is not Track.SCENE:
        return generate_from_sources(cfg, iteration, rung=rung, n=n)
    size = n or cfg.n
    counts = mix_counts(rung, size)
    iter_dir = cfg.iter_dir(iteration)
    iter_dir.mkdir(parents=True, exist_ok=True)
    out: dict[Language, Path] = {}
    ids: dict[str, list[str]] = {}
    for lang in cfg.languages:
        prompts: list[BenchPrompt] = []
        for level, k in counts.items():
            cached = rung_prompts(cfg, lang, level, iteration=iteration, chat_model=chat_model)
            if len(cached) < k:
                log.warning("rung %d %s holds %d prompts, the mix wanted %d", level, lang.value, len(cached), k)
            prompts.extend(cached[:k])
        battery = Battery(name=f"{cfg.name}_it{iteration:02d}_{lang.value}", track=cfg.track, language=lang,
                          description=f"loop {cfg.name} iteration {iteration}: {size} scene prompts, rungs "
                                      + ", ".join(f"{lvl}×{c}" for lvl, c in counts.items()),
                          prompts=prompts)
        path = iter_dir / f"battery_{lang.value}.yaml"
        _write_battery(path, battery)
        out[lang] = path
        ids[lang.value] = [p.id for p in prompts]
    write_json_atomic(iter_dir / "generate.json", {
        "iteration": iteration, "rung": rung, "n": size, "mix": {str(k): v for k, v in counts.items()},
        "languages": [x.value for x in cfg.languages], "batteries": {k.value: str(v) for k, v in out.items()},
        "ids": ids, "generated_at": datetime.now(UTC).isoformat()})
    return out


def generate_from_sources(cfg: LoopConfig, iteration: int, *, rung: int, n: int | None = None) -> dict[Language, Path]:
    """A non-scene iteration's batteries: the source batteries pooled (deduped by prompt text, ids
    verbatim), routed per language (a prompt naming a language goes to that language only) and
    selected by the rung's tier mix (``_loop_sources.SOURCE_RUNGS``, stable-hash order — the same
    rung gives the same ids every iteration).  A pool too small for ``n`` gives a shorter battery,
    logged and written to generate.json (``short``), never silently capped."""
    size = n or cfg.n
    paths = [source_path(s) for s in cfg.source]
    missing = [str(p) for p in paths if not p.is_file()]
    if missing:
        raise LoopError(f"source batteries not found: {missing}")
    try:
        pool = pool_sources(paths, cfg.track)
    except (OSError, ValueError) as e:  # a pydantic ValidationError is a ValueError
        raise LoopError(f"cannot pool the source batteries: {e}") from e
    if pool.duplicates:
        log.info("%d prompt(s) repeat an earlier prompt's text and are dropped: %s", len(pool.duplicates), pool.duplicates)
    iter_dir = cfg.iter_dir(iteration)
    iter_dir.mkdir(parents=True, exist_ok=True)
    tiers = [s.tier for s in rung_steps(rung)]
    out: dict[Language, Path] = {}
    ids: dict[str, list[str]] = {}
    tier_counts: dict[str, dict[str, int]] = {}
    short: dict[str, dict[str, int]] = {}
    for lang in cfg.languages:
        sel = select(route(pool.prompts, lang), rung=rung, n=size, loop_name=cfg.name)
        if not sel.prompts:
            raise LoopError(f"no {'/'.join(tiers)} prompt of {cfg.source} runs in {lang.value} (rung {rung})")
        if len(sel.prompts) < size:
            log.warning("%s: the sources hold %d %s prompt(s) for rung %d, n is %d — the battery runs %d",
                        lang.value, sel.available, "/".join(tiers), rung, size, len(sel.prompts))
            short[lang.value] = {"wanted": size, "got": len(sel.prompts)}
        battery = Battery(name=f"{cfg.name}_it{iteration:02d}_{lang.value}", track=cfg.track, language=lang,
                          description=f"loop {cfg.name} iteration {iteration}: {len(sel.prompts)} {cfg.track.value} "
                                      f"prompts selected from {', '.join(cfg.source)}, rung {rung} ("
                                      + ", ".join(f"{t}×{c}" for t, c in sel.tier_counts.items()) + ")",
                          prompts=sel.prompts)
        path = iter_dir / f"battery_{lang.value}.yaml"
        _write_battery(path, battery)
        out[lang] = path
        ids[lang.value] = [p.id for p in sel.prompts]
        tier_counts[lang.value] = sel.tier_counts
    write_json_atomic(iter_dir / "generate.json", {
        "iteration": iteration, "track": cfg.track.value, "rung": rung, "n": size,
        "tiers": [[s.tier, s.order] for s in rung_steps(rung)], "rung_table": rung_table_text(),
        "sources": list(cfg.source),
        "pool": {"size": len(pool.prompts), "duplicates": pool.duplicates, "id_collisions": pool.id_collisions},
        "languages": [x.value for x in cfg.languages], "batteries": {k.value: str(v) for k, v in out.items()},
        "tier_counts": tier_counts, "short": short, "ids": ids, "generated_at": datetime.now(UTC).isoformat()})
    return out


# --------------------------------------------------------------------------- run
def child_argv(cfg: LoopConfig, lang: Language, battery: Path, out_lang: Path, *, resume: bool) -> list[str]:
    """The ``python -m bench.run_bench`` command line of one language's child."""
    argv = [sys.executable, "-m", "bench.run_bench", str(battery), "--out", str(out_lang),
            "--generator", cfg.generator, "--judge", cfg.judge, "--parallel", str(cfg.parallel),
            "--rounds", str(cfg.rounds), "--max-minutes", str(cfg.max_minutes), "--no-report"]
    if cfg.planner:
        argv += ["--planner", cfg.planner]
    if cfg.variant:
        argv += ["--variant", cfg.variant]
    if cfg.stagger_s > 0:
        argv += ["--stagger", str(cfg.stagger_s)]
    if resume:
        argv += ["--redo-status", "infra_failed"]
    return argv


def child_env(cfg: LoopConfig, base: dict[str, str] | None = None) -> dict[str, str]:
    """The children's environment: the judge's thinking level, the variant (belt and braces beside
    ``--variant``: every process of the run reads it), three GPU render slots, unbuffered logs."""
    env = dict(os.environ if base is None else base)
    env["C3D_JUDGE__THINKING"] = cfg.judge_thinking
    if cfg.variant:
        env["C3D_VARIANT"] = cfg.variant
    else:
        env.pop("C3D_VARIANT", None)
    env["C3D_RENDER__BLENDER_GPU_SLOTS"] = BLENDER_GPU_SLOTS
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _terminate(proc: Any) -> None:
    """SIGTERM the child's group (its own stop handler records the stop), SIGKILL after the grace."""
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, AttributeError, TypeError):
        with suppress(Exception):
            proc.terminate()
    deadline = time.monotonic() + CHILD_STOP_GRACE_S
    while time.monotonic() < deadline and proc.poll() is None:
        time.sleep(0.2)
    if proc.poll() is None:
        with suppress(Exception):
            os.killpg(proc.pid, signal.SIGKILL)


def run_iteration(cfg: LoopConfig, iteration: int, *, languages: Sequence[Language] = (), resume: bool = False,
                  popen: Callable[..., Any] = subprocess.Popen) -> dict[str, Any]:
    """One child process per language, side by side (``parallel`` runs each), SIGTERM forwarded on a
    stop; ``iter_NN/run.json`` carries pids while they run and exit codes + wall after."""
    install_stop_handlers()
    _check_variant_name(cfg.variant)
    iter_dir = cfg.iter_dir(iteration)
    langs = list(languages) or cfg.languages
    meta: dict[str, Any] = {"iteration": iteration, "started_at": datetime.now(UTC).isoformat(), "resume": resume,
                            "children": {}}
    children: dict[str, tuple[Any, Any]] = {}
    t0 = time.monotonic()
    try:
        for lang in langs:
            battery = iter_dir / f"battery_{lang.value}.yaml"
            if not battery.is_file():
                raise LoopError(f"no {battery.name} under {iter_dir}: run `bench.loop generate --iter {iteration}` first")
            out_lang = iter_dir / lang.value
            log_path = iter_dir / f"run_{lang.value}.log"
            argv = child_argv(cfg, lang, battery, out_lang, resume=resume)
            if children and cfg.stagger_s > 0:
                # The languages' first waves interleave.  agy holds ONE session per key machine-wide
                # and there are 30 keys, so 15 + 15 parallel runs plus the harness's own planner /
                # asset / judge calls all draw on the same 64-slot pool; two children launched
                # together plan and single-shot their assets in the same minute and exhaust it
                # (iteration 0: 160 of its 172 `asset.generate_failed` outages fell in the first ten
                # minutes).  Each child staggers its own first wave by stagger_s (`--stagger`); half
                # that between the children puts one language's k-th launch between the other's.
                time.sleep(cfg.stagger_s / 2)
            fh = log_path.open("a")
            fh.write(f"--- {datetime.now(UTC).isoformat()} {' '.join(argv)}\n")
            fh.flush()
            proc = popen(argv, cwd=str(REPO), env=child_env(cfg), stdout=fh, stderr=subprocess.STDOUT,
                         start_new_session=True)
            children[lang.value] = (proc, fh)
            meta["children"][lang.value] = {"argv": argv, "pid": proc.pid, "log": str(log_path), "out": str(out_lang),
                                            "returncode": None}
        write_json_atomic(iter_dir / "run.json", meta)
        while any(p.poll() is None for p, _ in children.values()):
            time.sleep(0.5)
    except BaseException:  # SystemExit(143) from the stop handler, KeyboardInterrupt, a LoopError mid-launch
        for p, _ in children.values():
            if p.poll() is None:
                _terminate(p)
        raise
    finally:
        for name, (p, fh) in children.items():
            meta["children"][name]["returncode"] = p.poll()
            fh.close()
        meta["finished_at"] = datetime.now(UTC).isoformat()
        meta["wall_s"] = round(time.monotonic() - t0, 1)
        write_json_atomic(iter_dir / "run.json", meta)
    return meta


# --------------------------------------------------------------------------- diagnose / compare / judge-calib
def _present(cfg: LoopConfig, iteration: int, languages: Sequence[Language] = ()) -> list[tuple[Language, Path]]:
    out: list[tuple[Language, Path]] = []
    for lang in (list(languages) or cfg.languages):
        d = cfg.iter_dir(iteration) / lang.value
        if d.is_dir():
            out.append((lang, d))
        else:
            log.warning("iteration %d has no %s output (%s)", iteration, lang.value, d)
    if not out:
        raise LoopError(f"iteration {iteration} has no run output under {cfg.iter_dir(iteration)}")
    return out


def diagnose_iteration(cfg: LoopConfig, iteration: int, *, languages: Sequence[Language] = ()) -> dict[str, Diagnosis]:
    out: dict[str, Diagnosis] = {}
    for lang, d in _present(cfg, iteration, languages):
        out[lang.value] = diagnose_dir(lang.value, d, track=cfg.track)
    iter_dir = cfg.iter_dir(iteration)
    write_json_atomic(iter_dir / "diagnosis.json", {k: v.model_dump(mode="json") for k, v in out.items()})
    md = [f"# {cfg.name} — iteration {iteration} diagnosis ({cfg.track.value} track)", "",
          f"track `{cfg.track.value}`, variant `{cfg.variant}`, generator `{cfg.generator}`, judge `{cfg.judge}` "
          f"@ {cfg.judge_thinking}", ""]
    md += [render_diagnosis(v) for v in out.values()]
    (iter_dir / "diagnosis.md").write_text("\n".join(md))
    return out


def compare_iteration(cfg: LoopConfig, iteration: int, against: int, *,
                      languages: Sequence[Language] = ()) -> dict[str, Comparison]:
    if iteration == against:
        raise LoopError("--against must name a different iteration")
    out: dict[str, Comparison] = {}
    for lang, d in _present(cfg, iteration, languages):
        ref = cfg.iter_dir(against) / lang.value
        if not ref.is_dir():
            log.warning("iteration %d has no %s output to compare against", against, lang.value)
            continue
        c = compare_dirs(lang.value, d, ref, track=cfg.track)
        out[lang.value] = c
        iter_dir = cfg.iter_dir(iteration)
        (iter_dir / f"compare_{lang.value}.md").write_text(
            f"# {cfg.name} — iteration {iteration} vs {against}\n\n" + render_comparison(c))
        write_json_atomic(iter_dir / f"compare_{lang.value}.json", c.model_dump(mode="json"))
    if not out:
        raise LoopError(f"nothing to compare: iteration {against} has no output for {[x.value for x in cfg.languages]}")
    return out


def judge_calib(cfg: LoopConfig, iteration: int, *, model_id: str = "", thinking: str = "", n_samples: int = 3,
                languages: Sequence[Language] = (), calibrate_fn: Callable[..., Any] | None = None,
                workers: int = 4) -> dict[str, CalibSummary]:
    """Re-judge every run's PICKED round ``n_samples`` times in fixed montage order (the model's own
    re-judge σ, not its order σ) and summarise per language — the loop's judge arm."""
    model_id = model_id or cfg.judge
    thinking = thinking or cfg.judge_thinking
    if calibrate_fn is None:
        from codeverse3d.addons.calibration import calibrate as calibrate_fn
    out: dict[str, CalibSummary] = {}
    iter_dir = cfg.iter_dir(iteration)
    for lang, d in _present(cfg, iteration, languages):
        digests = digests_for(d, track=cfg.track)
        jobs = [(pid, dg.picked_round) for pid, dg in sorted(digests.items()) if dg.picked_round is not None]

        def one(job: tuple[str, int], *, root: Path = d, lang_name: str = lang.value) -> Any | None:
            pid, picked = job
            try:
                return calibrate_fn([root / "runs" / pid], model_id=model_id, n_samples=n_samples,
                                    out_dir=iter_dir / "judge_calib" / lang_name / pid, geometry_mode=None,
                                    rounds=[picked], thinking=thinking, max_workers=1, fixed_order=True)
            except Exception as e:  # noqa: BLE001 — one run's re-judge must not cost the table
                log.warning("judge-calib %s/%s failed: %s", lang_name, pid, e)
                return None

        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            tables = [t for t in pool.map(one, jobs) if t is not None]
        s = calib_summary(lang.value, tables, thinking=thinking)
        if not tables:
            s.model_id, s.n_samples = model_id, n_samples
        out[lang.value] = s
        (iter_dir / f"judge_calib_{lang.value}.md").write_text(
            f"# {cfg.name} — iteration {iteration} judge calibration\n\n" + render_calib(s))
        write_json_atomic(iter_dir / f"judge_calib_{lang.value}.json", s.model_dump(mode="json"))
    return out


# --------------------------------------------------------------------------- the ledger: accept / rollback / status
class LedgerRow(BaseModel):
    """One decision about the variant (append-only ``ledger.jsonl``; see bench/data/loop/README.md)."""

    ts: str
    iteration: int
    languages: list[str]
    variant: str
    version_from: int
    version_to: int
    variant_hash: str
    battery_sha: str
    verdicts: dict[str, str] = Field(default_factory=dict, description="language → keep | revert | inconclusive")
    decision: Literal["accept", "rollback"]
    cost_usd: float = 0.0
    tree_commit: str = ""
    tree_dirty: bool = False
    note: str = ""


def ledger_path(cfg: LoopConfig, data_dir: Path | None = None) -> Path:
    return (data_dir or DATA_DIR) / cfg.name / "ledger.jsonl"


def ledger_rows(path: Path) -> list[LedgerRow]:
    return read_jsonl(path, LedgerRow)


def append_ledger(path: Path, row: LedgerRow) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    seal_for_append(path)
    with path.open("a") as fh:
        fh.write(row.model_dump_json() + "\n")


def battery_sha(iter_dir: Path) -> str:
    """sha256[:12] over the iteration's battery files — what the row's verdict was measured on."""
    h = hashlib.sha256()
    for p in sorted(iter_dir.glob("battery_*.yaml")):
        h.update(p.name.encode() + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()[:12]


def tree_state(tree: Path = HARNESS) -> tuple[str, bool]:
    """``(commit, dirty)`` of the harness checkout — a ledger row names the code its verdict ran on."""
    try:
        head = subprocess.run(["git", "-C", str(tree), "rev-parse", "HEAD"], capture_output=True, text=True,
                              check=False, timeout=30)
        status = subprocess.run(["git", "-C", str(tree), "status", "--porcelain", "--untracked-files=no", "--", "."],
                                capture_output=True, text=True, check=False, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return "", False
    commit = head.stdout.strip() if head.returncode == 0 else ""
    return commit, bool(status.returncode == 0 and status.stdout.strip())


def iteration_cost(cfg: LoopConfig, iteration: int) -> float:
    from bench.report import load_results

    total = 0.0
    for lang in cfg.languages:
        d = cfg.iter_dir(iteration) / lang.value
        if (d / "results.jsonl").is_file() or (d / "results.json").is_file():
            total += sum(r.cost_usd for r in load_results(d))
    return round(total, 4)


def iteration_verdicts(cfg: LoopConfig, iteration: int) -> dict[str, str]:
    out: dict[str, str] = {}
    for lang in cfg.languages:
        p = cfg.iter_dir(iteration) / f"compare_{lang.value}.json"
        if p.is_file():
            out[lang.value] = str(json.loads(p.read_text()).get("decision", ""))
    return out


def _row(cfg: LoopConfig, iteration: int, *, paths: VariantPaths, decision: Literal["accept", "rollback"],
         version_from: int, version_to: int, note: str) -> LedgerRow:
    commit, dirty = tree_state()
    return LedgerRow(ts=datetime.now(UTC).isoformat(), iteration=iteration, languages=[x.value for x in cfg.languages],
                     variant=cfg.variant, version_from=version_from, version_to=version_to,
                     variant_hash=paths.ledger_hash(), battery_sha=battery_sha(cfg.iter_dir(iteration)),
                     verdicts=iteration_verdicts(cfg, iteration), decision=decision,
                     cost_usd=iteration_cost(cfg, iteration), tree_commit=commit, tree_dirty=dirty, note=note)


def accept(cfg: LoopConfig, iteration: int, *, note: str = "", paths: VariantPaths | None = None,
           data_dir: Path | None = None) -> LedgerRow:
    """The overlay as it stands becomes version N+1: preflight it (a ``routes.toml`` that does not
    type-check or an invalid bundle is refused — a run would have degraded to the base routing
    silently), bump ``variant.toml``, snapshot both overlay dirs to ``snapshots/<v>_v<N+1>/``, append
    the row."""
    if not cfg.iter_dir(iteration).is_dir():
        raise LoopError(f"iteration {iteration} does not exist under {cfg.out}")
    vp = paths or VariantPaths.default(cfg.variant)
    vp.preflight()
    version_from = vp.version()
    version_to = version_from + 1
    vp.set_version(version_to)
    vp.snapshot_to(snapshot_dir(cfg, version_to))
    row = _row(cfg, iteration, paths=vp, decision="accept", version_from=version_from, version_to=version_to, note=note)
    if not row.verdicts:
        log.warning("iteration %d has no compare_<lang>.json: accepting without a verdict", iteration)
    append_ledger(ledger_path(cfg, data_dir), row)
    return row


def rollback(cfg: LoopConfig, iteration: int, *, note: str = "", paths: VariantPaths | None = None,
             data_dir: Path | None = None) -> LedgerRow:
    """Return the overlay dirs to the last accepted state.  An iteration that was itself the latest
    ``accept`` is un-accepted (its ``version_from`` snapshot comes back and the version with it);
    otherwise the working edits made for the iteration are discarded in favour of the current
    version's snapshot."""
    vp = paths or VariantPaths.default(cfg.variant)
    current = vp.version()
    rows = ledger_rows(ledger_path(cfg, data_dir))
    accepts = [r for r in rows if r.decision == "accept"]
    target = current
    if accepts and accepts[-1].iteration == iteration and accepts[-1].version_to == current:
        target = accepts[-1].version_from
    src = snapshot_dir(cfg, target)
    if not src.is_dir():
        raise LoopError(f"no snapshot {src} to restore (init writes the current version's; accept writes each new one)")
    vp.restore_from(src)
    if vp.version() != target:  # a snapshot taken before the bump still says the old number: pin it
        vp.set_version(target)
    row = _row(cfg, iteration, paths=vp, decision="rollback", version_from=current, version_to=target, note=note)
    append_ledger(ledger_path(cfg, data_dir), row)
    return row


def iterations(cfg: LoopConfig) -> list[dict[str, Any]]:
    """What each ``iter_NN`` holds: batteries, child exit codes, result counts, the files written."""
    out: list[dict[str, Any]] = []
    for d in sorted(cfg.out.glob("iter_*")):
        if not d.is_dir():
            continue
        run = json.loads((d / "run.json").read_text()) if (d / "run.json").is_file() else {}
        results: dict[str, int] = {}
        for lang in cfg.languages:
            jl = d / lang.value / "results.jsonl"
            if jl.is_file():
                results[lang.value] = len({r.get("id") for r in read_jsonl(jl)})
        meta = generation_meta(cfg, int(d.name.split("_")[-1])) or {}
        out.append({
            "iteration": int(d.name.split("_")[-1]), "rung": meta.get("rung"),
            "batteries": sorted(p.name for p in d.glob("battery_*.yaml")),
            "exit_codes": {k: v.get("returncode") for k, v in run.get("children", {}).items()},
            "results": results,
            "diagnosis": (d / "diagnosis.md").is_file(),
            "compare": sorted(p.stem.removeprefix("compare_") for p in d.glob("compare_*.json")),
            "judge_calib": sorted(p.stem.removeprefix("judge_calib_") for p in d.glob("judge_calib_*.json")),
        })
    return out


def status_text(cfg: LoopConfig, *, paths: VariantPaths | None = None, data_dir: Path | None = None) -> str:
    vp = paths or VariantPaths.default(cfg.variant)
    try:
        version = f"v{vp.version()} ({vp.ledger_hash()})"
    except LoopError as e:
        version = f"unknown ({e})"
    lines = [f"loop {cfg.name}: variant {cfg.variant} {version}; track {cfg.track.value}; "
             f"languages {', '.join(x.value for x in cfg.languages)}; "
             + (f"source {', '.join(cfg.source)}; " if cfg.source else "") +
             f"generator {cfg.generator}; judge {cfg.judge} @ {cfg.judge_thinking}; parallel {cfg.parallel} per language "
             f"(stagger {cfg.stagger_s:g} s), rounds {cfg.rounds}, max-minutes {cfg.max_minutes:g}; out {cfg.out}", "",
             "| iter | rung | batteries | exit codes | results | diagnosis | compare | judge-calib |",
             "|---|---|---|---|---|---|---|---|"]
    for it in iterations(cfg):
        lines.append(f"| {it['iteration']} | {it['rung'] or '—'} | {len(it['batteries'])} | "
                     f"{', '.join(f'{k}={v}' for k, v in it['exit_codes'].items()) or '—'} | "
                     f"{', '.join(f'{k}={v}' for k, v in it['results'].items()) or '—'} | "
                     f"{'yes' if it['diagnosis'] else '—'} | {', '.join(it['compare']) or '—'} | "
                     f"{', '.join(it['judge_calib']) or '—'} |")
    rows = ledger_rows(ledger_path(cfg, data_dir))
    lines += ["", f"ledger {ledger_path(cfg, data_dir)}: {len(rows)} row(s)", "",
              "| ts | iter | decision | version | verdicts | cost | hash | tree | note |", "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r.ts[:19]} | {r.iteration} | {r.decision} | {r.version_from}→{r.version_to} | "
                     f"{', '.join(f'{k}={v}' for k, v in r.verdicts.items()) or '—'} | ${r.cost_usd:.2f} | {r.variant_hash} | "
                     f"{r.tree_commit[:9]}{'-dirty' if r.tree_dirty else ''} | {r.note} |")
    return "\n".join(lines)


# --------------------------------------------------------------------------- CLI
def _add_out(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("--out", type=Path, required=True, help="the loop directory (holds loop.json)")


def _add_iter(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("--iter", dest="iteration", type=int, required=True, help="iteration number (0 = the base)")


def _add_langs(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("--languages", default="", help="comma list; default: every language of the loop")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m bench.loop", description=__doc__.split("\n\n")[0])
    ap.add_argument("--data-dir", type=Path, default=None, help=f"where the ledgers live (default {DATA_DIR})")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="freeze the loop's configuration into <out>/loop.json")
    s.add_argument("--name", required=True)
    _add_out(s)
    s.add_argument("--variant", required=True, help="the prompt/skill variant under test (codeverse3d/prompts/variants/<name>)")
    s.add_argument("--track", default=Track.SCENE.value, choices=[t.value for t in Track],
                   help="the track the loop tunes (decides the languages allowed and where the prompts come from)")
    s.add_argument("--languages", default="", help="comma list of the track's languages (default: all of them)")
    s.add_argument("--source", default="",
                   help="comma list of battery files under bench/prompts/ (a bare name or a path relative to eval/) "
                        "the loop selects from; required for every track but scene")
    s.add_argument("--generator", default="agy:gemini-4-argon")
    s.add_argument("--judge", default="gemini:gemini-4-argon")
    s.add_argument("--judge-thinking", default="medium", choices=("off", "low", "medium", "high"))
    s.add_argument("--planner", default="", help="battery-generation model (default: the settings planner)")
    s.add_argument("--parallel", type=int, default=15, help="run_bench workers per language")
    s.add_argument("--stagger", type=float, default=40.0,
                   help="seconds between the launches of each child's first wave of workers; the children launch "
                        "half that apart (0 = everything at once)")
    s.add_argument("--rounds", type=int, default=2)
    s.add_argument("--max-minutes", type=float, default=90.0)
    s.add_argument("--n", type=int, default=15, help="prompts per battery")

    s = sub.add_parser("generate", help="write the iteration's batteries (scene: a mix of cached rungs; "
                                        "other tracks: a tier selection from the source batteries)")
    _add_out(s)
    _add_iter(s)
    s.add_argument("--n", type=int, default=None, help="prompts per battery (default: loop.json)")
    s.add_argument("--rung", type=int, default=None, help=f"top rung of the mix, 1..{max(RUNGS)} (default: the previous iteration's, else 1)")

    s = sub.add_parser("run", help="run the batteries: one bench.run_bench child per language")
    _add_out(s)
    _add_iter(s)
    _add_langs(s)
    s.add_argument("--resume", action="store_true", help="re-run the cells the weather lost (--redo-status infra_failed)")

    s = sub.add_parser("diagnose", help="diagnosis.{md,json} from the iteration's run records")
    _add_out(s)
    _add_iter(s)
    _add_langs(s)

    s = sub.add_parser("compare", help="pair the iteration with another by prompt id: keep / revert / inconclusive")
    _add_out(s)
    _add_iter(s)
    s.add_argument("--against", type=int, required=True, help="the reference iteration")
    _add_langs(s)

    s = sub.add_parser("judge-calib", help="re-judge the picked rounds n times: within / between σ, SNR, correlations")
    _add_out(s)
    _add_iter(s)
    s.add_argument("--model", default="", help="judge model (default: the loop's judge)")
    s.add_argument("--thinking", default="", choices=("", "off", "low", "medium", "high"), help="default: the loop's")
    s.add_argument("--n", type=int, default=3, help="samples per round")
    s.add_argument("--workers", type=int, default=4, help="runs re-judged side by side")
    _add_langs(s)

    for name, text in (("accept", "bump the variant version, snapshot the overlay, append the ledger row"),
                       ("rollback", "restore the last accepted overlay, append the ledger row")):
        s = sub.add_parser(name, help=text)
        _add_out(s)
        _add_iter(s)
        s.add_argument("--note", default="")

    s = sub.add_parser("status", help="the ledger and the variant version")
    _add_out(s)
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    ap = build_parser()
    ns = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        return _dispatch(ns)
    except LoopError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


def _dispatch(ns: argparse.Namespace) -> int:
    if ns.cmd == "init":
        track = Track(ns.track)
        languages = parse_languages(ns.languages) if ns.languages.strip() else list(TRACK_LANGUAGES[track])
        try:
            cfg = LoopConfig(name=ns.name, out=ns.out.resolve(), variant=ns.variant, languages=languages, track=track,
                             source=parse_sources(ns.source), generator=ns.generator, judge=ns.judge,
                             judge_thinking=ns.judge_thinking, planner=ns.planner, parallel=ns.parallel,
                             stagger_s=ns.stagger, rounds=ns.rounds, max_minutes=ns.max_minutes, n=ns.n)
        except ValueError as e:  # the track/languages/source rules: a configuration error, not a traceback
            raise LoopError(str(e)) from e
        print(f"wrote {init(cfg)}")
        return 0
    cfg = LoopConfig.load(ns.out)
    langs = parse_languages(ns.languages) if getattr(ns, "languages", "") else []
    if ns.cmd == "generate":
        for lang, path in generate_iteration(cfg, ns.iteration, rung=ns.rung, n=ns.n).items():
            print(f"{lang.value}: {path}")
        return 0
    if ns.cmd == "run":
        meta = run_iteration(cfg, ns.iteration, languages=langs, resume=ns.resume)
        for name, child in meta["children"].items():
            print(f"{name}: exit {child['returncode']} ({child['log']})")
        print(f"wall {meta['wall_s']:.0f} s → {cfg.iter_dir(ns.iteration) / 'run.json'}")
        return 0 if all(c["returncode"] == 0 for c in meta["children"].values()) else 1
    if ns.cmd == "diagnose":
        for name, d in diagnose_iteration(cfg, ns.iteration, languages=langs).items():
            print(f"{name}: {d.n} runs, picked mean {d.picked.get('mean')}, {len(d.recommendations)} recommendation(s)")
        print(f"→ {cfg.iter_dir(ns.iteration) / 'diagnosis.md'}")
        return 0
    if ns.cmd == "compare":
        for name, c in compare_iteration(cfg, ns.iteration, ns.against, languages=langs).items():
            print(f"{name}: {c.decision} — {c.reason}")
        return 0
    if ns.cmd == "judge-calib":
        for name, s in judge_calib(cfg, ns.iteration, model_id=ns.model, thinking=ns.thinking, n_samples=ns.n,
                                   languages=langs, workers=ns.workers).items():
            print(f"{name}: within-sd {s.within_sd} between-sd {s.between_sd} SNR {s.snr} (${s.cost_usd:.2f})")
        return 0
    if ns.cmd in ("accept", "rollback"):
        fn = accept if ns.cmd == "accept" else rollback
        row = fn(cfg, ns.iteration, note=ns.note, data_dir=ns.data_dir)
        print(f"{row.decision}: {cfg.variant} v{row.version_from} → v{row.version_to} ({row.variant_hash}); "
              f"verdicts {row.verdicts or '—'}; ledger {ledger_path(cfg, ns.data_dir)}")
        return 0
    if ns.cmd == "status":
        print(status_text(cfg, data_dir=ns.data_dir))
        return 0
    raise LoopError(f"unknown command {ns.cmd!r}")  # pragma: no cover — argparse restricts the choices


if __name__ == "__main__":
    raise SystemExit(main())

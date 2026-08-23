"""Caption one finished run: ``{detailed, instruction, factory}`` (STORAGE_RULES §6).

* ``detailed``     image-grounded, objective description of what was rendered
  (parts, materials, proportions; motion / animation for articulated + scenes).
* ``instruction``  one natural user request that asks for this as code and names
  the target language generically ("a Blender Python script", "a Three.js scene").
* ``factory``      code-grounded build recipe: parts + construction order + techniques.

Rules enforced in code: strict JSON schema, no API/class names, no brand names,
each field non-empty.  Captions are stored in ``record.extra["captions"]`` (with
provenance) and ``<ws>/captions.json``; ``record.json`` is rewritten — or, with
``out_dir`` (read-only runs), written as a side-car ``<out_dir>/<slug>.json`` that
``export_samples(captions_dir=...)`` picks up.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from codeverse.contracts.chat import ChatMessage, ChatRequest, ImagePart
from codeverse.contracts.common import Language, Track
from codeverse.contracts.run import RunRecord
from codeverse.flywheel import _git
from codeverse.flywheel.sample import ENTRY_BY_LANGUAGE, best_round_record, code_files_for_round
from codeverse.prompts import prompt_hash
from codeverse.workspace import Workspace

MAX_CODE_CHARS = 12_000
N_VIEWS = 2
#: view-name fragments that show motion (articulated track) — shown to the captioner first
MOTION_VIEW_HINTS = ("articulation", "pose_")

#: how the instruction must name the target (generic, no API names)
LANGUAGE_PHRASE: dict[Language, str] = {
    Language.BLENDER: "a Blender Python script",
    Language.CADQUERY: "a CadQuery script",
    Language.THREEJS: "a Three.js module",
    Language.URDF_BLENDER: "a URDF model with Blender-Python-built link meshes",
    Language.SCENE_THREEJS: "a Three.js scene",
    Language.GLSL_SHADER: "a GLSL fragment shader",
    Language.OPENGL_PYTHON: "an OpenGL Python program",
}
#: leaked API / platform tokens that must not appear in any caption.  NOTE:
#: ``THREE.`` (the API namespace) is checked case-sensitively below — the
#: platform name "Three.js" is REQUIRED in threejs instructions and must not trip it.
_FORBIDDEN = re.compile(
    r"\b(bpy|bmesh|mathutils|cq\.|cadquery\.|GLTFLoader|ShaderMaterial|MeshStandardMaterial|"
    r"Workplane|moderngl|Shadertoy|Sketchfab|Gemini|Claude|OpenAI|GPT|Antigravity)\b",
    re.IGNORECASE,
)
#: all-caps ``THREE.<Symbol>`` namespace usage (case-sensitive; "Three.js" / "THREE.js" stay legal)
_FORBIDDEN_THREE_NS = re.compile(r"\bTHREE\.(?!js\b)")


class Captions(BaseModel):
    detailed: str = Field(min_length=20, description="objective, image-grounded description (2-5 sentences)")
    instruction: str = Field(min_length=15, description="one natural user request asking to generate this as code")
    factory: str = Field(min_length=20, description="code-grounded build recipe: parts + construction order")


class CaptionProvenance(BaseModel):
    captioner: str
    round_index: int | None
    images_used: list[str]
    code_chars: int
    prompt_hash: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    cost_usd: float = 0.0


class CaptionError(RuntimeError):
    pass


def _system_prompt() -> str:
    return (
        "You label 3D assets for a code-generation dataset. You receive rendered views of an asset, "
        "the user's original request, and the raw source code that built it. Return STRICT JSON with "
        "exactly the keys detailed, instruction, factory (string values).\n"
        "- detailed: an objective description of what is VISIBLE in the renders: the object/scene, its "
        "main parts, proportions, materials/colours, and — if it articulates or animates — how its parts "
        "move. 2-5 sentences, grounded in the images.\n"
        "- instruction: ONE natural user request (1-2 sentences, imperative) asking an AI to generate this "
        "asset as {language_phrase}; mention the key parts/requirements a user would state.\n"
        "- factory: a technical build recipe (3-6 sentences) derived from the CODE: which parts are built, "
        "in what order, from which primitives/operations (extrusions, booleans, lofts, instancing, joints, "
        "shader effects...), how they are positioned and finished.\n"
        "Rules: be specific to THIS asset; describe shapes in plain words; do NOT mention API, module, "
        "class or function names, file names, model/vendor names or platforms; no markdown."
    )


def _round_images(ws: Workspace, record: RunRecord) -> tuple[list[ImagePart], list[str]]:
    rnd = best_round_record(record)
    images: list[ImagePart] = []
    used: list[str] = []
    if rnd is None or rnd.renders is None:
        return images, used

    def _add(path: str | None, label: str) -> None:
        if not path or len(images) >= N_VIEWS + 1:
            return
        p = Path(path)
        if not p.is_absolute():
            p = ws.root / p
        if p.is_file():
            images.append(ImagePart(path=str(p), label=label))
            used.append(str(p))

    _add(rnd.renders.contact_sheet, "contact sheet (all views, labelled)")
    views = list(rnd.renders.views)
    motion = [v for v in views if any(h in v.name for h in MOTION_VIEW_HINTS)]
    for v in (motion + [v for v in views if v not in motion])[:N_VIEWS]:
        _add(v.path, f"view: {v.name}")
    return images, used


def _code_excerpt(ws: Workspace, record: RunRecord) -> str:
    rnd = best_round_record(record)
    files, _src = code_files_for_round(ws, rnd)
    entry = ENTRY_BY_LANGUAGE[record.spec.language]
    ordered = {k: files[k] for k in sorted(files, key=lambda k: (k != entry, k))}
    text, _skipped = _git.decode_text_files(ordered, max_total=MAX_CODE_CHARS)
    return "\n\n".join(f"### {p}\n{t}" for p, t in text.items())


def _user_prompt(record: RunRecord, code: str) -> str:
    kind = {Track.STATIC_OBJECT: "static object", Track.ARTICULATED_OBJECT: "articulated object",
            Track.SCENE: "scene", Track.GRAPHICS: "animated procedural graphics (judged from sampled frames)"}[record.spec.track]
    return (
        f"Asset kind: {kind}.\nOriginal user request: {record.spec.prompt}\n\n"
        f"Source code (raw, may be truncated):\n{code}\n\nReturn the JSON now."
    )


def validate_captions(caps: Captions, language: Language) -> list[str]:
    """Rule violations (empty list = ok)."""
    problems = []
    for field in ("detailed", "instruction", "factory"):
        m = _FORBIDDEN.search(getattr(caps, field)) or _FORBIDDEN_THREE_NS.search(getattr(caps, field))
        if m:
            problems.append(f"{field}: must not mention '{m.group(0)}' (API/platform name)")
    phrase_words = {Language.BLENDER: "blender", Language.CADQUERY: "cadquery", Language.THREEJS: "three.js",
                    Language.URDF_BLENDER: "urdf", Language.SCENE_THREEJS: "three.js",
                    Language.GLSL_SHADER: "glsl", Language.OPENGL_PYTHON: "opengl"}[language]
    if phrase_words not in caps.instruction.lower():
        problems.append(f"instruction: must name the target language ({LANGUAGE_PHRASE[language]})")
    return problems


def caption_sample(
    ws: Workspace, record: RunRecord, model_id: str, *, model: object | None = None, out_dir: Path | str | None = None
) -> Captions:
    """Caption the best round of ``record``.

    Default: store into ``record.extra["captions"]`` + ``<ws>/captions.json`` and
    rewrite ``record.json``.  With ``out_dir`` the workspace is left untouched and
    ``<out_dir>/<slug>.json`` (captions + provenance) is written instead."""
    if model is None:
        from codeverse.models import get_chat_model

        model = get_chat_model(model_id)
    images, used = _round_images(ws, record)
    code = _code_excerpt(ws, record)
    if not code and not images:
        raise CaptionError(f"{ws.root}: no code and no renders to caption")
    system = _system_prompt().replace("{language_phrase}", LANGUAGE_PHRASE[record.spec.language])
    user = _user_prompt(record, code)
    messages = [ChatMessage.user(user, images=images)]
    schema = Captions.model_json_schema()
    cost = 0.0
    caps: Captions | None = None
    problems: list[str] = []
    for _attempt in range(2):
        resp = model.generate(ChatRequest(messages=messages, system=system, response_schema=schema,
                                          temperature=0.3, thinking="low", label="captioner"))  # type: ignore[attr-defined]
        cost += resp.usage.cost_usd
        data = resp.parsed if isinstance(resp.parsed, dict) else _parse_json(resp.text)
        try:
            caps = Captions.model_validate(data)
        except Exception as e:
            problems = [f"invalid JSON/schema: {e}"]
        else:
            problems = validate_captions(caps, record.spec.language)
        if not problems:
            break
        messages = messages + [ChatMessage.assistant(resp.text or json.dumps(data)),
                               ChatMessage.user("Fix these problems and return the JSON again:\n- " + "\n- ".join(problems))]
    if caps is None or problems:
        raise CaptionError(f"{ws.root}: captions rejected after retry: {problems}")
    rnd = best_round_record(record)
    prov = CaptionProvenance(captioner=model_id, round_index=rnd.index if rnd else None,
                             images_used=[_rel(ws, u) for u in used], code_chars=len(code),
                             prompt_hash=prompt_hash(system), cost_usd=cost)
    payload = {**caps.model_dump(), "provenance": prov.model_dump(mode="json")}
    record.extra["captions"] = payload
    if out_dir is not None:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{ws.root.name}.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        return caps
    ws.write_json(ws.root / "captions.json", payload)
    ws.write_json(ws.record_path, record)
    return caps


def _rel(ws: Workspace, path: str) -> str:
    try:
        return Path(path).relative_to(ws.root).as_posix()
    except ValueError:
        return path


def _parse_json(text: str) -> dict:
    s = text.strip()
    if s.startswith("```"):
        s = s.strip("`")
        s = s[s.find("{"):]
    try:
        return json.loads(s[s.find("{"): s.rfind("}") + 1])
    except ValueError as e:
        raise CaptionError(f"captioner returned non-JSON: {text[:200]!r}") from e

"""The scene_blender lint table: the object track's bpy traps + the scene policy (DESIGN §8, D10)."""

from __future__ import annotations

import pytest

from codeverse3d.contracts.artifacts import Severity
from codeverse3d.languages._ast_lint import BASE_FORBIDDEN_IMPORTS
from codeverse3d.languages.scene_blender import (
    FORBIDDEN_IMPORTS,
    lint_scene_source,
    simple_driver,
)

HEAD = "import bpy\n\n\ndef build(ctx):\n"


def _zone(body: str) -> str:
    return HEAD + "".join(f"    {line}\n" for line in body.splitlines()) + "    return ctx.collection\n"


def _hits(source: str, *, role: str = "zone", target: str = "src/zones/yard.py") -> dict[str, Severity]:
    return {f.message: f.severity for f in lint_scene_source(source, target=target, role=role)}


def _worst(source: str, **kw) -> Severity | None:
    sev = set(_hits(source, **kw).values())
    for s in (Severity.ERROR, Severity.WARN, Severity.INFO):
        if s in sev:
            return s
    return None


# (body inside a zone's build(ctx), the worst severity the lint must give it)
TABLE = [
    # D10: a Python driver never evaluates under --factory-startup (it reads 0) -> ERROR
    ('d = ob.driver_add("location", 2).driver\nd.expression = "noise.noise((frame, 0, 0))"', Severity.ERROR),
    ('d = ob.driver_add("location", 2).driver\nd.expression = "sin(frame * 0.2) * 2 if frame > 3 else 0"', None),
    # harness-owned: rendering, files, cameras, frames, scene render settings
    ("bpy.ops.render.render(write_still=True)", Severity.ERROR),
    ('ctx.scene.render.engine = "CYCLES"', Severity.ERROR),
    # the world is env.py's
    ('ctx.scene.world = bpy.data.worlds.new("W")', Severity.ERROR),
    # renamed / removed API
    ('bsdf.inputs["Specular"].default_value = 0.5', Severity.ERROR),
    # determinism: ctx.rng(seed) is the only randomness
    ("import random\nx = random.uniform(0, 1)", Severity.ERROR),
    ("import random\nr = random.Random(3)\nx = r.uniform(0, 1)", None),
    # build time and zone ownership
    ("for i in range(50):\n    bpy.ops.mesh.primitive_cube_add(size=1, location=(i, 0, 0))", Severity.WARN),
    ("ctx.scene.collection.objects.link(ob)", Severity.WARN),
    # the agent's own settings on its own data are NOT harness-owned
    ("ctx.collection.objects.link(ob)", None),
]


@pytest.mark.parametrize(("body", "worst"), TABLE, ids=[t[0].splitlines()[-1][:48] for t in TABLE])
def test_the_scene_lint_table(body: str, worst: Severity | None) -> None:
    assert _worst(_zone(body)) is worst, _hits(_zone(body))


@pytest.mark.parametrize("mod", ["os", "time"])
def test_clock_file_and_process_imports_are_forbidden(mod: str) -> None:
    assert _worst(f"import {mod}\n" + _zone("pass")) is Severity.ERROR
    assert BASE_FORBIDDEN_IMPORTS <= FORBIDDEN_IMPORTS


def test_simple_driver_is_blenders_subset() -> None:
    """Verified against Blender 5.0.1 ``driver.is_simple_expression`` (DESIGN probes/drv.py)."""
    for ok in ("sin(frame*0.2)*2", "frame*2 if frame > 3 else 1", "clamp(frame / 40, 0, 1)", "lerp(0, 2, 0.5)", "pi", "not frame"):
        assert simple_driver(ok), ok
    for bad in ("frame ** 2", "frame % 3", "noise.noise((frame, 0, 0))", "hypot(frame, 1)", "'a'", "[frame]", "sin(x=1)"):
        assert not simple_driver(bad), bad


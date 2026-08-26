"""A wheel that ships no SKILL.md routes nothing, silently (T: packaging).

This is not hypothetical.  The comment above ``[tool.setuptools.package-data]`` records
the last time it happened: the scene starter tree and ``languages/**/*.md`` were missing
from the wheel, so ``skeleton.write_example()`` rglob'd an absent directory and wrote ZERO
files while reporting success.  A skill library fails the same way and even more quietly —
the run passes, the index is empty, and the read rate reads 0% as if the model ignored it.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parents[2]
PROBE = "cv3d-packaging-probe"


def test_package_data_declares_the_skill_tree():
    data = tomllib.loads((HARNESS / "pyproject.toml").read_text())
    globs = data["tool"]["setuptools"]["package-data"]["codeverse"]
    assert "skills/**/*" in globs


@pytest.mark.slow
def test_a_built_wheel_actually_contains_the_bundles(tmp_path: Path):
    """Builds a real wheel.  A probe bundle is added when the library is still empty, so
    the guarantee is proven now rather than the first time a bundle lands."""
    probe = HARNESS / "codeverse" / "skills" / PROBE
    existing = [p.name for p in (HARNESS / "codeverse" / "skills").iterdir()
                if p.is_dir() and (p / "SKILL.md").is_file()]
    made_probe = False
    try:
        if not existing:
            (probe / "references").mkdir(parents=True)
            (probe / "SKILL.md").write_text(f"---\nname: {PROBE}\ndescription: probe\n---\nbody\n")
            (probe / "references" / "depth.md").write_text("depth\n")
            made_probe = True
        out = tmp_path / "wheel"
        r = subprocess.run([sys.executable, "-c",
                            f"from setuptools import build_meta as b; print(b.build_wheel({str(out)!r}))"],
                           cwd=HARNESS, capture_output=True, text=True, check=False)
        assert r.returncode == 0, r.stdout + r.stderr
        whl = next(out.glob("*.whl"))
        names = set(zipfile.ZipFile(whl).namelist())
        want = [PROBE] if made_probe else sorted(existing)
        for name in want:
            assert f"codeverse/skills/{name}/SKILL.md" in names, (
                f"{name} is in the source tree and NOT in the wheel: "
                f"{sorted(n for n in names if 'skills' in n)[:20]}")
            refs = [n for n in names if n.startswith(f"codeverse/skills/{name}/references/")]
            assert refs, f"{name}: references/ must ship too — it is the depth probe"
        if not made_probe:
            # every claims file too: `3dcv skills validate` runs against an installed wheel
            claims = sorted((HARNESS / "codeverse" / "skills" / "_claims").glob("*.toml"))
            for c in claims:
                assert f"codeverse/skills/_claims/{c.name}" in names, f"{c.name} missing from the wheel"
        assert not any(n.endswith(".pyc") for n in names if "skills" in n)
    finally:
        if made_probe:
            shutil.rmtree(probe, ignore_errors=True)
        shutil.rmtree(HARNESS / "build", ignore_errors=True)


@pytest.mark.slow
def test_the_library_resolves_through_importlib_resources_not_just___file__():
    """An installed wheel has no source tree; ``skills_dir`` must still find the bundles."""
    from importlib.resources import files

    from codeverse.skills import skills_dir

    assert Path(str(files("codeverse.skills"))) == skills_dir()

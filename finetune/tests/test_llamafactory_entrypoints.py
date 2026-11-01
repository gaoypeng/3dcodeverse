"""Offline regression checks for the consolidated dataset scripts and legacy CLIs.

Parquet I/O uses JSON-backed doubles; the actual CLI, audit, sample filtering,
and forwarding code runs without data/model dependencies or network access.
"""
import json
import os
import runpy
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

FINETUNE = Path(__file__).resolve().parents[1]
ENTRY_DIRS = ("llamafactory", "scripts")
LEGACY_SCRIPTS = (
    "audit_pairs.py",
    "build_pairs_3dcodebench.py",
    "convert_subdirs.py",
    "shrink_pair_images.py",
    "upload_pairs.py",
    "verify_unflattened_glsl.py",
)


class JsonFrame:
    def __init__(self, rows):
        self.rows = rows

    def __len__(self):
        return len(self.rows)

    def to_dict(self, orient):
        assert orient == "records"
        return self.rows

    def to_parquet(self, path, *, index):
        assert index is False
        Path(path).write_text(json.dumps(self.rows))


@pytest.fixture
def offline_io(monkeypatch):
    pandas = types.ModuleType("pandas")
    pandas.DataFrame = JsonFrame
    pandas.read_parquet = lambda path: JsonFrame(json.loads(Path(path).read_text()))
    monkeypatch.setitem(sys.modules, "pandas", pandas)
    parquet = types.ModuleType("pyarrow.parquet")
    arrow = types.ModuleType("pyarrow")
    arrow.parquet = parquet
    monkeypatch.setitem(sys.modules, "pyarrow", arrow)
    monkeypatch.setitem(sys.modules, "pyarrow.parquet", parquet)

    def no_network(*args, **kwargs):
        pytest.fail("dataset CLI attempted a network request")

    requests = types.ModuleType("requests")
    requests.get = no_network
    hub = types.ModuleType("huggingface_hub")
    hub.HfApi = hub.hf_hub_download = no_network
    monkeypatch.setitem(sys.modules, "requests", requests)
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)
    # Legacy entry points add their canonical sibling directory to sys.path.
    monkeypatch.setattr(sys, "path", list(sys.path))
    return parquet


def run_cli(monkeypatch, entry_dir, filename, *args):
    script = FINETUNE / entry_dir / filename
    monkeypatch.setattr(sys, "argv", [str(script), *map(str, args)])
    try:
        runpy.run_path(str(script), run_name="__main__")
    except SystemExit as exc:
        return exc.code
    return 0


@pytest.mark.parametrize("entry_dir", ENTRY_DIRS)
@pytest.mark.parametrize("kind", ("text", "img", "imgtext"))
@pytest.mark.parametrize("literal_in_answer", (False, True))
def test_audit_counts_image_tokens_in_assistant_turn(
    tmp_path, monkeypatch, capsys, offline_io, entry_dir, kind, literal_in_answer
):
    root = tmp_path / "datasets"
    dataset = root / f"fixture_llamafactory_{kind}"
    dataset.mkdir(parents=True)
    media = tmp_path / "media"
    media.mkdir()
    (media / "view.jpg").write_bytes(b"local image fixture")
    row = {
        "system": "Write a shader.",
        "conversations": [
            {"from": "human", "value": ("<image>" if kind != "text" else "") + "Draw a sphere."},
            {"from": "gpt", "value": "```glsl\n// " + ("<image>" if literal_in_answer else "sphere") + "\n```"},
        ],
    }
    if kind != "text":
        row["images"] = ["view.jpg"]
    (dataset / "train.parquet").write_text(json.dumps([row]))
    (dataset / "qc.json").write_text(json.dumps({"rows": 1}))

    status = run_cli(monkeypatch, entry_dir, "audit_pairs.py", "--roots", root, "--media_root", media)

    assert status == int(literal_in_answer)
    output = capsys.readouterr().out
    expected = "placeholder_in_text_pair" if kind == "text" else "placeholder_mismatch"
    assert (expected in output) == literal_in_answer
    if not literal_in_answer:
        assert "problems: NONE" in output


@pytest.mark.parametrize("entry_dir", ENTRY_DIRS)
def test_builder_drops_literal_image_token_without_dropping_valid_sample(
    tmp_path, monkeypatch, offline_io, entry_dir
):
    code = "void mainImage(out vec4 color, in vec2 xy) { color = vec4(1.0); }"
    rows = [
        {"id": "valid", "code": code, "captions": {"instruction": "A bright sphere against a dark background."}},
        {"id": "literal", "code": code + "\n// <image>", "captions": {"instruction": "A bright sphere against a dark background."}},
    ]
    offline_io.read_table = lambda path: types.SimpleNamespace(to_pylist=lambda: rows)
    exists = os.path.exists
    monkeypatch.setattr(
        os.path,
        "exists",
        lambda path: str(path).endswith("/review_fixture/metadata.parquet") or exists(path),
    )
    images = tmp_path / "images"
    source_images = images / "review_fixture"
    for row in rows:
        sample_dir = source_images / row["id"]
        sample_dir.mkdir(parents=True)
        (sample_dir / "view.png").write_bytes(b"cached local render")
    (source_images / ".scan_complete").write_text("2\n")
    output = tmp_path / "output"

    status = run_cli(
        monkeypatch, entry_dir, "build_pairs_3dcodebench.py",
        "--subdir", "review_fixture", "--dialect", "glsl",
        "--out", output, "--img_root", images, "--workers", "1",
    )

    assert status == 0
    for kind in ("text", "img", "imgtext"):
        dataset = output / f"review_fixture_llamafactory_{kind}"
        built = json.loads((dataset / "train.parquet").read_text())
        assert [row["id"] for row in built] == ["valid"]
        assert built[0]["conversations"][1]["value"] == f"```glsl\n{code}\n```"
        assert sum(t["value"].count("<image>") for t in built[0]["conversations"]) == len(built[0].get("images", []))
        qc = json.loads((dataset / "qc.json").read_text())
        assert qc["literal_image_token"] == 1
        assert qc["source_samples"] == 2
        assert qc["rows"] == qc["usable"] == 1


@pytest.mark.parametrize("filename", LEGACY_SCRIPTS)
def test_legacy_cli_forwards_arguments_exit_status_and_sibling_imports(tmp_path, filename):
    component = tmp_path / "checkout with spaces" / "finetune"
    legacy = component / "scripts"
    canonical = component / "llamafactory"
    legacy.mkdir(parents=True)
    canonical.mkdir()
    shutil.copyfile(FINETUNE / "scripts" / filename, legacy / filename)
    (canonical / "forwarder_sibling.py").write_text("VALUE = 'canonical sibling'\n")
    (canonical / filename).write_text(
        "import json, sys\n"
        "from forwarder_sibling import VALUE\n"
        "print(json.dumps({'args': sys.argv[1:], 'sibling': VALUE, 'name': __name__}))\n"
        "raise SystemExit(7)\n"
    )
    args = ["--out", "folder with spaces", "--workers", "3"]

    result = subprocess.run(
        [sys.executable, str(legacy / filename), *args],
        cwd=tmp_path, capture_output=True, text=True, timeout=10, check=False,
    )

    assert result.returncode == 7, result.stderr
    assert json.loads(result.stdout) == {"args": args, "sibling": "canonical sibling", "name": "__main__"}

"""Offline: frame statistics + the gl_frames gate on synthetic frames."""

from __future__ import annotations

import numpy as np
from PIL import Image

from codeverse3d.contracts.artifacts import Severity
from codeverse3d.spatial.frame_stats import frame_gate, sequence_stats


def _png(path, arr):
    Image.fromarray((np.clip(arr, 0, 1) * 255).astype(np.uint8), "RGB").save(path)
    return path


def _gradient(t: float, w=160, h=90, moving=True):
    x = np.linspace(0, 1, w, dtype=np.float32)[None, :].repeat(h, 0)
    y = np.linspace(0, 1, h, dtype=np.float32)[:, None].repeat(w, 1)
    shift = 0.2 * t if moving else 0.0
    r = 0.5 + 0.5 * np.sin(6.0 * (x + shift))
    g = y
    b = 0.5 + 0.5 * np.cos(4.0 * (y - shift))
    return np.stack([r, g, b], -1)


def _kinds(gate):
    return {f.data["kind"]: f.severity for f in gate.findings}


def test_static_sequence_warns_only_when_motion_expected(tmp_path):
    frames = [(t, _png(tmp_path / f"s{i}.png", _gradient(0.0, moving=False))) for i, t in enumerate((0.0, 1.0, 2.5))]
    seq = sequence_stats(frames)
    assert seq.static and seq.n_duplicates == 2
    gate = frame_gate(seq, motion_expected=True)
    assert gate.passed and _kinds(gate)["static"] == Severity.WARN
    assert "static" not in _kinds(frame_gate(seq, motion_expected=False))


def test_black_and_blown_and_nan_are_errors(tmp_path):
    black = [(t, _png(tmp_path / f"b{i}.png", np.zeros((90, 160, 3)))) for i, t in enumerate((0.0, 1.0))]
    g = frame_gate(sequence_stats(black))
    assert not g.passed and _kinds(g)["black"] == Severity.ERROR
    white = [(t, _png(tmp_path / f"w{i}.png", np.ones((90, 160, 3)))) for i, t in enumerate((0.0, 1.0))]
    g = frame_gate(sequence_stats(white))
    assert not g.passed and _kinds(g)["blown"] == Severity.ERROR
    ok = [(t, _png(tmp_path / f"n{i}.png", _gradient(t))) for i, t in enumerate((0.0, 1.0))]
    seq = sequence_stats(ok, nan_counts=[(0, 0), (12, 1)])
    assert seq.any_nan and seq.frames[1].nan == 12
    g = frame_gate(seq)
    assert not g.passed and _kinds(g)["nan"] == Severity.ERROR and "guard divisions" in g.errors[0].fix_hint


def test_flicker_and_low_detail_warn(tmp_path):
    a = _gradient(0.0)
    b = 1.0 - a
    frames = [(0.0, _png(tmp_path / "x0.png", a)), (1.0, _png(tmp_path / "x1.png", b)), (2.0, _png(tmp_path / "x2.png", a))]
    seq = sequence_stats(frames)
    assert seq.flicker and _kinds(frame_gate(seq))["flicker"] == Severity.WARN
    flat = [(t, _png(tmp_path / f"l{i}.png", np.full((90, 160, 3), 0.4 + 0.01 * t))) for i, t in enumerate((0.0, 1.0, 2.0))]
    g = frame_gate(sequence_stats(flat))
    assert _kinds(g)["low_detail"] == Severity.WARN and g.passed


def test_empty_sequence_is_an_error():
    g = frame_gate(sequence_stats([]))
    assert not g.passed and g.errors[0].data["kind"] == "no_frames"

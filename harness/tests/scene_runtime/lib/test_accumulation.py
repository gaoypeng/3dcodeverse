"""accumulation.js — regression: the lip under a cover's edge darkened walls that carried no deposit at all."""
from __future__ import annotations

import pytest
from _probe import SHADER_JS, _find, _main_body, measure

pytestmark = pytest.mark.node

_LIBS = ("shader.js", "accumulation.js")


_PRELUDE = SHADER_JS + """
import * as THREE from 'three';
import { patchSnow, patchSand } from './lib/accumulation.js';

const std = (o = {}) => new THREE.MeshStandardMaterial(
    Object.assign({ color: 0x8b8478, roughness: 0.7 }, o));
"""


def _probe(body: str) -> dict:
    return measure(_PRELUDE + body, _LIBS)


def test_the_cover_shades_only_the_material_it_actually_stops_against():
    """The reference took a fixed 0.55 below the coverage threshold, which falls
    under 0.55 past a dusting — so zero deposit sat inside the ramp and every bare
    wall came back up to 21% darker.  The band is a FRACTION of the threshold."""
    out = _probe("""
const m = std();
patchSnow(m, { depth: 0.05 });
patchSand(m, { amount: 0.5 });
const s = compile(m);
console.log(JSON.stringify({ fs: s.fragmentShader, vs: s.vertexShader }));
""")
    body = _main_body(out["fs"])
    for pre in ("sn", "sd"):
        lip = _find(rf"float {pre}Lip = \(1\.0 - {pre}K\)"
                    rf" \* smoothstep\({pre}T \* ([\d.]+),"
                    rf" {pre}T - ([\d.]+),\s*{pre}D\);", body)
        frac_, top = float(lip.group(1)), float(lip.group(2))
        assert 0 < frac_ < 1, f"{pre}Lip's floor must scale with {pre}T"
        assert frac_ * 0.18 > 0 and top < 0.18, (
            f"{pre}Lip's band must stay inside the smallest threshold")
        _find(rf"diffuseColor\.rgb \*= 1\.0 - ([\d.]+) \* {pre}Lip;", body)
    # No fixed offset anywhere: that is the shape of the bug.
    assert "smoothstep(snT - 0.55" not in body
    assert "smoothstep(sdT - 0.55" not in body
    # Thin snow lets the substrate through; deep snow does not.
    thin = _find(r"float snThin = mix\(([\d.]+), 1\.0,"
                 r" smoothstep\([\d.]+, [\d.]+, snD\)\);", body)
    assert 0 < float(thin.group(1)) < 1
    _find(r"float snAmt = clamp\(snK \* snThin", body)
    # Nothing is displaced, and the vertex stage does no work beyond the world
    # position: the base is the whole of it.
    vbody = _main_body(out["vs"])
    assert "transformed +=" not in vbody and "transformed *=" not in vbody

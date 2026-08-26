"""The delivered code's own vector — see codeverse/flywheel/code_quality.py."""

from __future__ import annotations

from codeverse.flywheel.code_quality import measure

TIDY = '''"""Stool."""
import bpy, bmesh, math
SEAT_D, SEAT_T, SEAT_Z = 0.34, 0.04, 0.45
LEG_R, LEG_N, LEG_RING = 0.02, 3, 0.12

def build_seat():
    """Seat disc with its top at SEAT_Z."""
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=48, radius1=SEAT_D / 2, radius2=SEAT_D / 2, depth=SEAT_T)
    return bm

def build_leg(i: int):
    """One of LEG_N legs on a ring of LEG_RING."""
    a = 2 * math.pi * i / LEG_N
    return (LEG_RING * math.cos(a), LEG_RING * math.sin(a))

def main():
    build_seat()
    for i in range(LEG_N):
        build_leg(i)
main()
'''

MESSY = '''import bpy, bmesh
def main():
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=48, radius1=0.17, radius2=0.17, depth=0.04)
    bmesh.ops.translate(bm, vec=(0, 0, 0.43), verts=bm.verts)
    bm2 = bmesh.new()
    bmesh.ops.create_cone(bm2, cap_ends=True, segments=24, radius1=0.02, radius2=0.02, depth=0.41)
    bmesh.ops.translate(bm2, vec=(0.12, 0, 0.205), verts=bm2.verts)
    bm3 = bmesh.new()
    bmesh.ops.create_cone(bm3, cap_ends=True, segments=24, radius1=0.02, radius2=0.02, depth=0.41)
    bmesh.ops.translate(bm3, vec=(-0.06, 0.104, 0.205), verts=bm3.verts)
def unused_helper(x):
    return x * 3.7
main()
'''


def test_named_constants_and_one_function_per_part_score_higher_than_a_script():
    tidy, messy = measure({"src/model.py": TIDY}), measure({"src/model.py": MESSY})
    assert tidy.index > 0.8 > 0.5 > messy.index, (tidy.index, messy.index)
    assert tidy.magic_per_100loc < 10 < messy.magic_per_100loc, "the contract's 'plan numbers = named constants' as a number"
    assert tidy.dead_functions == 0 and messy.dead_functions == 1
    assert tidy.docstring_cov and tidy.docstring_cov > 0.5
    assert tidy.const_names >= 6 and messy.const_names == 0
    assert tidy.method == "ast"


def test_the_same_fields_come_back_for_javascript_approximately():
    js = '''import * as THREE from 'three';
const SEAT_D = 0.34, SEAT_T = 0.04, LEG_N = 3;
export function buildSeat() { return new THREE.CylinderGeometry(SEAT_D / 2, SEAT_D / 2, SEAT_T, 48); }
export function buildLeg(i) { const a = 2 * Math.PI * i / LEG_N; return [Math.cos(a) * 0.12, Math.sin(a) * 0.12]; }
'''
    q = measure({"src/object.js": js})
    assert q.method == "tokens" and q.n_functions >= 2 and q.const_names == 3
    assert q.docstring_cov is None, "docstrings are a python notion; JS does not fake one"


def test_nothing_parseable_is_none_not_zero():
    assert measure({}) is None
    assert measure({"src/model.py": "def broken(:\n"}) is None, "a syntax error is 'unmeasured', not 'index 0'"


def test_duplication_sees_copy_paste():
    block = "    x = build_thing(1.234, 5.678)\n    y = other(x, 9.10)\n    z = third(y, 2.22)\n    w = fourth(z)\n    v = fifth(w)\n    u = sixth(v)\n"
    once = "def a():\n" + block + "def b():\n    return 1\n"
    twice = "def a():\n" + block + "def b():\n" + block
    assert measure({"m.py": twice}).duplication > measure({"m.py": once}).duplication

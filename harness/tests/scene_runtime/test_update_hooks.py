"""Detect disconnected animation dispatch without silently driving agent hooks."""
from __future__ import annotations

import json

import pytest

from codeverse3d.contracts.artifacts import Severity
from codeverse3d.spatial.probes import probe_scene
from tests.scene_runtime.conftest import needs_browser, needs_node, run_node_json


@pytest.mark.node
@needs_node
def test_observer_preserves_calls_aliases_context_and_restores_after_throw():
    result = run_node_json("""
import * as T from 'three';
import { observeUpdateHooks } from './lib/update_hooks.mjs';
const scene=new T.Scene(), zone=new T.Group(), moving=new T.Group(), idle=new T.Group();
zone.name='Bay';moving.name='Wing';idle.name='Detached';scene.add(zone);zone.add(moving,idle);
let calls=0;const original=function(t){calls++;this.time=t;};
moving.userData.update=original;moving.userData.tick=original;idle.userData.update=()=>{throw Error('must not invoke missing hook');};
const report=observeUpdateHooks(scene,()=>moving.userData.tick(1.5));
let caught=false;try{observeUpdateHooks(scene,()=>{moving.userData.update(2);throw Error('expected');});}catch(e){caught=e.message==='expected';}
console.log(JSON.stringify({report,calls,time:moving.userData.time,caught,
 restored:moving.userData.update===original&&moving.userData.tick===original}));
""")
    assert result["calls"] == 2 and result["time"] == 2
    assert result["caught"] and result["restored"]
    assert [row["path"] for row in result["report"]["unobserved"]] == ["Bay/Detached"]
    assert result["report"]["hooks"][0]["calls"] == 1


@pytest.mark.node
@needs_browser
@pytest.mark.parametrize("forward", [False, True])
def test_probe_identifies_disconnected_child_and_does_not_invoke_it(ws, forward):
    call = "wing.userData.update(t);" if forward else ""
    (ws.src / "scene.js").write_text("""
import * as THREE from 'three';
export function createScene() {
 const scene=new THREE.Scene(),zone=new THREE.Group(),wing=new THREE.Group();
 zone.name='CentralPad';wing.name='Shuttle';scene.add(zone);zone.add(wing);
 wing.add(new THREE.Mesh(new THREE.BoxGeometry(),new THREE.MeshBasicMaterial()));
 wing.userData.update=t=>{wing.rotation.y=t;};
 zone.userData.update=t=>{FORWARD};
 return {scene,cameras:[{name:'hero',position:[3,2,3],lookAt:[0,0,0]}],update:t=>zone.userData.update(t)};
}
""".replace("FORWARD", call))
    result = probe_scene(ws)
    assert result.ok and result.gate.passed
    findings = [f for f in result.gate.findings if f.data.get("kind") == "unobserved_animation_hook"]
    assert bool(findings) is not forward
    audit = json.loads((ws.artifacts / "scene_probe.json").read_text())["census"]["animation_hooks"]
    shuttle = next(row for row in audit["hooks"] if row["path"] == "CentralPad/Shuttle")
    assert (shuttle["calls"] > 0) is forward
    if not forward:
        assert findings[0].severity == Severity.WARN and findings[0].target == "CentralPad"
        assert "parent zone" in findings[0].fix_hint

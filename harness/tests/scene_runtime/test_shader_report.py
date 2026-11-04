"""Runtime compilation must arbitrate uncertain source-literal include findings."""
from __future__ import annotations

import json

import pytest

from tests.scene_runtime.conftest import needs_node, run_node_json

pytestmark = [pytest.mark.node, needs_node]


def compile_report(*, programs=2, shader_errors=None, boot_ok=True, uniform_error=False):
    body = """
import { compileIntoReport } from './lib/shader_report.mjs';
const options = OPTIONS;
const report = {errors: [{kind:'include_not_alone', file:'src/scene.js', line:7,
 message:'include inside a source literal', fix_hint:'put include on its own line'}], warnings:[]};
if(options.uniform_error) report.errors.push({kind:'unbound_uniform', message:'uTime missing'});
const comp = {shader_errors:options.shader_errors || [], material_audit:[],
 programs:options.programs, custom_materials:1, compile_ms:1};
const host = {boot:{ok:options.boot_ok, renderer:'test', stage:'ready', error:''},
 gpu:false, errors:{console:[],page:[],network:[],warnings:[],shader_console:[]}, page:{evaluate:async()=>comp}};
await compileIntoReport(report, [], host);
console.log(JSON.stringify(report));
""".replace(
        "OPTIONS",
        json.dumps(
            dict(
                programs=programs,
                shader_errors=shader_errors,
                boot_ok=boot_ok,
                uniform_error=uniform_error,
            )
        ),
    )
    return run_node_json(body)


def test_successful_evaluated_shader_retains_static_advice_without_false_failure():
    result = compile_report()
    assert result["errors"] == []
    assert result["warnings"][0]["kind"] == "include_not_alone"
    assert result["warnings"][0]["validation"] == "runtime_compile_passed"

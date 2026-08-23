# 3dcodeverse

A backend harness in which LLMs write **raw, executable 3D code** — Blender `bpy`,
CadQuery, Three.js, URDF, GLSL — and a harness plans, builds, measures, renders,
judges, refines and records every run as data-flywheel material.

* **Tracks:** `static_object` · `articulated_object` · `scene`
* **Languages:** `blender` · `cadquery` · `threejs` · `urdf_blender` · `scene_threejs`
* **Backends:** Gemini / Anthropic / OpenAI APIs; gemini-cli, Claude Code, Codex,
  Antigravity CLI; and an in-process `api-agent` tool loop on any API model.
* **Spatial tools** (also an MCP server for agentic CLIs): build, measure,
  render_views/sheet, isolate, cross_section, check_connectivity, check_contract,
  compare_silhouette, joint_sweep, shader_probe, scene_probe, read_cookbook.
* **Judging:** rubric VLM judge with code-computed scores, floors and caps from
  deterministic gates; pairwise and reference judges.
* **Flywheel:** git-versioned `src/` per round, `record.json`, sample export,
  preference/repair pairs, captions.

```bash
pip install -e .
c3v doctor
c3v make "a mid-century wooden dining chair" --track static_object --language blender
c3v make "a kitchen cabinet with one door and a drawer" --track articulated_object --language urdf_blender
c3v make "a small japanese garden at dusk with a koi pond" --track scene --language scene_threejs
```

Design: `docs/ARCHITECTURE.md` · Interfaces: `docs/INTERFACES.md` · Working rules: `CLAUDE.md`.

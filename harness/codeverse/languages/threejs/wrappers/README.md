The three.js build/export wrappers are node scripts and live in `runtime_js/`
(`export_glb.mjs`, `render_glb.mjs`, `lib/*.mjs`).  They are invoked through
`codeverse.spatial.node.run_node` with the `--import lib/resolve_three.mjs`
hook so agent code can `import 'three'` without a node_modules of its own.
Agent code never imports anything from here.

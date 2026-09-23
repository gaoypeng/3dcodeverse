# GPT-6 Luna usability cases

The original two modules were authored by GPT-6 Luna through Codex CLI in a separate
workspace. The model received the effects catalog, contract and shipped library
source. It was instructed not to read or copy the six hand-authored studies.
The retained source is copied unchanged from the second model invocation.

- [Candle courtyard](luna_candle_courtyard.js)
- [Coastal creek](luna_coastal_creek.js)
- [Initial prompt](prompt.md), [supervisor feedback](feedback.md), [provenance](provenance.json)

The initial courtyard failed to boot because a helper referenced an undefined
`THREE`. The initial creek rendered, but its water/bed ribbon sat above the
terrain. The supervisor rendered the drafts, inspected the images and returned
those concrete failures to the model. In the second invocation the model fixed
the import/scope error, revised lighting and camera framing, and rebuilt the
creek banks from the stream sampler. Both revised scenes produced six real GPU
views at 1280x720, times 0 and 2, with zero console or shader errors.

These are usability trials, not aesthetic gold standards. The creek still has
an overly regular water boundary and sparse sand grass, and the courtyard's
large props and lighting remain visibly procedural. A successful render does
not establish photographic realism. The six library studies offer more carefully
composed views of each effect. Neither these two examples nor the
Gemini trials constitute a controlled comparison of model quality.

The original sources and CLI provenance remain unchanged. The current gallery
uses A1 for the retained courtyard, A2 for the separately reviewed creek
refinement below, and A3 for a new garden case. `build.py` stages and renders them
through the same production host as the library studies, using current shipped
library code. `check.mjs` exercises their live controls and video decoding.
Raw CLI events and draft renders remain under `../output/agent_runs/` (ignored
by git); hashes and prompts above preserve the retained source's provenance.

The first invocation's browser attempt was blocked by its sandbox's localhost
bind policy. The second enabled sandbox network access to use the existing
local renderer, while retaining workspace write restrictions. No API key or new
dependency was provisioned. The CLI's non-interactive workflow is documented in
[official OpenAI documentation](https://learn.chatgpt.com/docs/non-interactive-mode).

## Later delegated-agent cases

The following work used a delegated GPT-6 Luna coding agent in this development
session. It is not an additional vendor-CLI or harness trial, and does not establish
relative model performance.

- A2: [Refined coastal creek](luna_coastal_creek_refined.js),
  [provenance and measurements](luna_coastal_creek_refined.provenance.json).
  Three review iterations corrected the world/local height conversion, carved
  the sand banks from the stream sampler, and replaced tiled pebble materials
  with individual rocks. The final triangle-height measurement sampled 41
  positions on each bank: maximum bank-to-base-water-grade error was 0.010613 m.
  Nine fixed-camera GPU frames at 0, 1.5 and 3 seconds had no console, shader or
  update errors. The original `luna_coastal_creek.js` remains available above.
- A3: [Garden workbench](luna_garden_workbench.js),
  [provenance](luna_garden_workbench.provenance.json).
  Two review iterations revised the workbench and mug to real dimensions,
  composed backlit steam, and batched repeated plants and masonry. Nine GPU views
  passed; its recorded census is 1,161,394 triangles, 133 meshes and 14,897 instances.

Both cases remain visibly procedural. The creek has a smooth, cool-looking
water ribbon; the garden has stylized planting and terrace geometry, and its
steam is clearest in the close view. The scene source and library hashes in each
new gallery build bind that build's media to its own source snapshot; the earlier
review records describe the exact earlier snapshots in their provenance files.

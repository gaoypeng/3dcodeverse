# plan.json for `graphics`

Fields: `title`, `summary`, `style`, `resolution`, `duration_s`, `passes[]`, `uniforms[]`,
`motion`, `key_visuals[]`, `acceptance[]`.  The effect is rendered headless at `resolution`
and judged on frames at t = 0, 1, 2.5, 4 and 6 s.

1. `title` short PascalCase; `summary`: what the viewer sees and what moves.
2. `style`: the palette in words with each colour's role (triples such as (0.9, 0.2, 0.6)),
   the mood, references, rendering character (soft/filmic, crisp/flat, glow) — the judge's
   `light_and_tone` criterion grades the frames against this paragraph.
3. `resolution`: `[1280, 720]` unless the request is explicitly vertical (`[720, 1280]`); at most
   1920 × 1080.  `duration_s` ≥ 6 (the preview loop; judged times stop at 6 s).
4. `passes[]` in draw order: `name` PascalCase, `kind` (`fullscreen` | `geometry` | `postprocess`
   | `feedback`), `description` — what it draws, the technique by name, the quantities (counts,
   sizes as screen fractions, speeds, the style's colours); optional `elements[]` (≤ 8 named
   things with their numbers) and `detail_hint`.  A fragment shader's pass is a layer composited
   inside the one shader; an OpenGL program's may be a real pass (geometry, an FBO).  One field
   filling the frame is the `flat_fill` defect (−0.08) and the 0.1 anchor of `depth_and_detail`
   (weight 0.16): plan structure at the scales the subject has.
5. `uniforms`: the harness uniforms the effect needs (`u_time`, `u_resolution`, `u_prev` for
   feedback, `u_noise`).
6. `motion`: ONE paragraph — what animates, direction, speed in screen fractions per second or
   Hz, easing, and what stays still.  Every request animates unless it says "static image": the
   largest mean |Δ| between consecutive judged frames must reach 0.002 or the score caps at 0.5.
7. `key_visuals`: the nouns a viewer must recognise.
8. `acceptance`: one item per must-have and per key visual (`how: "visual"`), ids `a1…`;
   `must` only for what makes it the wrong effect when absent (an unverified `must` caps at 0.7).

Plan inside the language: a fragment shader has no geometry (everything is a function of the
pixel, `u_time` and the two samplers); an OpenGL program may draw meshes, instances and passes.
Give the effect one idea that makes it specific to the request — not a textbook plasma with the
brief's colours swapped in (`placeholder_look`, −0.10).

# Image prompt style (texturing)

Every texture prompt sent to the image model is composed by the harness as:

    <subject from the material plan>, <family phrase>, seamless tileable texture,
    perfectly repeating at the edges, top-down orthographic view, flat even diffuse
    lighting, no shadows, no specular highlights, no vignette, no depth of field,
    no text, no watermark, no border, fills the whole frame edge to edge, photoreal,
    uniform scale, 1:1 square

Family phrases (appended after the subject so the image model knows what physical
surface it is drawing):

| family   | phrase |
|----------|--------|
| wood     | natural wood surface, visible grain and subtle pores, uniform plank-free grain unless planks are named |
| metal    | metal surface albedo only, matte base colour, no reflections or environment |
| fabric   | woven textile close-up, visible thread weave, soft matte |
| stone    | stone surface, natural mineral variation, matte |
| plastic  | smooth plastic surface, subtle fine noise, uniform colour |
| leather  | leather surface, fine natural grain and pores, matte |
| glass    | (skipped by default — glass keeps its flat material) |
| ceramic  | glazed ceramic surface, subtle speckle, uniform colour |
| painted  | painted surface, subtle brush or roller micro-texture, uniform colour |
| rubber   | matte rubber surface, fine uniform grain |
| other    | matte natural surface material, fine detail (objects: skipped unless the planner names a subject; scenes: soil, grass, moss, sand) |

Negative phrasing is kept inside the positive prompt (Gemini image models have no
negative prompt field).  The pattern MUST be scale-free: no objects, no horizon, no
single feature (one knot, one logo) that would repeat visibly every tile.

"""Curated teaser content — one entry per shipped artefact."""

M = "../bench/out/teaser/site/media/"

LEAD = dict(
    id="penny", title="Penny-farthing", slug="tsr_obj_penny_farthing",
    langs=["Blender · bpy"], gen="codex:gpt-5.6-sol", score=0.863, verdict="reads",
    rounds="2 rounds", mins="31 min", track="static_object",
    prompt="a Victorian penny-farthing bicycle: a very tall spoked front wheel, a small rear wheel, "
           "a curved backbone frame, a sprung leather saddle above the front hub, pedals on the front "
           "hub, curved handlebars and polished brass fittings",
    note="Kept as the first run. The spoke web is pure line art and the silhouette reads instantly from "
         "all eight canonical views; both wheels sit on the ground, nothing floats or explodes. "
         "<b>Honest miss:</b> the saddle and grips came out fire-engine red instead of tan leather, so the "
         "promised black-lacquer / brass / leather trio reads black / brass / <i>red</i>. The text-to-image "
         "texture pass ran and was correctly <i>refused</i> (Δ overall −0.290, materials 0.7 → 0.2) — what you "
         "see is <code>object.glb</code> with the raw bpy materials.",
    media=[("img", "penny_farthing", "hero · left elevation"),
           ("gif", "tt_penny_farthing", "turntable · 36 frames"),
           ("img", "penny_farthing_sheet", "8 canonical views")],
)

STATIC = [
    dict(id="arcade", title="Arcade cabinet", slug="tsr_tjs_arcade_cabinet",
         langs=["three.js"], gen="api-agent:gemini:gemini-3.7-flash", score=0.948, verdict="reads",
         rounds="1 round", mins="34 min",
         prompt="an upright 1980s arcade cabinet: a sloped control panel with a ball-top joystick and six "
                "buttons, a recessed CRT screen behind a bezel, a glowing backlit marquee, a speaker grille, "
                "side art panels, a coin door and T-molding trim",
         note="Best card in the lane and it landed on the first try — no refine round, no re-run. Sloped panel "
              "with ball-top stick and six buttons, recessed CRT behind a bezel, backlit marquee, saturated "
              "pink/cyan side art, coin door, red T-molding down both cabinet edges. Poster-bright, and it "
              "reads at thumbnail size from every angle. <b>Nits:</b> the marquee and CRT are authored emissive "
              "but the shaded render rig draws them as flat bright colour rather than glowing, and the speaker "
              "holes do not resolve. The plain black back and top are correct for a real cabinet.",
         media=[("img", "arcade", "hero · low front left"), ("gif", "tt_arcade", "turntable"),
                ("img", "arcade_sheet", "8 canonical views")]),
    dict(id="lighthouse", title="Coastal lighthouse", slug="tsr_obj_lighthouse",
         langs=["Blender · bpy", "text-to-image texture"], gen="api-agent:gemini:gemini-3.7-flash",
         score=0.7277, verdict="reads", rounds="3 rounds", mins="51 min",
         prompt="a coastal lighthouse on a rock plinth: a tapered stone tower with painted bands, a corbelled "
                "gallery with a railing, a glazed lantern room with a fresnel lens and a domed copper roof with "
                "a weathervane, and a keeper's door at the base",
         note="<b>The only piece in the whole wave where <code>--texture</code> paid.</b> The generated "
              "<code>weathered_granite_rock</code> map on the stepped plinth cleared the ship gate at "
              "Δ +0.065, so this card is rendered from <code>object_textured.glb</code>. Tapered banded tower, "
              "corbelled gallery with a full baluster railing, a brass fresnel drum visible inside the glazed "
              "lantern room, verdigris copper dome with weathervane. <b>Nits:</b> the winning round shows two "
              "white bands where an earlier round had three, and the coral/white livery is a touch cartoonish.",
         media=[("img", "lighthouse", "hero · low front left"), ("gif", "tt_lighthouse", "turntable"),
                ("img", "lighthouse_sheet", "8 canonical views")]),
    dict(id="gramophone", title="Wind-up gramophone", slug="tsr_obj_gramophone",
         langs=["Blender · bpy"], gen="api-agent:gemini:gemini-3.7-flash", score=0.75, verdict="reads",
         rounds="1 round", mins="48 min",
         prompt="an antique wind-up gramophone: a big flared brass horn on an elbow, an oak case with a felted "
                "turntable and a shellac record, a curved tonearm with a sound box, a crank handle on the side "
                "and turned feet",
         note="The horn flare is the reason this prompt was picked, and it delivered: a continuous specular "
              "sweep that travels across the bell as the turntable spins. Oak case with a moulded lip and four "
              "turned feet, green felt platter, black shellac record with a red centre label, curved tonearm "
              "with sound box, side crank — four distinct materials in one frame, and the horn does not "
              "intersect the case. <b>Nits:</b> the brass is a pale greenish gold rather than warm polished "
              "brass, and the case reads pine-pale rather than dark oak. Texture pass refused (Δ −0.371).",
         media=[("img", "gramophone", "hero · front right ¾"), ("gif", "tt_gramophone", "turntable"),
                ("img", "gramophone_sheet", "8 canonical views")]),
    dict(id="radial", title="Radial aero engine", slug="tsr_cad_radial_engine_v2",
         langs=["CadQuery"], gen="codex:gpt-5.6-sol", score=0.60, verdict="usable",
         rounds="3 rounds", mins="65 min", rerun="second attempt",
         prompt="a five-cylinder radial aero engine displayed on a low steel stand, in the correct museum "
                "orientation: ONE cylinder points straight UP at top dead centre and the other four are at 72 "
                "degree steps, so there is NO cylinder pointing straight down and the engine sits clear of the "
                "ground on its stand. A dark grey machined steel crankcase carries a bolted circular front cover "
                "and a splined propeller hub on the axis; each of the five barrels is a pale satin aluminium tube "
                "wrapped in evenly pitched square cooling fins, capped by a domed head with a black rocker cover; "
                "a slim brass pushrod tube lies flush along the front of each barrel from the crankcase to its "
                "rocker cover; a short polished steel exhaust stub leaves each head, all five swept the same way "
                "around the ring.",
         note="<b>Shown honestly as <i>usable</i>, not <i>reads</i>.</b> The first attempt scored the same 0.60 and "
              "was a broken picture — one barrel buried straight down through the ground plane, exhaust stubs "
              "reading as detached sticks, no stand, all grey. The sharpened re-run above added the museum "
              "orientation, an explicit stand, flush pushrods and a four-colour call-out, and now the star, the "
              "evenly-pitched fins, the black rocker covers and the splined prop hub all read from every view — "
              "<i>at the same judged score</i>. The remaining flaw is structural, not a bad draw: the CadQuery "
              "track's only material channel is <code>cq.Color(r,g,b)</code>, with no metalness, so the "
              "\"polished steel\" exhaust stubs render bone-white and the crankcase face is a flat grey disc.",
         media=[("img", "radial", "hero · front right ¾"), ("gif", "tt_radial", "turntable"),
                ("img", "radial_sheet", "8 canonical views")]),
    dict(id="chandelier", title="Art-deco chandelier", slug="tsr_tjs_deco_chandelier_v2",
         langs=["three.js"], gen="api-agent:gemini:gemini-3.7-flash", score=0.60, verdict="usable",
         rounds="4 rounds", mins="86 min", rerun="second attempt",
         prompt="an art-deco brass chandelier dripping with cut crystal: a stepped brass ceiling canopy and a "
                "chain drop to a fluted central stem; two tiers of curved brass arms, six above and eight below, "
                "each ending in a frosted glass candle sleeve with a small tapered flame-tip finial; and hanging "
                "from the rim of BOTH tiers, in even concentric rings, more than forty stepped faceted crystal "
                "drops — long slender prisms with cut facets, each hanging free on its own short brass link so "
                "the whole lower half of the fixture reads as a curtain of glass; a fluted clear glass bowl at "
                "the crown under the stem.",
         note="<b>Shipped despite a lower score than the run it replaces</b> (0.60 against 0.841). The "
              "higher-scoring first attempt was a competent brass armature with plain white cylinder shades and "
              "<i>zero</i> hanging glass — none of the sparkle the prompt was chosen for. This re-run made the "
              "drops the dominant feature and now has real candle sleeves with flame-tip finials, a clear fluted "
              "crown bowl, two proper tiers of curved arms on rings, and visible hanging crystal. <b>Nits:</b> "
              "the drops read as pale thin icicles rather than a curtain of cut glass, and the arms look "
              "slightly flat and ribbon-like.",
         media=[("img", "chandelier", "hero · front right ¾"), ("gif", "tt_chandelier", "turntable"),
                ("img", "chandelier_sheet", "8 canonical views")]),
]

ARTIC = [
    dict(id="rollcab", title="Rolling tool chest", slug="tsr_art_roll_cabinet",
         langs=["URDF", "Blender · bpy"], gen="api-agent:gemini:gemini-3.7-flash", score=0.957,
         verdict="reads", rounds="1 round", mins="31 min",
         prompt="a red steel rolling tool chest: seven drawers of three different heights on slides, full-width "
                "chrome pulls, a hinged top lid over a shallow tray, a push handle on one end and four castors",
         note="Passed at round zero, first try. Three materials genuinely separate — red carcass, chrome "
              "full-width pulls, black castors — and the articulation is the real prize: seven drawers on seven "
              "independent prismatic joints plus a hinged lid over a grey tray. The cascade still is the money "
              "shot the prompt was chosen for; it reads as <i>articulated</i> with no caption. <b>Honest miss "
              "the judge did not catch:</b> the brief asked for drawers in three distinct heights and they came "
              "out all the same height — <code>meshes/drawer1-6.glb</code> are byte-identical. Shipping "
              "<code>object.glb</code>, not the textured build: the texture pass painted one red powdercoat over "
              "all 28 parts including the chrome pulls and the castors, and shipped anyway on Δ 0.000.",
         media=[("img", "rollcab", "hero · seven drawers cascaded"),
                ("gif", "tt_rollcab", "joint sweep · 40 frames"),
                ("img", "rollcab_art", "articulation sheet · every joint"),
                ("img", "rollcab_sheet", "8 canonical views")]),
    dict(id="clock", title="Longcase clock", slug="tsr_art_grandfather_clock_v2",
         langs=["URDF", "Blender · bpy"], gen="api-agent:gemini:gemini-3.7-flash", score=0.9469,
         verdict="reads", rounds="2 rounds", mins="98 min", rerun="second attempt",
         prompt="a longcase grandfather clock in dark figured walnut with working articulation: a broken-arch "
                "bonnet with two turned finials over reeded columns, a warm brass dial with a silvered chapter "
                "ring and blued steel hour and minute hands, a glazed trunk door on brass hinges with CLEAR "
                "TRANSPARENT glass so the movement is visible through it, a long pendulum with a large polished "
                "brass lenticular bob swinging on a pivot, and two brass cylindrical driving weights hanging on "
                "either side of the pendulum inside the trunk, all on a stepped plinth base with bracket feet",
         note="The first attempt scored 0.600 and rendered the trunk door pane as an <i>opaque white panel</i>, "
              "hiding the pendulum and weights — the entire reason the prompt was picked. The re-run demanded "
              "base-colour alpha ≤ 0.25 on the glass, named the brass bob and weights, and asked for a stepped "
              "silhouette and four materials separated by <i>value</i>, not just hue. Now the movement is visible "
              "through the closed door, the door swings 90° onto it, the bob ticks left and right, and the hands "
              "turn on the silvered chapter ring. Round 1 also tightened the pendulum limits to ±0.09 rad, which "
              "fixed a round-0 flaw where the bob swung out <i>through</i> the case wall. <b>Nits:</b> the "
              "broken-arch crestings read as thin dark horns rather than crisp swan-neck mouldings, and the "
              "plinth front panel reads slightly as a hole. Shipping <code>object.glb</code> — the texture pass "
              "painted walnut burl over the brass dial <i>and</i> the transparent glass and shipped on Δ 0.000.",
         media=[("img", "clock", "hero · door open on the movement"),
                ("gif", "tt_clock", "48-frame loop · pendulum, hands, door"),
                ("img", "clock_strip", "motion strip"),
                ("img", "clock_sheet", "8 canonical views")]),
]

SCENES = [
    dict(id="alley", title="Neon alley", slug="tsr_scn_neon_alley_v2",
         langs=["three.js", "GLSL"], gen="api-agent:gemini:gemini-3.7-flash", score=0.8267,
         verdict="reads", rounds="5 rounds", mins="75 min", rerun="second attempt",
         prompt="a rain-soaked neon alley at night, seen down a narrow corridor between two TALL buildings whose "
                "walls fill the left and right thirds of the frame and rise out of the top of shot. MATERIALS AND "
                "LIGHTING ARE THE POINT: the ground is NEAR-BLACK wet asphalt (base colour 0x0a0c10, roughness "
                "0.08-0.15, metalness 0.6) that acts as a MIRROR — every sign above is reflected in it as a sharp, "
                "vertically-stretched coloured streak … Rain is THIN BRIGHT VERTICAL STREAKS, not dots: draw it as "
                "LineSegments or as very narrow elongated quads about 0.4 m long and 0.01 m wide … NEVER render "
                "rain as square point sprites or large white squares.",
         note="The first attempt was only <i>usable</i>, and a scene census said why: <code>custom_materials</code> "
              "was empty — there was no wet-asphalt <code>ShaderMaterial</code> at all, so \"every sign mirrored in "
              "the asphalt\", the whole point of the prompt, never existed. The re-run spelled the material out "
              "numerically and the rain out geometrically. Round 0 then overshot into an 89 %-black frame at mean "
              "luminance 0.001; the refine loop pulled it back by round 4. The final frame is genuinely "
              "teaser-grade — near-black wet ground carrying long hot-pink and cyan reflection columns, rain as "
              "thin vertical streaks, a pixelated holo billboard. <b>Weaknesses:</b> the upper walls are a smooth "
              "washed lavender gradient with no surface detail, and the litter props are crude low-poly boxes.",
         shots=[("alley_a", "Establishing · t = 0 s"), ("alley_b", "Establishing · t = 1.5 s"),
                ("alley_low", "LowAngleReflections · the wet-asphalt mirror"),
                ("alley_sign", "SignDetail · emissive signage")],
         sheet=("alley_sheet", "all authored cameras, both times")),
    dict(id="boat", title="Boatbuilder's workshop", slug="tsr_scn_boat_workshop_v2",
         langs=["three.js", "GLSL", "Blender · bpy"], gen="api-agent:gemini:gemini-3.7-flash", score=0.747,
         verdict="reads", rounds="4 rounds", mins="81 min", rerun="second attempt", ml=True,
         prompt="INSIDE a boatbuilder's workshop at dusk — a FULLY ENCLOSED INTERIOR. THE ENCLOSURE IS THE "
                "ENVIRONMENT AND MUST BE BUILT FIRST: a rectangular timber shed about 16 m x 10 m x 6 m high with "
                "FOUR SOLID WALLS … A GLSL shader makes the window sunbeams VOLUMETRIC — visible cones of hazy "
                "light crossing the room, brightest near the glass. Sawdust motes drifting in those beams must be "
                "FINE, SMALL and SOFT … NEVER render them as large opaque white squares.",
         note="A large win over the first attempt (0.465 → 0.747), and not a lighting fix: attempt one built an "
              "open-air truss frame standing in a <i>pine forest</i> under a flat red sky — no walls, no roof skin, "
              "trees inside the workshop. The scene planner's environment section is written for exteriors and "
              "treats interiors as a parenthetical, so the plan never contained walls and refining could not add "
              "them. Forcing the enclosure first produced the most complete scene in the wave: real interior, "
              "dusty window wall with cool blue dusk outside against warm raking sunbeams and stove glow, plank "
              "floor, timber stacks, tool racks, shavings. The sawdust motes render as soft additive sprites here, "
              "fixing the white-square particle bug that spoiled the first neon run. <b>Not shown:</b> a pack of 8 "
              "tileable textures (seam ≈ 0.0007) was generated for this scene and could not be wired in — a passed "
              "run refuses <code>resume</code> without <code>--force</code>, and <code>--force</code> re-plans and "
              "overwrites the passing artefact.",
         shots=[("boat_a", "Establishing · t = 0 s"), ("boat_b", "Establishing · t = 1.5 s"),
                ("boat_bench", "WorkbenchEye · volumetric GLSL sunbeams"),
                ("boat_skiff", "SkiffDetail · the lapstrake hull"),
                ("boat_stove", "StoveCornerDetail · the Blender stove, in scene")],
         sheet=("boat_sheet", "all authored cameras, both times")),
    dict(id="temple", title="Temple courtyard at night", slug="tsr_scn_temple_night_v2",
         langs=["three.js", "GLSL", "Blender · bpy"], gen="codex:gpt-5.6-sol", score=0.286,
         verdict="usable", rounds="3 rounds", mins="85 min", rerun="second attempt", ml=True,
         prompt="a small temple courtyard on a koi pond at NIGHT — a deep indigo night (background 0x0b1026), lit "
                "ONLY by warm lantern flame … TWO HERO PROPS ARE AUTHORED IN BLENDER WITH bpy AND COMPILED TO GLB "
                "(asset kind blender_glb), then loaded into the three.js scene: (1) StoneLantern … (2) BronzeCenser "
                "… A custom GLSL water material is the HERO EFFECT and must be unmistakable: the pond is dark and "
                "highly reflective, with visible travelling ripples, the warm lantern lights reflected in it as "
                "long wobbling vertical streaks, and several koi visible moving under the surface.",
         note="<b>The clearest evidence in this wave that the judge cannot see what the picture is doing.</b> The "
              "first attempt scored <b>0.729</b> and \"passed\": a washed-out pale blue-grey courtyard that reads "
              "as snow at dawn, a flat grey pond, no ripples, no reflections, no koi. The light census for a scene "
              "specified as lit by stone lanterns was 1 ambient + 1 hemisphere + 2 directional + 10 point — the "
              "fill added to clear the frame-luminance floor was drowning the water shader, which had existed with "
              "a live time uniform the whole time. Re-prompting for dark granite, banning hemisphere light and "
              "promoting the water to hero effect produced the correct deep-indigo night, warm lantern pools, the "
              "pond finally reading as long warm reflection streaks, and the best single detail frame in the wave "
              "— and scored <b>0.286</b>. This card ships round 1 rather than the harness's best round 2, which "
              "scored marginally higher and lost the pond reflections. <b>Still honestly <i>usable</i>:</b> the "
              "paving is <i>still</i> pale lavender-white despite an explicit base-colour instruction, an unshaded "
              "white lamp sphere reads as a bug bottom-right, and the maple leaves render as flat red diamonds.",
         shots=[("temple_a", "Establishing · t = 0 s"), ("temple_b", "Establishing · t = 1.5 s"),
                ("temple_censer", "CenserDetail · the Blender censer, in scene"),
                ("temple_bridge", "BridgeEye · GLSL pond, lantern streaks")],
         sheet=("temple_sheet", "all authored cameras, both times")),
]

GFX = [
    dict(id="aurora", title="Aurora over a ridge", slug="tsr_gfx_aurora_ridge_v3",
         langs=["GLSL · fragment shader"], gen="api-agent:gemini:gemini-3.7-flash", score=0.94,
         verdict="reads", rounds="1 round", mins="14 min", rerun="third attempt",
         prompt="an aurora over a snowy ridge on a black winter night, painted in three clean horizontal bands. "
                "TOP TWO THIRDS — the sky: near-black deep-blue, crowded with thousands of small crisp white stars, "
                "and across it hang layered aurora curtains made of many thin bright vertical rays … LOWER THIRD — "
                "the ridge: a single SOLID mountain silhouette, filled with ONE flat near-black colour and nothing "
                "else … no vertical stripes, no bars, no banding, no noise, no texture … BOTTOM FIFTH — the frozen "
                "lake: a smooth flat mirror below the ridge …",
         note="Third attempt, and the story is in what changed. Attempt one was a flat magenta wash over a "
              "speckle-noise ridge. Attempt two fixed the sky beautifully — emerald-to-violet rays, crisp stars, "
              "dark gaps — but drew the ridge as a barcode of vertical grey stripes mirrored into the lake; it "
              "looked like a rendering bug. Attempt three kept that sky language and added one paragraph "
              "specifying the ground as three named bands with the ridge as <i>one flat near-black fill</i>, plus "
              "a hard <code>--must</code> clause. That single instruction fixed it in one round. <b>Residue:</b> "
              "the star field is sparse, the lake band is a little muddy, and a faint horizontal band artifact "
              "survives in the lower third.",
         media=[("img", "aurora", "t = 2.5 s"), ("gif", "tt_aurora", "animated preview"),
                ("img", "aurora_strip", "frames at 0 / 1 / 2.5 / 4 / 6 s")]),
    dict(id="murmur", title="Starling murmuration", slug="tsr_ogl_murmuration",
         langs=["Python · OpenGL", "GLSL"], gen="api-agent:gemini:gemini-3.7-flash", score=0.865,
         verdict="reads", rounds="4 rounds", mins="43 min",
         prompt="a starling murmuration at dusk: several thousand instanced birds flocking with cohesion, "
                "separation and alignment around a slowly moving attractor, their dark silhouettes thickening and "
                "thinning into shifting shapes against a graded orange to deep-blue sky, with a low dark treeline "
                "along the bottom edge",
         note="The frame strip is the proof: the flock genuinely morphs — a dense column at 0.75 s, spread wide "
              "and thinning at 3 s, re-tightening and drifting left by 5–6 s. That is real flocking, not a "
              "scrolling texture. Worth saying that the baseline round scored a hard <b>0.0</b> — a flat mauve "
              "frame with no birds at all — and the harness's own refine loop recovered it to 0.865 over three "
              "rounds with no intervention. <b>Weaknesses:</b> the nearer birds are chunky arrowheads rather than "
              "fine specks, the flock edge sprays individuals across the whole sky instead of holding a tight "
              "murmuration shape, and the bottom silhouette reads as a rocky ridge rather than the treeline asked "
              "for.",
         media=[("img", "murmur", "t = 1 s"), ("gif", "tt_murmur", "animated preview"),
                ("img", "murmur_strip", "frames at 0 / 1 / 2.5 / 4 / 6 s")]),
    dict(id="accretion", title="Accretion disc", slug="tsr_gfx_accretion_disc",
         langs=["GLSL · fragment shader"], gen="api-agent:gemini:gemini-3.7-flash", score=0.753,
         verdict="reads", rounds="2 rounds", mins="33 min",
         prompt="a black hole with a glowing accretion disc: a pure black sphere ringed by a thin bright photon "
                "ring, a hot orange-white disc of turbulent filaments orbiting it with the approaching side "
                "visibly brighter, the far side of the disc lensed up and over the shadow into an arc, and a star "
                "field warping near the edge",
         note="The most dramatic image in the wave, and it got there in one refine round (0.242 → 0.753). The far "
              "side of the disc is lensed up and over the shadow into a bright arc — the Gargantua geometry "
              "actually reads correctly, which is the hard part — and across the sample strip the filaments "
              "visibly shear and swirl. A follow-up run specifically to clean the background <i>did</i> deliver "
              "true black vacuum with stars, but flattened the composition into a symmetric halo, drew the stars "
              "as cheap four-point sparkles and left a grey band bisecting the black shadow, so this one ships. "
              "<b>Blemishes:</b> the background is a lavender-purple haze rather than black vacuum, and there is "
              "a faint grey smear in the top-left corner.",
         media=[("img", "accretion", "t = 2.5 s"), ("gif", "tt_accretion", "animated preview"),
                ("img", "accretion_strip", "frames at 0 / 1 / 2.5 / 4 / 6 s")]),
    dict(id="rain", title="Rain on a window", slug="tsr_gfx_rain_window",
         langs=["GLSL · fragment shader"], gen="api-agent:gemini:gemini-3.7-flash", score=0.9087,
         verdict="usable", rounds="1 round", mins="7 min",
         prompt="raindrops running down a dark window in front of an out-of-focus neon city: each drop refracts a "
                "tiny inverted image of the lights behind it, drops grow until they break loose and cut clean "
                "trails downward, trails merge as they cross, the glass carries a fine mist between the drops and "
                "the city bokeh drifts slowly",
         note="<b>The highest score on this page that is not the best picture on this page</b> — shown as "
              "<i>usable</i> on purpose. It reads immediately as rain on a window over a neon city, the big drops "
              "genuinely refract tiny inverted images of the lights, and the bokeh drifts between frames. But the "
              "whole pane sits in a pale lavender-grey haze instead of the dark glass the brief asked for, the "
              "bokeh are washy blobs rather than tight light sources, and the trails do not read as clean streaks. "
              "Attractive but soft. Two re-runs to fix the contrast were both worse: pushing this model toward "
              "\"dark / high-contrast / saturated\" collapses it out of photographic softness into flat vector "
              "shapes — one attempt came back as cartoon confetti discs on black with no drops at all.",
         media=[("img", "rain", "t = 2.5 s"), ("gif", "tt_rain", "animated preview"),
                ("img", "rain_strip", "frames at 0 / 1 / 2.5 / 4 / 6 s")]),
]

ML_ASSETS = [
    dict(key="lantern", name="StoneLantern", scene="Temple courtyard", kb=443,
         src="_assets/stone_lantern/src/model.py",
         glb="public/assets/stone_lantern.glb",
         use="src/assets/stone_lantern.js → loaders.gltf.loadAsync, cloned into 3 zones",
         desc="Carved granite, chamfered cap, pierced fire box, lotus plinth, moss-worn edges — "
              "the sculpted-stone case a stack of three.js primitives cannot fake.",
         verified=True),
    dict(key="censer", name="BronzeCenser", scene="Temple courtyard", kb=367,
         src="_assets/bronze_censer/src/model.py",
         glb="public/assets/bronze_censer.glb",
         use="src/scene.js:12 assetFiles → cloned in src/zones/courtyard_veranda.js:32",
         desc="Three-legged cast bronze, domed pierced lid, two ring handles, relief band. "
              "The best single detail frame in the wave is this object lit by its own charcoal glow.",
         verified=True),
    dict(key="stove", name="PotbellyStove", scene="Boat workshop", kb=131,
         src="_assets/potbelly_stove/src/model.py",
         glb="public/assets/potbelly_stove.glb",
         use="src/scene.js:12 assetFiles → cloned in src/zones/stove_corner.js:130",
         desc="Bellied cast iron, bevelled door with a latch, claw feet, flue collar. "
              "In scene the flue runs to the roof and the door glows.",
         verified=True),
    dict(key="skiff", name="ClinkerSkiff", scene="Boat workshop", kb=400,
         src="_assets/clinker_skiff/src/model.py",
         glb="public/assets/clinker_skiff.glb",
         use="loaded into ctx.assets — but NOT used: the in-scene hull is procedural three.js",
         desc="Lapstrake planking, visible ribs, bevelled stem, upturned on trestles. Real bpy, real GLB, "
              "and a good standalone object — but see the caveat below.",
         verified=False),
]

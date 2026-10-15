# Principled recipes — the node snippets

Companion to `c3d-bpy-scene-materials`. Socket names and node ids verified against Blender
5.2.2 LTS on 2026-10-14 (`codeverse3d/languages/blender/__init__.py` keeps the rename table the
lint reports from). Every snippet assumes `mat = bpy.data.materials.new(name)` and
`nodes, links = mat.node_tree.nodes, mat.node_tree.links`, `bsdf = nodes["Principled BSDF"]`.
Prefer the `lib.materials` call that does the same thing; write nodes only for what no call covers.

## 1. Socket renames the lint reports

| old key (raises) | 4.x / 5.x key |
|---|---|
| `Specular` | `Specular IOR Level` |
| `Subsurface` | `Subsurface Weight` |
| `Subsurface Color` | folded into `Base Color` |
| `Transmission` | `Transmission Weight` |
| `Transmission Roughness` | removed — use `Roughness` |
| `Emission` | `Emission Color`, with `Emission Strength` |
| `Clearcoat` | `Coat Weight` |
| `Sheen` | `Sheen Weight` |

`Specular Tint` and `Anisotropic` still exist; `Specular Tint` is an RGBA colour.

## 2. Value variance: noise into colour and bump

```python
noise = nodes.new("ShaderNodeTexNoise")
noise.inputs["Scale"].default_value = <texels per metre: 2-5 cm at walking distance>
noise.inputs["Detail"].default_value = 6.0
mix = nodes.new("ShaderNodeMix")
mix.data_type = "RGBA"
mix.inputs["A"].default_value = (<r>, <g>, <b>, 1.0)        # the base colour
mix.inputs["B"].default_value = (<r2>, <g2>, <b2>, 1.0)     # a few percent darker or warmer
links.new(noise.outputs["Fac"], mix.inputs["Factor"])
links.new(mix.outputs["Result"], bsdf.inputs["Base Color"])
bump = nodes.new("ShaderNodeBump")
bump.inputs["Strength"].default_value = <0.05-0.15 for stone and plaster>
links.new(noise.outputs["Fac"], bump.inputs["Height"])
links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
```

`lib.materials.material(name, color, noise_scale=..., bump=...)` wires exactly this; roughness
variation is the same noise into `Roughness` through a Map Range node.

## 3. Ground bands by slope and height

```python
geo = nodes.new("ShaderNodeNewGeometry")
sep = nodes.new("ShaderNodeSeparateXYZ")
links.new(geo.outputs["Normal"], sep.inputs["Vector"])
ramp = nodes.new("ShaderNodeValToRGB")                          # a ColorRamp: slope -> 0..1
ramp.color_ramp.elements[0].position = <cos of the rock angle, e.g. 0.65>
ramp.color_ramp.elements[1].position = <a few hundredths higher: a soft edge>
links.new(sep.outputs["Z"], ramp.inputs["Fac"])
mix = nodes.new("ShaderNodeMix")
mix.data_type = "RGBA"
links.new(ramp.outputs["Color"], mix.inputs["Factor"])
links.new(<rock colour node>.outputs["Result"], mix.inputs["A"])
links.new(<grass colour node>.outputs["Result"], mix.inputs["B"])
links.new(mix.outputs["Result"], bsdf.inputs["Base Color"])
```

Height bands use the Object output of a Texture Coordinate node, Z through a second ramp.
A worn path is a third colour masked by a low-frequency noise sampled along the path's polyline.

## 4. One material, many copies: the Object Info tint

```python
info = nodes.new("ShaderNodeObjectInfo")
hsv = nodes.new("ShaderNodeHueSaturation")
links.new(info.outputs["Random"], hsv.inputs["Hue"])            # 0..1 per object / instance
hsv.inputs["Saturation"].default_value = 1.0
links.new(<colour node>.outputs["Result"], hsv.inputs["Color"])
links.new(hsv.outputs["Color"], bsdf.inputs["Base Color"])
```

`Random` differs per instance in Cycles, so the shrubs placed from one source collection no
longer match; keep the hue swing small (a Map Range from `Random` into a narrow band around
0.5) or the copies look like different species.

## 5. Per-face slots, before `bm.to_mesh`

```python
me.materials.append(stone)        # slot 0
me.materials.append(moss)         # slot 1
for f in bm.faces:
    f.material_index = 1 if f.normal.z > 0.7 else 0
bm.to_mesh(me)
```

The census reports a slot no polygon uses; assigning after `bm.to_mesh` goes through
`me.polygons` instead.

## 6. The volume-only box (fog the census recognises)

```python
mat = bpy.data.materials.new("Haze")
nodes, links = mat.node_tree.nodes, mat.node_tree.links
nodes.remove(nodes["Principled BSDF"])                         # no surface shader at all
vol = nodes.new("ShaderNodeVolumePrincipled")
vol.inputs["Density"].default_value = <0.002-0.02>
links.new(vol.outputs["Volume"], nodes["Material Output"].inputs["Volume"])
```

`lib.sky.haze(collection, loc, size, density=..., color=...)` builds this on a box sized to the
play area. Leave the Surface socket unlinked: a box with both is a placed asset to the gate.

## 7. Water and glass, as the lib sets them

| material | the sockets that matter |
|---|---|
| `water` / `clear_water` | `Transmission Weight` at one, `Roughness` low, `Metallic` zero, a bump from scrolled noise; the plane is named with a water word so feet on it count as on water |
| `glass` | `Transmission Weight` at one, `Roughness` near zero, `IOR` the lib's default; thin panes as single faces with a Solidify-free thickness read best at 32 samples |
| `emissive` | `Emission Color` and a modest `Emission Strength`; the illumination is a `practical` lamp |

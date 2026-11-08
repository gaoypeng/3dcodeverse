# The interface, executable (CadQuery 2.8)

Two plan parts, `Body` and `Knob` × 2: a module-level `result`, one named, coloured child per
part, instances `Name_i`, placement by `loc`.  Sizes and positions are the plan rows'.

```python
import cadquery as cq

body = cq.Workplane("XY").box(0.12, 0.08, 0.05, centered=(True, True, False))   # z 0 → 0.05
knob = cq.Workplane("XY").circle(0.01).extrude(0.02)                             # z 0 → 0.02
result = cq.Assembly(name="Object")
result.add(body, name="Body", color=cq.Color(0.55, 0.57, 0.6))
for i, x in enumerate((-0.04, 0.04)):   # each copy sits flush on Body's top face: contact
    result.add(knob, name=f"Knob_{i}", color=cq.Color(0.1, 0.1, 0.1), loc=cq.Location(cq.Vector(x, 0, 0.05)))
```

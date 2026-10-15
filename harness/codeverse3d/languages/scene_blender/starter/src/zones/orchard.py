"""src/zones/orchard.py — zone "Orchard": a stand of apple trees, a hedge, a flower bed and
harvest crates (instances of the crate asset).

CONTRACT: build(ctx) -> bpy.types.Collection — every placed thing is ONE root object seated on
ctx.height_at (lib.vegetation recipes stand on the z you pass; lib.scatter seats instances).
"""
from lib import materials, mesh, scatter, vegetation

SEED = 31
CENTER = (20.0, -12.0)


def build(ctx):
    rng = ctx.rng(SEED)
    h = ctx.height_at
    bark = materials.material("OrchardBark", "4A3524", roughness=0.9, noise_scale=6.0)
    leaves = materials.foliage("OrchardLeaf", "4F7A2E", variants=3)

    rows = scatter.grid(CENTER, (16.0, 12.0), 4.5, rng, jitter=0.2)
    vegetation.tree_stand(ctx.collection, mesh.Batch("AppleTrees_0"), rows, h, rng, bark=bark, foliage=leaves[1],
                          height=4.5, kind="broadleaf", max_leaves=700)

    hedge = mesh.Batch("Hedge_0")
    for i, (x, y) in enumerate(scatter.along_path([(10.0, -3.5), (30.0, -3.5)], 10)):
        vegetation.shrub(hedge, bark, leaves[i % 3], (x, y, h(x, y)), scale=1.1, seed=SEED + i)
    hedge.finish(ctx.collection)

    petals = materials.material("Petals", "D94A6A", roughness=0.7)
    bed = mesh.Batch("FlowerBed_0")
    vegetation.perennial_bed(bed, (12.5, -19.0), 2.0, h, leaves[0], petals, rng)
    bed.finish(ctx.collection)

    crate = ctx.assets.get("crate")
    if crate is not None:
        spots = scatter.poisson(rng, area=(24.0, -22.0, 30.0, -16.0), min_dist=1.6, max_count=5)
        scatter.scatter(ctx.collection, crate, "HarvestCrate", spots, height_at=h, rng=rng, scale=(0.85, 1.1))
    return ctx.collection

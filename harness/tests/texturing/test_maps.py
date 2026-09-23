"""maps.py: roughness + normal maps derived from a generated albedo tile."""

from __future__ import annotations

import numpy as np
from PIL import Image

from codeverse3d.texturing.apply import (
    metallic_roughness_image,
    normal_image,
)


def _grain(size: int = 64) -> Image.Image:
    """A vertical stripe pattern: light bands (polished high points) and dark ones."""
    x = np.arange(size)
    band = (0.5 + 0.45 * np.sin(2 * np.pi * 8 * x / size)).astype(np.float32)   # seamless: 8 periods
    a = np.repeat(band[None, :], size, axis=0)
    rgb = np.stack([a * 0.7, a * 0.45, a * 0.25], axis=-1)
    return Image.fromarray(np.round(rgb * 255).astype(np.uint8), mode="RGB")


def test_metallic_roughness_image_uses_the_gltf_channel_packing():
    img = _grain()
    mr = metallic_roughness_image(img, 0.62, 1.0, "cast_iron")
    a = np.asarray(mr)
    assert mr.mode == "RGB" and a.shape == (64, 64, 3)
    assert (a[..., 2] == 255).all(), "metallic lives in B"
    assert a[..., 1].std() > 3, "roughness lives in G and must actually vary"
    assert 140 < a[..., 1].mean() < 180


def test_normal_map_is_tangent_space_and_tiles():
    img = _grain()
    n = np.asarray(normal_image(img, "cast_iron"), dtype=np.float32) / 255.0 * 2.0 - 1.0
    assert np.allclose(np.linalg.norm(n, axis=-1), 1.0, atol=1e-2)
    assert (n[..., 2] > 0).all(), "z always points out of the surface"
    # wrap-around gradients: the map must be as seamless as the albedo it came from
    assert abs(float(n[:, 0, 0].mean() - n[:, -1, 0].mean())) < 0.35
    flat = normal_image(img, "cast_iron", strength=0.0)
    assert np.asarray(flat)[..., 0].std() < 1e-6

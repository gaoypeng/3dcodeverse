"""maps.py: roughness + normal maps derived from a generated albedo tile."""

from __future__ import annotations

import numpy as np
from PIL import Image

from codeverse.texturing.maps import (
    DEFAULT_VARIATION,
    VARIATION,
    is_flat,
    metallic_roughness_image,
    normal_image,
    roughness_array,
    variation_for,
)
from codeverse.texturing.materials import MATERIALS


def _grain(size: int = 64) -> Image.Image:
    """A vertical stripe pattern: light bands (polished high points) and dark ones."""
    x = np.arange(size)
    band = (0.5 + 0.45 * np.sin(2 * np.pi * 8 * x / size)).astype(np.float32)   # seamless: 8 periods
    a = np.repeat(band[None, :], size, axis=0)
    rgb = np.stack([a * 0.7, a * 0.45, a * 0.25], axis=-1)
    return Image.fromarray(np.round(rgb * 255).astype(np.uint8), mode="RGB")


def test_every_table_family_has_a_variation_row():
    assert set(VARIATION) == set(MATERIALS)
    assert variation_for("no_such_family") == DEFAULT_VARIATION
    assert variation_for("cast_iron")[0] > variation_for("chrome")[0]   # pitted iron varies more


def test_roughness_follows_the_grain_dark_is_rough():
    img = _grain()
    r = roughness_array(img, 0.45, "hardwood")
    assert r.shape == (64, 64) and r.min() >= 0.0 and r.max() <= 1.0
    lum = np.asarray(img.convert("L"), dtype=np.float32)[0]
    assert r[0][lum.argmin()] > r[0][lum.argmax()], "dark bands must be rougher than bright ones"
    assert abs(float(r.mean()) - 0.45) < 0.06, "the base roughness must survive as the mean"
    quiet = roughness_array(img, 0.45, "chrome")
    assert quiet.std() < r.std(), "the family amplitude controls how far roughness swings"


def test_roughness_is_clamped_for_extreme_bases():
    img = _grain()
    assert roughness_array(img, 0.0, "cast_iron").min() >= 0.0
    assert roughness_array(img, 1.0, "cast_iron").max() <= 1.0


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


def test_flat_tiles_are_detected_so_no_noise_maps_are_written():
    assert is_flat(Image.new("RGB", (32, 32), (120, 90, 40)))
    assert not is_flat(_grain())

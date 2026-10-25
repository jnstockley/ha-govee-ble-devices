"""Check the branding contract consumed by Home Assistant and releases."""

import shutil
import struct
from pathlib import Path

import pytest
from homeassistant.components.brands import _read_brand_file
from homeassistant.components.brands.const import ALLOWED_IMAGES
from homeassistant.helpers.icon import _load_icons_file

from scripts import check_release

INTEGRATION = (
    Path(__file__).resolve().parents[1] / "custom_components/govee_ble_devices"
)


@pytest.mark.parametrize("image", sorted(ALLOWED_IMAGES))
def test_all_brand_variants_resolve_locally(image):
    """Every frontend brand request must resolve without a CDN placeholder."""
    data = _read_brand_file(INTEGRATION / "brand", image)
    assert data is not None
    assert data.startswith(b"\x89PNG\r\n\x1a\n")


@pytest.mark.parametrize("damage", ["missing", "invalid", "wrong_size"])
@pytest.mark.parametrize("filename", ["icon.png", "icon@2x.png"])
def test_release_rejects_broken_icon(tmp_path, damage, filename):
    """A release must not silently lose or damage either required asset."""
    brand = tmp_path / "brand"
    shutil.copytree(INTEGRATION / "brand", brand)
    icon = brand / filename
    if damage == "missing":
        icon.unlink()
    elif damage == "invalid":
        icon.write_bytes(b"not a PNG")
    else:
        data = bytearray(icon.read_bytes())
        data[16:24] = struct.pack(">II", 128, 128)
        icon.write_bytes(data)
    with pytest.raises(ValueError, match=filename):
        check_release.validate_brand_images(brand)


def test_function_specific_entity_icons_load():
    """Entities without device-class icons must supply frontend icon metadata."""
    icons = _load_icons_file(INTEGRATION / "icons.json")
    for platform, keys in {
        "sensor": ("filter_life",),
        "button": ("reset_filter", "apply_custom"),
        "switch": ("display", "custom_oscillation"),
        "number": ("custom_speed", "custom_duration"),
        "select": ("custom_mode",),
    }.items():
        for key in keys:
            assert icons["entity"][platform][key]["default"].startswith("mdi:")

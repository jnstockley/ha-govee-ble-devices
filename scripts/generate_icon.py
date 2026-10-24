"""Generate the integration's simple, original Bluetooth brand icon.

The icon is drawn from geometric paths with the Python standard library so
the shipped PNG assets can be reproduced without design software.
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRAND = ROOT / "custom_components/govee_ble_devices/brand"


def distance_to_segment(
    x: float, y: float, x1: float, y1: float, x2: float, y2: float
) -> float:
    """Return the shortest distance from a point to a line segment."""
    dx, dy = x2 - x1, y2 - y1
    along = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)))
    return math.hypot(x - x1 - along * dx, y - y1 - along * dy)


def pixel(x: float, y: float) -> tuple[int, int, int, int]:
    """Draw a navy disc, cyan edge, and white Bluetooth rune in unit space."""
    radius = math.hypot(x - 0.5, y - 0.5)
    if radius > 0.47:
        return 0, 0, 0, 0
    if radius > 0.435:
        return 24, 198, 217, 255
    segments = (
        (0.5, 0.18, 0.5, 0.82),
        (0.5, 0.18, 0.695, 0.345),
        (0.695, 0.345, 0.305, 0.675),
        (0.305, 0.325, 0.695, 0.655),
        (0.695, 0.655, 0.5, 0.82),
    )
    if min(distance_to_segment(x, y, *line) for line in segments) < 0.023:
        return 255, 255, 255, 255
    return 21, 56, 117, 255


def png_chunk(kind: bytes, data: bytes) -> bytes:
    """Encode one PNG chunk."""
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", zlib.crc32(kind + data))
    )


def render(size: int) -> bytes:
    """Render antialiased RGBA PNG bytes at the requested square size."""
    raw = bytearray()
    samples = 3
    for row in range(size):
        raw.append(0)
        for column in range(size):
            colors = [
                pixel(
                    (column + (sx + 0.5) / samples) / size,
                    (row + (sy + 0.5) / samples) / size,
                )
                for sy in range(samples)
                for sx in range(samples)
            ]
            alpha = sum(color[3] for color in colors) // (samples * samples)
            if alpha:
                rgb = tuple(
                    sum(color[channel] * color[3] for color in colors)
                    // sum(color[3] for color in colors)
                    for channel in range(3)
                )
            else:
                rgb = (0, 0, 0)
            raw.extend((*rgb, alpha))
    header = struct.pack(">2I5B", size, size, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", header)
        + png_chunk(b"IDAT", zlib.compress(bytes(raw), level=9))
        + png_chunk(b"IEND", b"")
    )


if __name__ == "__main__":
    BRAND.mkdir(parents=True, exist_ok=True)
    for size, filename in ((256, "icon.png"), (512, "icon@2x.png")):
        (BRAND / filename).write_bytes(render(size))

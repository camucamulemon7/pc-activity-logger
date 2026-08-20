from __future__ import annotations

from io import BytesIO

from mss import mss
from PIL import Image


def capture_monitor(monitor: dict[str, int], jpeg_quality: int) -> bytes:
    with mss() as screen:
        shot = screen.grab(monitor)
    image = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    output = BytesIO()
    image.save(output, format="JPEG", quality=jpeg_quality, optimize=True)
    return output.getvalue()


def crop_to_active_window(
    monitor_image: bytes,
    monitor: dict[str, int],
    window_rect: dict[str, int] | None,
    jpeg_quality: int,
) -> bytes:
    """Crop a monitor image to the visible foreground-window intersection."""
    if not window_rect:
        return monitor_image

    monitor_right = monitor["left"] + monitor["width"]
    monitor_bottom = monitor["top"] + monitor["height"]
    window_right = window_rect["left"] + window_rect["width"]
    window_bottom = window_rect["top"] + window_rect["height"]

    left = max(monitor["left"], window_rect["left"])
    top = max(monitor["top"], window_rect["top"])
    right = min(monitor_right, window_right)
    bottom = min(monitor_bottom, window_bottom)
    if right - left < 100 or bottom - top < 100:
        return monitor_image

    crop_box = (
        left - monitor["left"],
        top - monitor["top"],
        right - monitor["left"],
        bottom - monitor["top"],
    )
    with Image.open(BytesIO(monitor_image)) as image:
        cropped = image.crop(crop_box)
        output = BytesIO()
        cropped.save(output, format="JPEG", quality=jpeg_quality, optimize=True)
    return output.getvalue()


def difference_hash(image_bytes: bytes) -> int:
    """Return a 64-bit perceptual dHash for inexpensive screen comparison."""
    with Image.open(BytesIO(image_bytes)) as image:
        pixels = list(image.convert("L").resize((9, 8)).getdata())
    value = 0
    for row in range(8):
        offset = row * 9
        for column in range(8):
            value = (value << 1) | int(
                pixels[offset + column] > pixels[offset + column + 1]
            )
    return value


def hash_distance(first: int, second: int) -> int:
    """Return the Hamming distance between two perceptual hashes."""
    return (first ^ second).bit_count()

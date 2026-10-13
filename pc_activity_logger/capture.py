from __future__ import annotations

from io import BytesIO

from mss import mss
from PIL import Image


class UnsafeCaptureError(ValueError):
    """No sufficiently large, confirmed foreground region is available."""


def active_window_crop_box(
    monitor: dict[str, int], window_rect: dict[str, int] | None,
) -> tuple[int, int, int, int]:
    if not window_rect:
        raise UnsafeCaptureError("Foreground window bounds are unavailable")
    left = max(monitor["left"], window_rect["left"])
    top = max(monitor["top"], window_rect["top"])
    right = min(monitor["left"] + monitor["width"], window_rect["left"] + window_rect["width"])
    bottom = min(monitor["top"] + monitor["height"], window_rect["top"] + window_rect["height"])
    if right - left < 100 or bottom - top < 100:
        raise UnsafeCaptureError("Foreground region is smaller than 100 by 100 pixels")
    return left - monitor["left"], top - monitor["top"], right - monitor["left"], bottom - monitor["top"]


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
    crop_box = active_window_crop_box(monitor, window_rect)
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

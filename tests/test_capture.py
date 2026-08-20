import unittest
from io import BytesIO

from PIL import Image

from pc_activity_logger.capture import (
    crop_to_active_window,
    difference_hash,
    hash_distance,
)


class CaptureTests(unittest.TestCase):
    def test_crops_window_using_screen_coordinates(self) -> None:
        source = BytesIO()
        Image.new("RGB", (800, 600), "white").save(source, format="JPEG")
        cropped_bytes = crop_to_active_window(
            source.getvalue(),
            {"left": -800, "top": 0, "width": 800, "height": 600},
            {"left": -700, "top": 50, "width": 500, "height": 400},
            80,
        )
        with Image.open(BytesIO(cropped_bytes)) as cropped:
            self.assertEqual(cropped.size, (500, 400))

    def test_uses_full_monitor_when_window_is_too_small(self) -> None:
        source = BytesIO()
        Image.new("RGB", (800, 600), "white").save(source, format="JPEG")
        original = source.getvalue()
        result = crop_to_active_window(
            original,
            {"left": 0, "top": 0, "width": 800, "height": 600},
            {"left": 10, "top": 10, "width": 20, "height": 20},
            80,
        )
        self.assertEqual(result, original)

    def test_difference_hash_is_stable_for_same_image(self) -> None:
        source = BytesIO()
        Image.new("RGB", (100, 100), "white").save(source, format="JPEG")
        first = difference_hash(source.getvalue())
        second = difference_hash(source.getvalue())
        self.assertEqual(hash_distance(first, second), 0)

    def test_hash_distance_counts_changed_bits(self) -> None:
        self.assertEqual(hash_distance(0b1010, 0b0011), 2)


if __name__ == "__main__":
    unittest.main()

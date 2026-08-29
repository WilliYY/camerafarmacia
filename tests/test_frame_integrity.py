import unittest

from PIL import Image, ImageDraw

from wimi_analytics.frame_integrity import FrameIntegrityGuard, assess_frame_integrity


class FrameIntegrityTests(unittest.TestCase):
    @staticmethod
    def _normal_scene():
        image = Image.new("RGB", (960, 540), (238, 239, 235))
        draw = ImageDraw.Draw(image)
        for row in range(6):
            for column in range(12):
                left = 18 + column * 78
                top = 18 + row * 82
                color = (
                    35 + (column * 29) % 190,
                    45 + (row * 37 + column * 11) % 180,
                    55 + (row * 19 + column * 23) % 170,
                )
                draw.rectangle((left, top, left + 58, top + 56), fill=color)
        return image

    def test_rejects_partial_white_pink_decoder_corruption(self):
        image = self._normal_scene()
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 130, 959, 539), fill=(250, 226, 249))
        for top in range(150, 540, 34):
            draw.rectangle((0, top, 959, top + 10), fill=(255, 247, 255))
            for left in range(10, 960, 150):
                draw.rectangle((left, top + 11, left + 70, top + 17), fill=(239, 199, 244))

        result = assess_frame_integrity(image)

        self.assertFalse(result["valid"])
        self.assertEqual(result["reason"], "horizontal_decode_bands")

    def test_keeps_normal_and_uniform_bright_scenes(self):
        normal = assess_frame_integrity(self._normal_scene())
        bright = assess_frame_integrity(Image.new("RGB", (960, 540), "white"))

        self.assertTrue(normal["valid"])
        self.assertTrue(bright["valid"])

    def test_rejects_decoder_sentinel_colors(self):
        result = assess_frame_integrity(Image.new("RGB", (960, 540), (255, 0, 255)))

        self.assertFalse(result["valid"])
        self.assertEqual(result["reason"], "decoder_sentinel_color")

    def test_guard_requests_preview_only_reconnect_after_repeated_rejections(self):
        guard = FrameIntegrityGuard(reconnect_after=3)

        self.assertFalse(guard.observe(valid=False))
        self.assertFalse(guard.observe(valid=False))
        self.assertTrue(guard.observe(valid=False))
        self.assertEqual(guard.total_rejected, 3)

        self.assertFalse(guard.observe(valid=True))
        self.assertEqual(guard.consecutive_rejected, 0)


if __name__ == "__main__":
    unittest.main()

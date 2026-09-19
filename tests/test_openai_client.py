import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image

from agent import config, openai_client


class ScaleTests(unittest.TestCase):
    def test_image_size_downscales_to_max_width(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "shot.png")
            Image.new("RGB", (1280, 800)).save(path)
            self.assertEqual(openai_client.image_size(path), (1024, 640))
            self.assertAlmostEqual(openai_client.image_scale(path), 1024 / 1280)

    def test_image_size_leaves_small_screens_alone(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "shot.png")
            Image.new("RGB", (800, 600)).save(path)
            self.assertEqual(openai_client.image_size(path), (800, 600))
            self.assertEqual(openai_client.image_scale(path), 1.0)

    def test_to_screen_maps_image_space_to_screen_space(self):
        scale = 1024 / 1280
        self.assertEqual(openai_client.to_screen(512, 320, scale), (640, 400))
        self.assertEqual(openai_client.to_screen(361, 198, scale), (451, 248))

    def test_to_screen_clamps_to_the_screen(self):
        x, y = openai_client.to_screen(99999, 99999, 0.5)
        self.assertEqual((x, y), (config.SCREEN_W - 1, config.SCREEN_H - 1))
        self.assertEqual(openai_client.to_screen(-40, -40, 1.0), (0, 0))


if __name__ == "__main__":
    unittest.main()

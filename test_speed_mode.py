import unittest

from generation.concat import _concat_list_line
from generation.video import build_video_prompt


class SpeedModeConfigTests(unittest.TestCase):
    def test_build_video_prompt_uses_configurable_duration_and_resolution(self):
        prompt = build_video_prompt("slow push in", 3, "480p")

        self.assertEqual(
            prompt,
            "slow push in --resolution 480p --duration 3 --watermark false",
        )

    def test_concat_list_line_uses_absolute_file_url_safe_path(self):
        line = _concat_list_line("outputs/videos/shot one.mp4")

        self.assertTrue(line.startswith("file '"))
        self.assertTrue(line.endswith("'\n"))
        self.assertIn("shot one.mp4", line)


if __name__ == "__main__":
    unittest.main()

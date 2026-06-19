import unittest
from unittest import mock

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

    def test_draft_mode_keeps_dubbing_enabled_by_default(self):
        with mock.patch.dict("os.environ", {"GENERATION_MODE": "draft"}, clear=True):
            import importlib
            import config

            reloaded = importlib.reload(config)
            self.assertEqual(reloaded.GENERATION_MODE, "draft")
            self.assertTrue(reloaded.ENABLE_DUBBING)
            self.assertEqual(reloaded.VIDEO_DURATION_SECONDS, 3)
            importlib.reload(config)


if __name__ == "__main__":
    unittest.main()

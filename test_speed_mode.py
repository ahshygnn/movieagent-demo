import unittest
from unittest import mock
import tempfile
from pathlib import Path

from generation.concat import _concat_list_line
from generation.video import build_video_prompt, generate_video


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
            self.assertEqual(reloaded.VIDEO_DURATION_SECONDS, 5)
            self.assertEqual(reloaded.VIDEO_MAX_CONCURRENCY, 2)
            importlib.reload(config)

    def test_generate_video_reuses_existing_raw_video(self):
        with tempfile.TemporaryDirectory() as tmp:
            existing = Path(tmp) / "cached_shot.mp4"
            existing.write_bytes(b"0" * 5000)

            with mock.patch("config.VIDEO_DIR", tmp), mock.patch("generation.video.submit_video") as submit:
                result = generate_video("cached_shot", "unused.png", "slow push")

            self.assertEqual(result["local_path"], str(existing))
            self.assertTrue(result["cache_hit"])
            submit.assert_not_called()


if __name__ == "__main__":
    unittest.main()

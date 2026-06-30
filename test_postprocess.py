import tempfile
import unittest
from pathlib import Path

from generation.postprocess import collect_dialogue_lines


class DialogueLinesTests(unittest.TestCase):
    def test_collect_dialogue_lines_keeps_order_and_skips_empty_text(self):
        lines = collect_dialogue_lines(
            {
                "李明": "我们出发吧。",
                "小雪": "  ",
                "老陈": "等等，前面有光。",
            }
        )
        self.assertEqual(lines, [("李明", "我们出发吧。"), ("老陈", "等等，前面有光。")])

    def test_collect_dialogue_lines_handles_empty_input(self):
        self.assertEqual(collect_dialogue_lines({}), [])
        self.assertEqual(collect_dialogue_lines(None), [])


class FinalVideoPathTests(unittest.TestCase):
    def test_collect_video_paths_prefers_enhanced_video(self):
        from app import _collect_video_paths_for_final

        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "raw.mp4"
            enhanced = Path(tmp) / "dubbed.mp4"
            raw.write_bytes(b"raw")
            enhanced.write_bytes(b"dubbed")

            task = {
                "shots": {
                    "Sub 1": {
                        "Scene 1": {
                            "Shot": {
                                "Shot 1": {
                                    "raw_video_local_path": str(raw),
                                    "video_local_path": str(enhanced),
                                }
                            }
                        }
                    }
                }
            }

            self.assertEqual(_collect_video_paths_for_final(task, []), [str(enhanced)])

    def test_collect_video_paths_falls_back_to_raw_video(self):
        from app import _collect_video_paths_for_final

        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "raw.mp4"
            raw.write_bytes(b"raw")

            task = {
                "shots": {
                    "Sub 1": {
                        "Scene 1": {
                            "Shot": {
                                "Shot 1": {
                                    "raw_video_local_path": str(raw),
                                }
                            }
                        }
                    }
                }
            }

            self.assertEqual(_collect_video_paths_for_final(task, []), [str(raw)])


class ShotResumeTests(unittest.TestCase):
    def test_shot_video_complete_requires_dubbing_assets_when_dialogue_exists(self):
        from generation.shot_pipeline import shot_video_complete

        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "dubbed.mp4"
            audio = Path(tmp) / "dialogue.mp3"
            video.write_bytes(b"v" * 5000)
            audio.write_bytes(b"a" * 2048)

            incomplete = {
                "video_local_path": str(video),
                "video_has_dubbing": True,
                "Dialogue": {"Baibai": "你好"},
            }
            complete = {
                **incomplete,
                "combined_audio_local_path": str(audio),
            }

            self.assertFalse(shot_video_complete(incomplete))
            self.assertTrue(shot_video_complete(complete))


if __name__ == "__main__":
    unittest.main()

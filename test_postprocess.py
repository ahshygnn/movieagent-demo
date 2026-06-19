import os
import tempfile
import unittest
from pathlib import Path

from generation.postprocess import (
    build_srt_entries,
    collect_subtitle_lines,
    srt_timestamp,
    write_srt,
)


class SubtitleTimelineTests(unittest.TestCase):
    def test_collect_subtitle_lines_keeps_order_and_skips_empty_text(self):
        lines = collect_subtitle_lines(
            {
                "李明": "我们出发吧。",
                "小雪": "  ",
                "老陈": "等等，前面有光。",
            }
        )

        self.assertEqual(lines, [("李明", "我们出发吧。"), ("老陈", "等等，前面有光。")])

    def test_collect_subtitle_lines_handles_empty_input(self):
        self.assertEqual(collect_subtitle_lines({}), [])
        self.assertEqual(collect_subtitle_lines(None), [])

    def test_srt_timestamp_formats_milliseconds(self):
        self.assertEqual(srt_timestamp(0), "00:00:00,000")
        self.assertEqual(srt_timestamp(65.432), "00:01:05,432")
        self.assertEqual(srt_timestamp(3661.009), "01:01:01,009")

    def test_build_srt_entries_adds_pauses_between_dialogue(self):
        entries = build_srt_entries(
            [
                ("李明", "第一句", 1.2),
                ("小雪", "第二句", 2.0),
            ],
            pause_seconds=0.5,
        )

        self.assertEqual(entries[0], (0.0, 1.2, "李明: 第一句"))
        self.assertEqual(entries[1], (1.7, 3.7, "小雪: 第二句"))

    def test_write_srt_preserves_chinese_and_special_characters(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shot.srt"
            write_srt([(0, 1.5, "李明: 你好，世界！<测试>")], str(path))

            content = path.read_text(encoding="utf-8")

        self.assertIn("00:00:00,000 --> 00:00:01,500", content)
        self.assertIn("李明: 你好，世界！<测试>", content)


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


if __name__ == "__main__":
    unittest.main()

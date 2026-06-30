import os
import tempfile
import unittest
from pathlib import Path

from generation.postprocess import (
    build_srt_entries,
    collect_subtitle_lines,
    srt_timestamp,
    vtt_timestamp,
    write_srt,
    write_vtt,
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

    def test_vtt_timestamp_uses_dot_separator(self):
        self.assertEqual(vtt_timestamp(65.432), "00:01:05.432")

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

    def test_write_vtt_preserves_chinese_and_uses_webvtt_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shot.vtt"
            write_vtt([(0, 1.5, "李明: 你好，世界！")], str(path))

            content = path.read_text(encoding="utf-8")

        self.assertTrue(content.startswith("WEBVTT"))
        self.assertIn("00:00:00.000 --> 00:00:01.500", content)
        self.assertIn("李明: 你好，世界！", content)


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
            vtt = Path(tmp) / "shot.vtt"
            srt = Path(tmp) / "shot.srt"
            video.write_bytes(b"v" * 5000)
            audio.write_bytes(b"a" * 2048)
            vtt.write_text("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nhello\n", encoding="utf-8")
            srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n", encoding="utf-8")

            incomplete = {
                "video_local_path": str(video),
                "video_has_dubbing": True,
                "Subtitles": {"Baibai": "你好"},
            }
            complete = {
                **incomplete,
                "combined_audio_local_path": str(audio),
                "subtitle_local_path": str(vtt),
                "subtitle_srt_local_path": str(srt),
            }

            self.assertFalse(shot_video_complete(incomplete))
            self.assertTrue(shot_video_complete(complete))


class SubtitleMergeTests(unittest.TestCase):
    def test_parse_srt_and_offset_entries(self):
        from generation.subtitles import offset_entries, parse_srt

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shot.srt"
            path.write_text(
                "1\n00:00:00,000 --> 00:00:01,500\nQiqi: 你好\n",
                encoding="utf-8",
            )

            entries = parse_srt(str(path))
            shifted = offset_entries(entries, 5.0)

        self.assertEqual(entries, [(0.0, 1.5, "Qiqi: 你好")])
        self.assertEqual(shifted, [(5.0, 6.5, "Qiqi: 你好")])


if __name__ == "__main__":
    unittest.main()

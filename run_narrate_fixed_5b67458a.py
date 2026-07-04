"""
重新合轨：把已生成的 16 条旁白音频截短到 ≤4.5s，再和原始视频合轨拼接。
不重新调 TTS API，直接处理已有 MP3 文件。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

os.environ.setdefault("GENERATION_MODE", "final")

from generation.concat import concat_videos
from generation.postprocess import _ffmpeg_exe

TASK_ID   = "5b67458a-19fb-4fbd-a215-3c309d45652e"
OLD_TAG   = "20260701_235112"          # 已生成的旁白 MP3 的时间戳
NEW_TAG   = "narrated_trimmed"
VIDEO_DIR = "outputs/videos"
AUDIO_DIR = "outputs/audio"
MAX_AUDIO_SECONDS = 4.5               # 音频上限，低于视频 5s，绝不触发定格

SHOT_KEYS = [
    "Sub-Script_1_Scene_1_Shot_1",
    "Sub-Script_1_Scene_1_Shot_2",
    "Sub-Script_1_Scene_1_Shot_3",
    "Sub-Script_1_Scene_1_Shot_4",
    "Sub-Script_1_Scene_1_Shot_5",
    "Sub-Script_1_Scene_1_Shot_6",
    "Sub-Script_1_Scene_1_Shot_7",
    "Sub-Script_1_Scene_2_Shot_1",
    "Sub-Script_1_Scene_2_Shot_2",
    "Sub-Script_1_Scene_2_Shot_3",
    "Sub-Script_1_Scene_2_Shot_4",
    "Sub-Script_1_Scene_2_Shot_5",
    "Sub-Script_1_Scene_2_Shot_6",
    "Sub-Script_1_Scene_2_Shot_7",
    "Sub-Script_1_Scene_3_Shot_1",
    "Sub-Script_1_Scene_3_Shot_2",
]


def log(msg: str) -> None:
    print(msg, flush=True)


def get_audio_duration(path: str) -> float:
    """Get audio duration in seconds via moviepy."""
    try:
        from moviepy import AudioFileClip
        clip = AudioFileClip(path)
        dur = float(clip.duration or 0)
        clip.close()
        return dur
    except Exception:
        # Fallback: file size estimate at 128kbps
        return os.path.getsize(path) / 16000


def trim_audio(src: str, dst: str, max_sec: float) -> str:
    """
    Trim audio to max_sec with a short fade-out at the end.
    If audio is already shorter, just copy.
    """
    dur = get_audio_duration(src)
    ffmpeg = _ffmpeg_exe()
    if dur <= max_sec:
        # Already short enough — copy as-is
        import shutil
        shutil.copy2(src, dst)
        return dst

    fade_start = max(0, max_sec - 0.4)
    cmd = [
        ffmpeg, "-y", "-i", src,
        "-t", str(max_sec),
        "-af", f"afade=t=out:st={fade_start:.2f}:d=0.4",
        dst,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg trim failed: {result.stderr[-300:]}")
    return dst


def mux_video_audio(raw_video: str, audio: str, output: str) -> str:
    """Mux raw video + audio with -c:v copy (no re-encode, no tpad).
    Audio must be shorter than video — ffmpeg will fill the gap with silence,
    keeping the video at full 5s duration and eliminating freeze frames.
    """
    ffmpeg = _ffmpeg_exe()
    cmd = [
        ffmpeg, "-y",
        "-i", raw_video,
        "-i", audio,
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", "aac",
        # NO -shortest: let video stream dictate duration (5s)
        # audio ends early → natural silence, no freeze-frame extension
        "-movflags", "+faststart",
        output,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg mux failed: {result.stderr[-300:]}")
    return output


def main() -> None:
    os.makedirs(AUDIO_DIR, exist_ok=True)
    os.makedirs(VIDEO_DIR, exist_ok=True)
    wall_start = time.time()
    video_paths: list[str] = []

    for i, sk in enumerate(SHOT_KEYS, 1):
        shot_id   = f"{TASK_ID}_{sk}"
        raw_mp4   = f"{VIDEO_DIR}/{shot_id}.mp4"
        src_audio = f"{AUDIO_DIR}/{shot_id}_narration_{OLD_TAG}.mp3"
        trim_mp3  = f"{AUDIO_DIR}/{shot_id}_narration_{NEW_TAG}.mp3"
        out_mp4   = f"{VIDEO_DIR}/{shot_id}_{NEW_TAG}.mp4"

        if not Path(raw_mp4).is_file():
            log(f"  [{i}/16] SKIP — raw video missing: {raw_mp4}")
            continue

        if not Path(src_audio).is_file():
            log(f"  [{i}/16] SKIP audio missing, use silent raw: {sk}")
            video_paths.append(raw_mp4)
            continue

        orig_dur = get_audio_duration(src_audio)
        trim_audio(src_audio, trim_mp3, MAX_AUDIO_SECONDS)
        new_dur = get_audio_duration(trim_mp3)
        trimmed = orig_dur > MAX_AUDIO_SECONDS + 0.05

        mux_video_audio(raw_mp4, trim_mp3, out_mp4)
        tag = f"trimmed {orig_dur:.1f}s→{new_dur:.1f}s" if trimmed else f"ok {new_dur:.1f}s"
        log(f"  [{i}/16] {sk.split('_',2)[2]}  [{tag}]")
        video_paths.append(out_mp4)

    final = f"{VIDEO_DIR}/{TASK_ID}_{NEW_TAG}_final.mp4"
    log(f"\nConcatenating {len(video_paths)} shots → {final}")
    concat_videos(video_paths, final, prefer_fast=True)

    try:
        from moviepy import VideoFileClip
        clip = VideoFileClip(final)
        dur = round(float(clip.duration), 2)
        clip.close()
    except Exception:
        dur = len(video_paths) * 5

    elapsed = round(time.time() - wall_start, 1)
    log("\n" + "=" * 60)
    log(f"完成！成片路径: {final}")
    log(f"成片时长: {dur}s  共 {len(video_paths)} 镜")
    log(f"总耗时:   {elapsed}s")
    log("=" * 60)


if __name__ == "__main__":
    main()

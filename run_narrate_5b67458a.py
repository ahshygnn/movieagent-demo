"""
为 5b67458a 视频的 16 个镜头生成英文旁白配音并拼接成片。
使用 raw video（不是之前的 dubbed 版）作为画面源，重新配音。
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from datetime import datetime

os.environ.setdefault("GENERATION_MODE", "final")
os.environ.setdefault("YIZHAN_TTS_MODEL", "qwen3-tts-flash")

import config
from generation.postprocess import mux_audio_with_video
from generation.concat import concat_videos
from tts.service import TTSService

TASK_ID   = "5b67458a-19fb-4fbd-a215-3c309d45652e"
VIDEO_DIR = "outputs/videos"
AUDIO_DIR = "outputs/audio"
TAG       = datetime.now().strftime("%Y%m%d_%H%M%S")

# ── 16 镜头的旁白（基于画面内容 + 故事文本） ─────────────────────────────────
SHOTS = [
    # (sub_script, scene, shot, narration_text)
    ("Sub-Script_1", "Scene_1", "Shot_1",
     "The ancient seal had begun to crack — and a primordial darkness stirred beneath the earth."),

    ("Sub-Script_1", "Scene_1", "Shot_2",
     "Elder Moros, guardian of the seal for decades, faced the moment he had always dreaded."),

    ("Sub-Script_1", "Scene_1", "Shot_3",
     "'The fractures are spreading,' Lyra said. Beside her, Caden stared into the light and said nothing."),

    ("Sub-Script_1", "Scene_1", "Shot_4",
     "Seraphine mapped the cracks with quiet precision. Finn kept his hand on his sword."),

    ("Sub-Script_1", "Scene_1", "Shot_5",
     "'A primordial darkness stirs,' Moros warned. 'The seal is failing. We must restore it.'"),

    ("Sub-Script_1", "Scene_1", "Shot_6",
     "Fear crossed Lyra's face — and then something harder, something resolute, took its place."),

    ("Sub-Script_1", "Scene_1", "Shot_7",
     "Five companions stood small against the fractured sky. Their quest had begun."),

    ("Sub-Script_1", "Scene_2", "Shot_1",
     "The Whispering Highlands — ancient, untamed, holding the ruins of those who bargained with gods."),

    ("Sub-Script_1", "Scene_2", "Shot_2",
     "Elder Moros led them deeper into the forest, where even the trees seemed to remember old wars."),

    ("Sub-Script_1", "Scene_2", "Shot_3",
     "Lyra's compass glowed brighter with each step — pointing toward the heart of the ruins."),

    ("Sub-Script_1", "Scene_2", "Shot_4",
     "'Trust is earned,' Caden said quietly. Finn didn't answer, but he kept walking."),

    ("Sub-Script_1", "Scene_2", "Shot_5",
     "Moros watched each companion in turn, measuring what they carried and what they hid."),

    ("Sub-Script_1", "Scene_2", "Shot_6",
     "Seraphine's eyes snapped toward the ruins. Something vast and ancient stirred — she felt it in her blood."),

    ("Sub-Script_1", "Scene_2", "Shot_7",
     "They pressed on through the mist — five shapes moving through a silence older than kingdoms."),

    ("Sub-Script_1", "Scene_3", "Shot_1",
     "The ruin collapsed around Caden and Finn. One passage remained open. Only one."),

    ("Sub-Script_1", "Scene_3", "Shot_2",
     "The trap triggered. A single escape — too narrow for two. The moment of choice had arrived."),
]


def log(msg: str) -> None:
    print(msg, flush=True)


def synth_narration(text: str, shot_id: str) -> str:
    out_path = os.path.join(AUDIO_DIR, f"{shot_id}_narration_{TAG}.mp3")
    svc = TTSService()
    svc.synthesize(
        text=text,
        voice=config.YIZHAN_TTS_DEFAULT_VOICE or "Cherry",
        output_path=out_path,
        response_format="mp3",
        model=config.YIZHAN_TTS_MODEL or "qwen3-tts-flash",
    )
    return out_path


def main() -> None:
    os.makedirs(AUDIO_DIR, exist_ok=True)
    os.makedirs(VIDEO_DIR, exist_ok=True)

    wall_start = time.time()
    video_paths: list[str] = []

    for i, (ss, sc, sh, narration) in enumerate(SHOTS, 1):
        shot_id  = f"{TASK_ID}_{ss}_{sc}_{sh}"
        raw_mp4  = os.path.join(VIDEO_DIR, f"{shot_id}.mp4")
        out_mp4  = os.path.join(VIDEO_DIR, f"{shot_id}_narrated_{TAG}.mp4")

        if not Path(raw_mp4).is_file():
            log(f"  [{i}/16] SKIP — raw video not found: {raw_mp4}")
            continue

        log(f"  [{i}/16] TTS → {sc} {sh}: \"{narration[:55]}...\"" if len(narration) > 55 else
            f"  [{i}/16] TTS → {sc} {sh}: \"{narration}\"")
        t0 = time.time()
        try:
            audio_path = synth_narration(narration, shot_id)
            mux_audio_with_video(raw_mp4, audio_path, out_mp4)
            elapsed = round(time.time() - t0, 1)
            log(f"         done {elapsed}s → {os.path.basename(out_mp4)}")
            video_paths.append(out_mp4)
        except Exception as e:
            log(f"         ERROR: {e} — using silent raw video instead")
            video_paths.append(raw_mp4)

    if not video_paths:
        raise RuntimeError("No videos to concatenate.")

    final_path = os.path.join(VIDEO_DIR, f"{TASK_ID}_narrated_{TAG}.mp4")
    log(f"\nConcatenating {len(video_paths)} shots → {final_path}")
    concat_videos(video_paths, final_path, prefer_fast=True)

    wall_elapsed = round(time.time() - wall_start, 1)
    try:
        from moviepy import VideoFileClip
        clip = VideoFileClip(final_path)
        dur = round(float(clip.duration), 2)
        clip.close()
    except Exception:
        dur = len(video_paths) * 5

    log("\n" + "=" * 60)
    log(f"完成！成片路径: {final_path}")
    log(f"成片时长: {dur}s   共 {len(video_paths)} 镜")
    log(f"总耗时:   {wall_elapsed}s ({wall_elapsed/60:.1f} min)")
    log("=" * 60)


if __name__ == "__main__":
    main()

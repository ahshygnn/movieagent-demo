import os
from concurrent.futures import ThreadPoolExecutor

import config
from generation.video import generate_video
from generation.postprocess import (
    collect_subtitle_lines,
    mux_prepared_dubbing_assets,
    prepare_dubbing_assets,
)


def build_shot_id(task_id: str, sub_script_name: str, scene_name: str, shot_name: str) -> str:
    return f"{task_id}_{sub_script_name}_{scene_name}_{shot_name}".replace(" ", "_")


def build_motion_prompt(shot_data: dict) -> str:
    return (
        (shot_data.get("Camera Movement", "") or "") + ". " +
        (shot_data.get("Coarse Plot", "") or "")
    ).strip()


def empty_postprocess_result(raw_video_path: str) -> dict:
    return {
        "local_path": raw_video_path,
        "dubbed": False,
        "audio_files": {},
        "subtitle_local_path": None,
        "subtitle_srt_local_path": None,
        "combined_audio_local_path": None,
    }


def _valid_file(path: str | None, min_bytes: int = 1024) -> bool:
    if not path or not isinstance(path, str):
        return False
    try:
        return os.path.isfile(path) and os.path.getsize(path) >= min_bytes
    except OSError:
        return False


def shot_video_complete(shot_data: dict) -> bool:
    video_path = (shot_data or {}).get("video_local_path")
    if not _valid_file(video_path, min_bytes=4096):
        return False
    if not config.ENABLE_DUBBING or not collect_subtitle_lines((shot_data or {}).get("Subtitles")):
        return True
    return (
        bool((shot_data or {}).get("video_has_dubbing"))
        and _valid_file((shot_data or {}).get("combined_audio_local_path"))
        and _valid_file((shot_data or {}).get("subtitle_local_path"), min_bytes=32)
        and _valid_file((shot_data or {}).get("subtitle_srt_local_path"), min_bytes=32)
    )


def video_update_fields(shot_id: str, raw_video_path: str, post_result: dict, video_result: dict) -> dict:
    dubbed = bool(post_result.get("dubbed"))
    return {
        "raw_video_local_path": raw_video_path,
        "raw_video_url": f"/outputs/videos/{shot_id}.mp4",
        "enhanced_video_local_path": post_result["local_path"] if dubbed else None,
        "video_local_path": post_result["local_path"],
        "video_url": f"/outputs/videos/{shot_id}_dubbed.mp4" if dubbed else f"/outputs/videos/{shot_id}.mp4",
        "subtitle_local_path": post_result.get("subtitle_local_path"),
        "subtitle_url": f"/outputs/subtitles/{shot_id}.vtt" if post_result.get("subtitle_local_path") else None,
        "subtitle_srt_local_path": post_result.get("subtitle_srt_local_path"),
        "subtitle_srt_url": f"/outputs/subtitles/{shot_id}.srt" if post_result.get("subtitle_srt_local_path") else None,
        "combined_audio_local_path": post_result.get("combined_audio_local_path"),
        "audio_files": post_result.get("audio_files") or {},
        "video_has_dubbing": dubbed,
        "generation_mode": config.GENERATION_MODE,
        "video_duration_seconds": video_result.get("duration_seconds"),
        "video_resolution": video_result.get("resolution"),
        "video_status": "done",
        "video_cache_hit": bool(video_result.get("cache_hit")),
        "dubbing_cache_hit": bool(post_result.get("cache_hit")),
    }


def generate_shot_video_artifacts(
    shot_id: str,
    shot_data: dict,
    keyframe_path: str,
    voice_refs: dict | None = None,
) -> dict:
    if not keyframe_path or not os.path.isfile(keyframe_path):
        raise FileNotFoundError("missing keyframe")

    motion_prompt = build_motion_prompt(shot_data)

    if config.ENABLE_DUBBING:
        with ThreadPoolExecutor(max_workers=2) as executor:
            video_future = executor.submit(generate_video, shot_id, keyframe_path, motion_prompt)
            dubbing_future = executor.submit(
                prepare_dubbing_assets,
                shot_id,
                shot_data,
                voice_refs or {},
            )
            video_result = video_future.result()
            raw_video_path = video_result["local_path"]
            dubbing_assets = dubbing_future.result()
        post_result = mux_prepared_dubbing_assets(shot_id, raw_video_path, dubbing_assets)
    else:
        video_result = generate_video(shot_id, keyframe_path, motion_prompt)
        raw_video_path = video_result["local_path"]
        post_result = empty_postprocess_result(raw_video_path)

    return {
        "raw_video_path": raw_video_path,
        "video_result": video_result,
        "post_result": post_result,
        "updates": video_update_fields(shot_id, raw_video_path, post_result, video_result),
    }

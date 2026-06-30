"""
视频帧提取工具
优先使用已有的 keyframe_local_path；如需从视频中提取中间帧则调用 ffmpeg。
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


def get_representative_frame(shot_data: dict, shot_id: str, output_dir: str | None = None) -> str | None:
    """
    获取 Shot 的代表帧路径。
    1. 优先返回已有的 keyframe_local_path
    2. 若有 video_local_path，用 ffmpeg 提取中间帧
    3. 否则返回 None
    """
    kf = (shot_data or {}).get("keyframe_local_path")
    if kf and os.path.isfile(kf):
        return kf

    video_path = (shot_data or {}).get("video_local_path")
    if not video_path or not os.path.isfile(video_path):
        return None

    out_dir = output_dir or str(Path(video_path).parent / "eval_frames")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{shot_id}_midframe.png")

    if os.path.isfile(out_path):
        return out_path

    duration = _get_video_duration(video_path)
    if duration is None or duration <= 0:
        mid_time = "00:00:02"
    else:
        mid_sec = duration / 2
        mid_time = f"{int(mid_sec // 60):02d}:{mid_sec % 60:06.3f}"

    try:
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-ss", mid_time,
                "-i", video_path,
                "-frames:v", "1",
                "-q:v", "2",
                out_path,
            ],
            capture_output=True,
            check=True,
            timeout=30,
        )
        return out_path if os.path.isfile(out_path) else None
    except Exception as e:
        print(f"  [ffmpeg] 提取中间帧失败 {video_path}: {e}")
        return None


def _get_video_duration(video_path: str) -> float | None:
    """用 ffprobe 获取视频时长（秒）。"""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                video_path,
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return float(result.stdout.strip())
    except Exception:
        return None


def collect_frames_for_task(task_id: str, tasks_dict: dict, output_dir: str | None = None) -> list[str]:
    """
    按顺序收集任务中所有 Shot 的代表帧路径（None 表示该 Shot 无可用帧）。
    """
    task = tasks_dict.get(task_id) or {}
    frames: list[str | None] = []
    for ss_name, scenes_dict in (task.get("shots") or {}).items():
        for scene_name, scene_data in (scenes_dict or {}).items():
            for shot_name, shot_data in (scene_data or {}).get("Shot", {}).items():
                shot_id = f"{task_id}_{ss_name}_{scene_name}_{shot_name}".replace(" ", "_")
                frame = get_representative_frame(shot_data, shot_id, output_dir)
                frames.append(frame)
    return [f for f in frames if f]

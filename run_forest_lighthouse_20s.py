"""20-second real full-chain test for the Forest Post Office story.

Flow:
LLM planning -> first 4 keyframes -> video + Chinese TTS dubbing -> final concat.

Resume with:
    python run_forest_lighthouse_20s.py <task_id>
"""
from __future__ import annotations

import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Lock this test's runtime parameters before importing project config.
os.environ.setdefault("GENERATION_MODE", "final")
os.environ.setdefault("SHOT_MAX_PER_SCENE", "0")
os.environ.setdefault("VIDEO_DURATION_SECONDS", "5")
os.environ.setdefault("VIDEO_RESOLUTION", "720p")
os.environ.setdefault("VIDEO_MAX_CONCURRENCY", "2")
os.environ.setdefault("YIZHAN_TTS_MODEL", "qwen3-tts-flash")
os.environ.setdefault("YIZHAN_TTS_DEFAULT_VOICE", "Cherry")

import config
from generation.concat import concat_videos
from generation.image import generate_keyframe
from generation.postprocess import collect_dialogue_lines
from generation.shot_pipeline import build_shot_id, generate_shot_video_artifacts, shot_video_complete
from pipeline import create_task, run_full_pipeline, save_tasks, tasks


TITLE = "山顶灯塔的旧信"
MAX_VIDEO_SHOTS = 4
SYNOPSIS = (
    "黄昏时，森林邮局的最后一盏暖灯亮起。团团在一叠旧信里发现一封边角泛黄的信，"
    "收件人是住在山顶灯塔的老海龟。窗外，乌云从海面压向山谷，邮局门前的风铃急促摇晃。"
    "团团抬头看见远处灯塔的光越来越微弱，立刻把信塞进邮包，提起一盏小煤油灯，"
    "沿着湿润的石阶冲进风雨。暴雨很快冲断了山路。团团站在湍急的溪流前，"
    "发现一根倒下的松树横在水面上。他把邮包紧紧抱在胸前，踩着树干，"
    "小心穿过飞溅的水花。穿过松树林后，团团终于来到灯塔。老海龟打开门，"
    "接过那封迟到了很多年的信。读完信后，它轻轻抬起头，眼里泛着泪光。"
    "雨停了。灯塔重新亮起金色光束，团团站在窗边，看见云层裂开，海面上升起一道彩虹。"
)
CHARACTERS = ["团团", "老海龟"]
CHARACTER_REFS = {
    "团团": "F:/movieagents_demo/movieagent-demo/examples/yueguangyouju/tuantuan.jpg",
    "老海龟": "F:/movieagents_demo/movieagent-demo/examples/yueguangyouju/laohuigui.png",
}
VOICE_REFS = {name: getattr(config, "YIZHAN_TTS_DEFAULT_VOICE", "Cherry") or "Cherry" for name in CHARACTERS}

NARRATION_LINES = [
    "黄昏时，森林邮局最后一盏暖灯亮起，团团发现了一封寄往山顶灯塔的旧信。",
    "乌云从海面压向山谷，远处灯塔的光越来越微弱。",
    "团团把旧信塞进邮包，提着小煤油灯冲进风雨。",
    "暴雨冲断山路，团团抱紧邮包，踩着倒下的松树穿过溪流。",
]


def log(message: str) -> None:
    print(message, flush=True)


def validate_inputs() -> None:
    missing = [f"{name}: {path}" for name, path in CHARACTER_REFS.items() if not Path(path).is_file()]
    if missing:
        raise FileNotFoundError("Missing character reference images: " + "; ".join(missing))


def ensure_task(task_id: str | None) -> str:
    if task_id and task_id in tasks:
        task = tasks[task_id]
        task.setdefault("character_refs", dict(CHARACTER_REFS))
        task.setdefault("voice_refs", dict(VOICE_REFS))
        task.setdefault("episode_title", TITLE)
        save_tasks()
        return task_id

    new_task_id = task_id or create_task()
    if task_id and task_id not in tasks:
        tasks[new_task_id] = {
            "status": "pending",
            "progress": 0,
            "logs": [],
            "sub_scripts": None,
            "scenes": {},
            "shots": {},
            "character_refs": {},
            "voice_refs": {},
            "cost": {"input_tokens": 0, "output_tokens": 0},
        }
    tasks[new_task_id]["episode_title"] = TITLE
    tasks[new_task_id]["character_refs"] = dict(CHARACTER_REFS)
    tasks[new_task_id]["voice_refs"] = dict(VOICE_REFS)
    save_tasks()
    return new_task_id


def planning_complete(task_id: str) -> bool:
    task = tasks.get(task_id) or {}
    return bool(task.get("shots")) and task.get("status") == "done"


def run_planning_if_needed(task_id: str) -> None:
    if planning_complete(task_id):
        log("Planning exists, skip LLM agents.")
        return

    tasks[task_id]["sub_scripts"] = None
    tasks[task_id]["scenes"] = {}
    tasks[task_id]["shots"] = {}
    tasks[task_id]["status"] = "pending"
    tasks[task_id]["progress"] = 0
    save_tasks()

    log("Running real LLM planning agents: Director -> Scene -> Shot ...")
    run_full_pipeline(task_id, SYNOPSIS, CHARACTERS)
    if tasks[task_id].get("status") == "error":
        raise RuntimeError("Planning failed. Check task logs in outputs/tasks.json.")
    tasks[task_id]["character_refs"] = dict(CHARACTER_REFS)
    tasks[task_id]["voice_refs"] = dict(VOICE_REFS)
    save_tasks()


def all_shots(task_id: str) -> list[dict]:
    result: list[dict] = []
    for ss_name, scenes in (tasks[task_id].get("shots") or {}).items():
        for scene_name, scene_data in (scenes or {}).items():
            for shot_name, shot_data in ((scene_data or {}).get("Shot") or {}).items():
                result.append({
                    "sub_script_name": ss_name,
                    "scene_name": scene_name,
                    "shot_name": shot_name,
                    "shot_data": shot_data,
                })
    return result


def _has_cjk(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def ensure_narration_dialogue(shots: list[dict]) -> int:
    added = 0
    for index, item in enumerate(shots):
        shot_data = item["shot_data"]
        existing_lines = collect_dialogue_lines(shot_data.get("Dialogue"))
        if existing_lines and any(_has_cjk(text) for _speaker, text in existing_lines):
            continue
        fallback = (
            NARRATION_LINES[index]
            if index < len(NARRATION_LINES)
            else str(shot_data.get("Coarse Plot") or shot_data.get("Plot/Visual Description") or "").strip()
        )
        if fallback:
            shot_data["Dialogue"] = {"旁白": fallback}
            shot_data["video_has_dubbing"] = False
            added += 1
    return added


def refs_for_shot(task_id: str, involving) -> dict[str, str]:
    refs = tasks[task_id].get("character_refs") or {}
    if isinstance(involving, dict):
        names = involving.keys()
    elif isinstance(involving, list):
        names = involving
    else:
        return {}
    return {name: refs[name] for name in names if name in refs}


def dialogue_char_count(shots: list[dict]) -> int:
    total = 0
    for item in shots:
        dialogue = item["shot_data"].get("Dialogue") or {}
        if isinstance(dialogue, dict):
            total += sum(len(str(text or "")) for text in dialogue.values())
    return total


def generate_keyframes(task_id: str, shots: list[dict]) -> int:
    generated = 0
    for index, item in enumerate(shots, start=1):
        shot_data = item["shot_data"]
        shot_id = build_shot_id(task_id, item["sub_script_name"], item["scene_name"], item["shot_name"])
        existing = shot_data.get("keyframe_local_path")
        if existing and Path(existing).is_file():
            log(f"[{index}/{len(shots)}] keyframe cache: {shot_id}")
            continue

        plot = shot_data.get("Plot/Visual Description", "")
        refs = refs_for_shot(task_id, shot_data.get("Involving Characters"))
        log(f"[{index}/{len(shots)}] keyframe start: {shot_id} refs={list(refs)}")
        result = generate_keyframe(plot, shot_id, refs)
        shot_data["keyframe_local_path"] = result["local_path"]
        shot_data["keyframe_url"] = f"/outputs/keyframes/{shot_id}.png"
        shot_data["keyframe_status"] = "done"
        generated += 1
        log(f"[{index}/{len(shots)}] keyframe done: {result['elapsed_seconds']}s")
        save_tasks()
    return generated


def generate_videos(task_id: str, shots: list[dict]) -> tuple[list[str], int]:
    jobs = []
    for index, item in enumerate(shots, start=1):
        shot_data = item["shot_data"]
        shot_id = build_shot_id(task_id, item["sub_script_name"], item["scene_name"], item["shot_name"])
        keyframe_path = shot_data.get("keyframe_local_path")
        if not keyframe_path or not Path(keyframe_path).is_file():
            shot_data["video_status"] = "skipped_missing_keyframe"
            log(f"[{index}/{len(shots)}] skip video, missing keyframe: {shot_id}")
            continue
        jobs.append((index, item, shot_id, keyframe_path))

    max_workers = max(1, min(int(config.VIDEO_MAX_CONCURRENCY or 1), len(jobs) or 1))
    log(f"Generating video+dubbing with {max_workers} workers for {len(jobs)} jobs...")

    def run_job(job):
        index, item, shot_id, keyframe_path = job
        shot_data = item["shot_data"]
        if shot_video_complete(shot_data):
            return index, item, 0.0, True, None
        shot_data["video_status"] = "running"
        save_tasks()
        started = time.time()
        try:
            artifact = generate_shot_video_artifacts(
                shot_id,
                shot_data,
                keyframe_path,
                tasks[task_id].get("voice_refs") or {},
            )
            shot_data.update(artifact["updates"])
            save_tasks()
            return index, item, time.time() - started, False, None
        except Exception as exc:
            shot_data["video_status"] = "error"
            shot_data["video_error"] = str(exc)
            save_tasks()
            return index, item, time.time() - started, False, exc

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {executor.submit(run_job, job): job for job in jobs}
        for future in as_completed(future_map):
            index, item, elapsed, cache_hit, error = future.result()
            label = build_shot_id(task_id, item["sub_script_name"], item["scene_name"], item["shot_name"])
            if error:
                log(f"[{index}/{len(shots)}] video+dubbing failed after {elapsed:.1f}s: {label}: {error}")
            else:
                log(f"[{index}/{len(shots)}] video+dubbing done {elapsed:.1f}s cache={cache_hit}: {label}")

    video_paths: list[str] = []
    completed = 0
    for item in shots:
        shot_data = item["shot_data"]
        video_path = shot_data.get("video_local_path")
        if video_path and Path(video_path).is_file():
            video_paths.append(video_path)
            completed += 1
    return video_paths, completed


def concat_outputs(task_id: str, video_paths: list[str]) -> dict:
    if not video_paths:
        raise RuntimeError("No generated videos to concatenate.")

    final_video = f"outputs/videos/{task_id}_forest_lighthouse_20s_final.mp4"
    concat_method = concat_videos(video_paths, final_video, prefer_fast=True)
    return {
        "concat_method": concat_method,
        "final_video": final_video,
    }


def inspect_video(path: str) -> dict:
    try:
        import moviepy

        VideoFileClip = getattr(moviepy, "VideoFileClip")
    except (ImportError, AttributeError):
        from moviepy import editor

        VideoFileClip = editor.VideoFileClip

    clip = VideoFileClip(path)
    try:
        return {
            "duration_seconds": round(float(clip.duration or 0), 2),
            "fps": float(clip.fps or 0),
            "size": list(clip.size),
        }
    finally:
        clip.close()


def main() -> None:
    started = time.time()
    validate_inputs()

    requested_task_id = sys.argv[1] if len(sys.argv) > 1 else None
    task_id = ensure_task(requested_task_id)

    log(f"Task ID: {task_id}")
    log(f"Episode: {TITLE}")
    log(f"Characters: {CHARACTERS}")
    log(
        "Params: "
        f"mode={config.GENERATION_MODE}, shot_max={config.SHOT_MAX_PER_SCENE}, "
        f"duration={config.VIDEO_DURATION_SECONDS}s, resolution={config.VIDEO_RESOLUTION}, "
        f"concurrency={config.VIDEO_MAX_CONCURRENCY}, max_video_shots={MAX_VIDEO_SHOTS}, "
        f"tts={config.YIZHAN_TTS_MODEL}"
    )

    run_planning_if_needed(task_id)
    planned_shots = all_shots(task_id)
    if not planned_shots:
        raise RuntimeError("Planning produced no shots.")

    selected_shots = planned_shots[:MAX_VIDEO_SHOTS]
    if len(selected_shots) < MAX_VIDEO_SHOTS:
        log(f"Planning produced only {len(selected_shots)} shots; generating all available shots.")
    log(f"Planned shots: {len(planned_shots)}; generating first {len(selected_shots)} shots.")

    added_narration = ensure_narration_dialogue(selected_shots)
    if added_narration:
        log(f"Added Chinese narration dialogue for {added_narration} shots.")
        save_tasks()

    image_count = generate_keyframes(task_id, selected_shots)
    video_paths, completed_videos = generate_videos(task_id, selected_shots)
    if completed_videos != len(selected_shots):
        raise RuntimeError(
            f"Only {completed_videos}/{len(selected_shots)} selected shots have generated videos. "
            f"Resume with: python run_forest_lighthouse_20s.py {task_id}"
        )

    outputs = concat_outputs(task_id, video_paths)
    video_info = inspect_video(outputs["final_video"])
    total_elapsed = time.time() - started

    task = tasks[task_id]
    task["forest_lighthouse_20s_outputs"] = outputs
    task["forest_lighthouse_20s_metrics"] = {
        "planned_shot_count": len(planned_shots),
        "generated_shot_count": len(selected_shots),
        "generated_keyframes_this_run": image_count,
        "video_seconds_requested": len(selected_shots) * int(config.VIDEO_DURATION_SECONDS),
        "tts_characters": dialogue_char_count(selected_shots),
        "elapsed_seconds": round(total_elapsed, 2),
        "final_video_info": video_info,
    }
    save_tasks()

    log("DONE")
    log(f"LLM tokens: {task.get('cost')}")
    log(f"Planned shot count: {len(planned_shots)}")
    log(f"Generated shot count: {len(selected_shots)}")
    log(f"Images generated this run: {image_count}")
    log(f"Requested video seconds: {len(selected_shots) * int(config.VIDEO_DURATION_SECONDS)}")
    log(f"TTS characters: {dialogue_char_count(selected_shots)}")
    log(f"Elapsed: {total_elapsed:.2f}s ({total_elapsed / 60:.2f} min)")
    log(f"Final video: {outputs['final_video']}")
    log(f"Video info: {video_info}")


if __name__ == "__main__":
    main()

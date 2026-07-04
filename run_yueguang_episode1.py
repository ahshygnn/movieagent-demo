"""Episode 1 full-chain runner for Yueguang Youju.

This script intentionally runs the real planning agents before media generation:
LLM planning -> keyframes -> video + TTS dubbing -> final concat.
It supports resume by passing an existing task id:

    python run_yueguang_episode1.py <task_id>
"""
from __future__ import annotations

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Lock the test parameters before importing project config.
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


TITLE = "会发光的信"
MAX_VIDEO_SHOTS = 8  # 5s/镜 × 8 ≈ 40s 成片
SYNOPSIS = (
    "第 1 集：《会发光的信》。"
    "团团是月光邮局里最认真的小邮差，圆圆是他最好的搭档，墨墨则是总爱埋头翻旧地图的老学究。"
    "这天深夜，一封会发光的神秘信件悄然送达邮局——信封散发着蓝色的微光，和普通的信截然不同。"
    "团团小心翼翼地拆开信封，信纸上只有一句话：星星花园正在慢慢熄灭。"
    "就在三人面面相觑之际，一颗蓝色的星光碎片从信封里滑落，在桌面上闪烁跳动，像是在无声地催促着什么。"
    "墨墨立刻翻出尘封已久的旧地图，随着碎片的光芒，地图上一座叫做云朵山的地方悄然亮起。"
    "三个伙伴对视一眼，心里都明白——这不是一封普通的信，星星花园需要他们。"
    "就在他们收拾行装准备出发的那一刻，窗外夜空中一颗星星骤然暗淡，属于他们的冒险，正式开始了。"
)
CHARACTERS = ["团团", "圆圆", "墨墨"]
CHARACTER_REFS = {
    "团团": "F:/movieagents_demo/movieagent-demo/examples/yueguangyouju/tuantuan.jpg",
    "圆圆": "F:/movieagents_demo/movieagent-demo/examples/yueguangyouju/yuanyuan.jpg",
    "墨墨": "F:/movieagents_demo/movieagent-demo/examples/yueguangyouju/momo.jpg",
}
VOICE_REFS = {name: getattr(config, "YIZHAN_TTS_DEFAULT_VOICE", "Cherry") or "Cherry" for name in CHARACTERS}

NARRATION_LINES = [
    "团团是月光邮局里最认真的小邮差，圆圆是他最好的搭档，墨墨则是总爱埋头翻旧地图的老学究。",
    "这天深夜，一封会发光的神秘信件悄然送达邮局，信封散发着蓝色的微光。",
    "团团小心翼翼地拆开信封，信纸上只有一句话：星星花园正在慢慢熄灭。",
    "就在三人面面相觑之际，一颗蓝色的星光碎片从信封里滑落，在桌面上闪烁跳动。",
    "墨墨立刻翻出尘封已久的旧地图，随着碎片的光芒，云朵山在地图上悄然亮起。",
    "三个伙伴对视一眼，心里都明白——这不是一封普通的信，星星花园需要他们。",
    "他们收拾行装准备出发，窗外夜空中一颗星星骤然暗淡。",
    "属于他们的冒险，正式开始了。",
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
        task.setdefault("character_refs", CHARACTER_REFS)
        task.setdefault("voice_refs", VOICE_REFS)
        save_tasks()
        return task_id

    if task_id:
        tasks[task_id] = {
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
        new_task_id = task_id
    else:
        new_task_id = create_task()
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


def ensure_narration_dialogue(shots: list[dict]) -> int:
    added = 0
    for index, item in enumerate(shots):
        shot_data = item["shot_data"]
        if collect_dialogue_lines(shot_data.get("Dialogue")):
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
    t0 = time.time()

    jobs = []
    for index, item in enumerate(shots, start=1):
        shot_data = item["shot_data"]
        shot_id = build_shot_id(task_id, item["sub_script_name"], item["scene_name"], item["shot_name"])
        existing = shot_data.get("keyframe_local_path")
        if existing and Path(existing).is_file():
            log(f"[{index}/{len(shots)}] keyframe cache: {shot_id}")
            continue
        jobs.append((index, item, shot_id))

    if not jobs:
        log(f"[关键帧] 全部命中缓存，耗时 {time.time() - t0:.1f} 秒")
        return 0

    max_workers = max(1, min(int(config.KEYFRAME_MAX_CONCURRENCY or 1), len(jobs)))
    log(f"[关键帧] 并行生成 {len(jobs)} 张，workers={max_workers}...")

    def run_job(job):
        index, item, shot_id = job
        shot_data = item["shot_data"]
        plot = shot_data.get("Plot/Visual Description", "")
        refs = refs_for_shot(task_id, shot_data.get("Involving Characters"))
        log(f"[{index}/{len(shots)}] keyframe start: {shot_id} refs={list(refs)}")
        try:
            result = generate_keyframe(plot, shot_id, refs)
            shot_data["keyframe_local_path"] = result["local_path"]
            shot_data["keyframe_url"] = f"/outputs/keyframes/{shot_id}.png"
            shot_data["keyframe_status"] = "done"
            save_tasks()
            return index, shot_id, result["elapsed_seconds"], None
        except Exception as exc:
            shot_data["keyframe_status"] = "error"
            save_tasks()
            return index, shot_id, 0.0, exc

    generated = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {executor.submit(run_job, job): job for job in jobs}
        for future in as_completed(future_map):
            index, shot_id, elapsed, error = future.result()
            if error:
                log(f"[{index}/{len(shots)}] keyframe failed: {shot_id}: {error}")
            else:
                generated += 1
                log(f"[{index}/{len(shots)}] keyframe done: {shot_id} {elapsed:.1f}s")

    total_elapsed = time.time() - t0
    log(f"[关键帧] {generated} 张并行生成完成，耗时 {total_elapsed:.1f} 秒")
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

    final_video = f"outputs/videos/{task_id}_yueguang_ep01_final.mp4"
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
    log(f"Planned shots: {len(planned_shots)}; generating first {len(selected_shots)} shots (~{len(selected_shots) * int(config.VIDEO_DURATION_SECONDS)}s).")

    added_narration = ensure_narration_dialogue(selected_shots)
    if added_narration:
        log(f"Added Chinese narration dialogue for {added_narration} visual-only shots.")
        save_tasks()

    image_count = generate_keyframes(task_id, selected_shots)
    video_paths, completed_videos = generate_videos(task_id, selected_shots)
    if completed_videos != len(selected_shots):
        raise RuntimeError(
            f"Only {completed_videos}/{len(selected_shots)} selected shots have generated videos. "
            f"Fix failed shots and resume with: python run_yueguang_episode1.py {task_id}"
        )

    outputs = concat_outputs(task_id, video_paths)
    video_info = inspect_video(outputs["final_video"])
    total_elapsed = time.time() - started

    task = tasks[task_id]
    task["episode_outputs"] = outputs
    task["episode_metrics"] = {
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
    log(f"Planned shots: {len(planned_shots)}; generated: {len(selected_shots)}")
    log(f"Images generated this run: {image_count}")
    log(f"Requested video seconds: {len(selected_shots) * int(config.VIDEO_DURATION_SECONDS)}")
    log(f"TTS characters: {dialogue_char_count(selected_shots)}")
    log(f"Elapsed: {total_elapsed:.2f}s ({total_elapsed / 60:.2f} min)")
    log(f"Final video: {outputs['final_video']}")
    log(f"Video info: {video_info}")


if __name__ == "__main__":
    main()

"""Direct 6-shot squirrel demo with keyframes, video, YiZhan TTS, sidecar subtitles, and final concat."""
from __future__ import annotations

import time
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import config
from generation.concat import concat_videos
from generation.image import generate_keyframe
from generation.shot_pipeline import (
    build_shot_id,
    generate_shot_video_artifacts,
    shot_video_complete,
)
from generation.subtitles import burn_subtitles, merge_sidecar_subtitles
from pipeline import create_task, save_tasks, tasks


SYNOPSIS = (
    "小松鼠奇奇住在松树上。冬天快到了，它储存的松果不够，决定去远处的松林找更多。"
    "路上下起大雨，奇奇躲进树洞等雨停。雨停后它继续赶路，终于找到满满的松果，"
    "抱着松果开心回家。"
)

SHOTS = [
    {
        "scene": "scene_1",
        "shot": "shot_1",
        "plot": "温暖儿童绘本风格，清晨的高大松树上，小松鼠奇奇从树洞小家探出头，尾巴蓬松，周围有少量松果，画面柔和可爱。",
        "motion": "Slow push in toward the squirrel's cozy tree hollow",
        "coarse": "Qiqi lives in a pine tree and wakes up in its small home.",
        "line": "我叫奇奇，住在高高的松树上。",
    },
    {
        "scene": "scene_2",
        "shot": "shot_1",
        "plot": "奇奇数着自己储存的小松果，旁边的小篮子快空了，远处森林泛着冬天将至的冷蓝色光，奇奇露出担心但勇敢的表情。",
        "motion": "Gentle tilt from the nearly empty basket to Qiqi's determined face",
        "coarse": "Winter is coming, but Qiqi does not have enough pine cones.",
        "line": "冬天快到了，我得找到更多松果才行。",
    },
    {
        "scene": "scene_3",
        "shot": "shot_1",
        "plot": "奇奇背着小布袋走在通往远处松林的小路上，路边是苔藓、蘑菇和落叶，天空慢慢变暗，冒险感但不危险。",
        "motion": "Side tracking shot following Qiqi along the forest path",
        "coarse": "Qiqi sets off toward a distant pine forest.",
        "line": "我要去远处的松林，那里一定有许多松果。",
    },
    {
        "scene": "scene_4",
        "shot": "shot_1",
        "plot": "森林里突然下起大雨，雨点落在叶子和地面上，奇奇抱着布袋躲进温暖干燥的树洞，外面雨帘清晰可见。",
        "motion": "Camera moves from the rain outside into the safe tree hollow",
        "coarse": "Heavy rain begins, and Qiqi waits inside a tree hollow.",
        "line": "雨下得好大，我先在树洞里等一等。",
    },
    {
        "scene": "scene_5",
        "shot": "shot_1",
        "plot": "雨停后阳光从云后照出来，森林叶子闪着水珠，奇奇重新走上小路，远处出现一片结满松果的松林。",
        "motion": "Bright reveal pan from wet leaves to the distant pine forest",
        "coarse": "After the rain stops, Qiqi continues and finds a pine forest full of cones.",
        "line": "雨停啦！前面真的有好多松果！",
    },
    {
        "scene": "scene_6",
        "shot": "shot_1",
        "plot": "夕阳下，奇奇抱着满满一袋松果走回松树小家，脸上开心满足，树洞里透出温暖灯光，儿童绘本结尾画面。",
        "motion": "Slow pull back as Qiqi returns home happily with pine cones",
        "coarse": "Qiqi happily carries many pine cones home.",
        "line": "现在我有足够的松果，可以安心过冬啦！",
    },
]


def build_task(task_id: str) -> None:
    tasks[task_id] = {
        "status": "done",
        "progress": 100,
        "logs": ["Direct squirrel demo: planned 6 core storybook shots."],
        "sub_scripts": {"Sub-Script": {"storybook": {"Plot": SYNOPSIS, "Involving Characters": ["Qiqi"]}}},
        "scenes": {},
        "shots": {"storybook": {}},
        "character_refs": {},
        "voice_refs": {"Qiqi": getattr(config, "YIZHAN_TTS_DEFAULT_VOICE", "Cherry") or "Cherry"},
        "cost": {"input_tokens": 0, "output_tokens": 0},
    }
    for item in SHOTS:
        scene = item["scene"]
        shot = item["shot"]
        tasks[task_id]["shots"]["storybook"].setdefault(scene, {"Shot": {}})
        tasks[task_id]["shots"]["storybook"][scene]["Shot"][shot] = {
            "Involving Characters": {"Qiqi": [0.2, 0.1, 0.8, 1.0]},
            "Plot/Visual Description": item["plot"],
            "Coarse Plot": item["coarse"],
            "Camera Movement": item["motion"],
            "Subtitles": {"Qiqi": item["line"]},
        }
    save_tasks()


def main() -> None:
    start = time.time()
    task_id = sys.argv[1] if len(sys.argv) > 1 else create_task()
    if task_id not in tasks or not tasks[task_id].get("shots"):
        build_task(task_id)
    print(f"Task ID: {task_id}")
    print(f"Story: {SYNOPSIS}")
    print(f"Params: shots={len(SHOTS)}, duration={config.VIDEO_DURATION_SECONDS}s, resolution={config.VIDEO_RESOLUTION}, concurrency={config.VIDEO_MAX_CONCURRENCY}")
    print(f"TTS: model={config.YIZHAN_TTS_MODEL}, voice={tasks[task_id]['voice_refs']['Qiqi']}")

    jobs = []
    for index, item in enumerate(SHOTS, start=1):
        shot_ref = tasks[task_id]["shots"]["storybook"][item["scene"]]["Shot"][item["shot"]]
        shot_id = build_shot_id(task_id, "storybook", item["scene"], item["shot"])
        keyframe_path = shot_ref.get("keyframe_local_path")
        if not keyframe_path or not Path(keyframe_path).exists():
            print(f"[{index}/{len(SHOTS)}] keyframe start: {shot_id}")
            keyframe = generate_keyframe(item["plot"], shot_id, {})
            shot_ref["keyframe_local_path"] = keyframe["local_path"]
            shot_ref["keyframe_url"] = f"/outputs/keyframes/{shot_id}.png"
            shot_ref["keyframe_status"] = "done"
            keyframe_path = keyframe["local_path"]
            print(f"[{index}/{len(SHOTS)}] keyframe done: {keyframe['elapsed_seconds']}s")
            save_tasks()
        jobs.append((index, item, shot_ref, shot_id, keyframe_path))

    video_paths: list[str] = []
    max_workers = max(1, min(int(config.VIDEO_MAX_CONCURRENCY or 1), len(jobs)))
    print(f"Generating videos+dubbing with {max_workers} workers...")

    def run_job(job):
        index, item, shot_ref, shot_id, keyframe_path = job
        if shot_video_complete(shot_ref):
            return index, item, shot_ref.get("video_local_path"), 0.0, True, None
        shot_ref["video_status"] = "running"
        save_tasks()
        t0 = time.time()
        try:
            artifact = generate_shot_video_artifacts(
                shot_id,
                shot_ref,
                keyframe_path,
                tasks[task_id].get("voice_refs") or {},
            )
            shot_ref.update(artifact["updates"])
            save_tasks()
            return index, item, shot_ref["video_local_path"], time.time() - t0, False, None
        except Exception as exc:
            shot_ref["video_status"] = "error"
            shot_ref["video_error"] = str(exc)
            save_tasks()
            raw_video = shot_ref.get("raw_video_local_path")
            return index, item, raw_video, time.time() - t0, False, exc

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(run_job, job): job for job in jobs}
        for future in as_completed(futures):
            index, item, video_path, elapsed, cache_hit, error = future.result()
            if video_path:
                video_paths.append(video_path)
            if error:
                print(f"[{index}/{len(SHOTS)}] video+dubbing failed after {elapsed:.1f}s: {error}")
            else:
                print(
                    f"[{index}/{len(SHOTS)}] video+dubbing done: "
                    f"{elapsed:.1f}s cache={cache_hit} path={video_path}"
                )

    ordered_paths = []
    for item in SHOTS:
        shot_ref = tasks[task_id]["shots"]["storybook"][item["scene"]]["Shot"][item["shot"]]
        path = shot_ref.get("video_local_path")
        if path and Path(path).exists():
            ordered_paths.append(path)

    if not ordered_paths:
        raise RuntimeError("No generated video clips to concatenate.")
    if len(ordered_paths) != len(SHOTS):
        raise RuntimeError(
            f"Only {len(ordered_paths)}/{len(SHOTS)} dubbed clips are complete. "
            f"Fix the failed shots and rerun: python run_squirrel_demo.py {task_id}"
        )

    out_path = f"outputs/videos/{task_id}_squirrel_final.mp4"
    concat_method = concat_videos(ordered_paths, out_path, prefer_fast=True)
    subtitle_srt_path = f"outputs/subtitles/{task_id}_squirrel_final.srt"
    subtitle_vtt_path = f"outputs/subtitles/{task_id}_squirrel_final.vtt"
    subtitle_paths = [
        tasks[task_id]["shots"]["storybook"][item["scene"]]["Shot"][item["shot"]].get("subtitle_srt_local_path")
        for item in SHOTS
    ]
    subtitle_result = merge_sidecar_subtitles(
        ordered_paths,
        subtitle_paths,
        subtitle_srt_path,
        subtitle_vtt_path,
    )
    subtitled_out_path = f"outputs/videos/{task_id}_squirrel_final_subtitled.mp4"
    if subtitle_result["entries"] > 0:
        burn_subtitles(out_path, subtitle_srt_path, subtitled_out_path)
    else:
        subtitled_out_path = out_path
    total = time.time() - start
    print(f"Concat: {concat_method}")
    print(f"Final: {out_path}")
    print(f"Final subtitled: {subtitled_out_path}")
    print(f"Final subtitles: {subtitle_vtt_path} ({subtitle_result['entries']} entries)")
    print(f"Elapsed: {total:.2f}s ({total / 60:.2f} min)")
    print(f"Task ID: {task_id}")


if __name__ == "__main__":
    main()

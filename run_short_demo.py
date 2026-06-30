"""短绘本一键测试：跳过 LLM，预设 6 镜，约 30 秒成片。

用法：
    python run_short_demo.py              # 默认 squirrel（小松鼠）
    python run_short_demo.py squirrel
    python run_short_demo.py rabbit
    python run_short_demo.py <task_id>    # 续跑已有任务
"""
import sys
import time
from pathlib import Path

import config
from pipeline import tasks, create_task, save_tasks
from generation.image import generate_keyframe
from generation.video import generate_video
from generation.concat import concat_videos

PRESETS = {
    "squirrel": {
        "title": "小松鼠找松果",
        "synopsis": (
            "小松鼠奇奇住在松树上。冬天快到了，它储存的松果不够，"
            "决定去远处的松林找更多。路上下起大雨，奇奇躲进树洞等雨停。"
            "雨停后它继续赶路，终于找到满满的松果，抱着松果开心回家。"
        ),
        "characters": ["奇奇"],
        "shots": [
            {
                "plot": "水彩绘本风，秋日松树上，棕色小松鼠奇奇在树洞口数松果，表情有点担心",
                "motion": "Slow zoom in on squirrel counting nuts",
                "coarse": "Squirrel counts stored pine cones",
            },
            {
                "plot": "奇奇背着小布袋，沿着林间小路向远处松林出发，阳光温暖，落叶飘落",
                "motion": "Tracking shot following squirrel walking on forest path",
                "coarse": "Squirrel sets off to distant pine forest",
            },
            {
                "plot": "天空乌云密布，大雨倾盆，奇奇赶紧跑进大树洞躲雨，洞口有雨滴飞溅",
                "motion": "Quick push in as squirrel rushes into tree hollow",
                "coarse": "Rain starts and squirrel hides in tree hollow",
            },
            {
                "plot": "雨停了，天边出现彩虹，奇奇从树洞探出头，眼睛亮晶晶充满期待",
                "motion": "Gentle pan from rainbow to squirrel peeking out",
                "coarse": "Rain stops and squirrel looks hopeful",
            },
            {
                "plot": "奇奇来到松树林，地上铺满大大的松果，它开心地捡起松果放进布袋",
                "motion": "Slow dolly in on squirrel gathering pine cones",
                "coarse": "Squirrel finds many pine cones in forest",
            },
            {
                "plot": "傍晚，奇奇抱着鼓鼓的布袋走在回家路上，夕阳金色，松树枝头温暖明亮",
                "motion": "Slow pull back as squirrel walks home happily",
                "coarse": "Squirrel carries pine cones home at sunset",
            },
        ],
    },
    "rabbit": {
        "title": "小兔子找胡萝卜",
        "synopsis": (
            "小兔子白白住在森林里。一天，它想去河对岸找胡萝卜，但桥断了。"
            "它用木头搭了一座小桥，成功过了河，带着胡萝卜开心回家。"
        ),
        "characters": ["白白"],
        "shots": [
            {
                "plot": "水彩绘本风格，清晨森林里，一只雪白的小兔子白白在树下醒来，阳光透过树叶，温馨可爱",
                "motion": "Slow zoom in on the rabbit",
                "coarse": "Rabbit wakes up in forest",
            },
            {
                "plot": "小兔子白白站在河边远望对岸，橙红色的胡萝卜田清晰可见，白白眼神充满期待",
                "motion": "Gentle pan from rabbit to carrot field across river",
                "coarse": "Rabbit looks at carrots across river",
            },
            {
                "plot": "白白来到木桥前，发现桥板断裂悬空，白白惊讶地停下，河水在下方流淌",
                "motion": "Static shot with slight camera shake for surprise",
                "coarse": "Rabbit discovers broken bridge",
            },
            {
                "plot": "白白搬运木头搭建小桥，认真专注，半成品的木桥逐渐成形，励志可爱的画面",
                "motion": "Slow dolly in on rabbit building bridge",
                "coarse": "Rabbit builds small wooden bridge",
            },
            {
                "plot": "白白小心翼翼地走过新搭的小桥，成功到达对岸，背景是金黄的胡萝卜田",
                "motion": "Tracking shot following rabbit crossing bridge",
                "coarse": "Rabbit crosses bridge successfully",
            },
            {
                "plot": "白白抱着大胡萝卜走在回家路上，夕阳西下，森林小路温暖明亮，开心满足的表情",
                "motion": "Slow pull back as rabbit walks home happily",
                "coarse": "Rabbit carries carrot home happily",
            },
        ],
    },
}


def _parse_args():
    preset_name = "squirrel"
    task_id = None
    for arg in sys.argv[1:]:
        if arg in PRESETS:
            preset_name = arg
        elif len(arg) == 36 and arg.count("-") == 4:
            task_id = arg
    return preset_name, task_id


def build_task(task_id: str, preset: dict):
    char = preset["characters"][0]
    tasks[task_id] = {
        "status": "done",
        "progress": 100,
        "logs": [f"跳过 LLM 规划，使用预设故事「{preset['title']}」"],
        "sub_scripts": {
            "Sub-Script": {
                "绘本": {
                    "Plot": preset["synopsis"],
                    "Involving Characters": preset["characters"],
                }
            }
        },
        "scenes": {},
        "shots": {"绘本": {}},
        "character_refs": {},
        "cost": {"input_tokens": 0, "output_tokens": 0},
    }
    for idx, item in enumerate(preset["shots"], start=1):
        sc = f"Scene {idx}"
        sh = "Shot 1"
        if sc not in tasks[task_id]["shots"]["绘本"]:
            tasks[task_id]["shots"]["绘本"][sc] = {"Shot": {}}
        tasks[task_id]["shots"]["绘本"][sc]["Shot"][sh] = {
            "Involving Characters": {char: [0.2, 0.1, 0.8, 1.0]},
            "Plot/Visual Description": item["plot"],
            "Coarse Plot": item["coarse"],
            "Camera Movement": item["motion"],
            "Dialogue": {},
        }
    save_tasks()


def main():
    preset_name, resume_id = _parse_args()
    preset = PRESETS[preset_name]
    shots = preset["shots"]

    t0 = time.time()
    task_id = resume_id or create_task()
    if task_id not in tasks or not tasks[task_id].get("shots"):
        build_task(task_id, preset)

    print(f"Story: {preset['title']}")
    print(f"Task ID: {task_id}")
    print(f"Shots: {len(shots)} x {config.VIDEO_DURATION_SECONDS}s = ~{len(shots) * config.VIDEO_DURATION_SECONDS}s final")

    video_paths = []
    for i, item in enumerate(shots, 1):
        sc = f"Scene {i}"
        sh = "Shot 1"
        shot_ref = tasks[task_id]["shots"]["绘本"][sc]["Shot"][sh]
        shot_id = f"{task_id}_绘本_{sc}_{sh}".replace(" ", "_")

        kf_path = shot_ref.get("keyframe_local_path")
        if not kf_path or not Path(kf_path).exists():
            print(f"\n[{i}/{len(shots)}] keyframe...")
            kf_t0 = time.time()
            for attempt in range(1, 4):
                try:
                    kf = generate_keyframe(item["plot"], shot_id, {})
                    shot_ref["keyframe_local_path"] = kf["local_path"]
                    shot_ref["keyframe_status"] = "done"
                    kf_path = kf["local_path"]
                    print(f"  keyframe {kf['elapsed_seconds']}s (wall {time.time()-kf_t0:.1f}s)")
                    save_tasks()
                    break
                except Exception as e:
                    print(f"  keyframe attempt {attempt}/3 failed: {e}")
                    if attempt == 3:
                        kf_path = None
                    else:
                        time.sleep(5)
        else:
            print(f"\n[{i}/{len(shots)}] keyframe exists, skip")

        if not kf_path or not Path(kf_path).exists():
            print(f"[{i}/{len(shots)}] skip video (no keyframe)")
            continue

        vp = shot_ref.get("video_local_path")
        if vp and Path(vp).exists():
            print(f"[{i}/{len(shots)}] video exists, skip")
            video_paths.append(vp)
            continue

        print(f"[{i}/{len(shots)}] video...")
        vid_t0 = time.time()
        motion = f"{item['motion']}. {item['coarse']}"
        for attempt in range(1, 4):
            try:
                vid = generate_video(shot_id, kf_path, motion)
                shot_ref["video_local_path"] = vid["local_path"]
                shot_ref["video_status"] = "done"
                video_paths.append(vid["local_path"])
                print(f"  video {vid['elapsed_seconds']}s (wall {time.time()-vid_t0:.1f}s)")
                save_tasks()
                break
            except Exception as e:
                print(f"  attempt {attempt}/3 failed: {e}")
                if attempt == 3:
                    print(f"[{i}/{len(shots)}] skip after video failures")
                else:
                    time.sleep(10)

    if not video_paths:
        print("No video clips generated, cannot concat.")
        return

    out = f"outputs/videos/{task_id}_final.mp4"
    concat_videos(video_paths, out, prefer_fast=True)
    total = time.time() - t0
    print(f"\nDone in {total:.0f}s ({total/60:.1f} min)")
    print(f"Final: {out}")
    print(f"Task ID: {task_id}")


if __name__ == "__main__":
    main()

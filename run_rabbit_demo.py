"""Direct 6-shot rabbit demo (~30s final video), skip LLM planning."""
import time
import uuid
from pathlib import Path

import config
from pipeline import tasks, create_task, save_tasks
from generation.image import generate_keyframe
from generation.video import generate_video
from generation.concat import concat_videos

SYNOPSIS = (
    "小兔子白白住在森林里。一天，它想去河对岸找胡萝卜，但桥断了。"
    "它用木头搭了一座小桥，成功过了河，带着胡萝卜开心回家。"
)
CHARACTERS = ["白白"]

SHOTS = [
    {
        "sub": "绘本",
        "scene": "Scene 1",
        "shot": "Shot 1",
        "plot": "水彩绘本风格，清晨森林里，一只雪白的小兔子白白在树下醒来，阳光透过树叶，温馨可爱",
        "motion": "Slow zoom in on the rabbit",
        "coarse": "Rabbit wakes up in forest",
    },
    {
        "sub": "绘本",
        "scene": "Scene 2",
        "shot": "Shot 1",
        "plot": "小兔子白白站在河边远望对岸，橙红色的胡萝卜田清晰可见，白白眼神充满期待",
        "motion": "Gentle pan from rabbit to carrot field across river",
        "coarse": "Rabbit looks at carrots across river",
    },
    {
        "sub": "绘本",
        "scene": "Scene 3",
        "shot": "Shot 1",
        "plot": "白白来到木桥前，发现桥板断裂悬空，白白惊讶地停下，河水在下方流淌",
        "motion": "Static shot with slight camera shake for surprise",
        "coarse": "Rabbit discovers broken bridge",
    },
    {
        "sub": "绘本",
        "scene": "Scene 4",
        "shot": "Shot 1",
        "plot": "白白搬运木头搭建小桥，认真专注，半成品的木桥逐渐成形，励志可爱的画面",
        "motion": "Slow dolly in on rabbit building bridge",
        "coarse": "Rabbit builds small wooden bridge",
    },
    {
        "sub": "绘本",
        "scene": "Scene 5",
        "shot": "Shot 1",
        "plot": "白白小心翼翼地走过新搭的小桥，成功到达对岸，背景是金黄的胡萝卜田",
        "motion": "Tracking shot following rabbit crossing bridge",
        "coarse": "Rabbit crosses bridge successfully",
    },
    {
        "sub": "绘本",
        "scene": "Scene 6",
        "shot": "Shot 1",
        "plot": "白白抱着大胡萝卜走在回家路上，夕阳西下，森林小路温暖明亮，开心满足的表情",
        "motion": "Slow pull back as rabbit walks home happily",
        "coarse": "Rabbit carries carrot home happily",
    },
]


def build_task(task_id: str):
    tasks[task_id] = {
        "status": "done",
        "progress": 100,
        "logs": ["跳过 LLM 规划，使用预设 6 镜绘本分镜"],
        "sub_scripts": {"Sub-Script": {"绘本": {"Plot": SYNOPSIS, "Involving Characters": CHARACTERS}}},
        "scenes": {},
        "shots": {"绘本": {}},
        "character_refs": {},
        "cost": {"input_tokens": 0, "output_tokens": 0},
    }
    for item in SHOTS:
        ss, sc, sh = item["sub"], item["scene"], item["shot"]
        if sc not in tasks[task_id]["shots"][ss]:
            tasks[task_id]["shots"][ss][sc] = {"Shot": {}}
        tasks[task_id]["shots"][ss][sc]["Shot"][sh] = {
            "Involving Characters": {"白白": [0.2, 0.1, 0.8, 1.0]},
            "Plot/Visual Description": item["plot"],
            "Coarse Plot": item["coarse"],
            "Camera Movement": item["motion"],
            "Subtitles": {},
        }
    save_tasks()


def main():
    t0 = time.time()
    import sys
    task_id = sys.argv[1] if len(sys.argv) > 1 else create_task()
    if task_id not in tasks or not tasks[task_id].get("shots"):
        build_task(task_id)
    print(f"Task ID: {task_id}")
    print(f"Shots: {len(SHOTS)} x {config.VIDEO_DURATION_SECONDS}s = ~{len(SHOTS)*config.VIDEO_DURATION_SECONDS}s final")

    video_paths = []
    for i, item in enumerate(SHOTS, 1):
        ss, sc, sh = item["sub"], item["scene"], item["shot"]
        shot_ref = tasks[task_id]["shots"][ss][sc]["Shot"][sh]
        shot_id = f"{task_id}_{ss}_{sc}_{sh}".replace(" ", "_")

        kf_path = shot_ref.get("keyframe_local_path")
        if not kf_path or not Path(kf_path).exists():
            print(f"\n[{i}/{len(SHOTS)}] keyframe...")
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
            print(f"\n[{i}/{len(SHOTS)}] keyframe exists, skip")

        if not kf_path or not Path(kf_path).exists():
            print(f"[{i}/{len(SHOTS)}] skip video (no keyframe)")
            continue

        vp = shot_ref.get("video_local_path")
        if vp and Path(vp).exists():
            print(f"[{i}/{len(SHOTS)}] video exists, skip")
            video_paths.append(vp)
            continue

        print(f"[{i}/{len(SHOTS)}] video...")
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
                    print(f"[{i}/{len(SHOTS)}] skip after video failures")
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

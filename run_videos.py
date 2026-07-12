"""
视频生成脚本：接续关键帧结果，批量生成所有镜头视频。
用法：python run_videos.py [task_id前8位]
      不传则使用最新一次 tianming 测试结果。
"""
import os
import sys
import io
import json
import time
import glob
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import config
from generation.video import generate_video
from generation.shot_pipeline import build_shot_id, build_motion_prompt

# ── 找任务文件 ─────────────────────────────────────────────────
if len(sys.argv) > 1:
    TASK_JSON = f"outputs/test_tianming_{sys.argv[1][:8]}.json"
else:
    files = sorted(glob.glob("outputs/test_tianming_*.json"), key=os.path.getmtime, reverse=True)
    if not files:
        print("找不到测试结果文件，请先运行 run_test.py")
        sys.exit(1)
    TASK_JSON = files[0]

print(f"读取规划结果：{TASK_JSON}")
with open(TASK_JSON, encoding="utf-8") as f:
    task_data = json.load(f)

task_id = task_data["task_id"]
shots_data = task_data.get("shots", {})
keyframes = task_data.get("keyframes", {})

# ── 收集所有镜头 ───────────────────────────────────────────────
all_shots = []
missing_keyframe = []

for ss_name, scenes in shots_data.items():
    for scene_name, scene_data in scenes.items():
        for shot_name, shot_data in scene_data.get("Shot", {}).items():
            key = f"{ss_name}/{scene_name}/{shot_name}"
            kf_path = keyframes.get(key)
            if not kf_path or not os.path.isfile(kf_path):
                missing_keyframe.append(key)
                continue
            all_shots.append((ss_name, scene_name, shot_name, shot_data, kf_path))

total = len(all_shots)
if missing_keyframe:
    print(f"⚠️  {len(missing_keyframe)} 个镜头缺少关键帧，已跳过：{missing_keyframe}")

print(f"\n共 {total} 个镜头，{config.VIDEO_MAX_CONCURRENCY} 路并发")
print(f"视频时长：{config.VIDEO_DURATION_SECONDS}s  分辨率：{config.VIDEO_RESOLUTION}\n")


def _gen_one(args):
    ss_name, scene_name, shot_name, shot_data, kf_path = args
    shot_id = build_shot_id(task_id, ss_name, scene_name, shot_name)
    motion_prompt = build_motion_prompt(shot_data)
    result = generate_video(shot_id, kf_path, motion_prompt)
    return ss_name, scene_name, shot_name, result


video_results = {}
failed = []
start_all = time.time()

with ThreadPoolExecutor(max_workers=config.VIDEO_MAX_CONCURRENCY) as executor:
    future_map = {executor.submit(_gen_one, args): args[:3] for args in all_shots}
    done = 0
    for future in as_completed(future_map):
        ss_name, scene_name, shot_name = future_map[future]
        try:
            _, _, _, result = future.result()
            done += 1
            cache = " (缓存)" if result.get("cache_hit") else ""
            print(f"[{done}/{total}] ✅ {ss_name} > {scene_name} > {shot_name}  ({result['elapsed_seconds']:.0f}s){cache}")
            video_results[f"{ss_name}/{scene_name}/{shot_name}"] = result["local_path"]
        except Exception as e:
            done += 1
            print(f"[{done}/{total}] ❌ {ss_name} > {scene_name} > {shot_name}: {e}")
            failed.append(f"{ss_name}/{scene_name}/{shot_name}")

elapsed = time.time() - start_all
print(f"\n── 完成 ──")
print(f"成功: {total - len(failed)}/{total}  耗时: {elapsed:.0f}s")
if failed:
    print(f"失败: {failed}")

# 写回 JSON
task_data["videos"] = video_results
with open(TASK_JSON, "w", encoding="utf-8") as f:
    json.dump(task_data, f, ensure_ascii=False, indent=2)
print(f"\n结果已写回：{TASK_JSON}")

"""
配音脚本：对已生成的镜头视频补跑 TTS + mux，再重新拼接成片。
用法：python run_dubbing.py [task_id前8位]
"""
import os
import sys
import io
import json
import glob
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import config
from generation.postprocess import postprocess_shot_video
from generation.concat import concat_videos
from generation.shot_pipeline import build_shot_id

# ── 找任务文件 ─────────────────────────────────────────────────
if len(sys.argv) > 1:
    TASK_JSON = f"outputs/test_tianming_{sys.argv[1][:8]}.json"
else:
    files = sorted(glob.glob("outputs/test_tianming_*.json"), key=os.path.getmtime, reverse=True)
    if not files:
        print("找不到测试结果文件，请先运行 run_test.py")
        sys.exit(1)
    TASK_JSON = files[0]

print(f"读取：{TASK_JSON}")
with open(TASK_JSON, encoding="utf-8") as f:
    task_data = json.load(f)

task_id = task_data["task_id"]
shots_data = task_data.get("shots", {})
videos = task_data.get("videos", {})

# ── 收集镜头 ───────────────────────────────────────────────────
all_shots = []
for ss_name, scenes in shots_data.items():
    for scene_name, scene_data in scenes.items():
        for shot_name, shot_data in scene_data.get("Shot", {}).items():
            key = f"{ss_name}/{scene_name}/{shot_name}"
            video_path = videos.get(key)
            if not video_path or not os.path.isfile(video_path):
                print(f"  ⏭  {key}  （无视频，跳过）")
                continue
            all_shots.append((ss_name, scene_name, shot_name, shot_data, video_path))

total = len(all_shots)
print(f"\n共 {total} 个镜头待处理\n")


def _dub_one(args):
    ss_name, scene_name, shot_name, shot_data, video_path = args
    shot_id = build_shot_id(task_id, ss_name, scene_name, shot_name)
    result = postprocess_shot_video(shot_id, video_path, shot_data)
    return ss_name, scene_name, shot_name, result


dubbed_videos = {}
failed = []

with ThreadPoolExecutor(max_workers=3) as executor:
    future_map = {executor.submit(_dub_one, args): args[:3] for args in all_shots}
    done = 0
    for future in as_completed(future_map):
        ss_name, scene_name, shot_name = future_map[future]
        key = f"{ss_name}/{scene_name}/{shot_name}"
        try:
            _, _, _, result = future.result()
            done += 1
            dubbed = "配音" if result.get("dubbed") else "无台词"
            cache = " (缓存)" if result.get("cache_hit") else ""
            print(f"[{done}/{total}] ✅ {key}  [{dubbed}]{cache}")
            dubbed_videos[key] = result["local_path"]
        except Exception as e:
            done += 1
            print(f"[{done}/{total}] ❌ {key}: {e}")
            failed.append(key)
            # 配音失败则回退到原始视频
            dubbed_videos[key] = videos.get(key, "")

# ── 按顺序拼接 ─────────────────────────────────────────────────
def _sort_key(key: str):
    return [int(n) for n in re.findall(r"\d+", key)]

sorted_keys = sorted(dubbed_videos.keys(), key=_sort_key)
video_paths = [dubbed_videos[k] for k in sorted_keys if os.path.isfile(dubbed_videos.get(k, ""))]

print(f"\n拼接 {len(video_paths)} 个片段...")
output_path = f"outputs/videos/{task_id[:8]}_dubbed_final.mp4"
method = concat_videos(video_paths, output_path)

size_mb = os.path.getsize(output_path) / 1024 / 1024
print(f"\n✅ 成片已保存：{output_path}")
print(f"   方式：{method}  大小：{size_mb:.1f} MB")
if failed:
    print(f"   配音失败（已用原视频）：{failed}")

# 写回 JSON
task_data["dubbed_videos"] = dubbed_videos
with open(TASK_JSON, "w", encoding="utf-8") as f:
    json.dump(task_data, f, ensure_ascii=False, indent=2)
print(f"结果已写回：{TASK_JSON}")

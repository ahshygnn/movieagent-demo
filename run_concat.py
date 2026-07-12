"""
拼接脚本：把测试任务的所有镜头视频按 Sub-Script → Scene → Shot 顺序合成成片。
用法：python run_concat.py [task_id前8位]
"""
import os
import sys
import io
import json
import glob
import re

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from generation.concat import concat_videos

# ── 找任务文件 ─────────────────────────────────────────────────
if len(sys.argv) > 1:
    TASK_JSON = f"outputs/test_tianming_{sys.argv[1][:8]}.json"
else:
    files = sorted(glob.glob("outputs/test_tianming_*.json"), key=os.path.getmtime, reverse=True)
    if not files:
        print("找不到测试结果文件")
        sys.exit(1)
    TASK_JSON = files[0]

print(f"读取：{TASK_JSON}")
with open(TASK_JSON, encoding="utf-8") as f:
    task_data = json.load(f)

task_id = task_data["task_id"]
videos = task_data.get("videos", {})

if not videos:
    print("没有视频记录，请先运行 run_videos.py")
    sys.exit(1)


def _sort_key(key: str):
    # key 格式: "Sub-Script 2/Scene 1/Shot 3"
    nums = [int(n) for n in re.findall(r"\d+", key)]
    return nums  # [sub_script_idx, scene_idx, shot_idx]


sorted_keys = sorted(videos.keys(), key=_sort_key)

video_paths = []
missing = []
for key in sorted_keys:
    path = videos[key]
    if path and os.path.isfile(path):
        video_paths.append(path)
        print(f"  ✅ {key}")
    else:
        missing.append(key)
        print(f"  ⏭  {key}  （文件不存在，跳过）")

print(f"\n共 {len(video_paths)} 个片段待拼接")
if missing:
    print(f"跳过 {len(missing)} 个：{missing}")

if not video_paths:
    print("没有可拼接的视频，退出")
    sys.exit(1)

output_path = f"outputs/videos/{task_id[:8]}_final.mp4"
print(f"\n输出：{output_path}")

method = concat_videos(video_paths, output_path)

size_mb = os.path.getsize(output_path) / 1024 / 1024
print(f"\n✅ 成片已保存：{output_path}")
print(f"   方式：{method}  大小：{size_mb:.1f} MB")

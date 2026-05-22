"""
用 opencv 把 Task 5b67458a 的 16 条视频按剧本顺序拼接成成片。
"""
import json
import cv2
import os
from pathlib import Path

TASK_ID = "5b67458a-19fb-4fbd-a215-3c309d45652e"
OUTPUT_PATH = f"outputs/videos/{TASK_ID}_final.mp4"

# 加载任务数据
data = json.loads(Path("outputs/tasks.json").read_text(encoding="utf-8"))
task = data[TASK_ID]

# 按 Sub-Script → Scene → Shot 顺序收集有视频的片段
video_paths = []
for ss_name, scenes in task["shots"].items():
    for sc_name, sc_data in scenes.items():
        for sh_name, sh_data in (sc_data.get("Shot") or {}).items():
            vp = sh_data.get("video_local_path")
            if vp and Path(vp).exists():
                video_paths.append(vp)
                print(f"  ✅ {ss_name}/{sc_name}/{sh_name}: {vp}")
            else:
                print(f"  ⏭  {ss_name}/{sc_name}/{sh_name}: 无视频，跳过")

print(f"\n共 {len(video_paths)} 个片段待拼接")

if not video_paths:
    print("没有可拼接的视频，退出")
    exit(1)

# 读取第一个视频确定参数
cap0 = cv2.VideoCapture(video_paths[0])
fps    = cap0.get(cv2.CAP_PROP_FPS) or 24.0
width  = int(cap0.get(cv2.CAP_PROP_FRAME_WIDTH))
height = int(cap0.get(cv2.CAP_PROP_FRAME_HEIGHT))
cap0.release()
print(f"输出规格：{width}x{height} @ {fps:.1f}fps")

os.makedirs("outputs/videos", exist_ok=True)
fourcc = cv2.VideoWriter_fourcc(*"mp4v")
out = cv2.VideoWriter(OUTPUT_PATH, fourcc, fps, (width, height))

for i, vp in enumerate(video_paths):
    print(f"  [{i+1}/{len(video_paths)}] 写入 {Path(vp).name} ...")
    cap = cv2.VideoCapture(vp)
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        # 如果分辨率不一致则缩放
        if frame.shape[1] != width or frame.shape[0] != height:
            frame = cv2.resize(frame, (width, height))
        out.write(frame)
    cap.release()

out.release()
print(f"\n✅ 成片已保存：{OUTPUT_PATH}")
print(f"   大小：{Path(OUTPUT_PATH).stat().st_size / 1024 / 1024:.1f} MB")

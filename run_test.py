"""
单次测试运行脚本：指定风格预设和剧本，直接跑三步规划 pipeline。
用法：python run_test.py
"""
import os
import sys
import io
import json
import time

# ── 在 import config 之前设置风格预设 ──
os.environ["VISUAL_STYLE_PRESET"] = "shinkai"
# os.environ["VISUAL_STYLE_PRESET"] = "ghibli"
# os.environ["VISUAL_STYLE_PRESET"] = "pixar"

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import config
from pipeline import create_task, run_full_pipeline, tasks

# ── 测试素材 ────────────────────────────────────────────────
SYNOPSIS = """高三的夏天,总是一个人在天台吃午饭的少年,某天发现栏杆上停着一只折纸做的白鸟。第二天,白鸟变成了一朵折纸花;再后来,是一颗折纸星星。少年从不知道是谁放的,但他开始期待每天的午休。城市的天空很蓝,远处的云缓缓流动,蝉鸣声里,他小心地把这些折纸收进铁盒。夏天快结束的一天,天台上没有折纸,只有一张字条,上面写着一个天台的方向和一个时间。少年攥着字条,望向那个方向——另一栋楼的天台上,一个扎马尾的女孩正望着他,有些紧张地挥了挥手。夕阳把两座天台染成金色,少年也慢慢举起了手。有些心意,要越过整片天空,才能抵达对方。"""

CHARACTERS = ["少年", "女孩"]

# ── 主流程 ──────────────────────────────────────────────────
print(f"{'='*60}")
print(f"测试：《天台上的信号》")
print(f"风格预设：VISUAL_STYLE_PRESET = shinkai")
print(f"实际风格：{config.VISUAL_STYLE}")
print(f"角色：{CHARACTERS}")
print(f"{'='*60}\n")

task_id = create_task()
print(f"Task ID: {task_id}\n")

start = time.time()

# 同步跑 pipeline（run_full_pipeline 本身是阻塞的，background_tasks 是 FastAPI 层封装）
run_full_pipeline(task_id, SYNOPSIS, CHARACTERS)

elapsed = time.time() - start

# ── 输出结果 ────────────────────────────────────────────────
t = tasks[task_id]
print(f"\n{'='*60}")
print(f"Pipeline 完成，耗时 {elapsed:.0f}s")
print(f"状态: {t['status']}")

# 统计镜头
shots_data = t.get("shots", {})
total_shots = 0
print("\n── 镜头规划结果 ──")
for ss_name, scenes in shots_data.items():
    for scene_name, scene_data in scenes.items():
        shots = scene_data.get("Shot", {})
        total_shots += len(shots)
        print(f"\n  {ss_name} > {scene_name}  ({len(shots)} shots)")
        for shot_name, shot_data in shots.items():
            plot = shot_data.get("Plot/Visual Description", "")[:80]
            dialogue = shot_data.get("Dialogue", {})
            shot_type = shot_data.get("Shot Type", "")
            camera = shot_data.get("Camera Movement", "")[:40]
            print(f"    [{shot_name}]")
            print(f"      画面: {plot}...")
            print(f"      景别: {shot_type}")
            print(f"      运镜: {camera}")
            if dialogue:
                for char, line in dialogue.items():
                    print(f"      旁白 ({char}): {str(line)[:60]}")

print(f"\n── 总计 {total_shots} 个镜头 ──")
print(f"\n日志:")
for log in t.get("logs", []):
    print(f"  {log}")

# 保存结果到文件方便查看
out_path = f"outputs/test_tianming_{task_id[:8]}.json"
with open(out_path, "w", encoding="utf-8") as f:
    json.dump({
        "task_id": task_id,
        "visual_style": config.VISUAL_STYLE,
        "synopsis": SYNOPSIS,
        "characters": CHARACTERS,
        "sub_scripts": t.get("sub_scripts"),
        "scenes": t.get("scenes"),
        "shots": t.get("shots"),
        "logs": t.get("logs"),
        "cost": t.get("cost"),
    }, f, ensure_ascii=False, indent=2)
print(f"\n完整结果已保存：{out_path}")

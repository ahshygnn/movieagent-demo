"""
修复提灯人定格卡顿：缩短4条超长旁白、清视频缓存、删 dubbed/audio 文件。
运行完后直接用 run_tidengman.py resume 重生成。
"""
import json
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

TASK_ID  = "f6731663-21ed-45e8-9c30-7e743ad2fc7b"
TASK_FILE = "outputs/tasks.json"

# 新旁白（21-22 字，目标配音 ≤ 4.8s）
PATCHES = {
    # (sub_script, scene, shot) : 新旁白文本
    ("Sub-Script 1", "Scene 2", "Shot 1"): "终年浓雾的山谷里,小满每天黄昏点亮山道的灯。",
    ("Sub-Script 1", "Scene 2", "Shot 2"): "一只奄奄一息的小鹿闯入木屋,鹿角之光渐渐暗去。",
    ("Sub-Script 1", "Scene 1", "Shot 1"): "老人说,这是引路鹿——它的光若熄灭,山谷将永堕黑暗。",
    ("Sub-Script 1", "Scene 3", "Shot 1"): "浓雾越来越浓,山风几次险些吹灭她手中的灯。",
}

# 读取 tasks.json
with open(TASK_FILE, encoding="utf-8") as f:
    data = json.load(f)

task = data[TASK_ID]
shots_root = task["shots"]

patched = []
for (ss_key, sc_key, sh_key), new_text in PATCHES.items():
    # tasks.json 键名可能带空格，兼容 "Sub-Script 1" / "Scene 1" / "Shot 1"
    ss = shots_root.get(ss_key)
    if ss is None:
        print(f"  [WARN] sub-script not found: {ss_key!r}")
        continue
    sc = ss.get(sc_key)
    if sc is None:
        print(f"  [WARN] scene not found: {sc_key!r}")
        continue
    shot_dict = sc.get("Shot") or {}
    sd = shot_dict.get(sh_key)
    if sd is None:
        print(f"  [WARN] shot not found: {sh_key!r}")
        continue

    old_text = ""
    if isinstance(sd.get("Dialogue"), dict):
        for v in sd["Dialogue"].values():
            old_text = v
    print(f"\n[{sc_key} {sh_key}]")
    print(f"  旧: {old_text}")
    print(f"  新: {new_text}")

    # 更新旁白
    sd["Dialogue"] = {"旁白": new_text}

    # 清视频状态，强制重生
    for field in ("video_local_path", "video_has_dubbing", "combined_audio_local_path",
                  "enhanced_video_local_path", "video_status", "dubbing_cache_hit",
                  "audio_files", "video_error"):
        sd.pop(field, None)
    sd["video_status"] = "pending"

    # 构造 shot_id（用于文件名）
    shot_id = f"{TASK_ID}_{ss_key}_{sc_key}_{sh_key}".replace(" ", "_")
    patched.append(shot_id)

# 保存 tasks.json
with open(TASK_FILE, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
print(f"\ntasks.json 已更新，共修改 {len(patched)} 个镜头。")

# 删缓存文件
VIDEO_DIR = "outputs/videos"
AUDIO_DIR = "outputs/audio"

deleted = []
for shot_id in patched:
    targets = [
        os.path.join(VIDEO_DIR, f"{shot_id}_dubbed.mp4"),
        os.path.join(AUDIO_DIR, f"{shot_id}_dialogue.mp3"),
    ]
    for p in targets:
        if os.path.isfile(p):
            os.remove(p)
            deleted.append(p)
            print(f"  删除: {p}")
        else:
            print(f"  已不存在(跳过): {p}")

print(f"\n共删除 {len(deleted)} 个缓存文件。")
print("\n现在运行:")
print(f"  python run_tidengman.py {TASK_ID}")

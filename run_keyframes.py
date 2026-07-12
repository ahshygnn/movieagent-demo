"""
关键帧生成脚本：接续已有规划结果，批量生成所有镜头的关键帧。
用法：python run_keyframes.py [task_id]
      不传 task_id 则使用最新一次 tianming 测试的结果。
"""
import os
import sys
import io
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── 风格预设：必须在 import config 之前设置 ──
os.environ["VISUAL_STYLE_PRESET"] = "shinkai"

import config
from generation.image import generate_keyframe
from generation.shot_pipeline import build_shot_id

# ── 角色参考图 ────────────────────────────────────────────────
CHARACTER_REFS = {
    "少年": "outputs/characters/少年.png",
    "女孩": "outputs/characters/女孩.png",
}

# ── 找任务 ────────────────────────────────────────────────────
if len(sys.argv) > 1:
    TASK_JSON = f"outputs/test_tianming_{sys.argv[1][:8]}.json"
else:
    # 找最新的 tianming 结果文件
    import glob
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

# ── 检查参考图 ─────────────────────────────────────────────────
print("\n── 角色参考图 ──")
for name, path in CHARACTER_REFS.items():
    exists = os.path.isfile(path)
    print(f"  {name}: {path}  {'✅' if exists else '❌ 文件不存在，将跳过'}")

# 过滤掉不存在的参考图
char_refs = {k: v for k, v in CHARACTER_REFS.items() if os.path.isfile(v)}

# ── 收集所有镜头 ───────────────────────────────────────────────
all_shots = []
for ss_name, scenes in shots_data.items():
    for scene_name, scene_data in scenes.items():
        for shot_name, shot_data in scene_data.get("Shot", {}).items():
            all_shots.append((ss_name, scene_name, shot_name, shot_data))

total = len(all_shots)
print(f"\n共 {total} 个镜头，最多 {config.KEYFRAME_MAX_CONCURRENCY} 路并发\n")
print(f"风格：{config.VISUAL_STYLE}")
print(f"尺寸：{config.KEYFRAME_SIZE_FINAL}\n")


def _gen_one(args):
    ss_name, scene_name, shot_name, shot_data = args
    shot_id = build_shot_id(task_id, ss_name, scene_name, shot_name)
    plot = shot_data.get("Plot/Visual Description", "")

    # 按镜头涉及角色筛选参考图
    involving = shot_data.get("Involving Characters", {})
    if isinstance(involving, dict):
        ref_names = set(involving.keys())
    elif isinstance(involving, list):
        ref_names = set(involving)
    else:
        ref_names = set()
    shot_refs = {k: v for k, v in char_refs.items() if k in ref_names}

    result = generate_keyframe(plot, shot_id, shot_refs)
    return ss_name, scene_name, shot_name, result


results = {}
failed = []
start_all = time.time()

with ThreadPoolExecutor(max_workers=config.KEYFRAME_MAX_CONCURRENCY) as executor:
    future_map = {executor.submit(_gen_one, args): args[:3] for args in all_shots}
    done = 0
    for future in as_completed(future_map):
        ss_name, scene_name, shot_name = future_map[future]
        try:
            _, _, _, result = future.result()
            done += 1
            print(f"[{done}/{total}] ✅ {ss_name} > {scene_name} > {shot_name}  ({result['elapsed_seconds']}s)")
            results[f"{ss_name}/{scene_name}/{shot_name}"] = result["local_path"]
        except Exception as e:
            done += 1
            print(f"[{done}/{total}] ❌ {ss_name} > {scene_name} > {shot_name}: {e}")
            failed.append(f"{ss_name}/{scene_name}/{shot_name}")

elapsed = time.time() - start_all
print(f"\n── 完成 ──")
print(f"成功: {total - len(failed)}/{total}  耗时: {elapsed:.0f}s")
if failed:
    print(f"失败: {failed}")

# 保存结果
out = {**task_data, "keyframes": results}
with open(TASK_JSON, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print(f"\n结果已写回：{TASK_JSON}")

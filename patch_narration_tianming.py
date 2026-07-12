"""
给《天台上的信号》的关键镜头添加旁白，然后重跑配音+拼接。
"""
import json
import os
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

TASK_JSON = "outputs/test_tianming_ede1f009.json"

# 旁白映射：(Sub-Script, Scene, Shot) → 旁白文本
NARRATION = {
    ("Sub-Script 1", "Scene 1", "Shot 1"): "高三的夏天，他总是一个人在天台吃午饭。",
    ("Sub-Script 1", "Scene 2", "Shot 1"): "那天，栏杆上多了一只折纸白鸟。",
    ("Sub-Script 2", "Scene 1", "Shot 3"): "第二天是一朵折纸花。他不知道是谁放的，但他开始期待每天的午休。",
    ("Sub-Script 2", "Scene 2", "Shot 3"): "再后来，是一颗折纸星星。",
    ("Sub-Script 2", "Scene 3", "Shot 1"): "城市的天空很蓝，远处的云缓缓流动，蝉鸣声里，他小心地把这些折纸收进铁盒。",
    ("Sub-Script 3", "Scene 1", "Shot 1"): "夏天快结束的一天，天台上没有折纸。",
    ("Sub-Script 3", "Scene 1", "Shot 3"): "只有一张字条，上面写着一个天台的方向和一个时间。",
    ("Sub-Script 4", "Scene 1", "Shot 3"): "少年攥着字条，望向那个方向——",
    ("Sub-Script 4", "Scene 2", "Shot 1"): "另一栋楼的天台上，一个扎马尾的女孩正望着他，有些紧张地挥了挥手。",
    ("Sub-Script 4", "Scene 3", "Shot 2"): "有些心意，要越过整片天空，才能抵达对方。",
}

with open(TASK_JSON, encoding="utf-8") as f:
    task_data = json.load(f)

shots_data = task_data["shots"]
patched = []

for (ss, sc, sh), text in NARRATION.items():
    shot = shots_data.get(ss, {}).get(sc, {}).get("Shot", {}).get(sh)
    if shot is None:
        print(f"  [WARN] 找不到镜头: {ss} > {sc} > {sh}")
        continue
    shot["Dialogue"] = {"旁白": text}
    patched.append(f"{ss} > {sc} > {sh}")
    print(f"  ✅ {ss} > {sc} > {sh}")
    print(f"     {text}")

with open(TASK_JSON, "w", encoding="utf-8") as f:
    json.dump(task_data, f, ensure_ascii=False, indent=2)

print(f"\n共写入 {len(patched)} 条旁白，已保存到 {TASK_JSON}")

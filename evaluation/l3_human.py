"""
L3 人工评测记录工具（命令行交互）

用法：
    python evaluation/l3_human.py --task_id <id> --video_path <path> [--title <title>]
"""
from __future__ import annotations

import json
import sys
import argparse
from datetime import datetime, timezone
from pathlib import Path


def _ask_int(prompt: str, min_val: int, max_val: int, default: int | None = None) -> int:
    while True:
        suffix = f" [{min_val}-{max_val}]" + (f" (默认 {default})" if default is not None else "")
        raw = input(f"{prompt}{suffix}: ").strip()
        if not raw and default is not None:
            return default
        try:
            val = int(raw)
            if min_val <= val <= max_val:
                return val
        except ValueError:
            pass
        print(f"  ⚠ 请输入 {min_val}~{max_val} 之间的整数")


def _ask_yes_no(prompt: str) -> bool:
    while True:
        raw = input(f"{prompt} [y/n]: ").strip().lower()
        if raw in ("y", "yes", "是", "1"):
            return True
        if raw in ("n", "no", "否", "0"):
            return False
        print("  ⚠ 请输入 y 或 n")


def _ask_text(prompt: str, required: bool = False) -> str:
    while True:
        val = input(f"{prompt}: ").strip()
        if val or not required:
            return val
        print("  ⚠ 此项不能为空")


def run_human_eval(task_id: str, video_path: str, tasks_dict: dict, story_title: str = "") -> dict:
    """
    命令行交互式人工评测，评完后保存到 evaluation/results/l3_{task_id}.json。
    """
    print("\n" + "═" * 60)
    print("  MovieAgent L3 人工评测")
    print("═" * 60)
    print(f"  任务 ID  : {task_id}")
    print(f"  视频路径 : {video_path}")
    if story_title:
        print(f"  故事标题 : {story_title}")
    print("─" * 60)
    print("  请先播放视频后再进行评测。\n")

    # 1. 角色跨镜头一致性
    print("【1. 角色跨镜头一致性】（1=完全不一致，2=部分一致，3=完全一致）")
    face_body = _ask_int("  脸型/体型一致性", 1, 3)
    costume = _ask_int("  服装颜色一致性", 1, 3)
    style = _ask_int("  整体风格一致性", 1, 3)
    avg_consistency = round((face_body + costume + style) / 3, 2)

    # 2. 首次成片满意度
    print("\n【2. 首次成片满意度】")
    print("  不做任何修改，视频是否可以直接发布？")
    first_pass = _ask_yes_no("  可以发布")

    # 3. 感知任务时长
    print("\n【3. 任务完成感知时长】")
    perceived_minutes = _ask_int("  从输入剧本到觉得视频可以发布，实际花费了多少分钟", 1, 999)

    # 4. 主要问题
    print("\n【4. 主要问题（可选，直接回车跳过）】")
    main_issues = _ask_text("  主要问题描述")

    # 5. 备注
    notes = _ask_text("  其他备注（可选）")

    result = {
        "task_id": task_id,
        "eval_time": datetime.now(timezone.utc).isoformat(),
        "video_path": video_path,
        "story_title": story_title,
        "character_consistency": {
            "face_body": face_body,
            "costume_color": costume,
            "overall_style": style,
            "average": avg_consistency,
        },
        "first_pass_satisfaction": first_pass,
        "perceived_duration_minutes": perceived_minutes,
        "main_issues": main_issues,
        "notes": notes,
    }

    # 保存
    out_dir = Path(__file__).parent / "results"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"l3_{task_id}.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "─" * 60)
    print(f"  ✅ L3 评测已保存：{out_path}")
    print(f"  角色一致性均分：{avg_consistency}/3")
    print(f"  首次成片满意度：{'✓ 可发布' if first_pass else '✗ 需修改'}")
    print(f"  感知时长：{perceived_minutes} 分钟")
    print("═" * 60 + "\n")

    return result


def main():
    parser = argparse.ArgumentParser(description="MovieAgent L3 人工评测")
    parser.add_argument("--task_id", required=True, help="要评测的任务 ID")
    parser.add_argument("--video_path", default="", help="最终视频路径")
    parser.add_argument("--title", default="", help="故事标题（可选）")
    args = parser.parse_args()

    # 尝试加载 tasks
    tasks_file = Path(__file__).parent.parent / "outputs" / "tasks.json"
    tasks_dict: dict = {}
    if tasks_file.exists():
        try:
            tasks_dict = json.loads(tasks_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    run_human_eval(args.task_id, args.video_path, tasks_dict, args.title)


if __name__ == "__main__":
    main()

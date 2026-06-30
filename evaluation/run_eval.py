"""
MovieAgent 评测主入口

用法：
  python evaluation/run_eval.py --task_id <task_id>
  python evaluation/run_eval.py --task_id <task_id> --skip-l2
  python evaluation/run_eval.py --task_id <task_id> --human
  python evaluation/run_eval.py --task_id <task_id> --output results/my_report.json

参数：
  --task_id   必填，要评测的任务 ID
  --skip-l2   跳过 L2 VLM 评测（节省 API 费用）
  --human     运行 L3 人工评测交互
  --output    结果输出路径（默认 evaluation/results/{task_id}_{ts}_eval.json）
"""
from __future__ import annotations

import argparse
import json
import sys
import os
from datetime import datetime, timezone
from pathlib import Path

# 允许从项目根目录或 evaluation/ 目录运行
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from evaluation.l1_technical import (
    eval_pipeline_completion,
    eval_latency,
    eval_token_cost,
    eval_bbox_validity,
)
from evaluation.utils.report import build_summary, save_report, print_report


def _load_tasks(tasks_file: Path) -> dict:
    if not tasks_file.exists():
        print(f"❌ tasks.json 未找到：{tasks_file}", file=sys.stderr)
        sys.exit(1)
    try:
        return json.loads(tasks_file.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"❌ 加载 tasks.json 失败：{e}", file=sys.stderr)
        sys.exit(1)


def _get_original_script(task_id: str, tasks_dict: dict) -> str:
    task = tasks_dict.get(task_id) or {}
    plots = []
    for ss in (task.get("sub_scripts") or {}).get("Sub-Script", {}).values():
        if isinstance(ss, dict) and ss.get("Plot"):
            plots.append(ss["Plot"])
    return "\n\n".join(plots)


def run_eval(
    task_id: str,
    tasks_dict: dict,
    skip_l2: bool = False,
    run_human: bool = False,
    output_path: str | None = None,
) -> dict:
    task = tasks_dict.get(task_id)
    if not task:
        print(f"❌ 任务 {task_id} 不存在", file=sys.stderr)
        sys.exit(1)

    print(f"\n🔍 开始评测任务：{task_id}")
    print(f"   状态：{task.get('status', 'unknown')}")
    print("─" * 50)

    # ── L1 技术指标 ─────────────────────────────────────────
    print("\n[L1] 计算技术指标...")
    l1_completion = eval_pipeline_completion(task_id, tasks_dict)
    l1_latency = eval_latency(task_id, tasks_dict)
    l1_token = eval_token_cost(task_id, tasks_dict)
    l1_bbox = eval_bbox_validity(task_id, tasks_dict)
    print(f"  Pipeline 完成率：{'PASS' if l1_completion['overall_completion'] else 'FAIL'}")
    print(f"  Shot 数量：{l1_latency['shots_count']}")
    print(f"  BBox 合法率：{l1_bbox.get('validity_rate', 'N/A')}")

    # ── L2 内容质量 ─────────────────────────────────────────
    l2_alignment: dict = {}
    l2_style: dict = {}
    l2_coherence: dict = {}

    if not skip_l2:
        print("\n[L2] 计算内容质量指标...")
        try:
            from evaluation.l2_content import (
                eval_all_text_visual_alignment,
                eval_style_consistency,
                eval_narrative_coherence,
                collect_keyframe_paths,
                build_shot_sequence,
            )
            anthropic_client = _make_anthropic_client()

            # 2.1 文本-视觉对齐
            print("  2.1 文本-视觉对齐评分中（VLM 调用，最多 3 路并发）...")
            l2_alignment = eval_all_text_visual_alignment(task_id, tasks_dict, anthropic_client)
            print(f"     已评 {l2_alignment.get('shots_evaluated', 0)} 个 Shot，"
                  f"均分 {l2_alignment.get('average_score', 'N/A')}")

            # 2.2 风格一致性（CLIP-I）
            print("  2.2 视觉风格一致性评分中（CLIP-I）...")
            kf_paths = collect_keyframe_paths(task_id, tasks_dict)
            l2_style = eval_style_consistency(kf_paths)
            if l2_style.get("available"):
                print(f"     方差={l2_style.get('embedding_variance', 'N/A')}，"
                      f"得分={l2_style.get('consistency_score', 'N/A')}")
            else:
                print(f"     ⚠ CLIP 不可用：{l2_style.get('error', '')}")

            # 2.3 叙事连贯性
            print("  2.3 叙事连贯性评分中（VLM 多图，最多 8 帧）...")
            original_script = _get_original_script(task_id, tasks_dict)
            shot_seq = build_shot_sequence(task_id, tasks_dict)
            l2_coherence = eval_narrative_coherence(original_script, shot_seq, anthropic_client)
            print(f"     得分={l2_coherence.get('coherence_score', 'N/A')}，"
                  f"评估帧数={l2_coherence.get('frames_evaluated', 0)}")

        except ValueError as e:
            print(f"  ⚠ L2 跳过（{e}）")
        except Exception as e:
            print(f"  ✗ L2 评测出错：{e}")
    else:
        print("\n[L2] --skip-l2 已指定，跳过 VLM 评测")

    # ── L3 人工评测 ─────────────────────────────────────────
    l3_result = None
    if run_human:
        from evaluation.l3_human import run_human_eval
        video_path = _find_final_video(task_id)
        l3_result = run_human_eval(task_id, video_path, tasks_dict)

    # ── 组装报告 ────────────────────────────────────────────
    full_report = {
        "task_id": task_id,
        "eval_timestamp": datetime.now(timezone.utc).isoformat(),
        "story_input": _get_original_script(task_id, tasks_dict),
        "l1_technical": {
            "pipeline_completion": l1_completion,
            "latency": l1_latency,
            "token_cost": l1_token,
            "bbox_validity": l1_bbox,
        },
        "l2_content": {
            "text_visual_alignment": l2_alignment or None,
            "style_consistency": l2_style or None,
            "narrative_coherence": l2_coherence or None,
        },
        "l3_human": l3_result,
        "summary": {},
    }
    full_report["summary"] = build_summary(full_report)

    # ── 保存 ────────────────────────────────────────────────
    if not output_path:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = Path(__file__).parent / "results"
        out_dir.mkdir(exist_ok=True)
        output_path = str(out_dir / f"{task_id}_{ts}_eval.json")

    save_report(full_report, output_path)
    print_report(full_report)

    return full_report


def _make_anthropic_client():
    try:
        import anthropic
    except ImportError:
        raise ValueError("anthropic 未安装，请执行：pip install anthropic")
    import config
    api_key = getattr(config, "ANTHROPIC_API_KEY", "") or os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise ValueError(
            "ANTHROPIC_API_KEY 未配置。\n"
            "请在 .env 中添加：ANTHROPIC_API_KEY=your_key_here"
        )
    return anthropic.Anthropic(api_key=api_key)


def _find_final_video(task_id: str) -> str:
    candidates = [
        os.path.join("outputs", "videos", f"{task_id}_final_subtitled.mp4"),
        os.path.join("outputs", "videos", f"{task_id}_final.mp4"),
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    return ""


def main():
    parser = argparse.ArgumentParser(
        description="MovieAgent 评测框架 (AnimationBench 方法论)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--task_id", required=True, help="要评测的任务 ID")
    parser.add_argument("--skip-l2", action="store_true", help="跳过 L2 VLM 评测")
    parser.add_argument("--human", action="store_true", help="运行 L3 人工评测")
    parser.add_argument("--output", default=None, help="报告输出路径")
    parser.add_argument(
        "--tasks-file",
        default=str(_ROOT / "outputs" / "tasks.json"),
        help="tasks.json 路径（默认 outputs/tasks.json）",
    )
    args = parser.parse_args()

    tasks_dict = _load_tasks(Path(args.tasks_file))
    run_eval(
        task_id=args.task_id,
        tasks_dict=tasks_dict,
        skip_l2=args.skip_l2,
        run_human=args.human,
        output_path=args.output,
    )


if __name__ == "__main__":
    main()

"""
评测结果汇总与打印工具
"""
from __future__ import annotations

import json
from pathlib import Path


def build_summary(full_report: dict) -> dict:
    """从完整评测报告中提取摘要，判断 L1 pass/fail，计算 L2 均分。"""
    l1 = full_report.get("l1_technical") or {}
    l2 = full_report.get("l2_content") or {}

    # L1 pass/fail
    comp = (l1.get("pipeline_completion") or {})
    l1_pass = (
        comp.get("director_success", False)
        and (comp.get("scene_success_rate") or 0) >= 0.9
        and (comp.get("shot_success_rate") or 0) >= 0.9
        and comp.get("overall_completion", False)
    )

    # L2 均分
    scores = []
    align_score = (l2.get("text_visual_alignment") or {}).get("average_score")
    style_score = (l2.get("style_consistency") or {}).get("consistency_score")
    coherence_score = (l2.get("narrative_coherence") or {}).get("coherence_score")
    for s in [align_score, style_score, coherence_score]:
        if s is not None:
            scores.append(s)
    l2_avg = round(sum(scores) / len(scores), 2) if scores else None

    # 找优势和问题
    strengths, issues = [], []

    if l1_pass:
        strengths.append("Pipeline 全流程运行成功")
    else:
        issues.append("Pipeline 存在失败步骤，请检查 json_parse_errors")

    bbox = (l1.get("bbox_validity") or {})
    vr = bbox.get("validity_rate")
    if vr is not None:
        if vr >= 0.95:
            strengths.append(f"Bounding box 合法率高（{vr:.1%}）")
        else:
            issues.append(f"Bounding box 合法率偏低（{vr:.1%}）")

    if align_score is not None:
        if align_score >= 4.0:
            strengths.append(f"文本-视觉对齐良好（{align_score}/5）")
        elif align_score < 3.0:
            issues.append(f"文本-视觉对齐较差（{align_score}/5）")

    if style_score is not None:
        if style_score >= 4.0:
            strengths.append(f"风格一致性稳定（{style_score}/5）")
        elif style_score < 3.0:
            issues.append(f"跨镜头风格漂移明显（{style_score}/5）")

    if coherence_score is not None:
        if coherence_score >= 4.0:
            strengths.append(f"叙事连贯性强（{coherence_score}/5）")
        elif coherence_score < 3.0:
            issues.append(f"叙事连贯性需改进（{coherence_score}/5）")

    return {
        "l1_overall": "pass" if l1_pass else "fail",
        "l2_average_score": l2_avg,
        "strengths": strengths,
        "issues": issues,
    }


def save_report(report: dict, output_path: str):
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n✅ 评测报告已保存：{output_path}")


def print_report(report: dict):
    """在终端打印易读的评测摘要。"""
    task_id = report.get("task_id", "?")
    summary = report.get("summary") or {}
    l1 = report.get("l1_technical") or {}
    l2 = report.get("l2_content") or {}

    print("\n" + "═" * 64)
    print(f"  MovieAgent 评测报告  |  task_id: {task_id}")
    print("═" * 64)

    # L1
    print("\n【L1 技术指标】")
    comp = l1.get("pipeline_completion") or {}
    print(f"  Pipeline 完成率     : {'✓ PASS' if summary.get('l1_overall') == 'pass' else '✗ FAIL'}")
    print(f"    Director 成功     : {comp.get('director_success')}")
    print(f"    Scene 成功率      : {_fmt_pct(comp.get('scene_success_rate'))}")
    print(f"    Shot 成功率       : {_fmt_pct(comp.get('shot_success_rate'))}")
    if comp.get("json_parse_errors"):
        print(f"    解析错误数        : {len(comp['json_parse_errors'])}")

    lat = l1.get("latency") or {}
    print(f"  规划总耗时          : {_fmt_sec(lat.get('planning_total_seconds'))}")
    print(f"    Director          : {_fmt_sec(lat.get('director_seconds'))}")
    print(f"    Scene             : {_fmt_sec(lat.get('scene_seconds'))}")
    print(f"    Shot              : {_fmt_sec(lat.get('shot_seconds'))}")
    print(f"  Shot 数量           : {lat.get('shots_count', '?')}")
    print(f"  每 Shot 平均规划时间 : {_fmt_sec(lat.get('seconds_per_shot'))}")

    tok = l1.get("token_cost") or {}
    print(f"  Token 消耗          : {tok.get('total_tokens', 0):,} "
          f"（in={tok.get('input_tokens', 0):,} / out={tok.get('output_tokens', 0):,}）")
    print(f"  预估费用 (USD)      : ${tok.get('estimated_cost_usd', 0):.4f}")

    bbox = l1.get("bbox_validity") or {}
    print(f"  BBox 合法率         : {_fmt_pct(bbox.get('validity_rate'))} "
          f"({bbox.get('valid_bbox_count', 0)}/{bbox.get('shots_with_bbox', 0)})")

    # L2
    print("\n【L2 内容质量】")
    align = l2.get("text_visual_alignment") or {}
    style = l2.get("style_consistency") or {}
    coherence = l2.get("narrative_coherence") or {}

    _print_l2_row("文本-视觉对齐", align.get("average_score"), align.get("shots_evaluated"))
    _print_l2_row("视觉风格一致性", style.get("consistency_score"),
                  style.get("frame_count"), extra=f"方差={style.get('embedding_variance', 'N/A')}")
    _print_l2_row("叙事连贯性", coherence.get("coherence_score"),
                  coherence.get("frames_evaluated"))
    if coherence.get("main_issues"):
        print(f"    主要问题: {coherence['main_issues']}")

    l2_avg = summary.get("l2_average_score")
    print(f"  L2 综合均分         : {f'{l2_avg:.2f}/5' if l2_avg else 'N/A'}")

    # L3
    l3 = report.get("l3_human")
    if l3:
        print("\n【L3 人工评测】")
        cc = l3.get("character_consistency") or {}
        print(f"  角色一致性均分     : {cc.get('average', '?')}/3")
        print(f"  首次成片满意度     : {'✓ 可发布' if l3.get('first_pass_satisfaction') else '✗ 需修改'}")
        print(f"  感知时长           : {l3.get('perceived_duration_minutes', '?')} 分钟")
        if l3.get("main_issues"):
            print(f"  主要问题           : {l3['main_issues']}")

    # Summary
    print("\n【总结】")
    for s in summary.get("strengths", []):
        print(f"  ✅ {s}")
    for i in summary.get("issues", []):
        print(f"  ⚠️  {i}")
    print("═" * 64 + "\n")


def _fmt_pct(v) -> str:
    if v is None:
        return "N/A"
    return f"{v:.1%}"


def _fmt_sec(v) -> str:
    if v is None:
        return "N/A"
    return f"{v:.1f}s"


def _print_l2_row(label: str, score, count, extra: str = ""):
    score_str = f"{score:.2f}/5" if score is not None else "N/A (未运行或无关键帧)"
    count_str = f"({count} 帧/shots)" if count is not None else ""
    extra_str = f"  {extra}" if extra else ""
    print(f"  {label:<12} : {score_str}  {count_str}{extra_str}")

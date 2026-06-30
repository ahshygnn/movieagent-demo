"""
L1 技术指标（自动化）
- 1.1 Pipeline 完成率
- 1.2 端到端延迟
- 1.3 Token 消耗统计
- 1.4 Bounding Box 合法率
"""


# ── 1.1 Pipeline 完成率 ───────────────────────────────────────────────────────

def eval_pipeline_completion(task_id: str, tasks: dict) -> dict:
    """
    返回：
    {
        "director_success": bool,
        "scene_success_rate": float,
        "shot_success_rate": float,
        "json_parse_errors": list,
        "overall_completion": bool
    }
    """
    task = tasks.get(task_id) or {}
    errors: list[dict] = []

    # Director
    sub_scripts = task.get("sub_scripts") or {}
    director_success = bool(sub_scripts.get("Sub-Script"))
    if not director_success:
        errors.append({"location": "director", "issue": "sub_scripts missing or empty Sub-Script field"})

    # Scene
    ss_dict = sub_scripts.get("Sub-Script") or {}
    scene_total = len(ss_dict)
    scene_success_count = 0
    scenes = task.get("scenes") or {}
    for ss_name in ss_dict:
        scene_data = scenes.get(ss_name)
        try:
            if scene_data and isinstance(scene_data.get("Scene"), dict):
                scene_success_count += 1
            else:
                errors.append({
                    "location": f"scene/{ss_name}",
                    "issue": "missing or non-dict Scene field",
                })
        except (KeyError, TypeError) as e:
            errors.append({"location": f"scene/{ss_name}", "issue": str(e)})

    scene_success_rate = round(scene_success_count / scene_total, 3) if scene_total else 0.0

    # Shot
    shots = task.get("shots") or {}
    shot_total = 0
    shot_success_count = 0
    for ss_name, scenes_dict in shots.items():
        for scene_name, scene_shot_data in (scenes_dict or {}).items():
            shot_dict = (scene_shot_data or {}).get("Shot") or {}
            for shot_name, shot_data in shot_dict.items():
                shot_total += 1
                try:
                    if shot_data and shot_data.get("Plot/Visual Description"):
                        shot_success_count += 1
                    else:
                        errors.append({
                            "location": f"shot/{ss_name}/{scene_name}/{shot_name}",
                            "issue": "missing Plot/Visual Description",
                        })
                except (KeyError, TypeError) as e:
                    errors.append({
                        "location": f"shot/{ss_name}/{scene_name}/{shot_name}",
                        "issue": str(e),
                    })

    shot_success_rate = round(shot_success_count / shot_total, 3) if shot_total else 0.0

    return {
        "director_success": director_success,
        "scene_success_rate": scene_success_rate,
        "shot_success_rate": shot_success_rate,
        "json_parse_errors": errors,
        "overall_completion": task.get("status") == "done",
    }


# ── 1.2 端到端延迟 ────────────────────────────────────────────────────────────

def eval_latency(task_id: str, tasks: dict) -> dict:
    """
    返回：
    {
        "planning_total_seconds": float,
        "director_seconds": float,
        "scene_seconds": float,
        "shot_seconds": float,
        "shots_count": int,
        "seconds_per_shot": float
    }
    """
    task = tasks.get(task_id) or {}
    timing = task.get("timing") or {}

    def _elapsed(start_key: str, end_key: str):
        s = timing.get(start_key)
        e = timing.get(end_key)
        if s is not None and e is not None:
            return round(e - s, 2)
        return None

    director_seconds = _elapsed("director_start", "director_end")
    scene_seconds = _elapsed("scene_start", "scene_end")
    shot_seconds = _elapsed("shot_start", "shot_end")

    valid_parts = [t for t in [director_seconds, scene_seconds, shot_seconds] if t is not None]
    planning_total = round(sum(valid_parts), 2) if valid_parts else None

    shots_count = sum(
        len((sd or {}).get("Shot") or {})
        for scenes_dict in (task.get("shots") or {}).values()
        for sd in (scenes_dict or {}).values()
    )

    seconds_per_shot = (
        round(planning_total / shots_count, 2)
        if planning_total is not None and shots_count > 0
        else None
    )

    return {
        "planning_total_seconds": planning_total,
        "director_seconds": director_seconds,
        "scene_seconds": scene_seconds,
        "shot_seconds": shot_seconds,
        "shots_count": shots_count,
        "seconds_per_shot": seconds_per_shot,
    }


# ── 1.3 Token 消耗统计 ────────────────────────────────────────────────────────

# claude-sonnet-4-6 pricing (USD per million tokens)
_INPUT_PRICE_PER_M = 3.0
_OUTPUT_PRICE_PER_M = 15.0


def eval_token_cost(task_id: str, tasks: dict) -> dict:
    """
    返回：
    {
        "input_tokens": int,
        "output_tokens": int,
        "total_tokens": int,
        "estimated_cost_usd": float,
        "cost_per_shot": float
    }
    """
    task = tasks.get(task_id) or {}
    cost = task.get("cost") or {}

    input_tokens = cost.get("input_tokens", 0)
    output_tokens = cost.get("output_tokens", 0)
    total_tokens = input_tokens + output_tokens

    estimated_cost_usd = round(
        (input_tokens / 1_000_000) * _INPUT_PRICE_PER_M
        + (output_tokens / 1_000_000) * _OUTPUT_PRICE_PER_M,
        4,
    )

    shots_count = sum(
        len((sd or {}).get("Shot") or {})
        for scenes_dict in (task.get("shots") or {}).values()
        for sd in (scenes_dict or {}).values()
    )

    cost_per_shot = round(estimated_cost_usd / shots_count, 4) if shots_count else None

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "estimated_cost_usd": estimated_cost_usd,
        "cost_per_shot": cost_per_shot,
    }


# ── 1.4 Bounding Box 合法率 ───────────────────────────────────────────────────

def eval_bbox_validity(task_id: str, tasks: dict) -> dict:
    """
    返回：
    {
        "total_shots": int,
        "shots_with_bbox": int,
        "valid_bbox_count": int,
        "invalid_bbox_count": int,
        "validity_rate": float,
        "invalid_details": list
    }
    """
    task = tasks.get(task_id) or {}
    shots = task.get("shots") or {}

    total_shots = 0
    shots_with_bbox = 0
    valid_count = 0
    invalid_count = 0
    invalid_details: list[dict] = []

    for ss_name, scenes_dict in shots.items():
        for scene_name, scene_data in (scenes_dict or {}).items():
            for shot_name, shot_data in (scene_data or {}).get("Shot", {}).items():
                total_shots += 1
                involving = (shot_data or {}).get("Involving Characters") or {}
                if not isinstance(involving, dict) or not involving:
                    continue

                shots_with_bbox += 1
                issues = _validate_boxes(involving)
                if issues:
                    invalid_count += 1
                    invalid_details.append({
                        "shot_name": f"{ss_name}/{scene_name}/{shot_name}",
                        "issues": issues,
                    })
                else:
                    valid_count += 1

    validity_rate = round(valid_count / shots_with_bbox, 3) if shots_with_bbox else None

    return {
        "total_shots": total_shots,
        "shots_with_bbox": shots_with_bbox,
        "valid_bbox_count": valid_count,
        "invalid_bbox_count": invalid_count,
        "validity_rate": validity_rate,
        "invalid_details": invalid_details,
    }


def _validate_boxes(involving: dict) -> list[str]:
    issues: list[str] = []
    valid_boxes: list[tuple[str, list]] = []

    for char, box in involving.items():
        if not isinstance(box, list) or len(box) != 4:
            issues.append(f"{char}: 格式错误，应为 [x1,y1,x2,y2]，实际为 {box}")
            continue
        x1, y1, x2, y2 = box
        if not all(isinstance(v, (int, float)) for v in box):
            issues.append(f"{char}: 坐标含非数字值")
            continue
        if not all(0.0 <= v <= 1.0 for v in box):
            issues.append(f"{char}: 坐标超出 [0,1] 范围 {box}")
        if x1 >= x2 or y1 >= y2:
            issues.append(f"{char}: 退化 box（x1≥x2 或 y1≥y2）{box}")
        else:
            valid_boxes.append((char, box))

    # Check pairwise overlaps among valid boxes
    for i in range(len(valid_boxes)):
        for j in range(i + 1, len(valid_boxes)):
            char_a, box_a = valid_boxes[i]
            char_b, box_b = valid_boxes[j]
            if _overlap(box_a, box_b):
                issues.append(f"{char_a} ↔ {char_b}: bounding box 相互重叠")

    return issues


def _overlap(a: list, b: list) -> bool:
    x1a, y1a, x2a, y2a = a
    x1b, y1b, x2b, y2b = b
    return x1a < x2b and x2a > x1b and y1a < y2b and y2a > y1b

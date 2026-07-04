"""
A/B 实验 resume：跳过规划，直接用已有 task_id 跑关键帧 + 盲评 + 报告。
"""
from __future__ import annotations

import base64
import json
import os
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

os.environ.setdefault("GENERATION_MODE", "draft")
os.environ.setdefault("KEYFRAME_MAX_CONCURRENCY", "3")

import config
from generation.image import generate_keyframe
from generation.shot_pipeline import build_shot_id
from openai import OpenAI
from pipeline import save_tasks, tasks

TASK_A = "7d610c9f-b1f1-4f11-a7ed-cb2a47a19d14"
TASK_B = "1dc99419-c0fc-4bbb-b0cb-dfe4caf6a5d6"

CHARACTER_REFS = {
    "团团": "F:/movieagents_demo/movieagent-demo/examples/yueguangyouju/tuantuan.jpg",
    "圆圆": "F:/movieagents_demo/movieagent-demo/examples/yueguangyouju/yuanyuan.jpg",
    "墨墨": "F:/movieagents_demo/movieagent-demo/examples/yueguangyouju/momo.jpg",
}

_BLIND_EVAL_PROMPT = """\
你是一位动画短片评审专家。以下是一组动画关键帧，请按顺序观察后独立回答以下五个问题。

请对每个问题只回答 Yes 或 No，然后用不超过 15 个字说明原因。

Q1: 这组关键帧的场景是否有明显的变化和推进？
Q2: 镜头构图是否多样（景别、角度有变化）？
Q3: 是否能从画面清晰看出叙事进展？
Q4: 角色形象在不同关键帧中是否保持一致？
Q5: 整体画面的信息丰富度是否充足？

请以 JSON 格式输出（键名固定为 q1~q5）：
{
  "q1": {"answer": "Yes/No", "reason": "..."},
  "q2": {"answer": "Yes/No", "reason": "..."},
  "q3": {"answer": "Yes/No", "reason": "..."},
  "q4": {"answer": "Yes/No", "reason": "..."},
  "q5": {"answer": "Yes/No", "reason": "..."},
  "overall_score": 1~5
}"""


def log(msg: str) -> None:
    print(msg, flush=True)


def refs_for_shot(task_id: str, involving) -> dict[str, str]:
    refs = tasks[task_id].get("character_refs") or {}
    if isinstance(involving, dict):
        names = list(involving.keys())
    elif isinstance(involving, list):
        names = involving
    else:
        return {}
    return {n: refs[n] for n in names if n in refs}


def all_shots(task_id: str) -> list[dict]:
    result = []
    for ss_name, scenes in (tasks[task_id].get("shots") or {}).items():
        for scene_name, scene_data in (scenes or {}).items():
            for shot_name, shot_data in ((scene_data or {}).get("Shot") or {}).items():
                result.append({
                    "sub_script_name": ss_name,
                    "scene_name": scene_name,
                    "shot_name": shot_name,
                    "shot_data": shot_data,
                })
    return result


def generate_keyframes(task_id: str, shots: list[dict], label: str) -> tuple[int, float]:
    t0 = time.time()
    jobs = []
    for index, item in enumerate(shots, start=1):
        shot_data = item["shot_data"]
        shot_id = build_shot_id(
            task_id,
            item["sub_script_name"],
            item["scene_name"],
            item["shot_name"],
        )
        existing = shot_data.get("keyframe_local_path")
        if existing and Path(existing).is_file():
            log(f"[{label}][{index}/{len(shots)}] cache: {shot_id}")
            continue
        jobs.append((index, item, shot_id))

    if not jobs:
        log(f"[{label}] 全部命中缓存")
        return 0, time.time() - t0

    max_workers = max(1, min(int(config.KEYFRAME_MAX_CONCURRENCY or 1), len(jobs)))
    log(f"[{label}] 生成 {len(jobs)} 张关键帧，workers={max_workers}...")

    def run_job(job):
        index, item, shot_id = job
        shot_data = item["shot_data"]
        plot = shot_data.get("Plot/Visual Description", "")
        refs = refs_for_shot(task_id, shot_data.get("Involving Characters"))
        try:
            result = generate_keyframe(plot, shot_id, refs)
            shot_data["keyframe_local_path"] = result["local_path"]
            shot_data["keyframe_url"] = f"/outputs/keyframes/{shot_id}.png"
            shot_data["keyframe_status"] = "done"
            save_tasks()
            return index, shot_id, result["elapsed_seconds"], None
        except Exception as exc:
            shot_data["keyframe_status"] = "error"
            save_tasks()
            return index, shot_id, 0.0, exc

    generated = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {executor.submit(run_job, job): job for job in jobs}
        for future in as_completed(future_map):
            index, shot_id, elapsed_s, error = future.result()
            if error:
                log(f"[{label}][{index}/{len(shots)}] FAILED: {shot_id}: {error}")
            else:
                generated += 1
                log(f"[{label}][{index}/{len(shots)}] done {elapsed_s:.1f}s: {shot_id}")

    total = time.time() - t0
    log(f"[{label}] {generated} 张完成，耗时 {total:.1f}s")
    return generated, total


def collect_keyframe_paths(task_id: str) -> list[str]:
    paths = []
    for scenes in (tasks[task_id].get("shots") or {}).values():
        for scene_data in (scenes or {}).values():
            for shot_data in (scene_data or {}).get("Shot", {}).values():
                kf = (shot_data or {}).get("keyframe_local_path")
                if kf and os.path.isfile(kf):
                    paths.append(kf)
    return paths


def _parse_eval(text: str) -> dict:
    import re
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except Exception:
            pass
    return {}


def blind_eval(keyframe_paths: list[str], sample_label: str, client: OpenAI, max_frames: int = 3) -> dict:
    valid = [p for p in keyframe_paths if os.path.isfile(p)]
    if not valid:
        return {"error": "no valid keyframes", "overall_score": None}

    if len(valid) > max_frames:
        step = len(valid) / max_frames
        valid = [valid[int(i * step)] for i in range(max_frames)]

    log(f"\n[盲评] 评估 {sample_label}（{len(valid)} 帧）...")

    from PIL import Image
    import io

    content: list[dict] = [{"type": "text", "text": "以下是待评估的关键帧序列："}]
    for i, path in enumerate(valid):
        # 缩小到 512×288 再编码，避免超 token 限制
        img = Image.open(path).convert("RGB")
        img.thumbnail((512, 288), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        data = base64.standard_b64encode(buf.getvalue()).decode("utf-8")
        content.append({"type": "text", "text": f"[帧 {i+1}]"})
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{data}"}})
    content.append({"type": "text", "text": _BLIND_EVAL_PROMPT})

    try:
        resp = client.chat.completions.create(
            model=config.LLM_MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": content}],
        )
        raw = resp.choices[0].message.content
        log(f"[盲评] {sample_label} 原始响应:\n{raw[:500]}")
        result = _parse_eval(raw)
        result["frames_evaluated"] = len(valid)
        return result
    except Exception as e:
        log(f"[盲评] {sample_label} 调用失败: {e}")
        return {"error": str(e), "overall_score": None, "frames_evaluated": len(valid)}


def yn(eval_result: dict, key: str) -> str:
    q = eval_result.get(key) or {}
    ans = q.get("answer", "?")
    reason = q.get("reason", "")
    return f"{ans}（{reason}）"


def print_report(a: dict, b: dict, eval_a: dict, eval_b: dict) -> None:
    print("\n" + "=" * 70)
    print("A/B 实验对比报告")
    print("=" * 70)
    print(f"{'指标':<22} {'A 组（原始）':<24} {'B 组（改写）'}")
    print("-" * 70)
    print(f"{'规划镜头数':<22} {a['shot_count']:<24} {b['shot_count']}")
    print(f"{'规划 input tokens':<22} {a['input_tokens']:<24} {b['input_tokens']}")
    print(f"{'规划 output tokens':<22} {a['output_tokens']:<24} {b['output_tokens']}")
    cost_a = round(a['input_tokens']/1e6*3 + a['output_tokens']/1e6*15, 4)
    cost_b = round(b['input_tokens']/1e6*3 + b['output_tokens']/1e6*15, 4)
    print(f"{'规划成本估算':<22} ${cost_a:<23} ${cost_b}")
    print(f"{'关键帧生成数':<22} {a['kf_count']:<24} {b['kf_count']}")
    print(f"{'关键帧生成耗时':<22} {a['kf_elapsed']:.1f}s{'':<20} {b['kf_elapsed']:.1f}s")
    print("-" * 70)
    print(f"{'评测帧数':<22} {eval_a.get('frames_evaluated','?'):<24} {eval_b.get('frames_evaluated','?')}")
    print(f"{'Q1 场景推进':<22} {yn(eval_a,'q1'):<24} {yn(eval_b,'q1')}")
    print(f"{'Q2 构图多样':<22} {yn(eval_a,'q2'):<24} {yn(eval_b,'q2')}")
    print(f"{'Q3 叙事清晰':<22} {yn(eval_a,'q3'):<24} {yn(eval_b,'q3')}")
    print(f"{'Q4 角色一致':<22} {yn(eval_a,'q4'):<24} {yn(eval_b,'q4')}")
    print(f"{'Q5 信息丰富度':<22} {yn(eval_a,'q5'):<24} {yn(eval_b,'q5')}")
    print(f"{'综合评分（1-5）':<22} {str(eval_a.get('overall_score','N/A')):<24} {str(eval_b.get('overall_score','N/A'))}")
    print("=" * 70)
    print("成本估算基于 claude-sonnet-4-6 官方定价（$3/M in, $15/M out），实际以一展账单为准。")


def main() -> None:
    # 确保角色 refs 写回 task
    for tid in (TASK_A, TASK_B):
        if tid not in tasks:
            raise RuntimeError(f"task_id {tid} 不在 tasks.json，请重跑完整实验")
        tasks[tid]["character_refs"] = dict(CHARACTER_REFS)
    save_tasks()

    vlm_client = OpenAI(
        api_key=config.YIZHAN_API_KEY,
        base_url=config.YIZHAN_BASE_URL.rstrip("/"),
    )

    # ── 生成关键帧 ────────────────────────────────────────────────────────────
    shots_a = all_shots(TASK_A)
    shots_b = all_shots(TASK_B)

    log(f"A 组镜头数: {len(shots_a)}，B 组镜头数: {len(shots_b)}")

    kf_count_a, kf_elapsed_a = generate_keyframes(TASK_A, shots_a, "A")
    kf_count_b, kf_elapsed_b = generate_keyframes(TASK_B, shots_b, "B")

    kf_paths_a = collect_keyframe_paths(TASK_A)
    kf_paths_b = collect_keyframe_paths(TASK_B)
    log(f"\nA 组有效关键帧: {len(kf_paths_a)} 张")
    log(f"B 组有效关键帧: {len(kf_paths_b)} 张")

    # ── 盲评（随机打乱标签顺序）──────────────────────────────────────────────
    log("\n[盲评] 开始 VLM 盲评...")
    order = [("样本一", "A", kf_paths_a), ("样本二", "B", kf_paths_b)]
    random.shuffle(order)

    eval_map: dict[str, dict] = {}
    for sample_label, group_key, paths in order:
        result = blind_eval(paths, sample_label, vlm_client)
        eval_map[group_key] = result
        log(f"[盲评] {sample_label}（实际={group_key}组）综合评分: {result.get('overall_score','?')}")

    # ── 报告 ──────────────────────────────────────────────────────────────────
    cost_a = tasks[TASK_A].get("cost") or {}
    cost_b = tasks[TASK_B].get("cost") or {}

    a_stats = {
        "shot_count": len(shots_a),
        "input_tokens": cost_a.get("input_tokens", 0),
        "output_tokens": cost_a.get("output_tokens", 0),
        "kf_count": kf_count_a,
        "kf_elapsed": kf_elapsed_a,
    }
    b_stats = {
        "shot_count": len(shots_b),
        "input_tokens": cost_b.get("input_tokens", 0),
        "output_tokens": cost_b.get("output_tokens", 0),
        "kf_count": kf_count_b,
        "kf_elapsed": kf_elapsed_b,
    }

    print_report(a_stats, b_stats, eval_map["A"], eval_map["B"])


if __name__ == "__main__":
    main()

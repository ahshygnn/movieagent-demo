"""
A/B 对照实验：剧本改写对关键帧生成质量的影响。

运行：
    python run_ab_experiment.py

流程：
    1. 两组各跑 Director → Scene → Shot 规划
    2. 两组各并行生成关键帧（不跑视频）
    3. 用 VLM 盲评打分（中性标签，不暴露分组）
    4. 揭晓分组，输出对比报告
"""
from __future__ import annotations

import os
import random
import sys
import time
from pathlib import Path

os.environ.setdefault("GENERATION_MODE", "draft")   # 用 draft 档位加快生图速度
os.environ.setdefault("SHOT_MAX_PER_SCENE", "0")
os.environ.setdefault("KEYFRAME_MAX_CONCURRENCY", "3")

from openai import OpenAI
import config
from generation.image import generate_keyframe
from generation.shot_pipeline import build_shot_id
from pipeline import create_task, run_full_pipeline, save_tasks, tasks

# ── 角色配置（两组共用，排除角色变量干扰）────────────────────────────────────
CHARACTERS = ["团团", "圆圆", "墨墨"]
CHARACTER_REFS = {
    "团团": "F:/movieagents_demo/movieagent-demo/examples/yueguangyouju/tuantuan.jpg",
    "圆圆": "F:/movieagents_demo/movieagent-demo/examples/yueguangyouju/yuanyuan.jpg",
    "墨墨": "F:/movieagents_demo/movieagent-demo/examples/yueguangyouju/momo.jpg",
}

# ── A 组输入：原始逐句分镜 ───────────────────────────────────────────────────
GROUP_A_SYNOPSIS = (
    "月光邮局的夜晚很安静,团团小心地打开一封会发光的信。"
    "信纸上只有一句话:星星花园正在慢慢熄灭。"
    "圆圆低头一看,一颗蓝色的星光碎片从信封里掉了出来。"
    "蓝色碎片在桌上闪烁,像是在催促大家赶快出发。"
    "团团和圆圆抬头望向彼此,心里都明白这不是一封普通的信。"
    "墨墨翻开旧地图,寻找星光碎片的来处。"
    "地图上的云朵山亮了起来,答案终于出现了。"
    "三个伙伴围在地图旁,决定去云朵山寻找真相。"
    "邮局外的天空中,一颗星星突然暗了下来,冒险也正式开始。"
)

# ── B 组输入：改写后段落 ─────────────────────────────────────────────────────
GROUP_B_SYNOPSIS = (
    "月光邮局的深夜静得只听见风声,团团轻手轻脚地取出那封信——信封边缘隐隐透着光,"
    "像是藏着什么不愿被黑暗压住的东西。他屏住呼吸,小心翼翼地拆开封口,展开信纸,"
    "整个邮局随即被一片柔和的光晕笼罩。然而信纸上只有一句话,寥寥数字,却沉甸甸的:"
    "星星花园正在慢慢熄灭。话音未落,守在一旁的圆圆忽然低头,只见一颗蓝色的星光碎片"
    "从信封深处悄悄滑落,跌在木桌上,发出细碎的、急促的闪烁,像一颗微弱的心跳,"
    "又像是某种无声的催促。团团和圆圆对视一眼,谁也没有说话,但彼此眼神里都读出了"
    "同一个念头——这绝不是一封寻常的信。一直沉默翻找着的墨墨这时抬起头,将一张泛黄的"
    "旧地图铺展在桌上,修长的手指沿着山脉轮廓缓缓移动。就在蓝色碎片的光芒触碰到地图"
    "的瞬间,图上的云朵山轮廓骤然亮起,像是久久等待的答案终于找到了开口。三个伙伴围拢"
    "在地图旁,沉默片刻后,一同点了点头——去云朵山,找到真相。就在他们收拾好出发的当口,"
    "邮局外的夜空中,一颗星星毫无预兆地暗了下去,原本密密的星河缺出一个小小的空洞。"
    "冒险,就从这一刻正式开始了。"
)

# ── VLM 盲评 prompt（不含任何分组暗示）──────────────────────────────────────
_BLIND_EVAL_PROMPT = """\
你是一位动画短片评审专家。以下是一组动画关键帧，请按顺序观察后独立回答以下五个问题。

请对每个问题只回答 Yes 或 No，然后用不超过 15 个字说明原因。

Q1: 这组关键帧的场景是否有明显的变化和推进？
Q2: 镜头构图是否多样（景别、角度有变化）？
Q3: 是否能从画面清晰看出叙事进展？
Q4: 角色形象在不同关键帧中是否保持一致？
Q5: 整体画面的信息丰富度是否充足？

请以 JSON 格式输出（键名固定为 q1~q5，值为含 answer 和 reason 的对象）：
{
  "q1": {"answer": "Yes/No", "reason": "..."},
  "q2": {"answer": "Yes/No", "reason": "..."},
  "q3": {"answer": "Yes/No", "reason": "..."},
  "q4": {"answer": "Yes/No", "reason": "..."},
  "q5": {"answer": "Yes/No", "reason": "..."},
  "overall_score": 1~5
}
overall_score 综合五题作答给出 1（很差）到 5（很好）的整体评分。"""


def log(msg: str) -> None:
    print(msg, flush=True)


def validate_refs() -> None:
    missing = [f"{n}: {p}" for n, p in CHARACTER_REFS.items() if not Path(p).is_file()]
    if missing:
        raise FileNotFoundError("角色参考图缺失:\n" + "\n".join(missing))


def setup_task(synopsis: str) -> str:
    task_id = create_task()
    tasks[task_id]["character_refs"] = dict(CHARACTER_REFS)
    tasks[task_id]["voice_refs"] = {}
    save_tasks()
    return task_id


def run_planning(task_id: str, synopsis: str, label: str) -> float:
    log(f"\n{'='*60}")
    log(f"[{label}] 开始规划: Director → Scene → Shot")
    t0 = time.time()
    run_full_pipeline(task_id, synopsis, CHARACTERS)
    elapsed = time.time() - t0
    if tasks[task_id].get("status") == "error":
        raise RuntimeError(f"[{label}] 规划失败，查看 tasks.json 日志")
    tasks[task_id]["character_refs"] = dict(CHARACTER_REFS)
    save_tasks()
    log(f"[{label}] 规划完成，耗时 {elapsed:.1f}s")
    return elapsed


def count_shots(task_id: str) -> int:
    total = 0
    for scenes in (tasks[task_id].get("shots") or {}).values():
        for scene_data in (scenes or {}).values():
            total += len((scene_data or {}).get("Shot") or {})
    return total


def get_planning_tokens(task_id: str) -> dict:
    cost = tasks[task_id].get("cost") or {}
    return {
        "input_tokens": cost.get("input_tokens", 0),
        "output_tokens": cost.get("output_tokens", 0),
    }


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


def refs_for_shot(task_id: str, involving) -> dict[str, str]:
    refs = tasks[task_id].get("character_refs") or {}
    if isinstance(involving, dict):
        names = list(involving.keys())
    elif isinstance(involving, list):
        names = involving
    else:
        return {}
    return {n: refs[n] for n in names if n in refs}


def generate_keyframes(task_id: str, shots: list[dict], label: str) -> tuple[int, float]:
    from concurrent.futures import ThreadPoolExecutor, as_completed

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
            log(f"[{label}][{index}/{len(shots)}] keyframe cache: {shot_id}")
            continue
        jobs.append((index, item, shot_id))

    if not jobs:
        elapsed = time.time() - t0
        log(f"[{label}] 全部命中缓存，耗时 {elapsed:.1f}s")
        return 0, elapsed

    max_workers = max(1, min(int(config.KEYFRAME_MAX_CONCURRENCY or 1), len(jobs)))
    log(f"[{label}] 并行生成 {len(jobs)} 张关键帧，workers={max_workers}...")

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
                log(f"[{label}][{index}/{len(shots)}] keyframe FAILED: {shot_id}: {error}")
            else:
                generated += 1
                log(f"[{label}][{index}/{len(shots)}] keyframe done {elapsed_s:.1f}s: {shot_id}")

    total_elapsed = time.time() - t0
    log(f"[{label}] {generated} 张并行生成完成，耗时 {total_elapsed:.1f}s")
    return generated, total_elapsed


def collect_keyframe_paths(task_id: str) -> list[str]:
    paths = []
    for scenes in (tasks[task_id].get("shots") or {}).values():
        for scene_data in (scenes or {}).values():
            for shot_data in (scene_data or {}).get("Shot", {}).values():
                kf = (shot_data or {}).get("keyframe_local_path")
                if kf and os.path.isfile(kf):
                    paths.append(kf)
    return paths


def _parse_blind_eval(text: str) -> dict:
    import json, re
    text = text.strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except Exception:
            pass
    return {}


def blind_eval(
    keyframe_paths: list[str],
    sample_label: str,
    vlm_client: OpenAI,
    max_frames: int = 8,
) -> dict:
    """对一组关键帧做盲评，sample_label 为中性标签（样本一/样本二）。"""
    import base64

    valid = [p for p in keyframe_paths if os.path.isfile(p)]
    if not valid:
        return {"error": "no valid keyframes", "overall_score": None}

    # 均匀采样最多 max_frames 帧
    if len(valid) > max_frames:
        step = len(valid) / max_frames
        valid = [valid[int(i * step)] for i in range(max_frames)]

    log(f"\n[盲评] 正在评估 {sample_label}（{len(valid)} 帧）...")

    content: list[dict] = [{"type": "text", "text": "以下是待评估的关键帧序列："}]
    for i, path in enumerate(valid):
        with open(path, "rb") as f:
            data = base64.standard_b64encode(f.read()).decode("utf-8")
        ext = path.rsplit(".", 1)[-1].lower() if "." in path else "png"
        mime = f"image/{ext}" if ext in ("png", "jpg", "jpeg", "webp") else "image/png"
        content.append({"type": "text", "text": f"[帧 {i+1}]"})
        content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}})

    content.append({"type": "text", "text": _BLIND_EVAL_PROMPT})

    try:
        response = vlm_client.chat.completions.create(
            model=config.LLM_MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": content}],
        )
        raw = response.choices[0].message.content
        result = _parse_blind_eval(raw)
        result["_raw"] = raw
        result["frames_evaluated"] = len(valid)
        return result
    except Exception as e:
        return {"error": str(e), "overall_score": None, "frames_evaluated": len(valid)}


def estimate_cost_usd(input_tokens: int, output_tokens: int) -> float:
    # claude-sonnet-4-6 via 一展：参考官方定价估算（$3/M in, $15/M out）
    return round(input_tokens / 1_000_000 * 3.0 + output_tokens / 1_000_000 * 15.0, 4)


def print_report(a: dict, b: dict, eval_a: dict, eval_b: dict) -> None:
    def yn(eval_result: dict, key: str) -> str:
        q = eval_result.get(key) or {}
        ans = q.get("answer", "?")
        reason = q.get("reason", "")
        return f"{ans}（{reason}）"

    def score(eval_result: dict) -> str:
        s = eval_result.get("overall_score")
        return str(s) if s is not None else "N/A"

    print("\n" + "="*70)
    print("A/B 实验结果报告")
    print("="*70)
    print(f"{'指标':<22} {'A 组（原始）':<22} {'B 组（改写）'}")
    print("-"*70)
    print(f"{'规划镜头数':<22} {a['shot_count']:<22} {b['shot_count']}")
    print(f"{'LLM 规划耗时':<22} {a['planning_elapsed']:.1f}s{'':<16} {b['planning_elapsed']:.1f}s")
    print(f"{'规划 input tokens':<22} {a['tokens']['input_tokens']:<22} {b['tokens']['input_tokens']}")
    print(f"{'规划 output tokens':<22} {a['tokens']['output_tokens']:<22} {b['tokens']['output_tokens']}")
    print(f"{'规划阶段成本估算':<22} ${a['cost_usd']:<21} ${b['cost_usd']}")
    print(f"{'关键帧数量':<22} {a['kf_count']:<22} {b['kf_count']}")
    print(f"{'关键帧生成耗时':<22} {a['kf_elapsed']:.1f}s{'':<16} {b['kf_elapsed']:.1f}s")
    print("-"*70)
    print(f"{'评测帧数':<22} {eval_a.get('frames_evaluated','?'):<22} {eval_b.get('frames_evaluated','?')}")
    print(f"{'Q1 场景推进':<22} {yn(eval_a,'q1'):<22} {yn(eval_b,'q1')}")
    print(f"{'Q2 构图多样':<22} {yn(eval_a,'q2'):<22} {yn(eval_b,'q2')}")
    print(f"{'Q3 叙事清晰':<22} {yn(eval_a,'q3'):<22} {yn(eval_b,'q3')}")
    print(f"{'Q4 角色一致':<22} {yn(eval_a,'q4'):<22} {yn(eval_b,'q4')}")
    print(f"{'Q5 信息丰富度':<22} {yn(eval_a,'q5'):<22} {yn(eval_b,'q5')}")
    print(f"{'综合评分（1-5）':<22} {score(eval_a):<22} {score(eval_b)}")
    print("="*70)
    print("注：成本为 claude-sonnet-4-6 官方定价估算（$3/M in, $15/M out），实际以一展账单为准。")


def main() -> None:
    validate_refs()

    vlm_client = OpenAI(
        api_key=config.YIZHAN_API_KEY,
        base_url=config.YIZHAN_BASE_URL.rstrip("/"),
    )

    # ── Step 1: 规划 ──────────────────────────────────────────────────────────
    log("\n[Step 1] 创建任务并运行 LLM 规划（A 组：原始 → B 组：改写）")

    task_a = setup_task(GROUP_A_SYNOPSIS)
    task_b = setup_task(GROUP_B_SYNOPSIS)
    log(f"A 组 task_id: {task_a}")
    log(f"B 组 task_id: {task_b}")

    planning_elapsed_a = run_planning(task_a, GROUP_A_SYNOPSIS, "A")
    planning_elapsed_b = run_planning(task_b, GROUP_B_SYNOPSIS, "B")

    shot_count_a = count_shots(task_a)
    shot_count_b = count_shots(task_b)
    tokens_a = get_planning_tokens(task_a)
    tokens_b = get_planning_tokens(task_b)

    log(f"\n[规划汇总] A 组镜头数={shot_count_a}, B 组镜头数={shot_count_b}")

    # ── Step 2: 生成关键帧 ────────────────────────────────────────────────────
    log("\n[Step 2] 并行生成关键帧（不跑视频）")

    shots_a = all_shots(task_a)
    shots_b = all_shots(task_b)

    kf_count_a, kf_elapsed_a = generate_keyframes(task_a, shots_a, "A")
    kf_count_b, kf_elapsed_b = generate_keyframes(task_b, shots_b, "B")

    kf_paths_a = collect_keyframe_paths(task_a)
    kf_paths_b = collect_keyframe_paths(task_b)

    # ── Step 3: 盲评（随机打乱标签顺序）────────────────────────────────────────
    log("\n[Step 3] VLM 盲评（中性标签，不暴露分组）")

    # 随机决定哪组先评，避免顺序偏差
    order = [("样本一", "A", kf_paths_a), ("样本二", "B", kf_paths_b)]
    random.shuffle(order)

    eval_map: dict[str, dict] = {}
    for sample_label, group_key, paths in order:
        result = blind_eval(paths, sample_label, vlm_client)
        eval_map[group_key] = result
        log(f"[盲评] {sample_label}（实际={group_key}组）综合评分: {result.get('overall_score','?')}")

    # ── Step 4: 揭晓 + 报告 ───────────────────────────────────────────────────
    log("\n[Step 4] 揭晓分组，输出对比报告")

    a_stats = {
        "shot_count": shot_count_a,
        "planning_elapsed": planning_elapsed_a,
        "tokens": tokens_a,
        "cost_usd": estimate_cost_usd(tokens_a["input_tokens"], tokens_a["output_tokens"]),
        "kf_count": kf_count_a,
        "kf_elapsed": kf_elapsed_a,
    }
    b_stats = {
        "shot_count": shot_count_b,
        "planning_elapsed": planning_elapsed_b,
        "tokens": tokens_b,
        "cost_usd": estimate_cost_usd(tokens_b["input_tokens"], tokens_b["output_tokens"]),
        "kf_count": kf_count_b,
        "kf_elapsed": kf_elapsed_b,
    }

    print_report(a_stats, b_stats, eval_map["A"], eval_map["B"])

    log(f"\nA 组 task_id（原始）: {task_a}")
    log(f"B 组 task_id（改写）: {task_b}")
    log("关键帧路径:")
    log(f"  A 组: {kf_paths_a}")
    log(f"  B 组: {kf_paths_b}")


if __name__ == "__main__":
    main()

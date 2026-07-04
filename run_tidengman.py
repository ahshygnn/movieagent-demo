"""《提灯人》完整正片生成脚本。
Director → Scene → Shot 规划 → 关键帧 → 视频+配音 → 拼接成片。

用法：
    python run_tidengman.py            # 新建任务
    python run_tidengman.py <task_id>  # 从已有任务 resume
"""
from __future__ import annotations

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# ── 运行参数（必须在 import config 前设置）──────────────────────────────────
os.environ["GENERATION_MODE"]        = "final"
os.environ["SHOT_MAX_PER_SCENE"]     = "2"      # 每场景最多 2 镜，压控总镜头数
os.environ["VIDEO_DURATION_SECONDS"] = "5"
os.environ["VIDEO_RESOLUTION"]       = "720p"
os.environ["VIDEO_MAX_CONCURRENCY"]  = "2"
os.environ["KEYFRAME_MAX_CONCURRENCY"] = "3"
os.environ.setdefault("YIZHAN_TTS_MODEL",        "qwen3-tts-flash")
os.environ.setdefault("YIZHAN_TTS_DEFAULT_VOICE", "Cherry")

import config
from generation.concat import concat_videos
from generation.image import generate_keyframe
from generation.postprocess import collect_dialogue_lines
from generation.shot_pipeline import build_shot_id, generate_shot_video_artifacts, shot_video_complete
from pipeline import create_task, run_full_pipeline, save_tasks, tasks

# ── 项目配置 ─────────────────────────────────────────────────────────────────
TITLE      = "提灯人"
MAX_VIDEO_SHOTS = 10   # 硬上限，10 镜 × 5s ≈ 50s 成片

SYNOPSIS = (
    "在一座终年被浓雾笼罩的山谷里,住着一个叫小满的提灯女孩,"
    "她的工作是每天黄昏点亮山道上的百盏灯,为夜行的旅人指路。"
    "这天傍晚,一只浑身泛着微光、却奄奄一息的小鹿闯进了她的木屋,"
    "鹿角上的光正在一点点黯淡下去。"
    "小满从村里的老人那里听说过,这是传说中的引路鹿,"
    "它的光一旦熄灭,整座山谷将永远陷入黑暗。"
    "为了救它,小满决定带着自己的灯,去寻找传说中位于山顶的初光之泉。"
    "一路上浓雾越来越浓,她的灯几次险些被山风吹灭,"
    "而黑暗中似乎有什么东西在窥视着她。"
    "当她终于爬上山顶,却发现初光之泉早已干涸,只剩下一颗微弱跳动的光种。"
    "就在小满绝望的时候,她忽然明白了老人话里的深意——"
    "光不在泉里,而在提灯人的手中。"
    "她轻轻把自己灯里的光,分了一半给怀中的小鹿。"
    "刹那间,小鹿的鹿角重新亮起,而那颗光种也随之苏醒,"
    "整座山谷第一次被温暖的光芒照亮。"
    "小满笑了,她终于懂得,真正的光,是愿意分给别人的那一半。"
)

CHARACTERS = ["小满", "小鹿", "山谷老人"]

CHARACTER_REFS = {
    "小满":    "F:/movieagents_demo/movieagent-demo/outputs/characters/xiaoman.png",
    "小鹿":    "F:/movieagents_demo/movieagent-demo/outputs/characters/xiaolu.png",
    "山谷老人": "F:/movieagents_demo/movieagent-demo/outputs/characters/laoren.png",
}

VOICE_REFS = {name: config.YIZHAN_TTS_DEFAULT_VOICE for name in CHARACTERS}

NARRATION_LINES = [
    "在终年被浓雾笼罩的山谷里,小满每天黄昏点亮山道上的百盏灯。",
    "这天傍晚,一只奄奄一息的小鹿闯进了她的木屋,鹿角上的光在慢慢熄灭。",
    "村里的老人说,这是传说中的引路鹿,它的光若熄灭,山谷将永堕黑暗。",
    "小满提起灯笼,决定去寻找山顶的初光之泉。",
    "浓雾越来越浓,山风几次险些吹灭她手中的灯。",
    "终于登上山顶,她却发现初光之泉早已干涸。",
    "就在绝望之际,她忽然明白了老人话里的深意。",
    "光不在泉里,而在提灯人的手中。",
    "她把灯里的光分了一半给小鹿,鹿角重新亮起,山谷被温暖的光芒照亮。",
    "小满笑了——真正的光,是愿意分给别人的那一半。",
]


def log(msg: str) -> None:
    print(msg, flush=True)


def validate_inputs() -> None:
    missing = [f"{n}: {p}" for n, p in CHARACTER_REFS.items() if not Path(p).is_file()]
    if missing:
        raise FileNotFoundError("角色参考图缺失:\n" + "\n".join(missing))


def ensure_task(task_id: str | None) -> str:
    if task_id and task_id in tasks:
        tasks[task_id].setdefault("character_refs", dict(CHARACTER_REFS))
        tasks[task_id].setdefault("voice_refs",     dict(VOICE_REFS))
        save_tasks()
        return task_id

    if task_id:
        tasks[task_id] = {
            "status": "pending", "progress": 0, "logs": [],
            "sub_scripts": None, "scenes": {}, "shots": {},
            "character_refs": {}, "voice_refs": {},
            "cost": {"input_tokens": 0, "output_tokens": 0},
        }
        new_id = task_id
    else:
        new_id = create_task()

    tasks[new_id]["episode_title"]  = TITLE
    tasks[new_id]["character_refs"] = dict(CHARACTER_REFS)
    tasks[new_id]["voice_refs"]     = dict(VOICE_REFS)
    save_tasks()
    return new_id


def planning_complete(task_id: str) -> bool:
    t = tasks.get(task_id) or {}
    return bool(t.get("shots")) and t.get("status") == "done"


def run_planning(task_id: str) -> float:
    if planning_complete(task_id):
        log("规划已存在，跳过。")
        return 0.0

    tasks[task_id].update({"sub_scripts": None, "scenes": {}, "shots": {},
                           "status": "pending", "progress": 0})
    save_tasks()

    log(">> 开始规划：Director -> Scene -> Shot ...")
    t0 = time.time()
    run_full_pipeline(task_id, SYNOPSIS, CHARACTERS)
    elapsed = time.time() - t0

    if tasks[task_id].get("status") == "error":
        raise RuntimeError("规划失败，查看任务日志。")

    tasks[task_id]["character_refs"] = dict(CHARACTER_REFS)
    save_tasks()
    log(f"[OK] 规划完成，耗时 {elapsed:.1f}s")
    return elapsed


def all_shots(task_id: str) -> list[dict]:
    result = []
    for ss, scenes in (tasks[task_id].get("shots") or {}).items():
        for sc, scene_data in (scenes or {}).items():
            for sh, shot_data in ((scene_data or {}).get("Shot") or {}).items():
                result.append({"sub_script_name": ss, "scene_name": sc,
                               "shot_name": sh, "shot_data": shot_data})
    return result


def refs_for_shot(task_id: str, involving) -> dict[str, str]:
    refs = tasks[task_id].get("character_refs") or {}
    if isinstance(involving, dict):   names = list(involving.keys())
    elif isinstance(involving, list): names = involving
    else:                             return {}
    return {n: refs[n] for n in names if n in refs}


def ensure_narration(shots: list[dict]) -> int:
    added = 0
    for i, item in enumerate(shots):
        sd = item["shot_data"]
        if collect_dialogue_lines(sd.get("Dialogue")):
            continue
        fallback = (NARRATION_LINES[i] if i < len(NARRATION_LINES)
                    else str(sd.get("Plot/Visual Description") or "").strip())
        if fallback:
            sd["Dialogue"] = {"旁白": fallback}
            sd["video_has_dubbing"] = False
            added += 1
    return added


def generate_keyframes(task_id: str, shots: list[dict]) -> tuple[int, float]:
    t0 = time.time()
    jobs = []
    for idx, item in enumerate(shots, 1):
        sd   = item["shot_data"]
        sid  = build_shot_id(task_id, item["sub_script_name"], item["scene_name"], item["shot_name"])
        existing = sd.get("keyframe_local_path")
        if existing and Path(existing).is_file():
            log(f"  [{idx}/{len(shots)}] keyframe cache: {sid}")
            continue
        jobs.append((idx, item, sid))

    if not jobs:
        log("  全部关键帧命中缓存")
        return 0, time.time() - t0

    max_w = max(1, min(int(config.KEYFRAME_MAX_CONCURRENCY or 1), len(jobs)))
    log(f"  并行生成 {len(jobs)} 张关键帧，workers={max_w}...")

    def run_job(job):
        idx, item, sid = job
        sd   = item["shot_data"]
        plot = sd.get("Plot/Visual Description", "")
        refs = refs_for_shot(task_id, sd.get("Involving Characters"))
        try:
            r = generate_keyframe(plot, sid, refs)
            sd["keyframe_local_path"] = r["local_path"]
            sd["keyframe_url"]        = f"/outputs/keyframes/{sid}.png"
            sd["keyframe_status"]     = "done"
            save_tasks()
            return idx, sid, r["elapsed_seconds"], None
        except Exception as exc:
            sd["keyframe_status"] = "error"
            save_tasks()
            return idx, sid, 0.0, exc

    generated = 0
    with ThreadPoolExecutor(max_workers=max_w) as executor:
        fmap = {executor.submit(run_job, j): j for j in jobs}
        for future in as_completed(fmap):
            idx, sid, el, err = future.result()
            if err:
                log(f"  [{idx}/{len(shots)}] keyframe FAILED: {sid}: {err}")
            else:
                generated += 1
                log(f"  [{idx}/{len(shots)}] keyframe done {el:.1f}s: {sid}")

    elapsed = time.time() - t0
    log(f"  [OK] 关键帧完成：{generated} 张，耗时 {elapsed:.1f}s")
    return generated, elapsed


def generate_videos(task_id: str, shots: list[dict]) -> tuple[list[str], int, float]:
    t0 = time.time()
    jobs = []
    for idx, item in enumerate(shots, 1):
        sd  = item["shot_data"]
        sid = build_shot_id(task_id, item["sub_script_name"], item["scene_name"], item["shot_name"])
        kf  = sd.get("keyframe_local_path")
        if not kf or not Path(kf).is_file():
            sd["video_status"] = "skipped_missing_keyframe"
            log(f"  [{idx}/{len(shots)}] skip video (no keyframe): {sid}")
            continue
        jobs.append((idx, item, sid, kf))

    max_w = max(1, min(int(config.VIDEO_MAX_CONCURRENCY or 1), len(jobs) or 1))
    log(f"  并行生成 {len(jobs)} 个视频+配音，workers={max_w}...")

    def run_job(job):
        idx, item, sid, kf = job
        sd = item["shot_data"]
        if shot_video_complete(sd):
            return idx, item, 0.0, True, None
        sd["video_status"] = "running"
        save_tasks()
        t = time.time()
        try:
            art = generate_shot_video_artifacts(
                sid, sd, kf, tasks[task_id].get("voice_refs") or {}
            )
            sd.update(art["updates"])
            save_tasks()
            return idx, item, time.time() - t, False, None
        except Exception as exc:
            sd["video_status"] = "error"
            sd["video_error"]  = str(exc)
            save_tasks()
            return idx, item, time.time() - t, False, exc

    with ThreadPoolExecutor(max_workers=max_w) as executor:
        fmap = {executor.submit(run_job, j): j for j in jobs}
        for future in as_completed(fmap):
            idx, item, el, cached, err = future.result()
            label = build_shot_id(task_id, item["sub_script_name"], item["scene_name"], item["shot_name"])
            if err:
                log(f"  [{idx}/{len(shots)}] video FAILED {el:.1f}s: {label}: {err}")
            else:
                log(f"  [{idx}/{len(shots)}] video done {el:.1f}s cache={cached}: {label}")

    video_paths, completed = [], 0
    for item in shots:
        vp = item["shot_data"].get("video_local_path")
        if vp and Path(vp).is_file():
            video_paths.append(vp)
            completed += 1

    elapsed = time.time() - t0
    log(f"  [OK] 视频完成：{completed}/{len(shots)} 个，耗时 {elapsed:.1f}s")
    return video_paths, completed, elapsed


def main() -> None:
    wall_start = time.time()
    validate_inputs()

    requested_id = sys.argv[1] if len(sys.argv) > 1 else None
    task_id = ensure_task(requested_id)
    log(f"\nTask ID : {task_id}")
    log(f"Title   : {TITLE}")
    log(f"Mode    : {config.GENERATION_MODE}  shot_max={config.SHOT_MAX_PER_SCENE}  "
        f"duration={config.VIDEO_DURATION_SECONDS}s  resolution={config.VIDEO_RESOLUTION}")

    # ── Phase 1: 规划 ─────────────────────────────────────────────────────────
    log("\n=== Phase 1: LLM 规划 ===")
    t_plan = time.time()
    planning_elapsed = run_planning(task_id)
    t_plan_end = time.time()

    planned = all_shots(task_id)
    if not planned:
        raise RuntimeError("规划未产出任何镜头，中止。")

    selected = planned[:MAX_VIDEO_SHOTS]
    log(f"规划镜头数: {len(planned)}，本次生成前 {len(selected)} 镜 "
        f"(≈{len(selected) * int(config.VIDEO_DURATION_SECONDS)}s)")

    added = ensure_narration(selected)
    if added:
        log(f"补充旁白对白：{added} 个镜头")
        save_tasks()

    # ── Phase 2: 关键帧 ───────────────────────────────────────────────────────
    log("\n=== Phase 2: 关键帧生成 ===")
    t_kf = time.time()
    kf_count, kf_elapsed = generate_keyframes(task_id, selected)
    t_kf_end = time.time()

    # ── Phase 3: 视频+配音 ────────────────────────────────────────────────────
    log("\n=== Phase 3: 视频+配音 ===")
    t_vid = time.time()
    video_paths, completed_videos, vid_elapsed = generate_videos(task_id, selected)
    t_vid_end = time.time()

    if completed_videos != len(selected):
        raise RuntimeError(
            f"视频未全部完成（{completed_videos}/{len(selected)}），"
            f"请修复后 resume：python run_tidengman.py {task_id}"
        )

    # ── Phase 4: 拼接 ─────────────────────────────────────────────────────────
    log("\n=== Phase 4: 拼接成片 ===")
    t_concat = time.time()
    final_path = f"outputs/videos/{task_id}_tidengman_final.mp4"
    concat_method = concat_videos(video_paths, final_path, prefer_fast=True)
    t_concat_end = time.time()
    concat_elapsed = t_concat_end - t_concat

    # ── 成片时长 ──────────────────────────────────────────────────────────────
    try:
        try:
            import moviepy
            VideoFileClip = getattr(moviepy, "VideoFileClip")
        except (ImportError, AttributeError):
            from moviepy import editor as _e
            VideoFileClip = _e.VideoFileClip
        clip = VideoFileClip(final_path)
        film_duration = round(float(clip.duration or 0), 2)
        clip.close()
    except Exception:
        film_duration = len(video_paths) * int(config.VIDEO_DURATION_SECONDS)

    wall_elapsed = time.time() - wall_start
    cost = tasks[task_id].get("cost") or {}
    in_tok  = cost.get("input_tokens",  0)
    out_tok = cost.get("output_tokens", 0)
    cost_usd = round(in_tok / 1e6 * 3.0 + out_tok / 1e6 * 15.0, 4)

    # ── 汇总报告 ──────────────────────────────────────────────────────────────
    log("\n" + "=" * 60)
    log("《提灯人》生成完成")
    log("=" * 60)
    log(f"最终成片路径   : {final_path}")
    log(f"成片时长       : {film_duration}s")
    log(f"总镜头数       : {len(selected)}")
    log("")
    log("── 各阶段耗时 ──")
    if planning_elapsed > 0:
        log(f"  规划（Director→Shot）: {planning_elapsed:.1f}s")
    log(f"  关键帧生成           : {kf_elapsed:.1f}s（{kf_count} 张新生成）")
    log(f"  视频+配音            : {vid_elapsed:.1f}s（{completed_videos} 个）")
    log(f"  拼接                 : {concat_elapsed:.1f}s（{concat_method}）")
    log(f"  总墙钟耗时           : {wall_elapsed:.1f}s（{wall_elapsed/60:.1f} 分钟）")
    log("")
    log("── Token & 成本 ──")
    log(f"  input_tokens  : {in_tok:,}")
    log(f"  output_tokens : {out_tok:,}")
    log(f"  预估成本      : ${cost_usd}（按 claude-sonnet-4-6 官方定价）")
    log("=" * 60)

    tasks[task_id]["episode_outputs"]  = {"final_video": final_path, "concat_method": concat_method}
    tasks[task_id]["episode_metrics"]  = {
        "planned_shots": len(planned), "selected_shots": len(selected),
        "kf_generated": kf_count, "video_completed": completed_videos,
        "film_duration_seconds": film_duration,
        "phase_planning_seconds": round(planning_elapsed, 1),
        "phase_keyframe_seconds": round(kf_elapsed, 1),
        "phase_video_seconds":    round(vid_elapsed, 1),
        "phase_concat_seconds":   round(concat_elapsed, 1),
        "total_wall_seconds":     round(wall_elapsed, 1),
        "cost_usd_estimate":      cost_usd,
    }
    save_tasks()


if __name__ == "__main__":
    main()

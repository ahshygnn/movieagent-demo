"""
run_batch.py
配置式一键生成脚本（无需交互输入）。
修改下方 CONFIG 区域，然后运行：
    python run_batch.py
"""

# ══════════════════════════════════════════════════════════════════
#  ↓↓↓ 在这里修改你的输入 ↓↓↓
# ══════════════════════════════════════════════════════════════════

# 续跑模式：填入已有 Task ID 可跳过规划和关键帧生成，直接续跑视频
# 留空 "" 表示从头开始新任务
RESUME_TASK_ID: str = "5b67458a-19fb-4fbd-a215-3c309d45652e"

SYNOPSIS = """
Lyra, Caden, Seraphine, Finn, and Elder Moros embark on a perilous quest to uncover the origin of a mysterious ancient seal that has begun to crack across the land. As they journey through the Whispering Highlands, they discover that forgotten gods once made a pact with mortal kingdoms to contain a primordial darkness beneath the earth. Moros, a seasoned guardian who has protected the seal for decades, guides the group through treacherous ruins and shifting labyrinths. Along the way, tensions rise between Lyra's people and the nomadic Veldran tribes, who are blamed for awakening the darkness. As Seraphine ventures deeper into the spirit realm, she learns that she carries an ancient bloodline connected to the seal itself. Meanwhile, Caden and Finn face their own trials—betrayal, sacrifice, and the courage to trust one another. In the end, the companions embrace their roles in destiny—Seraphine chooses to bind herself to the seal and restore balance to the spirit world, while Lyra rises as a new leader, forging an alliance between the kingdoms and the Veldran people, bringing lasting peace to a fractured land.
""".strip()

CHARACTERS = ["Lyra", "Caden", "Seraphine", "Finn", "Elder Moros"]

# ── 方式一：指定文件夹（推荐）──────────────────────────────────
# 把角色参考图放入该文件夹，文件名与角色名一致（扩展名不限）
# 例如文件夹内有：李明.png、小雨.jpg、老陈.webp
# 留空字符串 "" 表示不使用文件夹方式
CHARACTER_IMAGES_DIR: str = r"f:\movieagents_demo\movieagent-demo\outputs\characters"

# ── 方式二：手动指定路径（可与方式一并用，手动指定优先）──────
# { 角色名: 图片路径 }，留空 {} 表示不使用
CHARACTER_REFS_PATHS: dict[str, str] = {}

# ══════════════════════════════════════════════════════════════════
#  ↑↑↑ 修改到这里结束 ↑↑↑
# ══════════════════════════════════════════════════════════════════

import time
import sys
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import config
from pipeline import tasks, create_task, run_full_pipeline
from generation.image import generate_keyframe
from generation.shot_pipeline import build_shot_id, generate_shot_video_artifacts, shot_video_complete
from generation.concat import concat_videos

VIDEO_SLEEP = 3


def log(msg: str):
    enc = getattr(sys.stdout, "encoding", "utf-8") or "utf-8"
    if enc.lower() in ("utf-8", "utf8"):
        print(msg, flush=True)
    else:
        print(msg.encode(enc, errors="replace").decode(enc, errors="replace"), flush=True)


SUPPORTED_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


def resolve_character_refs() -> dict:
    """
    合并两种方式的角色参考图，复制到 outputs/characters/ 并返回路径字典。

    优先级：CHARACTER_REFS_PATHS（手动指定）> CHARACTER_IMAGES_DIR（文件夹扫描）
    文件夹扫描时：文件名（不含扩展名）与角色名完全匹配（忽略大小写扩展名）。
    """
    save_dir = Path("outputs/characters")
    save_dir.mkdir(parents=True, exist_ok=True)
    refs: dict[str, str] = {}

    # ── 方式一：扫描文件夹 ────────────────────────────────────────
    if CHARACTER_IMAGES_DIR:
        img_dir = Path(CHARACTER_IMAGES_DIR)
        if not img_dir.is_dir():
            log(f"  ⚠️  文件夹不存在，跳过文件夹扫描: {CHARACTER_IMAGES_DIR}")
        else:
            # 建立 文件名小写 → 文件路径 的映射
            dir_map: dict[str, Path] = {}
            for f in img_dir.iterdir():
                if f.is_file() and f.suffix.lower() in SUPPORTED_EXTS:
                    dir_map[f.stem.lower()] = f

            for char_name in CHARACTERS:
                key = char_name.lower()
                if key in dir_map:
                    src = dir_map[key]
                    safe = char_name.replace(" ", "_")
                    dest = save_dir / f"{safe}{src.suffix.lower()}"
                    if src.resolve() != dest.resolve():
                        shutil.copy2(src, dest)
                    refs[char_name] = str(dest)
                    log(f"  ✅ [{char_name}] 文件夹匹配: {src.name}")
                else:
                    log(f"  ℹ️  [{char_name}] 文件夹中未找到同名图片，跳过")

    # ── 方式二：手动指定路径（会覆盖文件夹方式的同名角色）────────
    for char_name, img_path in CHARACTER_REFS_PATHS.items():
        src = Path(img_path)
        if not src.exists():
            log(f"  ⚠️  [{char_name}] 图片不存在，跳过: {img_path}")
            continue
        if src.suffix.lower() not in SUPPORTED_EXTS:
            log(f"  ⚠️  [{char_name}] 不支持的格式（请使用 png/jpg/jpeg/webp），跳过")
            continue
        safe = char_name.replace(" ", "_")
        dest = save_dir / f"{safe}{src.suffix.lower()}"
        if src.resolve() != dest.resolve():
            shutil.copy2(src, dest)
        refs[char_name] = str(dest)
        log(f"  ✅ [{char_name}] 手动指定: {dest}")

    return refs


def get_all_shots(task_id: str):
    task = tasks[task_id]
    result = []
    for ss_name, scenes in task["shots"].items():
        for scene_name, scene_data in scenes.items():
            for shot_name, shot_data in (scene_data.get("Shot") or {}).items():
                result.append({
                    "task_id": task_id,
                    "sub_script_name": ss_name,
                    "scene_name": scene_name,
                    "shot_name": shot_name,
                    "shot_data": shot_data,
                })
    return result


def character_refs_for_shot(task_id: str, involving):
    all_refs = tasks[task_id].get("character_refs") or {}
    if not all_refs:
        return {}
    names = list(involving.keys()) if isinstance(involving, dict) else (involving or [])
    return {n: all_refs[n] for n in names if n in all_refs}


def save_progress():
    from pipeline import save_tasks
    save_tasks()


# ── 主流程 ─────────────────────────────────────────────────────────

def main():
    log("=" * 60)
    log("🎬 MovieAgent 全流程生成（配置式）")
    log("=" * 60)
    log(f"\n剧本梗概：{SYNOPSIS[:80]}...")
    log(f"角色列表：{CHARACTERS}")

    # 处理参考图
    character_refs = resolve_character_refs()
    if character_refs:
        log(f"角色参考图：{list(character_refs.keys())}")
    else:
        log("角色参考图：无，使用纯文本生成关键帧")

    # ── Step 1: 规划（或续跑） ────────────────────────────────────
    log("\n" + "=" * 60)

    if RESUME_TASK_ID:
        # 续跑模式：直接使用已有 Task，跳过规划
        if RESUME_TASK_ID not in tasks:
            log(f"❌ 找不到 Task ID: {RESUME_TASK_ID}，请检查是否正确")
            sys.exit(1)
        task_id = RESUME_TASK_ID
        log(f"Step 1: 续跑模式，跳过规划（Task ID: {task_id}）")
        log("=" * 60)
        if not tasks[task_id].get("character_refs"):
            tasks[task_id]["character_refs"] = character_refs
    else:
        log("Step 1: 启动规划 pipeline（Director → Scene → Shot）")
        log("=" * 60)

        task_id = create_task()
        tasks[task_id]["character_refs"] = character_refs
        log(f"Task ID: {task_id}")

        run_full_pipeline(task_id, SYNOPSIS, CHARACTERS)

        if tasks[task_id]["status"] == "error":
            log("❌ 规划阶段失败，退出")
            sys.exit(1)

        log(f"\n✅ 规划完成！Task ID: {task_id}")
        save_progress()

    all_shots = get_all_shots(task_id)
    log(f"共规划了 {len(all_shots)} 个 Shot\n")

    # ── Step 2: 批量生成关键帧 ───────────────────────────────────
    log("=" * 60)
    if RESUME_TASK_ID:
        log("Step 2: 续跑模式，跳过关键帧生成")
        log("=" * 60)
    else:
        log("Step 2: 批量生成关键帧")
        log("=" * 60)

    for i, item in enumerate(all_shots):
        if RESUME_TASK_ID:
            # 续跑模式：只打印进度，不生成关键帧
            ss, sc, sh = item["sub_script_name"], item["scene_name"], item["shot_name"]
            shot_data = item["shot_data"]
            has_kf = shot_data.get("keyframe_local_path") and Path(shot_data["keyframe_local_path"]).exists()
            if not has_kf:
                log(f"  [{i+1}/{len(all_shots)}] {ss}/{sc}/{sh} ↳ 无关键帧，跳过")
            continue
        ss, sc, sh = item["sub_script_name"], item["scene_name"], item["shot_name"]
        shot_data = item["shot_data"]

        log(f"\n[{i+1}/{len(all_shots)}] {ss} / {sc} / {sh}")

        if shot_data.get("keyframe_local_path") and \
                Path(shot_data["keyframe_local_path"]).exists():
            log("  ↳ 已有关键帧，跳过")
            continue

        plot = shot_data.get("Plot/Visual Description", "")
        shot_id = f"{task_id}_{ss}_{sc}_{sh}".replace(" ", "_")
        matched_refs = character_refs_for_shot(task_id, shot_data.get("Involving Characters"))

        try:
            result = generate_keyframe(plot, shot_id, matched_refs)
            tasks[task_id]["shots"][ss][sc]["Shot"][sh]["keyframe_local_path"] = result["local_path"]
            tasks[task_id]["shots"][ss][sc]["Shot"][sh]["keyframe_url"] = f"/outputs/keyframes/{shot_id}.png"
            tasks[task_id]["shots"][ss][sc]["Shot"][sh]["keyframe_status"] = "done"
            log(f"  ✅ 关键帧完成，耗时 {result['elapsed_seconds']:.1f}s")
            save_progress()
        except Exception as e:
            log(f"  ❌ 关键帧失败: {e}")
            continue

        time.sleep(1)

    # ── Step 3: 批量生成视频 ─────────────────────────────────────
    log("\n" + "=" * 60)
    log("Step 3: 批量生成视频")
    log("=" * 60)

    video_jobs = []
    for i, item in enumerate(all_shots):
        ss, sc, sh = item["sub_script_name"], item["scene_name"], item["shot_name"]
        shot_data = tasks[task_id]["shots"][ss][sc]["Shot"][sh]

        log(f"\n[{i+1}/{len(all_shots)}] {ss} / {sc} / {sh}")

        keyframe_path = shot_data.get("keyframe_local_path")
        if not keyframe_path or not Path(keyframe_path).exists():
            log("  ⚠️  没有关键帧，跳过")
            shot_data["video_status"] = "skipped_missing_keyframe"
            continue

        if shot_video_complete(shot_data):
            log("  ↳ 已有视频，跳过")
            continue

        shot_data["video_status"] = "queued"
        video_jobs.append({
            "index": i,
            "sub_script_name": ss,
            "scene_name": sc,
            "shot_name": sh,
            "shot_data": shot_data,
            "keyframe_path": keyframe_path,
            "shot_id": build_shot_id(task_id, ss, sc, sh),
        })

    save_progress()

    max_workers = max(1, int(config.VIDEO_MAX_CONCURRENCY or 1))
    max_workers = min(max_workers, max(1, len(video_jobs)))
    log(f"\n待生成视频 {len(video_jobs)} 个，并发数 {max_workers}")

    def run_video_job(job: dict) -> dict:
        artifact = generate_shot_video_artifacts(
            job["shot_id"],
            job["shot_data"],
            job["keyframe_path"],
            tasks[task_id].get("voice_refs") or {},
        )
        return {"job": job, "artifact": artifact}

    if video_jobs:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {executor.submit(run_video_job, job): job for job in video_jobs}
            for future in as_completed(future_map):
                job = future_map[future]
                ss, sc, sh = job["sub_script_name"], job["scene_name"], job["shot_name"]
                shot_ref = tasks[task_id]["shots"][ss][sc]["Shot"][sh]
                try:
                    result = future.result()
                    artifact = result["artifact"]
                    shot_ref.update(artifact["updates"])
                    elapsed = artifact["video_result"]["elapsed_seconds"]
                    log(f"  ✅ [{job['index']+1}/{len(all_shots)}] {ss} / {sc} / {sh} 视频完成，耗时 {elapsed:.1f}s")
                except Exception as e:
                    shot_ref["video_status"] = "error"
                    shot_ref["video_error"] = str(e)
                    log(f"  ❌ [{job['index']+1}/{len(all_shots)}] {ss} / {sc} / {sh} 视频失败: {e}")
                save_progress()

    # ── Step 4: 拼接成片 ─────────────────────────────────────────
    log("\n" + "=" * 60)
    log("Step 4: 拼接所有视频为成片")
    log("=" * 60)

    video_paths = []
    for item in all_shots:
        ss, sc, sh = item["sub_script_name"], item["scene_name"], item["shot_name"]
        shot_ref = tasks[task_id]["shots"][ss][sc]["Shot"][sh]
        vp = shot_ref.get("video_local_path")
        if vp and Path(vp).exists():
            video_paths.append(vp)

    if not video_paths:
        log("❌ 没有可拼接的视频片段")
        sys.exit(1)

    log(f"共 {len(video_paths)} 个片段待拼接")

    out_path = f"outputs/videos/{task_id}_final.mp4"
    try:
        concat_method = concat_videos(video_paths, out_path, prefer_fast=True)
        log(f"\n🎉 成片已保存：{out_path}（拼接方式：{concat_method}）")
    except Exception as e:
        log(f"❌ 拼接失败: {e}")

    # ── 完成 ────────────────────────────────────────────────────
    log("\n" + "=" * 60)
    log("全部完成！")
    log(f"成片路径：{out_path}")
    log(f"Task ID：{task_id}")
    log("（把上面的 Task ID 粘贴到前端「加载已有任务」输入框）")
    log("=" * 60)


if __name__ == "__main__":
    main()

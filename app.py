from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import time
from pathlib import Path
from typing import List

from fastapi import FastAPI, BackgroundTasks, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import config
from pipeline import tasks, create_task, run_full_pipeline, save_tasks
import metrics.collector as mc

from generation.image import generate_keyframe
from generation.character import generate_character_references, generate_character_reference
from generation.concat import concat_videos
from generation.postprocess import prepare_dubbing_assets
from generation.shot_pipeline import build_shot_id, generate_shot_video_artifacts, shot_video_complete
from script_rewriter import rewrite_script

app = FastAPI(title="MovieAgent Demo API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静态文件服务，前端可通过 /outputs/keyframes/xxx.png 访问生成的图片和视频
os.makedirs("outputs/keyframes", exist_ok=True)
os.makedirs("outputs/videos", exist_ok=True)
os.makedirs("outputs/audio", exist_ok=True)
os.makedirs("outputs/characters", exist_ok=True)
os.makedirs("outputs/metrics", exist_ok=True)
app.mount("/outputs", StaticFiles(directory="outputs"), name="outputs")


# ── 请求体数据模型 ─────────────────────────────────────────

class GenerateRequest(BaseModel):
    script_synopsis: str
    characters: list[str]
    character_refs: dict
    voice_refs: dict = {}
    raw_script: str = ""


class RewriteRequest(BaseModel):
    raw_script: str

class KeyframeRequest(BaseModel):
    task_id: str
    sub_script_name: str
    scene_name: str
    shot_name: str

class CharacterGenRequest(BaseModel):
    task_id: str
    characters: list[str] = []       # 不传则用 task 规划阶段的角色
    script_synopsis: str = ""        # 不传则用 task 里保存的剧本

class CharacterRegenRequest(BaseModel):
    task_id: str
    name: str                        # 要重生成的角色名
    feedback: str = ""               # 用户对上一版定妆图的修改意见

class CharacterApproveRequest(BaseModel):
    task_id: str
    names: list[str] = []            # 不传则采用全部 pending 角色

class VideoRequest(BaseModel):
    task_id: str
    sub_script_name: str
    scene_name: str
    shot_name: str


class FinalVideoRequest(BaseModel):
    task_id: str
    sub_script_names: list[str] = []


class BatchVideoRequest(BaseModel):
    task_id: str
    sub_script_names: list[str] = []
    max_concurrency: int | None = None


class BatchKeyframeRequest(BaseModel):
    task_id: str
    mode: str = "final"   # "draft"（1024x576）或 "final"（1280x720）

class RegenerateSceneRequest(BaseModel):
    task_id: str
    sub_script_name: str   # 重新规划这个子剧本下的所有场景

class RegenerateShotRequest(BaseModel):
    task_id: str
    sub_script_name: str
    scene_name: str        # 重新规划这个场景下的所有镜头


class UpdateShotRequest(BaseModel):
    task_id: str
    sub_script_name: str
    scene_name: str
    shot_name: str
    updates: dict


class UpdateSceneRequest(BaseModel):
    task_id: str
    sub_script_name: str
    scene_name: str
    updates: dict


class AudioRequest(BaseModel):
    task_id: str
    sub_script_name: str
    scene_name: str
    shot_name: str
    voice_refs: dict = {}


# ── 接口 ──────────────────────────────────────────────────

@app.get("/api/health", summary="部署健康检查")
def health_check():
    return {"status": "ok"}

@app.post("/api/rewrite", summary="改写原始剧本")
def api_rewrite_script(req: RewriteRequest):
    try:
        return rewrite_script(req.raw_script)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

@app.post("/api/generate", summary="启动完整规划 pipeline")
def start_generate(req: GenerateRequest, background_tasks: BackgroundTasks):
    task_id = create_task()
    tasks[task_id]["character_refs"] = req.character_refs or {}
    tasks[task_id]["voice_refs"] = req.voice_refs or {}
    # 保存原始剧本与角色名，供人物定妆图生成接口复用
    tasks[task_id]["script_synopsis"] = req.script_synopsis
    tasks[task_id]["raw_script"] = req.raw_script or req.script_synopsis
    tasks[task_id]["characters"] = req.characters
    background_tasks.add_task(
        run_full_pipeline, task_id, req.script_synopsis, req.characters
    )
    return {"task_id": task_id}


def _characters_from_sub_scripts(sub_scripts: dict) -> list[str]:
    """从 sub_scripts 里汇总所有出现过的角色名（去重、保持出现顺序）作为兜底。"""
    names: list[str] = []
    for ss in (sub_scripts or {}).get("Sub-Script", {}).values():
        for name in (ss or {}).get("Involving Characters", []) or []:
            if name and name not in names:
                names.append(name)
    return names


def _script_from_sub_scripts(sub_scripts: dict) -> str:
    """把各子剧本的 Plot 拼成一段文本作为剧本兜底。"""
    plots = [
        str((ss or {}).get("Plot", "") or "").strip()
        for ss in (sub_scripts or {}).get("Sub-Script", {}).values()
    ]
    return "\n\n".join(p for p in plots if p)


@app.post("/api/generate/characters", summary="为剧本人物生成全身正面定妆照（Seedream 5.0）")
def api_generate_characters(req: CharacterGenRequest):
    task = tasks.get(req.task_id)
    if not task:
        return {"error": "task not found"}

    sub_scripts = task.get("sub_scripts") or {}
    characters = (
        req.characters
        or task.get("characters")
        or _characters_from_sub_scripts(sub_scripts)
    )
    if not characters:
        return {"error": "no characters found for this task; pass 'characters' explicitly"}

    script = (
        req.script_synopsis
        or task.get("script_synopsis")
        or _script_from_sub_scripts(sub_scripts)
    )

    try:
        out = generate_character_references(script, characters)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    # 合并写回任务级 character_refs（下游关键帧按 Involving Characters 自动取用）
    refs = out.get("character_refs") or {}
    designs = out.get("designs") or {}
    errors = out.get("errors") or {}
    tasks[req.task_id].setdefault("character_refs", {})
    tasks[req.task_id]["character_refs"].update(refs)
    # 存 designs（供关键帧反思审核取 Appearance 文本）+ 每角色状态（pending，供人工审核）
    tasks[req.task_id].setdefault("character_designs", {})
    tasks[req.task_id]["character_designs"].update(designs)
    tasks[req.task_id].setdefault("character_ref_status", {})
    for name in refs:
        tasks[req.task_id]["character_ref_status"][name] = "pending"
    for name in errors:
        tasks[req.task_id]["character_ref_status"][name] = "failed"
    tasks[req.task_id].setdefault("character_ref_errors", {})
    tasks[req.task_id]["character_ref_errors"].update(errors)
    for name in refs:
        tasks[req.task_id]["character_ref_errors"].pop(name, None)
    save_tasks()

    if errors and not refs:
        detail = "; ".join(f"{name}: {message}" for name, message in errors.items())
        raise HTTPException(status_code=502, detail=detail)

    return {
        "characters": {
            name: {
                "character_url": f"/outputs/characters/{os.path.basename(path)}",
                "status": tasks[req.task_id]["character_ref_status"].get(name, "pending"),
            }
            for name, path in refs.items()
        },
        "errors": errors,
        "designs": designs,
    }


@app.post("/api/regenerate/character_ref", summary="带用户反馈重生成单个角色定妆图")
def api_regenerate_character_ref(req: CharacterRegenRequest):
    task = tasks.get(req.task_id)
    if not task:
        return {"error": "task not found"}

    designs = task.get("character_designs") or {}
    entry = designs.get(req.name) or {}
    appearance = str(entry.get("Appearance", "") or "").strip()
    background = str(entry.get("Background", "") or "").strip()

    try:
        res = generate_character_reference(
            req.name, appearance, background, feedback=req.feedback,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    path = res["path"]
    tasks[req.task_id].setdefault("character_refs", {})[req.name] = path
    tasks[req.task_id].setdefault("character_ref_status", {})[req.name] = "pending"
    save_tasks()

    return {
        "name": req.name,
        "character_url": f"/outputs/characters/{os.path.basename(path)}",
        "status": "pending",
        "elapsed_seconds": res.get("elapsed_seconds"),
    }


@app.post("/api/character_refs/approve", summary="人工审核通过，锁定角色定妆图")
def api_approve_character_refs(req: CharacterApproveRequest):
    task = tasks.get(req.task_id)
    if not task:
        return {"error": "task not found"}

    status_map = tasks[req.task_id].setdefault("character_ref_status", {})
    targets = req.names or list((task.get("character_refs") or {}).keys())
    approved = []
    for name in targets:
        if name in (task.get("character_refs") or {}):
            status_map[name] = "approved"
            approved.append(name)
    save_tasks()
    return {"approved": approved, "character_ref_status": status_map}


def _involving_names(involving) -> list:
    if isinstance(involving, dict):
        return list(involving.keys())
    if isinstance(involving, list):
        return list(involving)
    return []


def _character_refs_for_shot(task_character_refs: dict, involving) -> dict:
    """按 Shot 的 Involving Characters 从任务级 character_refs 中取路径。"""
    if not task_character_refs:
        return {}
    out = {}
    for name in _involving_names(involving):
        if name in task_character_refs:
            out[name] = task_character_refs[name]
    return out


def _appearance_texts_for_shot(task_character_designs: dict, involving) -> dict:
    """按 Shot 的 Involving Characters 从任务级 character_designs 取英文外貌描述，供反思审核人物一致性作辅助。"""
    if not task_character_designs:
        return {}
    out = {}
    for name in _involving_names(involving):
        entry = task_character_designs.get(name)
        if isinstance(entry, dict):
            appearance = str(entry.get("Appearance", "") or "").strip()
            if appearance:
                out[name] = appearance
    return out


def _add_review_cost(task_id: str, review: dict | None) -> None:
    """把关键帧反思审核消耗的 token 累加进任务成本。"""
    if not review:
        return
    usage = review.get("usage") or {}
    cost = tasks[task_id].setdefault("cost", {"input_tokens": 0, "output_tokens": 0})
    cost["input_tokens"] = cost.get("input_tokens", 0) + int(usage.get("input_tokens", 0) or 0)
    cost["output_tokens"] = cost.get("output_tokens", 0) + int(usage.get("output_tokens", 0) or 0)


@app.get("/api/status/{task_id}", summary="查询任务状态和中间结果")
def get_status(task_id: str):
    task = tasks.get(task_id)
    if not task:
        return {"error": "task not found"}
    return task


@app.post("/api/generate/keyframe", summary="为单个 Shot 生成关键帧")
def api_generate_keyframe(req: KeyframeRequest):
    task = tasks.get(req.task_id)
    if not task:
        return {"error": "task not found"}

    # 找到对应的 shot 数据
    shot_data = (
        task["shots"]
        .get(req.sub_script_name, {})
        .get(req.scene_name, {})
        .get("Shot", {})
        .get(req.shot_name, {})
    )
    if not shot_data:
        return {"error": f"shot not found: {req.sub_script_name} / {req.scene_name} / {req.shot_name}"}

    shot_id = build_shot_id(req.task_id, req.sub_script_name, req.scene_name, req.shot_name)
    plot = shot_data.get("Plot/Visual Description", "")
    involving = shot_data.get("Involving Characters")
    matched_refs = _character_refs_for_shot(task.get("character_refs") or {}, involving)
    matched_appearance = _appearance_texts_for_shot(task.get("character_designs") or {}, involving)

    try:
        result = generate_keyframe(
            plot, shot_id, matched_refs, appearance_texts=matched_appearance,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    # 写回任务状态
    shot_ref = tasks[req.task_id]["shots"][req.sub_script_name][req.scene_name]["Shot"][req.shot_name]
    shot_ref["keyframe_local_path"] = result["local_path"]
    shot_ref["keyframe_url"] = f"/outputs/keyframes/{shot_id}.png"
    shot_ref["keyframe_status"] = "done"
    review = result.get("review")
    if review is not None:
        shot_ref["keyframe_review"] = review
        shot_ref["keyframe_review_status"] = review.get("review_status")
        _add_review_cost(req.task_id, review)

    mc.record_keyframe_latency(req.task_id, shot_id, result["elapsed_seconds"])
    save_tasks()

    return {
        "keyframe_url": f"/outputs/keyframes/{shot_id}.png",
        "elapsed_seconds": result["elapsed_seconds"],
        "keyframe_size": result.get("keyframe_size"),
        "keyframe_review_status": (review or {}).get("review_status") if review else None,
    }


@app.post("/api/generate/keyframes_batch", summary="批量并行生成所有 Shot 的关键帧")
def api_generate_keyframes_batch(req: BatchKeyframeRequest):
    task = tasks.get(req.task_id)
    if not task:
        return {"error": "task not found"}

    # 收集所有待生成的 shot（跳过已完成的）
    shot_jobs = []
    for ss_name, scenes in (task.get("shots") or {}).items():
        for scene_name, scene_data in (scenes or {}).items():
            for shot_name, shot_data in (scene_data or {}).get("Shot", {}).items():
                if (shot_data or {}).get("keyframe_status") == "done":
                    continue
                shot_jobs.append((ss_name, scene_name, shot_name, shot_data))

    if not shot_jobs:
        return {"message": "所有关键帧已生成", "generated": 0, "skipped": True}

    def _gen_one(job):
        ss_name, scene_name, shot_name, shot_data = job
        shot_id = build_shot_id(req.task_id, ss_name, scene_name, shot_name)
        plot = (shot_data or {}).get("Plot/Visual Description", "")
        involving = (shot_data or {}).get("Involving Characters")
        matched_refs = _character_refs_for_shot(task.get("character_refs") or {}, involving)
        matched_appearance = _appearance_texts_for_shot(task.get("character_designs") or {}, involving)
        result = generate_keyframe(
            plot, shot_id, matched_refs, mode=req.mode, appearance_texts=matched_appearance,
        )
        return ss_name, scene_name, shot_name, shot_id, result

    start = time.time()
    results = []
    errors = []
    review_fail = 0
    workers = min(config.KEYFRAME_MAX_CONCURRENCY, len(shot_jobs))

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_gen_one, job): job for job in shot_jobs}
        for future in as_completed(futures):
            job = futures[future]
            try:
                ss_name, scene_name, shot_name, shot_id, result = future.result()
                shot_ref = tasks[req.task_id]["shots"][ss_name][scene_name]["Shot"][shot_name]
                shot_ref["keyframe_local_path"] = result["local_path"]
                shot_ref["keyframe_url"] = f"/outputs/keyframes/{shot_id}.png"
                shot_ref["keyframe_status"] = "done"
                shot_ref["keyframe_size"] = result.get("keyframe_size")
                review = result.get("review")
                if review is not None:
                    shot_ref["keyframe_review"] = review
                    shot_ref["keyframe_review_status"] = review.get("review_status")
                    _add_review_cost(req.task_id, review)
                    if review.get("review_status") == "fail":
                        review_fail += 1
                mc.record_keyframe_latency(req.task_id, shot_id, result["elapsed_seconds"])
                results.append(shot_id)
            except Exception as e:
                errors.append({"shot": f"{job[0]}/{job[1]}/{job[2]}", "error": str(e)})

    elapsed = round(time.time() - start, 1)
    avg = round(elapsed / max(len(results), 1), 1)

    # 埋点计时，写入 tasks["timing"] 供评测模块读取
    tasks[req.task_id].setdefault("timing", {})["keyframes_batch_seconds"] = elapsed
    tasks[req.task_id]["timing"]["keyframes_count"] = len(results)
    tasks[req.task_id]["timing"]["keyframes_avg_seconds"] = avg
    save_tasks()

    return {
        "generated": len(results),
        "failed": len(errors),
        "review_fail": review_fail,
        "errors": errors,
        "elapsed_seconds": elapsed,
        "avg_seconds_per_frame": avg,
        "mode": req.mode,
        "keyframe_size": config.KEYFRAME_SIZE_DRAFT if req.mode == "draft" else config.KEYFRAME_SIZE_FINAL,
        "concurrency": workers,
    }


@app.post("/api/update/shot", summary="修改单个 Shot 的文字内容（不调用LLM）")
def update_shot(req: UpdateShotRequest):
    task = tasks.get(req.task_id)
    if not task:
        return {"error": "task not found"}

    shot_ref = (
        task["shots"]
        .get(req.sub_script_name, {})
        .get(req.scene_name, {})
        .get("Shot", {})
        .get(req.shot_name)
    )
    if not shot_ref:
        return {"error": "shot not found"}

    updated_fields = []
    for k, v in req.updates.items():
        shot_ref[k] = v
        updated_fields.append(k)

    save_tasks()
    return {"message": "更新成功", "updated_fields": updated_fields}


@app.post("/api/update/scene", summary="修改单个 Scene 的文字内容（不调用LLM）")
def update_scene(req: UpdateSceneRequest):
    task = tasks.get(req.task_id)
    if not task:
        return {"error": "task not found"}

    scene_ref = (
        task["scenes"]
        .get(req.sub_script_name, {})
        .get("Scene", {})
        .get(req.scene_name)
    )
    if not scene_ref:
        return {"error": "scene not found"}

    updated_fields = []
    for k, v in req.updates.items():
        scene_ref[k] = v
        updated_fields.append(k)

    save_tasks()
    return {"message": "更新成功", "updated_fields": updated_fields}


@app.post("/api/generate/video", summary="为单个 Shot 生成视频（需先有关键帧）")
def api_generate_video(req: VideoRequest):
    task = tasks.get(req.task_id)
    if not task:
        return {"error": "task not found"}

    shot_data = (
        task["shots"]
        .get(req.sub_script_name, {})
        .get(req.scene_name, {})
        .get("Shot", {})
        .get(req.shot_name, {})
    )
    if not shot_data:
        return {"error": "shot not found"}

    keyframe_path = shot_data.get("keyframe_local_path")
    if not keyframe_path:
        return {"error": "请先生成关键帧再生成视频"}

    try:
        artifact = generate_shot_video_artifacts(
            build_shot_id(req.task_id, req.sub_script_name, req.scene_name, req.shot_name),
            shot_data,
            keyframe_path,
            dict(task.get("voice_refs") or {}),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    shot_id_key = build_shot_id(req.task_id, req.sub_script_name, req.scene_name, req.shot_name)
    shot_ref = tasks[req.task_id]["shots"][req.sub_script_name][req.scene_name]["Shot"][req.shot_name]
    shot_ref.update(artifact["updates"])
    save_tasks()

    mc.record_video_latency(req.task_id, shot_id_key, artifact["video_result"]["elapsed_seconds"])

    return {
        "video_url": shot_ref["video_url"],
        "raw_video_url": shot_ref["raw_video_url"],
        "audio_files": shot_ref["audio_files"],
        "has_dubbing": shot_ref["video_has_dubbing"],
        "generation_mode": shot_ref["generation_mode"],
        "duration_seconds": shot_ref["video_duration_seconds"],
        "resolution": shot_ref["video_resolution"],
        "elapsed_seconds": artifact["video_result"]["elapsed_seconds"]
    }


def _iter_video_jobs(task: dict, task_id: str, sub_script_names: list[str]) -> list[dict]:
    shots_root = task.get("shots") or {}
    if sub_script_names:
        ss_order = [ss for ss in sub_script_names if ss in shots_root]
    else:
        ss_order = list(shots_root.keys())

    jobs: list[dict] = []
    for ss_name in ss_order:
        scenes_dict = shots_root.get(ss_name) or {}
        for scene_name, scene_data in scenes_dict.items():
            shot_dict = (scene_data or {}).get("Shot") or {}
            for shot_name, shot_data in shot_dict.items():
                jobs.append({
                    "task_id": task_id,
                    "sub_script_name": ss_name,
                    "scene_name": scene_name,
                    "shot_name": shot_name,
                    "shot_id": build_shot_id(task_id, ss_name, scene_name, shot_name),
                    "shot_data": shot_data,
                    "keyframe_path": (shot_data or {}).get("keyframe_local_path"),
                })
    return jobs


def _run_batch_video_generation(task_id: str, sub_script_names: list[str], max_concurrency: int | None = None):
    task = tasks.get(task_id)
    if not task:
        return

    jobs = _iter_video_jobs(task, task_id, sub_script_names)
    runnable_jobs = []
    skipped = 0
    for job in jobs:
        shot_data = job["shot_data"] or {}
        if shot_video_complete(shot_data):
            skipped += 1
            continue
        keyframe_path = job.get("keyframe_path")
        if not keyframe_path or not os.path.isfile(keyframe_path):
            shot_data["video_status"] = "skipped_missing_keyframe"
            continue
        shot_data["video_status"] = "queued"
        runnable_jobs.append(job)

    workers = max(1, int(max_concurrency or config.VIDEO_MAX_CONCURRENCY or 1))
    workers = min(workers, max(1, len(runnable_jobs)))
    task["video_batch"] = {
        "status": "running",
        "total": len(jobs),
        "queued": len(runnable_jobs),
        "completed": 0,
        "failed": 0,
        "skipped_existing": skipped,
        "max_concurrency": workers,
    }
    save_tasks()

    if not runnable_jobs:
        task["video_batch"]["status"] = "done"
        save_tasks()
        return

    voice_refs = dict(task.get("voice_refs") or {})

    def run_job(job: dict) -> dict:
        artifact = generate_shot_video_artifacts(
            job["shot_id"],
            job["shot_data"],
            job["keyframe_path"],
            voice_refs,
        )
        return {"job": job, "artifact": artifact}

    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {executor.submit(run_job, job): job for job in runnable_jobs}
        for future in as_completed(future_map):
            job = future_map[future]
            shot_ref = (
                tasks[task_id]["shots"][job["sub_script_name"]][job["scene_name"]]["Shot"][job["shot_name"]]
            )
            try:
                result = future.result()
                shot_ref.update(result["artifact"]["updates"])
                task["video_batch"]["completed"] += 1
                mc.record_video_latency(
                    task_id,
                    job["shot_id"],
                    result["artifact"]["video_result"]["elapsed_seconds"],
                )
            except Exception as e:
                shot_ref["video_status"] = "error"
                shot_ref["video_error"] = str(e)
                task["video_batch"]["failed"] += 1
            save_tasks()

    task["video_batch"]["status"] = "done" if task["video_batch"]["failed"] == 0 else "partial_error"
    save_tasks()


@app.post("/api/generate/videos", summary="批量并发生成 Shot 视频")
def api_generate_videos(req: BatchVideoRequest, background_tasks: BackgroundTasks):
    task = tasks.get(req.task_id)
    if not task:
        return {"error": "task not found"}

    jobs = _iter_video_jobs(task, req.task_id, req.sub_script_names or [])
    runnable = [
        job for job in jobs
        if job.get("keyframe_path")
        and os.path.isfile(job["keyframe_path"])
        and not shot_video_complete(job["shot_data"] or {})
    ]
    workers = max(1, int(req.max_concurrency or config.VIDEO_MAX_CONCURRENCY or 1))
    background_tasks.add_task(
        _run_batch_video_generation,
        req.task_id,
        req.sub_script_names or [],
        workers,
    )
    return {
        "message": "batch video generation started",
        "total_shots": len(jobs),
        "queued_shots": len(runnable),
        "max_concurrency": workers,
    }


def _collect_media_for_final(task: dict, sub_script_names: list[str]) -> list[dict]:
    """按 Sub-Script → Scene → Shot 顺序收集已有本地视频和字幕路径。"""
    shots_root = task.get("shots") or {}
    if sub_script_names:
        ss_order = [ss for ss in sub_script_names if ss in shots_root]
    else:
        ss_order = list(shots_root.keys())

    items: list[dict] = []
    for ss_name in ss_order:
        scenes_dict = shots_root[ss_name] or {}
        for _scene_name, scene_data in scenes_dict.items():
            shot_dict = (scene_data or {}).get("Shot") or {}
            for _shot_name, shot_data in shot_dict.items():
                for key in ("enhanced_video_local_path", "video_local_path", "raw_video_local_path"):
                    vp = (shot_data or {}).get(key)
                    if vp and isinstance(vp, str) and os.path.isfile(vp):
                        items.append({"video_path": vp})
                        break
    return items


def _collect_video_paths_for_final(task: dict, sub_script_names: list[str]) -> list[str]:
    return [item["video_path"] for item in _collect_media_for_final(task, sub_script_names)]


@app.post("/api/generate/final_video", summary="拼接任务内已生成的镜头视频为成片")
def api_generate_final_video(req: FinalVideoRequest):
    task = tasks.get(req.task_id)
    if not task:
        return {"error": "task not found"}

    media_items = _collect_media_for_final(task, req.sub_script_names or [])
    paths = [item["video_path"] for item in media_items]
    if not paths:
        raise HTTPException(
            status_code=400,
            detail="没有可拼接的视频片段（请先生成各 Shot 的视频，或检查 sub_script_names）",
        )

    os.makedirs("outputs/videos", exist_ok=True)
    out_rel = f"/outputs/videos/{req.task_id}_final.mp4"
    out_path = os.path.join("outputs/videos", f"{req.task_id}_final.mp4")

    try:
        concat_method = concat_videos(paths, out_path, prefer_fast=True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"拼接视频失败: {e}") from e

    task["final_video_url"] = out_rel
    save_tasks()
    return {
        "final_video_url": out_rel,
        "concat_method": concat_method,
    }


@app.post("/api/regenerate/scene", summary="重新规划某子剧本的所有场景")
def regenerate_scene(req: RegenerateSceneRequest, background_tasks: BackgroundTasks):
    from agents.scene import run_scene_agent
    task = tasks.get(req.task_id)
    if not task:
        return {"error": "task not found"}

    ss_data = task["sub_scripts"].get("Sub-Script", {}).get(req.sub_script_name)
    relationships = task["sub_scripts"].get("Relationships", {})
    if not ss_data:
        return {"error": f"sub_script not found: {req.sub_script_name}"}

    tasks[req.task_id]["status"] = "regenerating_scene"
    save_tasks()

    def _regen():
        try:
            out = run_scene_agent(ss_data["Plot"], relationships)
            tasks[req.task_id]["scenes"][req.sub_script_name] = out["result"]
            tasks[req.task_id]["logs"].append(f"✅ {req.sub_script_name} 场景重新规划完成")
            tasks[req.task_id]["status"] = "done"
        except Exception as e:
            tasks[req.task_id]["status"] = "error"
            tasks[req.task_id]["logs"].append(f"❌ 场景重新规划出错: {e}")
        finally:
            save_tasks()

    background_tasks.add_task(_regen)
    return {"message": f"正在重新规划 {req.sub_script_name} 的场景", "status": "regenerating_scene"}


@app.post("/api/regenerate/shot", summary="重新规划某场景的所有镜头")
def regenerate_shot(req: RegenerateShotRequest, background_tasks: BackgroundTasks):
    from agents.shot import run_shot_agent
    task = tasks.get(req.task_id)
    if not task:
        return {"error": "task not found"}

    scene_data = (
        task["scenes"]
        .get(req.sub_script_name, {})
        .get("Scene", {})
        .get(req.scene_name)
    )
    if not scene_data:
        return {"error": f"scene not found: {req.sub_script_name} / {req.scene_name}"}

    tasks[req.task_id]["status"] = "regenerating_shot"
    save_tasks()

    def _regen():
        try:
            out = run_shot_agent(scene_data)
            tasks[req.task_id]["shots"][req.sub_script_name][req.scene_name] = out["result"]
            tasks[req.task_id]["logs"].append(f"✅ {req.scene_name} 镜头重新规划完成")
            tasks[req.task_id]["status"] = "done"
        except Exception as e:
            tasks[req.task_id]["status"] = "error"
            tasks[req.task_id]["logs"].append(f"❌ 镜头重新规划出错: {e}")
        finally:
            save_tasks()

    background_tasks.add_task(_regen)
    return {"message": f"正在重新规划 {req.scene_name} 的镜头", "status": "regenerating_shot"}


@app.get("/api/cost/{task_id}", summary="查询当前任务的 token 消耗")
def get_cost(task_id: str):
    task = tasks.get(task_id)
    if not task:
        return {"error": "task not found"}
    return task.get("cost", {})


@app.post("/api/upload/audios", summary="批量上传角色参考音频")
async def upload_character_audios(
    character_names: str = Form(...),
    texts: str = Form(...),
    files: List[UploadFile] = File(...),
):
    raise HTTPException(
        status_code=410,
        detail=(
            "当前 TTS 已切换为一展 API Pro 的 qwen3-tts-flash。"
            "该接口不再上传参考音频；请在 voice_refs 中传入音色名称，"
            "例如 Cherry，或配置 YIZHAN_TTS_DEFAULT_VOICE。"
        ),
    )


@app.post("/api/generate/audio", summary="为单个 Shot 生成角色台词音频")
def api_generate_audio(req: AudioRequest):
    task = tasks.get(req.task_id)
    if not task:
        return {"error": "task not found"}

    shot_data = (
        task["shots"]
        .get(req.sub_script_name, {})
        .get(req.scene_name, {})
        .get("Shot", {})
        .get(req.shot_name, {})
    )
    if not shot_data:
        return {"error": "shot not found"}

    dialogue = shot_data.get("Dialogue") or shot_data.get("Subtitles") or {}
    if not dialogue:
        return {"message": "该Shot没有台词", "audio_files": {}}

    merged_voice = dict(task.get("voice_refs") or {})
    merged_voice.update(req.voice_refs or {})

    shot_id = build_shot_id(req.task_id, req.sub_script_name, req.scene_name, req.shot_name)
    try:
        assets = prepare_dubbing_assets(shot_id, shot_data, merged_voice)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    shot_ref = tasks[req.task_id]["shots"][req.sub_script_name][req.scene_name]["Shot"][req.shot_name]
    shot_ref["audio_files"] = assets.get("audio_files") or {}
    shot_ref["combined_audio_local_path"] = assets.get("combined_audio_local_path")
    save_tasks()

    return {
        "audio_files": shot_ref["audio_files"],
        "combined_audio_local_path": shot_ref["combined_audio_local_path"],
    }


@app.post("/api/upload/characters", summary="批量上传角色参考图")
async def upload_character_images(
    character_names: str = Form(...),
    files: List[UploadFile] = File(...),
):
    try:
        names = json.loads(character_names)
    except json.JSONDecodeError:
        return JSONResponse(
            status_code=400,
            content={"error": "character_names 须为合法 JSON 字符串"},
        )
    if not isinstance(names, list):
        return JSONResponse(
            status_code=400,
            content={"error": "character_names 须解析为 JSON 数组"},
        )
    if len(files) != len(names):
        return JSONResponse(
            status_code=400,
            content={"error": "角色名数量和文件数量不一致"},
        )

    save_dir = "outputs/characters"
    results = {}
    for name, file in zip(names, files):
        fn = file.filename or ""
        parts = fn.rsplit(".", 1)
        ext = parts[-1].lower() if len(parts) == 2 and parts[-1] else "png"
        safe_name = str(name).replace(" ", "_")
        local_path = os.path.join(save_dir, f"{safe_name}.{ext}")
        content = await file.read()
        with open(local_path, "wb") as f:
            f.write(content)
        results[name] = local_path

    return {
        "results": results,
        "message": f"成功上传 {len(results)} 个角色参考图",
    }


# ── 指标评估 API ──────────────────────────────────────────────────────────────

class L3FeedbackRequest(BaseModel):
    first_video_satisfaction: str | None = None   # "yes" | "no"
    controllability_rating: int | None = None      # 1-5


class ComputeL2Request(BaseModel):
    include_clip: bool = False   # 需要安装 torch+transformers 才有效


@app.get("/api/metrics/{task_id}", summary="查看任务全量指标")
def get_metrics(task_id: str):
    from metrics.collector import load_metrics, _path
    if not _path(task_id).exists():
        raise HTTPException(status_code=404, detail="metrics not found for this task")
    return load_metrics(task_id)


@app.get("/api/metrics/{task_id}/summary", summary="查看指标摘要（L1完成率+延迟+L2均分）")
def get_metrics_summary(task_id: str):
    from metrics.collector import load_metrics, _path
    if not _path(task_id).exists():
        raise HTTPException(status_code=404, detail="metrics not found for this task")
    m = load_metrics(task_id)
    l1 = m["l1"]
    l2 = m["l2"]
    l3 = m["l3"]

    lat = l1["latency"]
    kf_vals = list(lat.get("keyframe_seconds", {}).values())
    vid_vals = list(lat.get("video_seconds", {}).values())

    return {
        "l1": {
            "pipeline_completion_rate": l1["pipeline_completion"]["completion_rate"],
            "json_parse_rate": l1["pipeline_completion"]["json_parse_rate"],
            "director_seconds": lat.get("director_seconds"),
            "scene_total_seconds": lat.get("scene_total_seconds"),
            "shot_total_seconds": lat.get("shot_total_seconds"),
            "pipeline_planning_seconds": lat.get("pipeline_planning_seconds"),
            "keyframe_mean_seconds": round(sum(kf_vals) / len(kf_vals), 2) if kf_vals else None,
            "video_mean_seconds": round(sum(vid_vals) / len(vid_vals), 2) if vid_vals else None,
            "character_consistency_mean": l1["character_consistency"]["mean"],
        },
        "l2": {
            "narrative_coherence_mean": l2["narrative_coherence"]["mean"],
            "narrative_coherence_status": l2["narrative_coherence"]["status"],
            "visual_text_alignment_mean": l2["visual_text_alignment"]["mean"],
            "style_consistency_variance": l2["style_consistency"]["clip_i_variance"],
        },
        "l3": {
            "task_start_time": l3.get("task_start_time"),
            "duration_minutes": l3.get("duration_minutes"),
            "first_video_satisfaction": l3.get("first_video_satisfaction"),
            "controllability_rating": l3.get("controllability_rating"),
        },
    }


@app.post("/api/metrics/{task_id}/compute/l2", summary="触发 L2 内容质量评分（叙事连贯性 + 可选 CLIP）")
def compute_l2_metrics(task_id: str, req: ComputeL2Request, background_tasks: BackgroundTasks):
    task = tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")

    script = (task.get("sub_scripts") or {})
    original_script = " ".join(
        ss.get("Plot", "")
        for ss in script.get("Sub-Script", {}).values()
    )
    shots = task.get("shots", {})

    def _run():
        from metrics.l2_content import run_narrative_scoring
        try:
            run_narrative_scoring(task_id, original_script, shots)
        except Exception as e:
            print(f"[L2.1] 叙事评分失败: {e}")

        if req.include_clip:
            from metrics.clip_scorer import (
                score_visual_text_alignment,
                score_style_consistency,
                score_character_consistency,
            )
            score_visual_text_alignment(task_id, shots)
            score_style_consistency(task_id, shots)
            score_character_consistency(task_id, shots)

    background_tasks.add_task(_run)
    return {
        "message": "L2 评分已在后台启动",
        "clip_requested": req.include_clip,
        "clip_available": _clip_available(),
    }


@app.post("/api/metrics/{task_id}/compute/clip", summary="触发 CLIP 指标（L1.3 角色一致性 + L2.2 视觉对齐 + L2.3 风格一致性）")
def compute_clip_metrics(task_id: str, background_tasks: BackgroundTasks):
    from metrics.clip_scorer import is_available
    if not is_available():
        raise HTTPException(
            status_code=422,
            detail="CLIP 不可用，请先安装: pip install torch torchvision transformers pillow",
        )
    task = tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")

    shots = task.get("shots", {})

    def _run():
        from metrics.clip_scorer import (
            score_visual_text_alignment,
            score_style_consistency,
            score_character_consistency,
        )
        score_visual_text_alignment(task_id, shots)
        score_style_consistency(task_id, shots)
        score_character_consistency(task_id, shots)

    background_tasks.add_task(_run)
    return {"message": "CLIP 指标计算已在后台启动"}


@app.post("/api/metrics/{task_id}/feedback", summary="提交 L3 用户体验反馈")
def submit_l3_feedback(task_id: str, req: L3FeedbackRequest):
    if not tasks.get(task_id):
        raise HTTPException(status_code=404, detail="task not found")
    if req.first_video_satisfaction not in (None, "yes", "no"):
        raise HTTPException(status_code=422, detail="first_video_satisfaction 须为 'yes' 或 'no'")
    if req.controllability_rating is not None and req.controllability_rating not in range(1, 6):
        raise HTTPException(status_code=422, detail="controllability_rating 须为 1-5 整数")

    mc.record_l3_feedback(task_id, req.first_video_satisfaction, req.controllability_rating)
    return {"message": "L3 反馈已记录", "task_id": task_id}


def _clip_available() -> bool:
    try:
        from metrics.clip_scorer import is_available
        return is_available()
    except Exception:
        return False


# Production deploy: serve the built Vite app from the same origin as the API.
# API and /outputs mounts are registered first, so they keep precedence.
FRONTEND_DIST = Path(__file__).parent / "movieagent-live-frontend" / "dist"
if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")

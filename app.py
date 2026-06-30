from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import time
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
from generation.concat import concat_videos
from generation.postprocess import prepare_dubbing_assets
from generation.shot_pipeline import build_shot_id, generate_shot_video_artifacts, shot_video_complete
from generation.subtitles import burn_subtitles, merge_sidecar_subtitles

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
os.makedirs("outputs/subtitles", exist_ok=True)
os.makedirs("outputs/characters", exist_ok=True)
os.makedirs("outputs/metrics", exist_ok=True)
app.mount("/outputs", StaticFiles(directory="outputs"), name="outputs")


# ── 请求体数据模型 ─────────────────────────────────────────

class GenerateRequest(BaseModel):
    script_synopsis: str
    characters: list[str]
    character_refs: dict
    voice_refs: dict = {}

class KeyframeRequest(BaseModel):
    task_id: str
    sub_script_name: str
    scene_name: str
    shot_name: str

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

@app.post("/api/generate", summary="启动完整规划 pipeline")
def start_generate(req: GenerateRequest, background_tasks: BackgroundTasks):
    task_id = create_task()
    tasks[task_id]["character_refs"] = req.character_refs or {}
    tasks[task_id]["voice_refs"] = req.voice_refs or {}
    background_tasks.add_task(
        run_full_pipeline, task_id, req.script_synopsis, req.characters
    )
    return {"task_id": task_id}


def _character_refs_for_shot(task_character_refs: dict, involving) -> dict:
    """按 Shot 的 Involving Characters 从任务级 character_refs 中取路径。"""
    if not task_character_refs:
        return {}
    out = {}
    if isinstance(involving, dict):
        names = involving.keys()
    elif isinstance(involving, list):
        names = involving
    else:
        return {}
    for name in names:
        if name in task_character_refs:
            out[name] = task_character_refs[name]
    return out


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
    matched_refs = _character_refs_for_shot(
        task.get("character_refs") or {},
        shot_data.get("Involving Characters"),
    )

    try:
        result = generate_keyframe(plot, shot_id, matched_refs)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    # 写回任务状态
    shot_ref = tasks[req.task_id]["shots"][req.sub_script_name][req.scene_name]["Shot"][req.shot_name]
    shot_ref["keyframe_local_path"] = result["local_path"]
    shot_ref["keyframe_url"] = f"/outputs/keyframes/{shot_id}.png"
    shot_ref["keyframe_status"] = "done"

    mc.record_keyframe_latency(req.task_id, shot_id, result["elapsed_seconds"])

    return {
        "keyframe_url": f"/outputs/keyframes/{shot_id}.png",
        "elapsed_seconds": result["elapsed_seconds"],
        "keyframe_size": result.get("keyframe_size"),
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
        matched_refs = _character_refs_for_shot(
            task.get("character_refs") or {},
            (shot_data or {}).get("Involving Characters"),
        )
        result = generate_keyframe(plot, shot_id, matched_refs, mode=req.mode)
        return ss_name, scene_name, shot_name, shot_id, result

    start = time.time()
    results = []
    errors = []
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
        "subtitle_url": shot_ref["subtitle_url"],
        "subtitle_srt_url": shot_ref["subtitle_srt_url"],
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
                        items.append({
                            "video_path": vp,
                            "subtitle_srt_path": (shot_data or {}).get("subtitle_srt_local_path"),
                            "subtitle_vtt_path": (shot_data or {}).get("subtitle_local_path"),
                        })
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

    subtitle_srt_path = os.path.join("outputs/subtitles", f"{req.task_id}_final.srt")
    subtitle_vtt_path = os.path.join("outputs/subtitles", f"{req.task_id}_final.vtt")
    subtitle_result = merge_sidecar_subtitles(
        paths,
        [item.get("subtitle_srt_path") for item in media_items],
        subtitle_srt_path,
        subtitle_vtt_path,
    )
    subtitled_path = os.path.join("outputs/videos", f"{req.task_id}_final_subtitled.mp4")
    subtitled_rel = f"/outputs/videos/{req.task_id}_final_subtitled.mp4"
    if subtitle_result["entries"] > 0:
        burn_subtitles(out_path, subtitle_srt_path, subtitled_path)
    else:
        subtitled_path = out_path
        subtitled_rel = out_rel

    return {
        "final_video_url": out_rel,
        "final_subtitled_video_url": subtitled_rel,
        "final_subtitle_url": f"/outputs/subtitles/{req.task_id}_final.vtt",
        "final_subtitle_srt_url": f"/outputs/subtitles/{req.task_id}_final.srt",
        "subtitle_entries": subtitle_result["entries"],
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

    subtitles = shot_data.get("Subtitles") or {}
    if not subtitles:
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
    shot_ref["subtitle_local_path"] = assets.get("subtitle_local_path")
    shot_ref["subtitle_srt_local_path"] = assets.get("subtitle_srt_local_path")
    shot_ref["combined_audio_local_path"] = assets.get("combined_audio_local_path")
    save_tasks()

    return {
        "audio_files": shot_ref["audio_files"],
        "subtitle_local_path": shot_ref["subtitle_local_path"],
        "subtitle_srt_local_path": shot_ref["subtitle_srt_local_path"],
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

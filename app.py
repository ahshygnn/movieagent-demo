import base64
import json
import os
import uuid
from typing import List

import requests
from fastapi import FastAPI, BackgroundTasks, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import config
from pipeline import tasks, create_task, run_full_pipeline, save_tasks

from generation.image import generate_keyframe
from generation.video import generate_video
from generation.postprocess import postprocess_shot_video
from generation.concat import concat_videos

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

    shot_id = f"{req.task_id}_{req.sub_script_name}_{req.scene_name}_{req.shot_name}".replace(" ", "_")
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

    return {
        "keyframe_url": f"/outputs/keyframes/{shot_id}.png",
        "elapsed_seconds": result["elapsed_seconds"]
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

    shot_id = f"{req.task_id}_{req.sub_script_name}_{req.scene_name}_{req.shot_name}".replace(" ", "_")
    # motion prompt = 运镜描述 + 粗粒度剧情
    motion_prompt = (
        shot_data.get("Camera Movement", "") + ". " +
        shot_data.get("Coarse Plot", "")
    ).strip()

    try:
        result = generate_video(shot_id, keyframe_path, motion_prompt)
        raw_video_path = result["local_path"]
        if config.ENABLE_DUBBING:
            merged_voice = dict(task.get("voice_refs") or {})
            post_result = postprocess_shot_video(
                shot_id,
                raw_video_path,
                shot_data,
                merged_voice,
            )
        else:
            post_result = {
                "local_path": raw_video_path,
                "dubbed": False,
                "audio_files": {},
                "subtitle_local_path": None,
                "subtitle_srt_local_path": None,
                "combined_audio_local_path": None,
            }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    shot_ref = tasks[req.task_id]["shots"][req.sub_script_name][req.scene_name]["Shot"][req.shot_name]
    shot_ref["raw_video_local_path"] = raw_video_path
    shot_ref["raw_video_url"] = f"/outputs/videos/{shot_id}.mp4"
    shot_ref["enhanced_video_local_path"] = (
        post_result["local_path"] if post_result.get("dubbed") else None
    )
    shot_ref["video_local_path"] = post_result["local_path"]
    shot_ref["video_url"] = (
        f"/outputs/videos/{shot_id}_dubbed.mp4"
        if post_result.get("dubbed")
        else f"/outputs/videos/{shot_id}.mp4"
    )
    shot_ref["subtitle_local_path"] = post_result.get("subtitle_local_path")
    shot_ref["subtitle_url"] = (
        f"/outputs/subtitles/{shot_id}.vtt"
        if shot_ref.get("subtitle_local_path")
        else None
    )
    shot_ref["subtitle_srt_local_path"] = post_result.get("subtitle_srt_local_path")
    shot_ref["subtitle_srt_url"] = (
        f"/outputs/subtitles/{shot_id}.srt"
        if shot_ref.get("subtitle_srt_local_path")
        else None
    )
    shot_ref["combined_audio_local_path"] = post_result.get("combined_audio_local_path")
    shot_ref["audio_files"] = post_result.get("audio_files") or {}
    shot_ref["video_has_dubbing"] = bool(post_result.get("dubbed"))
    shot_ref["generation_mode"] = config.GENERATION_MODE
    shot_ref["video_duration_seconds"] = result.get("duration_seconds")
    shot_ref["video_resolution"] = result.get("resolution")
    shot_ref["video_status"] = "done"
    save_tasks()

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
        "elapsed_seconds": result["elapsed_seconds"]
    }


def _collect_video_paths_for_final(task: dict, sub_script_names: list[str]) -> list[str]:
    """按 Sub-Script → Scene → Shot 顺序收集已有本地视频路径。"""
    shots_root = task.get("shots") or {}
    if sub_script_names:
        ss_order = [ss for ss in sub_script_names if ss in shots_root]
    else:
        ss_order = list(shots_root.keys())

    paths: list[str] = []
    for ss_name in ss_order:
        scenes_dict = shots_root[ss_name] or {}
        for _scene_name, scene_data in scenes_dict.items():
            shot_dict = (scene_data or {}).get("Shot") or {}
            for _shot_name, shot_data in shot_dict.items():
                for key in ("enhanced_video_local_path", "video_local_path", "raw_video_local_path"):
                    vp = (shot_data or {}).get(key)
                    if vp and isinstance(vp, str) and os.path.isfile(vp):
                        paths.append(vp)
                        break
    return paths


@app.post("/api/generate/final_video", summary="拼接任务内已生成的镜头视频为成片")
def api_generate_final_video(req: FinalVideoRequest):
    task = tasks.get(req.task_id)
    if not task:
        return {"error": "task not found"}

    paths = _collect_video_paths_for_final(task, req.sub_script_names or [])
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

    return {"final_video_url": out_rel, "concat_method": concat_method}


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

    def _regen():
        out = run_scene_agent(ss_data["Plot"], relationships)
        tasks[req.task_id]["scenes"][req.sub_script_name] = out["result"]
        tasks[req.task_id]["logs"].append(f"✅ {req.sub_script_name} 场景重新规划完成")

    background_tasks.add_task(_regen)
    return {"message": f"正在重新规划 {req.sub_script_name} 的场景"}


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

    def _regen():
        out = run_shot_agent(scene_data)
        tasks[req.task_id]["shots"][req.sub_script_name][req.scene_name] = out["result"]
        tasks[req.task_id]["logs"].append(f"✅ {req.scene_name} 镜头重新规划完成")

    background_tasks.add_task(_regen)
    return {"message": f"正在重新规划 {req.scene_name} 的镜头"}


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
    try:
        names = json.loads(character_names)
        parsed_texts = json.loads(texts)
    except json.JSONDecodeError:
        return JSONResponse({"error": "character_names 或 texts 不是合法 JSON 字符串"}, 400)

    if len(names) != len(parsed_texts) or len(parsed_texts) != len(files):
        return JSONResponse({"error": "角色名、文字和文件数量不一致"}, 400)

    headers = {
        "Authorization": f"Bearer {config.SILICONFLOW_API_KEY}",
        "Content-Type": "application/json",
    }
    results = {}
    for name, text, file in zip(names, parsed_texts, files):
        raw = await file.read()
        b64 = base64.b64encode(raw).decode()

        fname = file.filename or "audio.bin"
        ext = fname.rsplit(".", 1)[-1].lower()
        mime = {
            "mp3": "audio/mpeg",
            "mpeg": "audio/mpeg",
            "wav": "audio/wav",
            "m4a": "audio/mp4",
        }.get(ext, "audio/mpeg")
        audio_data = f"data:{mime};base64,{b64}"

        payload = {
            "model": "FunAudioLLM/CosyVoice2-0.5B",
            "customName": f"{str(name).replace(' ', '_')}_{uuid.uuid4().hex[:8]}",
            "audio": audio_data,
            "text": text,
        }
        try:
            resp = requests.post(
                f"{config.BASE_URL}/uploads/audio/voice",
                json=payload,
                headers=headers,
                timeout=120,
            )
            if resp.status_code != 200:
                raise HTTPException(
                    status_code=500,
                    detail=f"角色 {name} 上传失败 {resp.status_code}: {resp.text}",
                )
            data = resp.json()
            uri = data.get("uri")
            if not uri:
                raise HTTPException(
                    status_code=500,
                    detail=f"角色 {name} 上传失败，响应无 uri 字段: {data}",
                )
            results[name] = uri
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"角色 {name} 上传失败: {e}") from e

    return {
        "results": results,
        "message": f"成功上传 {len(results)} 个角色参考音频",
    }


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

    shot_id = f"{req.task_id}_{req.sub_script_name}_{req.scene_name}_{req.shot_name}".replace(" ", "_")
    os.makedirs("outputs/audio", exist_ok=True)

    headers = {
        "Authorization": f"Bearer {config.SILICONFLOW_API_KEY}",
        "Content-Type": "application/json",
    }
    audio_files = {}
    for char_name, line_text in subtitles.items():
        voice_uri = merged_voice.get(char_name) or config.DEFAULT_TTS_VOICE
        if not voice_uri:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"角色 {char_name} 没有可用音色：请上传角色参考音频，"
                    "或在 .env 中配置 DEFAULT_TTS_VOICE。"
                ),
            )
        payload = {
            "model": "FunAudioLLM/CosyVoice2-0.5B",
            "input": line_text,
            "voice": voice_uri,
            "response_format": "mp3",
            "stream": False,
        }
        resp = requests.post(
            f"{config.BASE_URL}/audio/speech",
            json=payload,
            headers=headers,
            timeout=120,
        )
        if resp.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"语音合成失败 {resp.status_code}: {resp.text}",
            )

        safe_char = str(char_name).replace(" ", "_")
        local_path = os.path.join("outputs/audio", f"{shot_id}_{safe_char}.mp3")
        with open(local_path, "wb") as f:
            f.write(resp.content)
        audio_files[char_name] = local_path

    tasks[req.task_id]["shots"][req.sub_script_name][req.scene_name]["Shot"][req.shot_name]["audio_files"] = audio_files
    save_tasks()

    return {"audio_files": audio_files}


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

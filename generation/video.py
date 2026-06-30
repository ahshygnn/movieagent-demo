"""
火山方舟 Seedance 1.0 Pro 视频生成（直接调用 Ark API）
鉴权：Bearer ARK_API_KEY（与 Seedream 图片生成同一个 Key）
"""
import base64
import os
import time
import requests
import config


ARK_BASE = "https://ark.cn-beijing.volces.com/api/v3"


def _valid_video_file(path: str, min_bytes: int = 4096) -> bool:
    try:
        return os.path.isfile(path) and os.path.getsize(path) >= min_bytes
    except OSError:
        return False


def _ark_headers() -> dict:
    return {
        "Authorization": f"Bearer {config.ARK_API_KEY}",
        "Content-Type": "application/json",
    }


def build_video_prompt(motion_prompt: str, duration_seconds: int, resolution: str) -> str:
    return (
        f"{motion_prompt} --resolution {resolution} "
        f"--duration {int(duration_seconds)} --watermark false"
    )


def submit_video(
    keyframe_path: str,
    motion_prompt: str,
    duration_seconds: int | None = None,
    resolution: str | None = None,
) -> str:
    """提交 Seedance 视频生成任务，返回 task_id。"""
    with open(keyframe_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()

    ext = keyframe_path.rsplit(".", 1)[-1].lower() if "." in keyframe_path else "png"
    mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg",
            "png": "image/png", "webp": "image/webp"}.get(ext, "image/png")
    duration = int(duration_seconds or config.VIDEO_DURATION_SECONDS)
    video_resolution = (resolution or config.VIDEO_RESOLUTION or "720p").strip()

    payload = {
        "model": config.VIDEO_MODEL,
        "content": [
            {
                "type": "text",
                "text": build_video_prompt(motion_prompt, duration, video_resolution),
            },
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
            },
        ],
    }

    resp = requests.post(
        f"{ARK_BASE}/contents/generations/tasks",
        json=payload,
        headers=_ark_headers(),
        timeout=120,   # base64 大图上传需要更长时间
    )
    if resp.status_code not in (200, 201):
        raise Exception(f"Seedance 任务提交失败 {resp.status_code}: {resp.text}")

    data = resp.json()
    task_id = data.get("id")
    if not task_id:
        raise Exception(f"Seedance 未返回 task id: {data}")
    return task_id


def poll_video_status(task_id: str) -> dict:
    """
    轮询 Seedance 任务状态。
    status: queued → running → succeeded / failed
    """
    max_wait = 300
    interval = 5
    waited = 0

    while waited < max_wait:
        time.sleep(interval)
        waited += interval

        resp = requests.get(
            f"{ARK_BASE}/contents/generations/tasks/{task_id}",
            headers=_ark_headers(),
            timeout=90,
        )
        if resp.status_code != 200:
            raise Exception(f"Seedance 状态查询失败 {resp.status_code}: {resp.text}")

        data = resp.json()
        status = data.get("status")

        if status == "succeeded":
            content = data.get("content", {})
            # content 是 dict：{"video_url": "https://..."}
            video_url = content.get("video_url") if isinstance(content, dict) else None
            if not video_url:
                raise Exception(f"Seedance 返回 succeeded 但无 video_url: {data}")
            return {"status": "succeeded", "video_url": video_url}

        elif status == "failed":
            reason = data.get("error", {}).get("message", "未知错误")
            return {"status": "failed", "reason": reason}
        # queued / running，继续等待

    return {"status": "failed", "reason": f"轮询超时（{max_wait} 秒）"}


def generate_video(
    shot_id: str,
    keyframe_path: str,
    motion_prompt: str,
    duration_seconds: int | None = None,
    resolution: str | None = None,
) -> dict:
    """完整流程：提交 → 轮询 → 下载到本地。"""
    start = time.time()
    duration = int(duration_seconds or config.VIDEO_DURATION_SECONDS)
    video_resolution = (resolution or config.VIDEO_RESOLUTION or "720p").strip()
    os.makedirs(config.VIDEO_DIR, exist_ok=True)
    local_path = os.path.join(config.VIDEO_DIR, f"{shot_id}.mp4")

    if _valid_video_file(local_path):
        return {
            "local_path": local_path,
            "elapsed_seconds": 0.0,
            "duration_seconds": duration,
            "resolution": video_resolution,
            "cache_hit": True,
        }

    task_id = submit_video(keyframe_path, motion_prompt, duration, video_resolution)
    result = poll_video_status(task_id)

    if result["status"] != "succeeded":
        raise Exception(f"视频生成失败：{result.get('reason')}")

    last_dl_err = None
    for dl_attempt in range(3):
        try:
            video_resp = requests.get(result["video_url"], timeout=180, stream=True)
            video_resp.raise_for_status()
            with open(local_path, "wb") as f:
                for chunk in video_resp.iter_content(chunk_size=1024 * 256):
                    if chunk:
                        f.write(chunk)
            if _valid_video_file(local_path):
                break
            raise OSError(f"下载完成但文件无效: {local_path}")
        except Exception as e:
            last_dl_err = e
            if os.path.isfile(local_path):
                try:
                    os.remove(local_path)
                except OSError:
                    pass
            if dl_attempt < 2:
                print(f"  [视频下载重试 {dl_attempt + 2}/3] {e}")
                time.sleep(5 * (dl_attempt + 1))
                continue
            raise Exception(f"视频下载失败（已重试 3 次）：{last_dl_err}") from last_dl_err

    elapsed = time.time() - start
    return {
        "local_path": local_path,
        "elapsed_seconds": round(elapsed, 2),
        "duration_seconds": duration,
        "resolution": video_resolution,
        "cache_hit": False,
    }

"""
火山方舟 Seedream 5.0 lite 图片生成
支持最多 14 张角色参考图（输入图 + 输出图 ≤ 15）
"""
import time
import base64
import contextlib
import os
import requests
import config


@contextlib.contextmanager
def _no_proxy():
    """临时移除代理环境变量，让火山方舟请求直连，用完后还原。"""
    keys = ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"]
    saved = {k: os.environ.pop(k, None) for k in keys}
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v

# 加在 prompt 前，降低安全过滤误触发概率
_SAFE_PREFIX = "Family-friendly animated film scene, Disney/Pixar style, safe for all ages. "


def _image_to_base64(path: str) -> str:
    """把本地图片转成 base64 data URL。"""
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else "png"
    mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp"}.get(ext, "png")
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    return f"data:image/{mime};base64,{b64}"


def _get_keyframe_size(mode: str | None = None) -> str:
    """返回当前档位对应的关键帧尺寸字符串。"""
    m = mode or config.KEYFRAME_MODE
    return config.KEYFRAME_SIZE_DRAFT if m == "draft" else config.KEYFRAME_SIZE_FINAL


def _call_api(prompt: str, ref_images: list, size: str = "", timeout: int = 150) -> str:
    """调用 Seedream API，返回图片 URL。失败时抛出异常。"""
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config.ARK_API_KEY}",
    }
    payload = {
        "model": "doubao-seedream-5-0-260128",
        "prompt": prompt,
        "size": size or _get_keyframe_size(),
        "output_format": "png",
        "watermark": False,
        "response_format": "url",
    }
    if ref_images:
        payload["image"] = ref_images if len(ref_images) > 1 else ref_images[0]

    resp = requests.post(
        "https://ark.cn-beijing.volces.com/api/v3/images/generations",
        headers=headers,
        json=payload,
        timeout=timeout,
    )
    if resp.status_code != 200:
        raise Exception(f"Seedream API 请求失败 {resp.status_code}: {resp.text}")
    return resp.json()["data"][0]["url"]


def generate_keyframe(shot_plot: str, shot_id: str,
                      character_refs: dict = None,
                      mode: str | None = None) -> dict:
    """
    调用火山方舟 Seedream 5.0 生成关键帧。

    character_refs: {"角色名": "/local/path/to/image.png", ...}
    最多传 14 张参考图（接口限制：输入图 + 输出图 ≤ 15）。
    mode: "draft"（1024x576）或 "final"（1280x720），None 则读 config.KEYFRAME_MODE。
    """
    start = time.time()
    size = _get_keyframe_size(mode)

    # 构建参考图列表
    ref_images = []
    if character_refs:
        for _name, path in list(character_refs.items())[:14]:
            if os.path.isfile(path):
                ref_images.append(_image_to_base64(path))

    # 重试策略：
    #   第 1 次：加 safe prefix + 参考图
    #   第 2 次：加 safe prefix + 无参考图（排除参考图干扰）
    #   第 3 次：只用简化 prompt + 无参考图
    attempts = [
        (_SAFE_PREFIX + shot_plot, ref_images),
        (_SAFE_PREFIX + shot_plot, []),
        (_SAFE_PREFIX + "Animated movie scene. " + shot_plot[:200], []),
    ]

    last_err = None
    for i, (prompt, images) in enumerate(attempts):
        try:
            with _no_proxy():
                image_url = _call_api(prompt, images, size=size)
            break
        except Exception as e:
            last_err = e
            err_str = str(e)
            if "SensitiveContent" in err_str or "sensitive" in err_str.lower():
                print(f"  [内容过滤，尝试 {i+2}/3] 简化 prompt 重试...")
                time.sleep(2)
                continue
            elif "timed out" in err_str.lower() or "timeout" in err_str.lower():
                print(f"  [超时，尝试 {i+2}/3] 重试...")
                time.sleep(5)
                continue
            else:
                raise  # 其他错误直接抛出
    else:
        raise Exception(f"生成关键帧失败（已重试 3 次）：{last_err}")

    # 下载图片保存到本地（带重试，大图下载易断连）
    os.makedirs(config.KEYFRAME_DIR, exist_ok=True)
    local_path = os.path.join(config.KEYFRAME_DIR, f"{shot_id}.png")
    last_dl_err = None
    for dl_attempt in range(3):
        try:
            with _no_proxy():
                img_resp = requests.get(image_url, timeout=120, stream=True)
            img_resp.raise_for_status()
            with open(local_path, "wb") as f:
                for chunk in img_resp.iter_content(chunk_size=1024 * 256):
                    if chunk:
                        f.write(chunk)
            break
        except Exception as e:
            last_dl_err = e
            print(f"  [图片下载重试 {dl_attempt+2}/3] {e}")
            time.sleep(3)
    else:
        raise Exception(f"图片下载失败（已重试 3 次）：{last_dl_err}")

    elapsed = round(time.time() - start, 2)
    return {
        "local_path": local_path,
        "elapsed_seconds": elapsed,
        "inference_time": 0,
        "keyframe_size": size,
    }
